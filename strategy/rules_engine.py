"""
247trader-v2 Strategy: Rules Engine

Deterministic trading rules (NO AI).
Pattern: Jesse-style pure strategy + Freqtrade-style protections

This is your baseline. Must be profitable WITHOUT AI.
AI (M1/M2/M3) can only adjust/veto, never create.
"""

from typing import List, Optional, Dict
from dataclasses import dataclass
from datetime import datetime
import logging
import yaml
from pathlib import Path

from core.universe import UniverseAsset, UniverseSnapshot
from core.triggers import TriggerSignal

logger = logging.getLogger(__name__)


@dataclass
class TradeProposal:
    """A proposed trade from rules engine"""
    symbol: str
    side: str  # "BUY" | "SELL"
    size_pct: float  # % of account
    reason: str
    confidence: float  # 0.0 to 1.0
    
    # Source data
    trigger: Optional[TriggerSignal] = None
    asset: Optional[UniverseAsset] = None
    
    # Risk parameters
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    max_hold_hours: Optional[int] = None
    
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


class RulesEngine:
    """
    Deterministic trading rules.
    
    Philosophy:
    - Universe defines WHAT is tradeable
    - Triggers define WHEN to look closer
    - Rules define HOW to size and direction
    
    No AI. No magic. Just:
    - Mean reversion on strong assets
    - Momentum on breakouts
    - Volume confirmation required
    """
    
    def __init__(self, config: Dict):
        self.config = config
        
        # Load policy.yaml for production trading parameters
        policy_path = Path(__file__).parent.parent / "config" / "policy.yaml"
        if policy_path.exists():
            with open(policy_path, 'r') as f:
                self.policy = yaml.safe_load(f)
                logger.info(f"Loaded policy.yaml from {policy_path}")
        else:
            logger.warning(f"policy.yaml not found at {policy_path}, using defaults")
            self.policy = {}
        
        # Extract strategy parameters from policy.yaml
        strategy_cfg = self.policy.get("strategy", {})
        
        # Tier-based position sizing (spec requirement)
        base_position_pct = strategy_cfg.get("base_position_pct", {})
        self.tier1_base_size = base_position_pct.get("tier1", 0.02) * 100  # Convert to %
        self.tier2_base_size = base_position_pct.get("tier2", 0.01) * 100
        self.tier3_base_size = base_position_pct.get("tier3", 0.005) * 100
        
        # Minimum conviction threshold (spec requirement)
        self.min_conviction_to_propose = strategy_cfg.get("min_conviction_to_propose", 0.5)
        
        logger.info(
            f"Initialized RulesEngine with tier sizing: "
            f"T1={self.tier1_base_size:.1f}%, T2={self.tier2_base_size:.1f}%, T3={self.tier3_base_size:.1f}% | "
            f"min_conviction={self.min_conviction_to_propose}"
        )
    
    def propose_trades(self, 
                      universe: UniverseSnapshot,
                      triggers: List[TriggerSignal],
                      regime: str = "chop") -> List[TradeProposal]:
        """
        Generate trade proposals based on rules.
        
        Args:
            universe: Current universe snapshot
            triggers: Detected trigger signals
            regime: Market regime
            
        Returns:
            List of trade proposals
        """
        logger.info(
            f"Running rules engine: {len(triggers)} triggers, "
            f"{universe.total_eligible} eligible assets, regime={regime}"
        )
        
        proposals = []
        
        # Use minimum score from policy.yaml triggers section (spec requirement)
        # NOTE: This is trigger qualification, different from proposal conviction filter
        policy_triggers = self.policy.get("triggers", {})
        min_trigger_score = policy_triggers.get("min_score", 0.2)
        
        qualified_triggers = [
            t for t in triggers
            if (t.strength * t.confidence) >= min_trigger_score
        ]
        
        logger.debug(f"Qualified triggers: {len(qualified_triggers)} (min_score={min_trigger_score})")
        
        # Debug: log all triggers
        for t in triggers:
            score = t.strength * t.confidence
            logger.debug(
                f"  Trigger: {t.symbol} type={t.trigger_type} "
                f"str={t.strength:.2f} conf={t.confidence:.2f} score={score:.2f} "
                f"qualified={score >= min_trigger_score}"
            )
        
        for trigger in qualified_triggers:
            # Get asset info
            asset = universe.get_asset(trigger.symbol)
            if not asset:
                logger.warning(f"Trigger for {trigger.symbol} but asset not in universe")
                continue
            
            # Apply rules based on trigger type
            if trigger.trigger_type == "price_move":
                proposal = self._rule_price_move(trigger, asset, regime)
            elif trigger.trigger_type == "volume_spike":
                proposal = self._rule_volume_spike(trigger, asset, regime)
            elif trigger.trigger_type == "breakout":
                proposal = self._rule_breakout(trigger, asset, regime)
            elif trigger.trigger_type == "reversal":
                proposal = self._rule_reversal(trigger, asset, regime)
            elif trigger.trigger_type == "momentum":
                proposal = self._rule_momentum(trigger, asset, regime)
            else:
                logger.warning(f"Unknown trigger type: {trigger.trigger_type}")
                continue
            
            if proposal:
                # Apply minimum conviction threshold (spec requirement)
                if proposal.confidence >= self.min_conviction_to_propose:
                    proposals.append(proposal)
                    logger.info(
                        f"✓ Proposal: {proposal.side} {proposal.symbol} "
                        f"size={proposal.size_pct:.1f}% conf={proposal.confidence:.2f} reason='{proposal.reason}'"
                    )
                else:
                    logger.info(
                        f"✗ Rejected: {proposal.symbol} conf={proposal.confidence:.2f} "
                        f"< min_conviction={self.min_conviction_to_propose} reason='{proposal.reason}'"
                    )
            else:
                # Log why no proposal was created
                logger.debug(
                    f"No proposal for trigger: {trigger.symbol} type={trigger.trigger_type} "
                    f"(failed rule logic checks)"
                )
        
        logger.info(
            f"Generated {len(proposals)} trade proposals "
            f"(filtered by min_conviction={self.min_conviction_to_propose})"
        )
        return proposals
    
    def _rule_price_move(self, trigger: TriggerSignal, asset: UniverseAsset,
                        regime: str) -> Optional[TradeProposal]:
        """
        Rule: Sharp price move → Momentum/reversal trade
        
        Logic: Rapid price changes (15m ≥ 3.5% or 60m ≥ 6.0%) signal opportunity.
        - Large upward move → momentum continuation
        - Large downward move → potential reversal/bounce
        """
        if trigger.price_change_pct is None:
            return None
        
        price_change = trigger.price_change_pct
        
        # Determine direction and strategy
        if price_change > 1.5:
            # Upward price move → momentum play
            side = "BUY"
            reason = f"Price move: +{price_change:.1f}% ({trigger.reason})"
            stop_loss = 6.0  # Tighter stop for momentum
            take_profit = 12.0  # Quick profit target
            max_hold = 48  # 2 days
            boost = 1.0  # Standard sizing
        elif price_change < -2.5:
            # Downward price move → reversal/bounce play
            side = "BUY"
            reason = f"Price move reversal: {price_change:.1f}% ({trigger.reason})"
            stop_loss = 10.0  # Wider stop (catching falling knife)
            take_profit = 20.0  # Big bounce target
            max_hold = 24  # 1 day (quick bounce or exit)
            boost = 0.7  # Conservative sizing for reversals
        else:
            # Move too small or ambiguous
            return None
        
        # Volatility-adjusted position sizing
        base_size = self._tier_base_size(asset.tier)
        size_pct = self.calculate_volatility_adjusted_size(
            trigger, base_size * boost, stop_loss, target_risk_pct=1.0
        )
        # Scale by confidence
        size_pct *= trigger.confidence
        
        return TradeProposal(
            symbol=trigger.symbol,
            side=side,
            size_pct=size_pct,
            reason=reason,
            confidence=trigger.confidence,
            trigger=trigger,
            asset=asset,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            max_hold_hours=max_hold
        )
    
    def _rule_volume_spike(self, trigger: TriggerSignal, asset: UniverseAsset,
                          regime: str) -> Optional[TradeProposal]:
        """
        Rule: Volume spike → Mean reversion trade
        
        Logic: Big volume usually precedes a move. 
        If accompanied by momentum, follow it.
        """
        # Volume spike alone is not enough - need directional bias
        if trigger.price_change_pct is None:
            return None
        
        # If price is up significantly, expect continuation
        if trigger.price_change_pct > 2.0:
            side = "BUY"
            reason = f"Volume spike {trigger.volume_ratio:.1f}x + price up {trigger.price_change_pct:.1f}%"
        # If price is down significantly, expect bounce
        elif trigger.price_change_pct < -2.0:
            side = "BUY"
            reason = f"Volume spike {trigger.volume_ratio:.1f}x + price down {trigger.price_change_pct:.1f}% (reversal)"
        else:
            # No clear direction, skip
            return None
        
        # Risk parameters
        stop_loss = 8.0  # 8% stop
        take_profit = 15.0  # 15% target
        max_hold = 72  # 3 days
        
        # Volatility-adjusted position sizing
        base_size = self._tier_base_size(asset.tier)
        size_pct = self.calculate_volatility_adjusted_size(
            trigger, base_size, stop_loss, target_risk_pct=1.0
        )
        # Scale by confidence
        size_pct *= trigger.confidence
        
        return TradeProposal(
            symbol=trigger.symbol,
            side=side,
            size_pct=size_pct,
            reason=reason,
            confidence=trigger.confidence,
            trigger=trigger,
            asset=asset,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            max_hold_hours=max_hold
        )
    
    def _rule_breakout(self, trigger: TriggerSignal, asset: UniverseAsset,
                      regime: str) -> Optional[TradeProposal]:
        """
        Rule: Breakout → Momentum trade
        
        Logic: New highs often lead to continuation.
        """
        side = "BUY"
        reason = f"Breakout: {trigger.reason}"
        
        # Tighter stops for breakouts
        stop_loss = 6.0  # 6% stop (vs 8% default)
        take_profit = 20.0  # 20% target (vs 15% default)
        max_hold = 120  # 5 days (breakouts can run)
        
        # Volatility-adjusted position sizing with breakout boost
        base_size = self._tier_base_size(asset.tier)
        size_pct = self.calculate_volatility_adjusted_size(
            trigger, base_size * 1.2, stop_loss, target_risk_pct=1.0
        )
        # Scale by confidence
        size_pct *= trigger.confidence
        
        return TradeProposal(
            symbol=trigger.symbol,
            side=side,
            size_pct=size_pct,
            reason=reason,
            confidence=trigger.confidence,
            trigger=trigger,
            asset=asset,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            max_hold_hours=max_hold
        )
    
    def _rule_reversal(self, trigger: TriggerSignal, asset: UniverseAsset,
                      regime: str) -> Optional[TradeProposal]:
        """
        Rule: Reversal → Mean reversion trade
        
        Logic: Bouncing off lows with volume = potential V-shape recovery.
        """
        # Only take reversals in non-crash regimes
        if regime == "crash":
            return None
        
        side = "BUY"
        reason = f"Reversal: {trigger.reason}"
        
        # Wider stops (catching a falling knife is risky)
        stop_loss = 12.0  # 12% stop
        take_profit = 25.0  # 25% target (big bounces if right)
        max_hold = 48  # 2 days (quick bounce or exit)
        
        # Conservative sizing for reversals (higher risk) - reduce by 20%
        base_size = self._tier_base_size(asset.tier)
        size_pct = self.calculate_volatility_adjusted_size(
            trigger, base_size * 0.8, stop_loss, target_risk_pct=1.0
        )
        # Scale by reduced confidence (reversals are risky)
        size_pct *= (trigger.confidence * 0.8)
        
        return TradeProposal(
            symbol=trigger.symbol,
            side=side,
            size_pct=size_pct,
            reason=reason,
            confidence=trigger.confidence * 0.8,  # Lower confidence
            trigger=trigger,
            asset=asset,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            max_hold_hours=max_hold
        )
    
    def _rule_momentum(self, trigger: TriggerSignal, asset: UniverseAsset,
                      regime: str) -> Optional[TradeProposal]:
        """
        Rule: Sustained momentum → Trend following
        
        Logic: If price is consistently moving one direction, follow it.
        """
        # Direction based on price change
        if trigger.price_change_pct > 0:
            side = "BUY"
        else:
            # Only short if explicitly enabled
            return None  # No shorts in v2 phase 1
        
        reason = f"Momentum: {trigger.reason}"
        
        # Standard risk parameters
        stop_loss = 8.0
        take_profit = 15.0
        max_hold = 72
        
        # Volatility-adjusted position sizing
        base_size = self._tier_base_size(asset.tier)
        size_pct = self.calculate_volatility_adjusted_size(
            trigger, base_size, stop_loss, target_risk_pct=1.0
        )
        # Scale by confidence
        size_pct *= trigger.confidence
        
        return TradeProposal(
            symbol=trigger.symbol,
            side=side,
            size_pct=size_pct,
            reason=reason,
            confidence=trigger.confidence,
            trigger=trigger,
            asset=asset,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            max_hold_hours=max_hold
        )
    
    def _tier_base_size(self, tier: int) -> float:
        """
        Get base position size for tier (spec requirement).
        
        Reads from policy.yaml strategy.base_position_pct:
        - tier1 (BTC, ETH): 2.0% (core/liquid)
        - tier2 (SOL, AVAX, etc): 1.0% (rotational)
        - tier3 (small cap/event): 0.5% (speculative)
        """
        if tier == 1:
            return self.tier1_base_size
        elif tier == 2:
            return self.tier2_base_size
        elif tier == 3:
            return self.tier3_base_size
        else:
            # Default to tier 2 sizing for unknown tiers
            return self.tier2_base_size
    
    def calculate_volatility_adjusted_size(self, trigger: TriggerSignal, base_size_pct: float,
                                          stop_loss_pct: float, target_risk_pct: float = 1.0) -> float:
        """
        Calculate position size using volatility-based risk parity.
        
        Risk Parity Logic:
        - Target: Risk same % of capital per trade (default 1%)
        - Risk = Position_Size * Stop_Loss_Distance
        - Position_Size = Target_Risk / Stop_Loss_Distance
        
        Example:
        - If stop is 8%, to risk 1%: position = 1% / 8% = 12.5%
        - If stop is 4%, to risk 1%: position = 1% / 4% = 25%
        
        Args:
            trigger: Trigger signal with price info
            base_size_pct: Base size from tier (used as cap)
            stop_loss_pct: Stop loss percentage (e.g., 8.0 for 8%)
            target_risk_pct: Target risk per trade (default 1%)
            
        Returns:
            Adjusted position size in %
        """
        # Calculate risk-parity size
        # Position that risks target_risk_pct given stop_loss_pct
        risk_parity_size = (target_risk_pct / stop_loss_pct) * 100
        
        # Apply volatility adjustment if available
        if hasattr(trigger, 'volatility') and trigger.volatility:
            # Scale by volatility: higher vol = smaller position
            # Normalize around 50% volatility as baseline
            vol_adjustment = 50.0 / max(trigger.volatility, 10.0)  # Avoid div by zero
            risk_parity_size *= vol_adjustment
        
        # Cap at base_size (don't exceed tier limits)
        final_size = min(risk_parity_size, base_size_pct)
        
        # Floor at minimum viable size (0.5%)
        final_size = max(final_size, 0.5)
        
        return final_size
    
    def rank_proposals(self, proposals: List[TradeProposal]) -> List[TradeProposal]:
        """
        Rank proposals by confidence and return sorted list.
        """
        ranked = sorted(proposals, key=lambda p: p.confidence, reverse=True)
        logger.info(f"Ranked {len(ranked)} proposals")
        return ranked
