"""
Meta-Arbitration Layer - Merge proposals from multiple strategies.

Combines proposals from local rules engine and AI trader, applying
deterministic arbitration logic to resolve conflicts and agreements.
"""

import logging
from typing import List, Dict, Literal
from dataclasses import dataclass
from collections import defaultdict

from strategy.rules_engine import TradeProposal

logger = logging.getLogger(__name__)


# ─── Arbitration Result ────────────────────────────────────────────────────

@dataclass
class ArbitrationDecision:
    """Result of arbitrating between local and AI proposals."""
    symbol: str
    resolution: Literal["LOCAL", "AI", "BLEND", "NONE", "SINGLE"]
    final_proposal: TradeProposal | None
    local_proposal: TradeProposal | None
    ai_proposal: TradeProposal | None
    reason: str


# ─── Meta-Arbitrator ───────────────────────────────────────────────────────

class MetaArbitrator:
    """
    Arbitrates between local and AI trade proposals.
    
    Responsibilities:
    - Group proposals by symbol
    - Apply deterministic arbitration rules
    - Handle agreement, disagreement, single-source
    - Return final merged proposal list
    - Log all arbitration decisions for audit
    
    Arbitration Rules (v1 - deterministic):
    1. Single source → take it (with optional AI confidence filter)
    2. Agreement (same side) → blend sizes conservatively
    3. Low AI confidence → trust local
    4. High AI confidence + low local conviction → trust AI
    5. Otherwise → stand down (no trade)
    """
    
    def __init__(self, config: Dict):
        """
        Initialize arbitrator.
        
        Args:
            config: Arbitration config with thresholds
        """
        self.min_ai_confidence = config.get("min_ai_confidence", 0.6)
        self.ai_override_threshold = config.get("ai_override_threshold", 0.7)
        self.local_weak_conviction = config.get("local_weak_conviction", 0.35)
        self.ai_confidence_advantage = config.get("ai_confidence_advantage", 0.25)
        self.blend_mode = config.get("blend_mode", "conservative")  # conservative|average
        
        logger.info(
            f"MetaArbitrator initialized: min_ai_conf={self.min_ai_confidence}, "
            f"ai_override={self.ai_override_threshold}, blend={self.blend_mode}"
        )
    
    def aggregate_proposals(
        self,
        local_proposals: List[TradeProposal],
        ai_proposals: List[TradeProposal],
    ) -> tuple[List[TradeProposal], List[ArbitrationDecision]]:
        """
        Aggregate proposals from local and AI strategies.
        
        Args:
            local_proposals: Proposals from rules engine
            ai_proposals: Proposals from AI trader
            
        Returns:
            Tuple of (final proposals, arbitration decisions for audit)
        """
        # Index proposals by symbol
        by_symbol: Dict[str, Dict[str, TradeProposal]] = defaultdict(dict)
        
        for p in local_proposals:
            by_symbol[p.product_id]["local"] = p
        
        for p in ai_proposals:
            by_symbol[p.product_id]["ai"] = p
        
        final_proposals = []
        arbitration_log = []
        
        # Arbitrate each symbol
        for symbol, sources in by_symbol.items():
            decision = self._arbitrate_symbol(
                symbol,
                sources.get("local"),
                sources.get("ai"),
            )
            
            arbitration_log.append(decision)
            
            if decision.final_proposal:
                final_proposals.append(decision.final_proposal)
        
        logger.info(
            f"Arbitration complete: {len(local_proposals)} local + {len(ai_proposals)} AI "
            f"→ {len(final_proposals)} final proposals"
        )
        
        return final_proposals, arbitration_log
    
    def _arbitrate_symbol(
        self,
        symbol: str,
        local: TradeProposal | None,
        ai: TradeProposal | None,
    ) -> ArbitrationDecision:
        """
        Arbitrate proposals for a single symbol.
        
        Args:
            symbol: Symbol to arbitrate
            local: Local proposal (or None)
            ai: AI proposal (or None)
            
        Returns:
            ArbitrationDecision with resolution and final proposal
        """
        # Case 1: Neither present (shouldn't happen, but handle gracefully)
        if not local and not ai:
            return ArbitrationDecision(
                symbol=symbol,
                resolution="NONE",
                final_proposal=None,
                local_proposal=None,
                ai_proposal=None,
                reason="No proposals for symbol",
            )
        
        # Case 2: Only local present
        if local and not ai:
            return ArbitrationDecision(
                symbol=symbol,
                resolution="SINGLE",
                final_proposal=local,
                local_proposal=local,
                ai_proposal=None,
                reason=f"Local only: {local.side} {local.target_weight_pct:.2f}% (conv={local.conviction:.2f})",
            )
        
        # Case 3: Only AI present
        if ai and not local:
            # Apply confidence filter
            if ai.conviction < self.min_ai_confidence:
                return ArbitrationDecision(
                    symbol=symbol,
                    resolution="NONE",
                    final_proposal=None,
                    local_proposal=None,
                    ai_proposal=ai,
                    reason=f"AI confidence {ai.conviction:.2f} < min {self.min_ai_confidence}",
                )
            
            return ArbitrationDecision(
                symbol=symbol,
                resolution="SINGLE",
                final_proposal=ai,
                local_proposal=None,
                ai_proposal=ai,
                reason=f"AI only: {ai.side} {ai.target_weight_pct:.2f}% (conf={ai.conviction:.2f})",
            )
        
        # Case 4: Both present - apply arbitration logic
        assert local and ai  # type checker
        
        # Sub-case 4a: Same side → blend
        if local.side == ai.side:
            return self._blend_proposals(symbol, local, ai)
        
        # Sub-case 4b: Opposite sides → resolve conflict
        return self._resolve_conflict(symbol, local, ai)
    
    def _blend_proposals(
        self,
        symbol: str,
        local: TradeProposal,
        ai: TradeProposal,
    ) -> ArbitrationDecision:
        """
        Blend proposals when both agree on direction.
        
        Args:
            symbol: Symbol
            local: Local proposal
            ai: AI proposal
            
        Returns:
            ArbitrationDecision with blended proposal
        """
        if self.blend_mode == "conservative":
            # Take minimum of the two sizes
            blended_weight = min(local.target_weight_pct, ai.target_weight_pct)
        else:  # average
            blended_weight = (local.target_weight_pct + ai.target_weight_pct) / 2
        
        # Create blended proposal (use local as base)
        blended = TradeProposal(
            product_id=local.product_id,
            side=local.side,
            target_weight_pct=blended_weight,
            conviction=(local.conviction + ai.conviction) / 2,  # average conviction
            source="meta_arb",
            notes=(
                f"Blend: local={local.target_weight_pct:.2f}% (conv={local.conviction:.2f}) + "
                f"AI={ai.target_weight_pct:.2f}% (conf={ai.conviction:.2f}) "
                f"→ {blended_weight:.2f}% | {local.notes[:100]}"
            ),
            stop_loss_pct=local.stop_loss_pct,  # preserve local risk controls
            take_profit_pct=local.take_profit_pct,
        )
        
        return ArbitrationDecision(
            symbol=symbol,
            resolution="BLEND",
            final_proposal=blended,
            local_proposal=local,
            ai_proposal=ai,
            reason=f"Agreement: both {local.side}, blended {blended_weight:.2f}%",
        )
    
    def _resolve_conflict(
        self,
        symbol: str,
        local: TradeProposal,
        ai: TradeProposal,
    ) -> ArbitrationDecision:
        """
        Resolve conflict when proposals have opposite sides.
        
        Args:
            symbol: Symbol
            local: Local proposal
            ai: AI proposal
            
        Returns:
            ArbitrationDecision with chosen proposal or NONE
        """
        # Rule 1: Low AI confidence → trust local
        if ai.conviction < self.ai_override_threshold:
            local.notes += f" | AI disagreed ({ai.side}) but conf={ai.conviction:.2f} < {self.ai_override_threshold}"
            return ArbitrationDecision(
                symbol=symbol,
                resolution="LOCAL",
                final_proposal=local,
                local_proposal=local,
                ai_proposal=ai,
                reason=f"Conflict: AI conf {ai.conviction:.2f} < override threshold",
            )
        
        # Rule 2: Low local conviction + high AI confidence advantage → trust AI
        confidence_gap = ai.conviction - local.conviction
        if local.conviction < self.local_weak_conviction and confidence_gap > self.ai_confidence_advantage:
            ai.notes += f" | Overriding local {local.side} (conv={local.conviction:.2f}, gap={confidence_gap:.2f})"
            return ArbitrationDecision(
                symbol=symbol,
                resolution="AI",
                final_proposal=ai,
                local_proposal=local,
                ai_proposal=ai,
                reason=(
                    f"Conflict: local weak (conv={local.conviction:.2f}), "
                    f"AI strong (conf={ai.conviction:.2f}, gap={confidence_gap:.2f})"
                ),
            )
        
        # Rule 3: Otherwise, stand down
        return ArbitrationDecision(
            symbol=symbol,
            resolution="NONE",
            final_proposal=None,
            local_proposal=local,
            ai_proposal=ai,
            reason=(
                f"Conflict unresolved: local {local.side} (conv={local.conviction:.2f}) vs "
                f"AI {ai.side} (conf={ai.conviction:.2f}) - standing down"
            ),
        )


# ─── Convenience Function ──────────────────────────────────────────────────

def aggregate_proposals(
    local_proposals: List[TradeProposal],
    ai_proposals: List[TradeProposal],
    config: Dict,
) -> tuple[List[TradeProposal], List[ArbitrationDecision]]:
    """
    Convenience function to aggregate proposals.
    
    Args:
        local_proposals: Local rules proposals
        ai_proposals: AI trader proposals
        config: Arbitration config
        
    Returns:
        Tuple of (final proposals, arbitration log)
    """
    arbitrator = MetaArbitrator(config)
    return arbitrator.aggregate_proposals(local_proposals, ai_proposals)
