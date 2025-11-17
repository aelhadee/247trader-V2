"""
AI Advisor Service - Core decision layer.

Single entry point for AI-driven trade proposal filtering.
Handles model calls, response parsing, safety clamping, and fallback logic.
"""

import logging
import time
from typing import List, Optional

from .schemas import (
    AIAdvisorInput,
    AIAdvisorOutput,
    AIProposalDecision,
    RiskMode,
)
from .model_client import ModelClient

log = logging.getLogger(__name__)


class AIAdvisorService:
    """
    AI advisor service for filtering trade proposals.
    
    Core principles:
    - AI can only SHRINK, SKIP, or TAG trades
    - Never increases notional (size_factor capped at max_scale_up ≤ 1.0)
    - Falls back safely on any error (no AI influence)
    - Strict timeout enforcement
    - Full audit trail of decisions
    """
    
    def __init__(
        self,
        enabled: bool = False,
        timeout_s: float = 1.0,
        max_scale_up: float = 1.0,   # NEVER >1.0 in v1
        fallback_on_error: bool = True,
    ):
        """
        Initialize AI advisor service.
        
        Args:
            enabled: Whether AI advisor is active
            timeout_s: Hard timeout for model calls
            max_scale_up: Maximum size multiplier (must be ≤ 1.0)
            fallback_on_error: If True, errors → no AI influence (safe)
        """
        self.enabled = enabled
        self.timeout_s = timeout_s
        self.max_scale_up = min(max_scale_up, 1.0)  # Safety clamp
        self.fallback_on_error = fallback_on_error
        
        if max_scale_up > 1.0:
            log.warning(
                f"max_scale_up={max_scale_up} clamped to 1.0 for safety. "
                "AI cannot increase trade sizes in v1."
            )
    
    def advise(
        self,
        payload: AIAdvisorInput,
        model_client: ModelClient,
    ) -> AIAdvisorOutput:
        """
        Get AI advice on trade proposals.
        
        Args:
            payload: Input with market context and proposals
            model_client: Model client (OpenAI, Anthropic, etc.)
            
        Returns:
            AIAdvisorOutput with risk_mode and per-proposal decisions
            
        Note:
            On any error, returns empty output (no AI influence) if fallback_on_error=True
        """
        # Quick exit if disabled or no proposals
        if not self.enabled:
            log.debug("AI advisor disabled, skipping")
            return self._empty_output(reason="disabled")
        
        if not payload.proposals:
            log.debug("No proposals to advise on")
            return self._empty_output(reason="no_proposals")
        
        try:
            start = time.perf_counter()
            
            # 1) Convert payload to model request format
            req = self._to_model_request(payload)
            
            # 2) Call model with strict timeout
            resp = model_client.call(req, timeout=self.timeout_s)
            
            # 3) Parse and sanitize response
            output = self._parse_and_sanitize(resp, payload)
            
            # Record latency
            latency = (time.perf_counter() - start) * 1000
            output.latency_ms = latency
            output.model_used = getattr(model_client, "model", "unknown")
            
            log.info(
                f"AI advisor completed in {latency:.1f}ms: "
                f"risk_mode={output.risk_mode}, "
                f"decisions={len(output.proposal_decisions)}/{len(payload.proposals)}"
            )
            
            return output
            
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000 if 'start' in locals() else 0
            
            log.error(
                f"AI advisor error after {elapsed:.1f}ms: {e}",
                exc_info=True,
            )
            
            if self.fallback_on_error:
                log.warning("Falling back to no-AI mode (safe)")
                return self._empty_output(reason=f"error: {str(e)[:100]}")
            else:
                # Re-raise if strict mode
                raise
    
    def _to_model_request(self, payload: AIAdvisorInput) -> dict:
        """
        Convert AIAdvisorInput to model request format.
        
        Args:
            payload: Structured input payload
            
        Returns:
            Dict suitable for model client
        """
        return {
            "market": {
                "regime": payload.market.regime,
                "nav": payload.market.nav,
                "exposure_pct": payload.market.exposure_pct,
                "drawdown_24h_pct": payload.market.drawdown_24h_pct,
                "realized_vol_24h": payload.market.realized_vol_24h,
            },
            "portfolio": {
                "positions": payload.portfolio.positions,
                "realized_pnl_24h": payload.portfolio.realized_pnl_24h,
                "num_positions": payload.portfolio.num_positions,
            },
            "proposals": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "tier": p.tier,
                    "conviction": p.conviction,
                    "notional": p.notional,
                    "reason": p.reason,
                }
                for p in payload.proposals
            ],
        }
    
    def _parse_and_sanitize(
        self,
        resp: dict,
        payload: AIAdvisorInput,
    ) -> AIAdvisorOutput:
        """
        Parse model response and apply safety constraints.
        
        Args:
            resp: Raw response from model
            payload: Original input (for validation)
            
        Returns:
            Sanitized AIAdvisorOutput
        """
        # Extract risk mode
        risk_mode = resp.get("risk_mode")
        if risk_mode not in ("OFF", "DEFENSIVE", "NORMAL", "AGGRESSIVE", None):
            log.warning(f"Invalid risk_mode '{risk_mode}', ignoring")
            risk_mode = None
        
        # Extract decisions
        decisions_raw = resp.get("decisions", [])
        
        # Build lookup for valid (symbol, side) pairs
        valid_pairs = {(p.symbol, p.side) for p in payload.proposals}
        
        decisions: List[AIProposalDecision] = []
        
        for d in decisions_raw:
            symbol = d.get("symbol")
            side = d.get("side")
            
            # Ignore hallucinated entries
            if (symbol, side) not in valid_pairs:
                log.warning(f"AI returned decision for unknown proposal: {symbol} {side}")
                continue
            
            # Validate decision type
            decision = d.get("decision", "accept")
            if decision not in ("accept", "reduce", "skip"):
                log.warning(f"Invalid decision '{decision}' for {symbol}, treating as 'skip'")
                decision = "skip"
            
            # Clamp size_factor to [0, max_scale_up]
            size_factor = float(d.get("size_factor", 1.0))
            
            if size_factor > self.max_scale_up:
                log.warning(
                    f"AI tried size_factor={size_factor:.2f} for {symbol}, "
                    f"clamping to {self.max_scale_up:.2f}"
                )
                size_factor = self.max_scale_up
            
            if size_factor < 0.0:
                log.warning(f"Negative size_factor={size_factor:.2f} for {symbol}, clamping to 0")
                size_factor = 0.0
            
            # Consistency check: skip/reduce should have appropriate size_factor
            if decision == "skip" and size_factor > 0.0:
                log.warning(f"Decision=skip but size_factor={size_factor:.2f}, forcing to 0")
                size_factor = 0.0
            elif decision == "accept" and size_factor != 1.0:
                log.warning(
                    f"Decision=accept but size_factor={size_factor:.2f}, "
                    f"changing decision to 'reduce'"
                )
                decision = "reduce"
            
            decisions.append(
                AIProposalDecision(
                    symbol=symbol,
                    side=side,
                    decision=decision,
                    size_factor=size_factor,
                    comment=d.get("comment", "")[:200],  # Truncate long comments
                )
            )
        
        return AIAdvisorOutput(
            risk_mode=risk_mode,
            proposal_decisions=decisions,
        )
    
    def _empty_output(self, reason: str) -> AIAdvisorOutput:
        """
        Create empty output (no AI influence).
        
        Args:
            reason: Why output is empty (for audit)
            
        Returns:
            Empty AIAdvisorOutput
        """
        return AIAdvisorOutput(
            risk_mode=None,
            proposal_decisions=[],
            error=reason,
        )
