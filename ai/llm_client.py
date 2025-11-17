"""
AI Trader LLM Client - Direct trade decision interface for AI models.

This module provides structured communication with LLMs to get trade decisions,
enforcing JSON schemas, numeric bounds, and safe error handling.
"""

import json
import logging
from dataclasses import dataclass, asdict
from typing import Literal, Any, Optional

logger = logging.getLogger(__name__)

# ─── Data Structures ───────────────────────────────────────────────────────

@dataclass
class AiTradeDecision:
    """Single trade decision from AI model."""
    symbol: str
    action: Literal["BUY", "SELL", "HOLD", "NONE"]
    target_weight_pct: float      # desired *final* position size as % of NAV
    confidence: float             # 0–1
    time_horizon_minutes: int
    rationale: str

    def __post_init__(self):
        """Clamp values to sane ranges."""
        self.target_weight_pct = max(0.0, min(100.0, self.target_weight_pct))
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.time_horizon_minutes = max(1, min(1440, self.time_horizon_minutes))  # 1 min - 24h
        self.rationale = self.rationale[:500]  # cap rationale length


# ─── AI Trader Client ──────────────────────────────────────────────────────

class AiTraderClient:
    """
    Client for getting trade decisions from LLM models.
    
    Responsibilities:
    - Build structured prompts with market snapshot
    - Enforce JSON schema on LLM output
    - Clamp nonsense values (negative sizes, >100%, etc.)
    - Handle errors gracefully (return [] on failure)
    """
    
    def __init__(
        self,
        provider: Literal["openai", "anthropic"],
        model: str,
        api_key: str,
        timeout_s: float = 2.0,
        max_tokens: int = 2000,
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        
        # Lazy-import provider SDKs
        if provider == "openai":
            import openai
            self.client = openai.OpenAI(api_key=api_key, timeout=timeout_s)
        elif provider == "anthropic":
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)
        else:
            raise ValueError(f"Unknown provider: {provider}")
    
    def get_decisions(
        self,
        snapshot: dict[str, Any],
        max_decisions: int = 5,
    ) -> list[AiTradeDecision]:
        """
        Get trade decisions from LLM.
        
        Args:
            snapshot: Market snapshot with universe, positions, regime, guardrails
            max_decisions: Maximum number of trade decisions to return
            
        Returns:
            List of AiTradeDecision (empty on error)
        """
        try:
            prompt = self._build_prompt(snapshot, max_decisions)
            
            if self.provider == "openai":
                decisions = self._call_openai(prompt)
            else:  # anthropic
                decisions = self._call_anthropic(prompt)
            
            logger.info(f"AI trader returned {len(decisions)} decisions")
            return decisions
            
        except Exception as e:
            logger.error(f"AI trader failed: {e}", exc_info=True)
            return []
    
    def _build_prompt(self, snapshot: dict[str, Any], max_decisions: int) -> str:
        """Build structured prompt with market snapshot."""
        
        system_msg = """You are a quantitative crypto trader. Analyze the market snapshot and propose trades.

CRITICAL CONSTRAINTS:
- You can propose up to {max_decisions} trades
- Each trade must specify: symbol, action (BUY/SELL/HOLD/NONE), target_weight_pct (0-100), confidence (0-1), time_horizon_minutes, rationale
- target_weight_pct is the FINAL position size as % of NAV (not size change)
- Respect guardrails (max_position_size_pct, max_total_at_risk_pct, etc.)
- No leverage, no shorts
- Only trade symbols in the universe

RESPONSE FORMAT (JSON):
{{
  "decisions": [
    {{
      "symbol": "BTC-USD",
      "action": "BUY",
      "target_weight_pct": 5.0,
      "confidence": 0.85,
      "time_horizon_minutes": 120,
      "rationale": "Strong momentum with increasing volume..."
    }}
  ]
}}
""".format(max_decisions=max_decisions)
        
        # Build user message with snapshot
        user_msg = f"""MARKET SNAPSHOT:

Universe: {len(snapshot.get('universe', []))} symbols
Regime: {snapshot.get('regime', 'UNKNOWN')}
Current Positions: {snapshot.get('positions_count', 0)}
Available Capital: ${snapshot.get('available_capital_usd', 0):.2f}

GUARDRAILS:
- Max total at risk: {snapshot.get('guardrails', {}).get('max_total_at_risk_pct', 25)}%
- Max position size: {snapshot.get('guardrails', {}).get('max_position_size_pct', 7)}%
- Min trade notional: ${snapshot.get('guardrails', {}).get('min_trade_notional', 5)}

TOP UNIVERSE SYMBOLS:
{self._format_universe(snapshot.get('universe', [])[:20])}

CURRENT POSITIONS:
{self._format_positions(snapshot.get('positions', []))}

RECENT TRIGGERS:
{self._format_triggers(snapshot.get('triggers', [])[:10])}

Analyze and propose up to {max_decisions} trades. Return JSON only."""
        
        return system_msg + "\n\n" + user_msg
    
    def _format_universe(self, universe: list[dict]) -> str:
        """Format universe data for prompt."""
        if not universe:
            return "(empty)"
        
        lines = []
        for u in universe[:10]:  # top 10
            lines.append(
                f"  {u['symbol']}: ${u['price']:.4f} | "
                f"1h: {u.get('change_1h_pct', 0):+.2f}% | "
                f"24h: {u.get('change_24h_pct', 0):+.2f}% | "
                f"vol: {u.get('volatility', 0):.2%}"
            )
        return "\n".join(lines)
    
    def _format_positions(self, positions: list[dict]) -> str:
        """Format current positions for prompt."""
        if not positions:
            return "(none)"
        
        lines = []
        for p in positions:
            lines.append(
                f"  {p['symbol']}: {p['size']:.4f} @ ${p['avg_price']:.4f} | "
                f"PnL: {p.get('unrealized_pnl_pct', 0):+.2f}%"
            )
        return "\n".join(lines)
    
    def _format_triggers(self, triggers: list[dict]) -> str:
        """Format recent triggers for prompt."""
        if not triggers:
            return "(none)"
        
        lines = []
        for t in triggers[:5]:  # top 5
            lines.append(
                f"  {t['symbol']}: {t['type']} | "
                f"strength={t.get('strength', 0):.2f} | "
                f"confidence={t.get('confidence', 0):.2f}"
            )
        return "\n".join(lines)
    
    def _call_openai(self, prompt: str) -> list[AiTradeDecision]:
        """Call OpenAI API and parse response."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=self.max_tokens,
            temperature=0.7,
        )
        
        content = response.choices[0].message.content
        data = json.loads(content)
        
        return self._parse_decisions(data)
    
    def _call_anthropic(self, prompt: str) -> list[AiTradeDecision]:
        """Call Anthropic API and parse response."""
        response = self.client.messages.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
            temperature=0.7,
        )
        
        content = response.content[0].text
        
        # Extract JSON from markdown if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        data = json.loads(content)
        
        return self._parse_decisions(data)
    
    def _parse_decisions(self, data: dict) -> list[AiTradeDecision]:
        """Parse and validate decisions from LLM response."""
        decisions = []
        
        raw_decisions = data.get("decisions", [])
        if not isinstance(raw_decisions, list):
            logger.warning("AI response 'decisions' is not a list")
            return []
        
        for d in raw_decisions:
            try:
                # Validate required fields
                if not all(k in d for k in ["symbol", "action", "target_weight_pct", "confidence"]):
                    logger.warning(f"Missing required fields in decision: {d}")
                    continue
                
                # Validate action
                if d["action"] not in ["BUY", "SELL", "HOLD", "NONE"]:
                    logger.warning(f"Invalid action: {d['action']}")
                    continue
                
                # Create decision (post_init will clamp values)
                decision = AiTradeDecision(
                    symbol=d["symbol"],
                    action=d["action"],
                    target_weight_pct=float(d["target_weight_pct"]),
                    confidence=float(d["confidence"]),
                    time_horizon_minutes=int(d.get("time_horizon_minutes", 60)),
                    rationale=d.get("rationale", "No rationale provided"),
                )
                
                decisions.append(decision)
                
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Failed to parse decision: {d} - {e}")
                continue
        
        return decisions


# ─── Mock Client for Testing ───────────────────────────────────────────────

class MockAiTraderClient:
    """Mock client that returns deterministic decisions for testing."""
    
    def __init__(self, decisions: Optional[list[AiTradeDecision]] = None):
        self.decisions = decisions or []
        self.call_count = 0
    
    def get_decisions(
        self,
        snapshot: dict[str, Any],
        max_decisions: int = 5,
    ) -> list[AiTradeDecision]:
        """Return pre-configured decisions."""
        self.call_count += 1
        return self.decisions[:max_decisions]


# ─── Factory ───────────────────────────────────────────────────────────────

def create_ai_trader_client(
    provider: str,
    model: str,
    api_key: str,
    timeout_s: float = 2.0,
    **kwargs
) -> AiTraderClient:
    """
    Factory for creating AI trader clients.
    
    Args:
        provider: "openai" or "anthropic"
        model: Model identifier
        api_key: API key
        timeout_s: Request timeout
        
    Returns:
        AiTraderClient instance
    """
    return AiTraderClient(
        provider=provider,
        model=model,
        api_key=api_key,
        timeout_s=timeout_s,
        **kwargs
    )
