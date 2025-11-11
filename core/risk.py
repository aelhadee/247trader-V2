"""
247trader-v2 Core: Risk Engine

Hard constraints from policy.yaml.
Pattern: Freqtrade-style protections + custom policy enforcement

NO component (rules, AI, or human) can violate these.
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import defaultdict
import logging

from strategy.rules_engine import TradeProposal

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Result of risk check"""
    approved: bool
    reason: Optional[str] = None
    violated_checks: List[str] = None
    approved_proposals: Optional[List] = None  # Filtered list of approved proposals
    
    def __post_init__(self):
        if self.violated_checks is None:
            self.violated_checks = []
        if self.approved_proposals is None:
            self.approved_proposals = []


@dataclass
class PortfolioState:
    """
    Snapshot of portfolio state for risk checks.
    
    Position Schema (ENFORCED):
    open_positions = {
        "BTC-USD": {"units": 0.12, "usd": 8400.0},
        "ETH-USD": {"units": 1.5, "usd": 4200.0}
    }
    
    The "usd" field is used for all risk calculations (exposure, limits, etc).
    The "units" field is for reference only.
    """
    account_value_usd: float
    open_positions: dict  # {symbol: {"units": float, "usd": float}}
    daily_pnl_pct: float
    max_drawdown_pct: float
    trades_today: int
    trades_this_hour: int
    consecutive_losses: int = 0
    last_loss_time: Optional[datetime] = None
    current_time: Optional[datetime] = None  # Current time (simulation or real)
    
    def get_position_usd(self, symbol: str) -> float:
        """Get USD value of a position (enforces schema)"""
        pos = self.open_positions.get(symbol, {})
        return pos.get("usd", 0.0) if isinstance(pos, dict) else 0.0
    
    def get_total_exposure_usd(self) -> float:
        """Get total USD exposure across all positions"""
        return sum(
            pos.get("usd", 0.0) if isinstance(pos, dict) else 0.0
            for pos in self.open_positions.values()
        )


class RiskEngine:
    """
    Enforces hard risk constraints from policy.yaml.
    
    Checks (in order):
    1. Kill switch
    2. Daily stop loss
    3. Max drawdown
    4. Trade frequency limits
    5. Position size limits
    6. Correlation/cluster limits
    7. Microstructure quality
    
    Returns: approved=True + vetoed proposals, OR approved=False + reason
    """
    
    def __init__(self, policy: Dict, universe_manager=None):
        self.policy = policy
        self.risk_config = policy.get("risk", {})
        self.sizing_config = policy.get("position_sizing", {})
        self.micro_config = policy.get("microstructure", {})
        self.regime_config = policy.get("regime", {})
        self.governance_config = policy.get("governance", {})
        self.universe_manager = universe_manager
        
        logger.info("Initialized RiskEngine with policy constraints")
    
    def check_all(self, 
                  proposals: List[TradeProposal],
                  portfolio: PortfolioState,
                  regime: str = "chop") -> RiskCheckResult:
        """
        Run all risk checks on trade proposals.
        
        Args:
            proposals: List of proposed trades
            portfolio: Current portfolio state
            regime: Market regime
            
        Returns:
            RiskCheckResult with approved proposals or rejection reason
        """
        logger.info(f"Running risk checks on {len(proposals)} proposals (regime={regime})")
        
        # CRITICAL: Handle empty proposals correctly
        if not proposals:
            return RiskCheckResult(
                approved=True,
                reason="No proposals to evaluate",
                approved_checks=[],
                approved_proposals=[]
            )
        
        # 1. Kill switch
        result = self._check_kill_switch()
        if not result.approved:
            return result
        
        # 2. Daily stop loss
        result = self._check_daily_stop(portfolio)
        if not result.approved:
            return result
        
        # 2b. Weekly stop loss
        result = self._check_weekly_stop(portfolio)
        if not result.approved:
            return result
        
        # 3. Max drawdown
        result = self._check_max_drawdown(portfolio)
        if not result.approved:
            return result
        
        # 3b. Global at-risk limit (existing + proposed)
        result = self._check_global_at_risk(proposals, portfolio)
        if not result.approved:
            return result
        
        # 4. Trade frequency
        result = self._check_trade_frequency(proposals, portfolio)
        if not result.approved:
            return result
        
        # 4b. Consecutive loss cooldown
        result = self._check_loss_cooldown(portfolio)
        if not result.approved:
            return result
        
        # 5. Position sizing (per proposal)
        approved_proposals = []
        violated = []
        
        for proposal in proposals:
            result = self._check_position_size(proposal, portfolio, regime)
            if result.approved:
                approved_proposals.append(proposal)
            else:
                violated.extend(result.violated_checks)
                logger.debug(f"Rejected {proposal.symbol}: {result.reason}")
        
        if not approved_proposals:
            return RiskCheckResult(
                approved=False,
                reason="All proposals violated risk constraints",
                violated_checks=violated
            )
        
        # 6. Cluster limits
        result = self._check_cluster_limits(approved_proposals, portfolio)
        if not result.approved:
            return result
        
        logger.info(f"Risk checks passed: {len(approved_proposals)}/{len(proposals)} proposals approved")
        
        return RiskCheckResult(
            approved=True,
            violated_checks=violated if violated else [],
            approved_proposals=approved_proposals  # Return filtered list
        )
    
    def _check_kill_switch(self) -> RiskCheckResult:
        """Check if kill switch file exists"""
        import os
        kill_switch_file = self.governance_config.get("kill_switch_file", "data/KILL_SWITCH")
        
        if os.path.exists(kill_switch_file):
            logger.error("KILL SWITCH ACTIVATED - All trading halted")
            return RiskCheckResult(
                approved=False,
                reason="KILL_SWITCH file exists - trading halted",
                violated_checks=["kill_switch"]
            )
        
        return RiskCheckResult(approved=True)
    
    def _check_daily_stop(self, portfolio: PortfolioState) -> RiskCheckResult:
        """Check if daily stop loss hit"""
        # Support both naming conventions: daily_stop_pnl_pct (policy.yaml) and daily_stop_loss_pct (legacy)
        max_daily_loss_pct = abs(self.risk_config.get("daily_stop_pnl_pct", 
                                                      self.risk_config.get("daily_stop_loss_pct", 3.0)))
        
        if portfolio.daily_pnl_pct <= -max_daily_loss_pct:
            logger.error(f"Daily stop loss hit: {portfolio.daily_pnl_pct:.2f}% (max: -{max_daily_loss_pct}%)")
            return RiskCheckResult(
                approved=False,
                reason=f"Daily stop loss hit: {portfolio.daily_pnl_pct:.2f}% loss",
                violated_checks=["daily_stop_loss"]
            )
        
        return RiskCheckResult(approved=True)
    
    def _check_weekly_stop(self, portfolio: PortfolioState) -> RiskCheckResult:
        """Check if weekly stop loss hit"""
        # Support both naming conventions
        max_weekly_loss_pct = abs(self.risk_config.get("weekly_stop_pnl_pct",
                                                       self.risk_config.get("weekly_stop_loss_pct", 7.0)))
        
        # Assume portfolio has weekly_pnl_pct attribute; if not, skip this check
        weekly_pnl = getattr(portfolio, 'weekly_pnl_pct', None)
        if weekly_pnl is not None and weekly_pnl <= -max_weekly_loss_pct:
            logger.error(f"Weekly stop loss hit: {weekly_pnl:.2f}% (max: -{max_weekly_loss_pct}%)")
            return RiskCheckResult(
                approved=False,
                reason=f"Weekly stop loss hit: {weekly_pnl:.2f}% loss",
                violated_checks=["weekly_stop_loss"]
            )
        
        return RiskCheckResult(approved=True)
    
    def _check_max_drawdown(self, portfolio: PortfolioState) -> RiskCheckResult:
        """Check if max drawdown exceeded"""
        max_dd_pct = self.risk_config.get("max_drawdown_pct", 10.0)
        
        if portfolio.max_drawdown_pct >= max_dd_pct:
            logger.error(f"Max drawdown exceeded: {portfolio.max_drawdown_pct:.2f}% (max: {max_dd_pct}%)")
            return RiskCheckResult(
                approved=False,
                reason=f"Max drawdown {portfolio.max_drawdown_pct:.2f}% exceeds limit",
                violated_checks=["max_drawdown"]
            )
        
        return RiskCheckResult(approved=True)
    
    def _check_global_at_risk(self, proposals: List[TradeProposal],
                             portfolio: PortfolioState) -> RiskCheckResult:
        """Check if total at-risk (existing + proposed) exceeds global limit"""
        max_total_at_risk_pct = self.risk_config.get("max_total_at_risk_pct", 15.0)
        
        # Calculate current exposure from open positions (using enforced schema)
        current_exposure_usd = portfolio.get_total_exposure_usd()
        current_exposure_pct = (current_exposure_usd / portfolio.account_value_usd) * 100
        
        # Calculate proposed exposure
        proposed_pct = sum(p.size_pct for p in proposals)
        
        total_at_risk_pct = current_exposure_pct + proposed_pct
        
        if total_at_risk_pct > max_total_at_risk_pct:
            logger.error(
                f"Total at-risk would exceed limit: {total_at_risk_pct:.1f}% > {max_total_at_risk_pct:.1f}% "
                f"(current: {current_exposure_pct:.1f}% + proposed: {proposed_pct:.1f}%)"
            )
            return RiskCheckResult(
                approved=False,
                reason=f"Total at-risk {total_at_risk_pct:.1f}% exceeds cap of {max_total_at_risk_pct:.1f}%",
                violated_checks=["max_total_at_risk_pct"]
            )
        
        logger.debug(
            f"Global at-risk check passed: {total_at_risk_pct:.1f}%/{max_total_at_risk_pct:.1f}% "
            f"(current: {current_exposure_pct:.1f}% + proposed: {proposed_pct:.1f}%)"
        )
        
        return RiskCheckResult(approved=True)
    
    def _check_trade_frequency(self, proposals: List[TradeProposal],
                              portfolio: PortfolioState) -> RiskCheckResult:
        """Check trade frequency limits"""
        max_per_day = self.risk_config.get("max_trades_per_day", 10)
        max_per_hour = self.risk_config.get("max_trades_per_hour", 3)
        
        if portfolio.trades_today >= max_per_day:
            return RiskCheckResult(
                approved=False,
                reason=f"Daily trade limit reached ({portfolio.trades_today}/{max_per_day})",
                violated_checks=["trade_frequency_daily"]
            )
        
        if portfolio.trades_this_hour >= max_per_hour:
            return RiskCheckResult(
                approved=False,
                reason=f"Hourly trade limit reached ({portfolio.trades_this_hour}/{max_per_hour})",
                violated_checks=["trade_frequency_hourly"]
            )
        
        return RiskCheckResult(approved=True)
    
    def _check_loss_cooldown(self, portfolio: PortfolioState) -> RiskCheckResult:
        """Check if in cooldown period after consecutive losses"""
        cooldown_after = self.risk_config.get("cooldown_after_loss_trades", 3)
        cooldown_minutes = self.risk_config.get("cooldown_minutes", 60)
        
        if portfolio.consecutive_losses >= cooldown_after:
            # Check if cooldown period has expired
            if portfolio.last_loss_time:
                cooldown_expires = portfolio.last_loss_time + timedelta(minutes=cooldown_minutes)
                now = portfolio.current_time if portfolio.current_time else datetime.utcnow()
                
                if now < cooldown_expires:
                    minutes_left = (cooldown_expires - now).total_seconds() / 60
                    logger.warning(
                        f"Cooldown active: {portfolio.consecutive_losses} consecutive losses. "
                        f"{minutes_left:.0f} minutes remaining"
                    )
                    return RiskCheckResult(
                        approved=False,
                        reason=f"Cooldown: {portfolio.consecutive_losses} consecutive losses ({minutes_left:.0f}min left)",
                        violated_checks=["consecutive_loss_cooldown"]
                    )
                else:
                    logger.info(f"Cooldown period expired at {cooldown_expires}, resuming trading")
        
        return RiskCheckResult(approved=True)
    
    def _check_position_size(self, proposal: TradeProposal,
                            portfolio: PortfolioState,
                            regime: str) -> RiskCheckResult:
        """Check if position size is within limits"""
        violated = []
        
        # Get base limits
        max_pos_pct = self.risk_config.get("max_position_size_pct", 5.0)
        min_pos_pct = self.risk_config.get("min_position_size_pct", 0.5)
        
        # Apply regime adjustment if enabled
        if self.regime_config.get("enabled", True):
            regime_settings = self.regime_config.get(regime, {})
            multiplier = regime_settings.get("position_size_multiplier", 1.0)
            max_pos_pct *= multiplier
        
        # Check max
        if proposal.size_pct > max_pos_pct:
            violated.append(f"position_size_too_large ({proposal.size_pct:.1f}% > {max_pos_pct:.1f}%)")
        
        # Check min
        if proposal.size_pct < min_pos_pct:
            violated.append(f"position_size_too_small ({proposal.size_pct:.1f}% < {min_pos_pct:.1f}%)")
        
        # Check if already have position
        if proposal.symbol in portfolio.open_positions:
            # Check pyramiding rules
            allow_pyramid = self.sizing_config.get("allow_pyramiding", False)
            if not allow_pyramid:
                violated.append(f"already_have_position ({proposal.symbol})")
        
        if violated:
            return RiskCheckResult(
                approved=False,
                reason="; ".join(violated),
                violated_checks=violated
            )
        
        return RiskCheckResult(approved=True)
    
    def _check_cluster_limits(self, proposals: List[TradeProposal],
                             portfolio: PortfolioState) -> RiskCheckResult:
        """
        Check cluster exposure limits.
        
        Enforces max_per_theme_pct from policy.yaml:
        - MEME: 5%
        - L2: 10%
        - DEFI: 10%
        """
        if not self.universe_manager:
            # No universe manager, can't check clusters
            return RiskCheckResult(approved=True)
        
        # Get policy limits for each theme/cluster
        max_per_theme = self.risk_config.get("max_per_theme_pct", {})
        
        # Calculate current cluster exposure from open positions
        cluster_exposure = defaultdict(float)  # {cluster_name: total_size_pct}
        
        for symbol, size_usd in portfolio.open_positions.items():
            cluster = self.universe_manager.get_asset_cluster(symbol)
            if cluster:
                size_pct = (size_usd / portfolio.account_value_usd) * 100
                cluster_exposure[cluster] += size_pct
        
        # Add proposed trades to cluster exposure
        approved_proposals = []
        violated = []
        
        for proposal in proposals:
            cluster = self.universe_manager.get_asset_cluster(proposal.symbol)
            
            if cluster:
                # Check if this proposal would violate cluster limit
                max_cluster_pct = max_per_theme.get(cluster)
                
                if max_cluster_pct:
                    new_exposure = cluster_exposure[cluster] + proposal.size_pct
                    
                    if new_exposure > max_cluster_pct:
                        reason = (
                            f"{cluster} limit would be violated: "
                            f"{new_exposure:.1f}% > {max_cluster_pct:.1f}% "
                            f"(current: {cluster_exposure[cluster]:.1f}% + proposed: {proposal.size_pct:.1f}%)"
                        )
                        violated.append(reason)
                        logger.warning(f"Rejected {proposal.symbol}: {reason}")
                        continue
                    
                    # Update temporary exposure for next proposals
                    cluster_exposure[cluster] = new_exposure
            
            approved_proposals.append(proposal)
        
        if not approved_proposals and proposals:
            return RiskCheckResult(
                approved=False,
                reason="All proposals would violate cluster limits",
                violated_checks=violated
            )
        
        # Update proposals list to only include approved ones
        proposals.clear()
        proposals.extend(approved_proposals)
        
        return RiskCheckResult(approved=True, violated_checks=violated)
    
    def adjust_proposal_size(self, proposal: TradeProposal,
                            portfolio: PortfolioState,
                            regime: str) -> TradeProposal:
        """
        Adjust proposal size to fit within risk constraints.
        
        Returns: Adjusted proposal (or None if no valid size exists)
        """
        max_pos_pct = self.risk_config.get("max_position_size_pct", 5.0)
        min_pos_pct = self.risk_config.get("min_position_size_pct", 0.5)
        
        # Apply regime adjustment
        if self.regime_config.get("enabled", True):
            regime_settings = self.regime_config.get(regime, {})
            multiplier = regime_settings.get("position_size_multiplier", 1.0)
            max_pos_pct *= multiplier
        
        # Clamp to limits
        adjusted_size = max(min_pos_pct, min(proposal.size_pct, max_pos_pct))
        
        if adjusted_size != proposal.size_pct:
            logger.debug(
                f"Adjusted {proposal.symbol} size: {proposal.size_pct:.1f}% → {adjusted_size:.1f}%"
            )
            proposal.size_pct = adjusted_size
        
        return proposal
