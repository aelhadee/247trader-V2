"""
Model client abstraction for AI providers (OpenAI, Anthropic, local models).

Handles API calls, timeouts, retries, and JSON response parsing.
"""

import json
import logging
import time
from typing import Any, Dict, Optional
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class ModelClient(ABC):
    """Abstract base class for AI model clients."""
    
    @abstractmethod
    def call(self, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """
        Call the model with a request payload.
        
        Args:
            request: Request dict with market/portfolio/proposals
            timeout: Max time in seconds
            
        Returns:
            Response dict with risk_mode and decisions
            
        Raises:
            TimeoutError: If call exceeds timeout
            Exception: On API or parsing errors
        """
        pass


class OpenAIClient(ModelClient):
    """OpenAI GPT-5 client implementation."""
    
    def __init__(self, api_key: str, model: str = "gpt-5-mini-2025-08-07", base_url: Optional[str] = None):
        """
        Initialize OpenAI client.
        
        Args:
            api_key: OpenAI API key
            model: Model name (gpt-5-mini-2025-08-07, gpt-4o, o1-preview, etc.)
            base_url: Optional custom base URL
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url or "https://api.openai.com/v1"
        
        # Lazy import to avoid requiring openai unless used
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key, base_url=self.base_url, timeout=5.0)
        except ImportError:
            log.warning("openai package not installed - OpenAIClient will fail at runtime")
            self.client = None
    
    def call(self, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """Call OpenAI API with structured JSON response."""
        if not self.client:
            raise RuntimeError("OpenAI client not initialized - install openai package")
        
        start = time.perf_counter()
        
        # Build system prompt
        system_prompt = self._build_system_prompt()
        
        # Build user message with market context
        user_content = self._format_request(request)
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,  # Low temp for consistency
                timeout=timeout,
            )
            
            elapsed = time.perf_counter() - start
            log.info(f"OpenAI call completed in {elapsed*1000:.1f}ms")
            
            # Parse JSON response
            content = response.choices[0].message.content
            return json.loads(content)
            
        except Exception as e:
            elapsed = time.perf_counter() - start
            log.error(f"OpenAI call failed after {elapsed*1000:.1f}ms: {e}")
            raise
    
    def _build_system_prompt(self) -> str:
        """Build system prompt with AI advisor role and constraints."""
        return """You are a crypto trade reviewer for a Halal-compliant trading bot.

Your role:
- Review trade proposals and market context
- Suggest risk modes: OFF, DEFENSIVE, NORMAL, AGGRESSIVE
- Favor capital preservation in choppy regimes
- Respect all policy caps (you cannot override them)

Response format (valid JSON):
{
  "risk_mode": "DEFENSIVE|NORMAL|AGGRESSIVE|OFF",
  "decisions": [
    {
      "symbol": "BTC-USD",
      "side": "BUY",
      "decision": "accept|reduce|skip",
      "size_factor": 0.0-1.0,
      "comment": "brief reasoning"
    }
  ]
}

Rules:
- size_factor must be 0.0 to 1.0 (1.0 = accept full size)
- decision="skip" or size_factor=0 → drop trade entirely
- decision="reduce" → use size_factor between 0.0-1.0
- decision="accept" → size_factor should be 1.0
- Comment should explain why (e.g., "High conviction in trend regime", "Low conviction + choppy market")
"""
    
    def _format_request(self, request: Dict[str, Any]) -> str:
        """Format request dict as structured prompt."""
        market = request.get("market", {})
        portfolio = request.get("portfolio", {})
        proposals = request.get("proposals", [])
        
        parts = [
            "=== Market Context ===",
            f"Regime: {market.get('regime', 'unknown')}",
            f"NAV: ${market.get('nav', 0):.2f}",
            f"Exposure: {market.get('exposure_pct', 0):.1f}%",
        ]
        
        if market.get("drawdown_24h_pct") is not None:
            parts.append(f"24h Drawdown: {market['drawdown_24h_pct']:.2f}%")
        if market.get("realized_vol_24h") is not None:
            parts.append(f"24h Volatility: {market['realized_vol_24h']:.4f}")
        
        parts.extend([
            "",
            "=== Portfolio ===",
            f"Positions: {portfolio.get('num_positions', 0)}",
            f"24h P&L: ${portfolio.get('realized_pnl_24h', 0):.2f}",
        ])
        
        if portfolio.get("positions"):
            parts.append("Current positions:")
            for sym, exp in list(portfolio["positions"].items())[:5]:  # Top 5
                parts.append(f"  {sym}: ${exp:.2f}")
        
        parts.extend([
            "",
            "=== Proposals ===",
        ])
        
        for p in proposals:
            parts.append(
                f"{p['symbol']} {p['side']} | Tier: {p['tier']} | "
                f"Conviction: {p['conviction']:.2f} | ${p['notional']:.2f} | "
                f"{p['reason']}"
            )
        
        parts.append("")
        parts.append("Provide your decisions in JSON format.")
        
        return "\n".join(parts)


class AnthropicClient(ModelClient):
    """Anthropic Claude client implementation."""
    
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        """
        Initialize Anthropic client.
        
        Args:
            api_key: Anthropic API key
            model: Model name (claude-3-opus, claude-3-sonnet, etc.)
        """
        self.api_key = api_key
        self.model = model
        
        # Lazy import
        try:
            from anthropic import Anthropic
            self.client = Anthropic(api_key=api_key, timeout=5.0)
        except ImportError:
            log.warning("anthropic package not installed - AnthropicClient will fail at runtime")
            self.client = None
    
    def call(self, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """Call Anthropic API with structured JSON response."""
        if not self.client:
            raise RuntimeError("Anthropic client not initialized - install anthropic package")
        
        start = time.perf_counter()
        
        system_prompt = self._build_system_prompt()
        user_content = self._format_request(request)
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                temperature=0.3,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_content}
                ],
            )
            
            elapsed = time.perf_counter() - start
            log.info(f"Anthropic call completed in {elapsed*1000:.1f}ms")
            
            # Extract JSON from response
            content = response.content[0].text
            
            # Try to extract JSON if wrapped in markdown
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            
            return json.loads(content)
            
        except Exception as e:
            elapsed = time.perf_counter() - start
            log.error(f"Anthropic call failed after {elapsed*1000:.1f}ms: {e}")
            raise
    
    def _build_system_prompt(self) -> str:
        """Build system prompt - same as OpenAI."""
        return OpenAIClient._build_system_prompt(self)
    
    def _format_request(self, request: Dict[str, Any]) -> str:
        """Format request - same as OpenAI."""
        return OpenAIClient._format_request(self, request)


class MockClient(ModelClient):
    """Mock client for testing - always returns safe defaults."""
    
    def __init__(self, fixed_response: Optional[Dict[str, Any]] = None):
        """
        Initialize mock client.
        
        Args:
            fixed_response: Optional fixed response to return
        """
        self.fixed_response = fixed_response
    
    def call(self, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """Return mock response."""
        if self.fixed_response:
            return self.fixed_response
        
        # Default: accept all proposals at full size in NORMAL mode
        proposals = request.get("proposals", [])
        return {
            "risk_mode": "NORMAL",
            "decisions": [
                {
                    "symbol": p["symbol"],
                    "side": p["side"],
                    "decision": "accept",
                    "size_factor": 1.0,
                    "comment": "Mock approval",
                }
                for p in proposals
            ],
        }


def create_model_client(
    provider: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs
) -> ModelClient:
    """
    Factory function to create appropriate model client.
    
    Args:
        provider: "openai", "anthropic", or "mock"
        api_key: API key for the provider
        model: Model name (provider-specific)
        **kwargs: Additional provider-specific args
        
    Returns:
        ModelClient instance
        
    Raises:
        ValueError: If provider is unknown
    """
    provider = provider.lower()
    
    if provider == "openai":
        if not api_key:
            raise ValueError("OpenAI requires api_key")
        return OpenAIClient(api_key=api_key, model=model or "gpt-4o", **kwargs)
    
    elif provider == "anthropic":
        if not api_key:
            raise ValueError("Anthropic requires api_key")
        return AnthropicClient(api_key=api_key, model=model or "claude-3-5-sonnet-20241022")
    
    elif provider == "mock":
        return MockClient(fixed_response=kwargs.get("fixed_response"))
    
    else:
        raise ValueError(f"Unknown provider: {provider}. Use 'openai', 'anthropic', or 'mock'")
