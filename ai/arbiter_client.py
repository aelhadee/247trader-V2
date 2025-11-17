"""
AI Arbiter Client - Model #2 for conflict resolution.

Narrow-purpose LLM client that acts as tie-breaker when local and AI
trader have conflicting proposals. Returns simple resolution decision.
"""

import json
import logging
from typing import Any, Dict, Literal
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ─── Data Structures ───────────────────────────────────────────────────────

@dataclass
class ArbiterInput:
    """Input to arbiter for conflict resolution."""
    symbol: str
    market_snapshot: Dict[str, Any]
    local_decision: Dict[str, Any]
    ai_decision: Dict[str, Any]
    guardrails: Dict[str, Any]


@dataclass
class ArbiterOutput:
    """Arbiter's resolution decision."""
    resolution: Literal["LOCAL", "AI", "BLEND", "NONE"]
    final_size_pct: float
    comment: str

    def __post_init__(self):
        """Validate output."""
        if self.resolution not in ["LOCAL", "AI", "BLEND", "NONE"]:
            raise ValueError(f"Invalid resolution: {self.resolution}")
        self.final_size_pct = max(0.0, min(100.0, self.final_size_pct))
        self.comment = self.comment[:500]


# ─── AI Arbiter Client ─────────────────────────────────────────────────────

class AiArbiterClient:
    """
    Client for AI-based conflict arbitration.
    
    Used only when deterministic rules can't resolve conflicts.
    Much narrower scope than AI trader:
    - Only chooses between known options (LOCAL/AI/BLEND/NONE)
    - Cannot invent new trades
    - Simple output schema
    """
    
    def __init__(
        self,
        provider: Literal["openai", "anthropic"],
        model: str,
        api_key: str,
        timeout_s: float = 1.5,
        max_tokens: int = 500,
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
    
    def arbitrate(self, arb_input: ArbiterInput) -> ArbiterOutput:
        """
        Arbitrate between local and AI decisions.
        
        Args:
            arb_input: Input with local/AI decisions and context
            
        Returns:
            ArbiterOutput with resolution choice
            
        Raises:
            Exception on failure (caller should handle)
        """
        try:
            prompt = self._build_prompt(arb_input)
            
            if self.provider == "openai":
                output = self._call_openai(prompt)
            else:  # anthropic
                output = self._call_anthropic(prompt)
            
            logger.info(
                f"Arbiter resolved {arb_input.symbol}: {output.resolution} "
                f"(size={output.final_size_pct:.2f}%)"
            )
            
            return output
            
        except Exception as e:
            logger.error(f"Arbiter failed for {arb_input.symbol}: {e}", exc_info=True)
            raise
    
    def _build_prompt(self, arb_input: ArbiterInput) -> str:
        """Build focused prompt for arbiter."""
        
        system_msg = """You are a trade arbitrator. Two trading systems have conflicting proposals for the same symbol.

Your ONLY job: choose LOCAL, AI, BLEND, or NONE.

CRITICAL CONSTRAINTS:
- You CANNOT invent new trades
- You MUST pick from the given options
- Your output MUST be valid JSON

OUTPUT FORMAT:
{
  "resolution": "LOCAL | AI | BLEND | NONE",
  "final_size_pct": <number>,
  "comment": "<brief explanation>"
}

BLEND means: average the two sizes.
NONE means: stand down, no trade."""
        
        user_msg = f"""CONFLICT TO RESOLVE:

Symbol: {arb_input.symbol}

LOCAL DECISION:
- Side: {arb_input.local_decision.get('side', 'UNKNOWN')}
- Size: {arb_input.local_decision.get('size_pct', 0):.2f}%
- Conviction: {arb_input.local_decision.get('conviction', 0):.2f}
- Rationale: {arb_input.local_decision.get('rationale', 'N/A')[:200]}

AI DECISION:
- Side: {arb_input.ai_decision.get('side', 'UNKNOWN')}
- Size: {arb_input.ai_decision.get('size_pct', 0):.2f}%
- Confidence: {arb_input.ai_decision.get('confidence', 0):.2f}
- Rationale: {arb_input.ai_decision.get('rationale', 'N/A')[:200]}

MARKET SNAPSHOT:
- Price: ${arb_input.market_snapshot.get('price', 0):.4f}
- 1h change: {arb_input.market_snapshot.get('change_1h_pct', 0):+.2f}%
- 24h change: {arb_input.market_snapshot.get('change_24h_pct', 0):+.2f}%
- Volatility: {arb_input.market_snapshot.get('volatility', 0):.2%}
- Regime: {arb_input.market_snapshot.get('regime', 'UNKNOWN')}

GUARDRAILS:
- Max position size: {arb_input.guardrails.get('max_position_size_pct', 7)}%
- Max total at risk: {arb_input.guardrails.get('max_total_at_risk_pct', 25)}%

Choose: LOCAL, AI, BLEND, or NONE. Return JSON only."""
        
        return system_msg + "\n\n" + user_msg
    
    def _call_openai(self, prompt: str) -> ArbiterOutput:
        """Call OpenAI and parse response."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=self.max_tokens,
            temperature=0.3,  # lower temp for more deterministic
        )
        
        content = response.choices[0].message.content
        data = json.loads(content)
        
        return self._parse_output(data)
    
    def _call_anthropic(self, prompt: str) -> ArbiterOutput:
        """Call Anthropic and parse response."""
        response = self.client.messages.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
            temperature=0.3,
        )
        
        content = response.content[0].text
        
        # Extract JSON from markdown if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        data = json.loads(content)
        
        return self._parse_output(data)
    
    def _parse_output(self, data: Dict) -> ArbiterOutput:
        """Parse and validate arbiter output."""
        if not all(k in data for k in ["resolution", "final_size_pct", "comment"]):
            raise ValueError(f"Missing required fields in arbiter output: {data}")
        
        # Create output (post_init will validate)
        return ArbiterOutput(
            resolution=data["resolution"],
            final_size_pct=float(data["final_size_pct"]),
            comment=data["comment"],
        )


# ─── Mock Arbiter for Testing ──────────────────────────────────────────────

class MockAiArbiterClient:
    """Mock arbiter that returns deterministic resolutions."""
    
    def __init__(self, default_resolution: str = "NONE"):
        self.default_resolution = default_resolution
        self.call_count = 0
    
    def arbitrate(self, arb_input: ArbiterInput) -> ArbiterOutput:
        """Return deterministic resolution."""
        self.call_count += 1
        
        # Simple logic: if both high confidence, blend
        local_conv = arb_input.local_decision.get("conviction", 0)
        ai_conf = arb_input.ai_decision.get("confidence", 0)
        
        if local_conv > 0.7 and ai_conf > 0.7:
            resolution = "BLEND"
            size = (arb_input.local_decision.get("size_pct", 0) + 
                    arb_input.ai_decision.get("size_pct", 0)) / 2
        else:
            resolution = self.default_resolution
            size = 0.0
        
        return ArbiterOutput(
            resolution=resolution,
            final_size_pct=size,
            comment=f"Mock arbiter: {resolution}",
        )


# ─── Factory ───────────────────────────────────────────────────────────────

def create_ai_arbiter_client(
    provider: str,
    model: str,
    api_key: str,
    timeout_s: float = 1.5,
    **kwargs
) -> AiArbiterClient:
    """Factory for creating AI arbiter clients."""
    return AiArbiterClient(
        provider=provider,
        model=model,
        api_key=api_key,
        timeout_s=timeout_s,
        **kwargs
    )
