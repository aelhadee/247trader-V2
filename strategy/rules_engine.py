"""
247trader-v2 Strategy: Rules Engine

Deterministic trading rules (NO AI).
Pattern: Jesse-style pure strategy + Freqtrade-style protections

This is your baseline. Must be profitable WITHOUT AI.
AI (M1/M2/M3) can only adjust/veto, never create.
"""

from typing import List, Optional, Dict, Tuple, Any
from dataclasses import dataclass, field
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
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    conviction_breakdown: Dict[str, Any] = field(default_factory=dict)
    
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
        self.liquidity_policy = self.policy.get("liquidity", {})
        
        # Tier-based position sizing (spec requirement)
        base_position_pct = strategy_cfg.get("base_position_pct", {})
        self.tier1_base_size = base_position_pct.get("tier1", 0.02) * 100  # Convert to %
        self.tier2_base_size = base_position_pct.get("tier2", 0.01) * 100
        self.tier3_base_size = base_position_pct.get("tier3", 0.005) * 100
        
        # Minimum conviction threshold (spec requirement)
        self.min_conviction_default = strategy_cfg.get("min_conviction_to_propose", 0.5)
        self.min_conviction_by_regime = strategy_cfg.get("min_conviction_by_regime", {})

        # Conviction weighting (strength/confidence + quality boosts)
        conviction_cfg = strategy_cfg.get("conviction_weights", {})
        self.conviction_weights = {
            "base": conviction_cfg.get("base", 0.0),
            "strength": conviction_cfg.get("trigger_strength", 0.5),
            "confidence": conviction_cfg.get("trigger_confidence", 0.3),
        }
        self.conviction_quality_boosts = conviction_cfg.get("quality_boosts", {})

        # Canary trade config
        self.canary_cfg = strategy_cfg.get("canary", {"enabled": False})
        self.canary_tier_whitelist = {
            1 if tier.upper() == "T1" else 2
            for tier in self.canary_cfg.get("require_tier_in", [])
            if tier.upper() in {"T1", "T2"}
        }
        
        logger.info(
            f"Initialized RulesEngine with tier sizing: "
            f"T1={self.tier1_base_size:.1f}%, T2={self.tier2_base_size:.1f}%, T3={self.tier3_base_size:.1f}% | "
            f"min_conviction={self.min_conviction_default}"
        )
    
    def _min_conviction_threshold(self, regime: str) -> float:
        regime_key = (regime or "").lower()
        if isinstance(self.min_conviction_by_regime, dict):
            threshold = self.min_conviction_by_regime.get(regime_key)
            if threshold is not None:
                return threshold
        return self.min_conviction_default

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
        min_conviction = self._min_conviction_threshold(regime)
        
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
                conviction, breakdown = self._calculate_conviction(trigger, asset, proposal)
                proposal.confidence = conviction
                proposal.conviction_breakdown = breakdown
                proposal.metadata["conviction_threshold"] = min_conviction
                proposal.metadata["conviction_trigger_strength"] = trigger.strength
                proposal.metadata["conviction_trigger_confidence"] = trigger.confidence
                self._log_conviction(proposal, breakdown, min_conviction)

                if conviction >= min_conviction:
                    proposals.append(proposal)
                    logger.info(
                        f"✓ Proposal: {proposal.side} {proposal.symbol} "
                        f"size={proposal.size_pct:.1f}% conf={proposal.confidence:.2f} reason='{proposal.reason}'"
                    )
                else:
                    canary_proposal = self._try_canary(
                        proposal=proposal,
                        asset=asset,
                        conviction=conviction,
                        threshold=min_conviction,
                        breakdown=breakdown,
                        total_triggers=len(qualified_triggers)
                    )
                    if canary_proposal:
                        proposals.append(canary_proposal)
                    else:
                        logger.info(
                            f"✗ Rejected: {proposal.symbol} conf={conviction:.2f} "
                            f"< min_conviction={min_conviction:.2f} reason='min_conviction'"
                        )
            else:
                # Log why no proposal was created
                logger.debug(
                    f"No proposal for trigger: {trigger.symbol} type={trigger.trigger_type} "
                    f"(failed rule logic checks)"
                )
        
        logger.info(
            f"Generated {len(proposals)} trade proposals "
            f"(filtered by min_conviction={min_conviction:.2f})"
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
    
    def _calculate_conviction(self, trigger: TriggerSignal, asset: UniverseAsset,
                              proposal: TradeProposal) -> Tuple[float, Dict[str, float]]:
        weights = self.conviction_weights
        base_component = weights.get("base", 0.0)
        strength_weight = weights.get("strength", 0.5)
        confidence_weight = weights.get("confidence", 0.3)
        strength_component = strength_weight * trigger.strength
        confidence_component = confidence_weight * trigger.confidence

        boosts_total = 0.0
        boosts_applied = []

        for key, boost_value in self.conviction_quality_boosts.items():
            applied = False
            if key.startswith("tier_bias_"):
                tier_label = key.split("_")[-1].upper()
                tier_match = (tier_label == "T1" and asset.tier == 1) or \
                             (tier_label == "T2" and asset.tier == 2) or \
                             (tier_label == "T3" and asset.tier == 3)
                applied = tier_match
            else:
                applied = trigger.qualifiers.get(key, False)

            if applied:
                boosts_total += boost_value
                boosts_applied.append((key, boost_value))

        conviction = base_component + strength_component + confidence_component + boosts_total
        conviction = max(0.0, min(1.0, conviction))

        breakdown = {
            "base": base_component,
            "strength_component": strength_component,
            "strength_weight": strength_weight,
            "strength_value": trigger.strength,
            "confidence_component": confidence_component,
            "confidence_weight": confidence_weight,
            "confidence_value": trigger.confidence,
            "boosts_total": boosts_total,
            "boosts": boosts_applied,
            "trigger_score": trigger.strength * trigger.confidence,
        }

        # Preserve booster insight for downstream consumers
        if boosts_applied:
            proposal.metadata.setdefault("conviction_boosts", [k for k, _ in boosts_applied])

        return conviction, breakdown

    def _log_conviction(self, proposal: TradeProposal, breakdown: Dict[str, float],
                        threshold: float) -> None:
        boosts = breakdown.get("boosts", [])
        boost_summary = ", ".join(f"{name}+{value:.2f}" for name, value in boosts) if boosts else "none"

        base_component = breakdown.get("base", 0.0)
        strength_component = breakdown.get("strength_component", 0.0)
        strength_weight = breakdown.get("strength_weight", 0.0)
        strength_value = breakdown.get("strength_value", 0.0)
        confidence_component = breakdown.get("confidence_component", 0.0)
        confidence_weight = breakdown.get("confidence_weight", 0.0)
        confidence_value = breakdown.get("confidence_value", 0.0)
        boosts_total = breakdown.get("boosts_total", 0.0)
        trigger_score = breakdown.get("trigger_score")
        if trigger_score is None:
            trigger_score = strength_value * confidence_value

        formula = (
            f"{proposal.confidence:.3f} = base {base_component:.3f} + "
            f"strength {strength_component:.3f} ({strength_weight:.2f}*{strength_value:.2f}) + "
            f"confidence {confidence_component:.3f} ({confidence_weight:.2f}*{confidence_value:.2f}) + "
            f"boosts {boosts_total:.3f}"
        )

        trigger_label = proposal.trigger.trigger_type if proposal.trigger else "n/a"

        logger.info(
            f"CONVICTION {proposal.symbol} ({trigger_label}): {formula} "
            f"[{boost_summary}] threshold={threshold:.2f} trigger_score={trigger_score:.3f}"
        )

    def _try_canary(self, proposal: TradeProposal, asset: UniverseAsset, conviction: float,
                    threshold: float, breakdown: Dict[str, float], total_triggers: int) -> Optional[TradeProposal]:
        cfg = self.canary_cfg or {}
        if not cfg.get("enabled", False):
            return None
        if total_triggers != 1:
            return None

        window_cfg = cfg.get("conviction_window", {})
        lower = window_cfg.get("lower", 0.0)
        upper = window_cfg.get("upper", threshold)
        inclusive_upper = window_cfg.get("inclusive_upper", False)

        upper_check = conviction <= upper if inclusive_upper else conviction < upper
        if not (conviction >= lower and upper_check):
            return None

        if self.canary_tier_whitelist and asset.tier not in self.canary_tier_whitelist:
            return None

        if not self._canary_liquidity_ok(asset):
            logger.info(
                f"✗ Canary blocked: {asset.symbol} liquidity guard failed (depth={asset.depth_usd:.0f}, spread={asset.spread_bps:.1f}bps)"
            )
            return None

        size_multiplier = float(cfg.get("size_multiplier", 0.25))
        proposal.size_pct *= size_multiplier
        proposal.tags.append("canary")
        proposal.metadata["canary"] = True
        proposal.metadata["canary_size_multiplier"] = size_multiplier
        if "CANARY" not in proposal.reason:
            proposal.reason = f"{proposal.reason} | CANARY".strip()

        if cfg.get("maker_only", True):
            proposal.metadata["order_type_override"] = "limit_post_only"

        logger.info(
            f"🪶 Canary: {proposal.symbol} conviction={conviction:.3f} (< {threshold:.2f}) "
            f"size={proposal.size_pct:.2f}% tags={proposal.tags}"
        )
        return proposal

    def _canary_liquidity_ok(self, asset: UniverseAsset) -> bool:
        spreads_cfg = self.liquidity_policy.get("spreads_bps", {})
        depth_cfg = self.liquidity_policy.get("min_depth_floor_usd", {})
        tier_key = f"T{asset.tier}" if asset.tier in (1, 2, 3) else None

        max_spread = spreads_cfg.get(tier_key, self.liquidity_policy.get("max_spread_bps", 100))
        min_depth = depth_cfg.get(tier_key, self.liquidity_policy.get("min_depth_20bps_usd", 0))

        spread_ok = asset.spread_bps <= max_spread if max_spread is not None else True
        depth_ok = asset.depth_usd >= min_depth if min_depth is not None else True
        return spread_ok and depth_ok

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
