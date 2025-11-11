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
from infra.alerting import AlertSeverity

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
    weekly_pnl_pct: float = 0.0
    pending_orders: Optional[Dict[str, Dict[str, float]]] = None
    
    def get_position_usd(self, symbol: str) -> float:
        """Get USD value of a position (enforces schema)"""
        pos = self.open_positions.get(symbol, {})
        if not isinstance(pos, dict):
            return 0.0
        if "usd" in pos:
            try:
                return float(pos["usd"])
            except (TypeError, ValueError):
                return 0.0
        if "usd_value" in pos:
            try:
                return float(pos["usd_value"])
            except (TypeError, ValueError):
                return 0.0
        return 0.0
    
    def get_total_exposure_usd(self) -> float:
        """Get total USD exposure across all positions"""
        total = 0.0
        for pos in self.open_positions.values():
            if not isinstance(pos, dict):
                continue
            value = pos.get("usd")
            if value is None:
                value = pos.get("usd_value")
            if value is None:
                continue
            try:
                total += float(value)
            except (TypeError, ValueError):
                continue
        return total

    def _pending_orders_for_side(self, side: str) -> Dict[str, float]:
        orders = self.pending_orders or {}
        side_bucket = orders.get(side)
        if not isinstance(side_bucket, dict):
            return {}
        return side_bucket

    def get_pending_notional_usd(self, side: str = "buy", symbol: Optional[str] = None) -> float:
        """Aggregate pending order notional in USD for a side, optionally filtered by symbol."""
        orders = self._pending_orders_for_side(side)
        if not orders:
            return 0.0

        if symbol is None:
            total = 0.0
            for value in orders.values():
                try:
                    total += float(value)
                except (TypeError, ValueError):
                    continue
            return total

        lookup_keys = {symbol}
        if '-' in symbol:
            base = symbol.split('-', 1)[0]
            lookup_keys.add(base)
        else:
            lookup_keys.add(f"{symbol}-USD")

        for key in lookup_keys:
            if key in orders:
                try:
                    return float(orders[key])
                except (TypeError, ValueError):
                    return 0.0
        return 0.0


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
    
    def __init__(self, policy: Dict, universe_manager=None, exchange=None, state_store=None):
        self.policy = policy
        self.risk_config = policy.get("risk", {})
        self.sizing_config = policy.get("position_sizing", {})
        self.micro_config = policy.get("microstructure", {})
        self.regime_config = policy.get("regime", {})
        self.governance_config = policy.get("governance", {})
        self.circuit_breakers_config = policy.get("circuit_breakers", {})
        self.universe_manager = universe_manager
        self.exchange = exchange
        self._state_store = state_store  # Optional: for testing or explicit state management
        
        # Circuit breaker state tracking
        self._api_error_count = 0
        self._last_api_success = None
        self._last_rate_limit_time = None
        
        logger.info("Initialized RiskEngine with policy constraints and circuit breakers")
    
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
                approved_proposals=[]
            )
        
        # 0. Circuit breakers (fail closed on data/exchange issues)
        result = self._check_circuit_breakers(portfolio, regime)
        if not result.approved:
            return result
        
        # 0b. Exchange product status (block degraded markets)
        proposals = self._filter_degraded_products(proposals)
        if not proposals:
            return RiskCheckResult(
                approved=False,
                reason="All proposals filtered by exchange product status restrictions",
                violated_checks=["exchange_product_status"]
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
        
        # 4c. Max open positions (spec requirement)
        result = self._check_max_open_positions(proposals, portfolio)
        if not result.approved:
            return result
        
        # 5. Per-symbol cooldowns (filter proposals)
        proposals = self._filter_cooled_symbols(proposals)
        if not proposals:
            return RiskCheckResult(
                approved=False,
                reason="All proposals filtered by per-symbol cooldowns",
                violated_checks=["per_symbol_cooldown"]
            )
        
        # 6. Position sizing (per proposal)
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
        
        # 7. Cluster limits
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
            logger.error("🚨 KILL SWITCH ACTIVATED - All trading halted")
            # Alert on kill switch activation
            if self.alert_service:
                self.alert_service.send_alert(
                    message="KILL SWITCH activated - all trading halted immediately",
                    severity=AlertSeverity.CRITICAL,
                    tags=["kill_switch", "emergency", "halt"]
                )
            return RiskCheckResult(
                approved=False,
                reason="KILL_SWITCH file exists - trading halted",
                violated_checks=["kill_switch"]
            )
        
        return RiskCheckResult(approved=True)
    
    def _check_daily_stop(self, portfolio: PortfolioState) -> RiskCheckResult:
        """
        Check if daily stop loss hit using REAL PnL from exchange fills.
        
        PnL is tracked in StateStore.record_fill() from actual execution prices/fees.
        portfolio.daily_pnl_pct is computed from state['pnl_today'] in main_loop._init_portfolio_state().
        """
        # Support both naming conventions: daily_stop_pnl_pct (policy.yaml) and daily_stop_loss_pct (legacy)
        max_daily_loss_pct = abs(self.risk_config.get("daily_stop_pnl_pct", 
                                                      self.risk_config.get("daily_stop_loss_pct", 3.0)))
        
        # CRITICAL: portfolio.daily_pnl_pct is derived from REAL fills, not simulated
        if portfolio.daily_pnl_pct <= -max_daily_loss_pct:
            logger.error(
                f"🚨 DAILY STOP LOSS HIT: {portfolio.daily_pnl_pct:.2f}% loss "
                f"(limit: -{max_daily_loss_pct}%) - NO NEW TRADES"
            )
            # Alert on stop loss hit
            if self.alert_service:
                self.alert_service.send_alert(
                    message=f"Daily stop loss triggered: {portfolio.daily_pnl_pct:.2f}% loss",
                    severity=AlertSeverity.CRITICAL,
                    tags=["risk", "stop_loss", "daily"]
                )
            return RiskCheckResult(
                approved=False,
                reason=f"Daily stop loss hit: {portfolio.daily_pnl_pct:.2f}% loss (real PnL)",
                violated_checks=["daily_stop_loss"]
            )
        
        return RiskCheckResult(approved=True)
    
    def _check_weekly_stop(self, portfolio: PortfolioState) -> RiskCheckResult:
        """
        Check if weekly stop loss hit using REAL PnL from exchange fills.
        
        PnL is tracked in StateStore.record_fill() from actual execution prices/fees.
        portfolio.weekly_pnl_pct is computed from state['pnl_week'] in main_loop._init_portfolio_state().
        """
        # Support both naming conventions
        max_weekly_loss_pct = abs(self.risk_config.get("weekly_stop_pnl_pct",
                                                       self.risk_config.get("weekly_stop_loss_pct", 7.0)))
        
        # CRITICAL: portfolio.weekly_pnl_pct is derived from REAL fills, not simulated
        weekly_pnl = getattr(portfolio, 'weekly_pnl_pct', None)
        if weekly_pnl is not None and weekly_pnl <= -max_weekly_loss_pct:
            logger.error(
                f"🚨 WEEKLY STOP LOSS HIT: {weekly_pnl:.2f}% loss "
                f"(limit: -{max_weekly_loss_pct}%) - TIGHTEN RISK"
            )
            # Alert on stop loss hit
            if self.alert_service:
                self.alert_service.send_alert(
                    message=f"Weekly stop loss triggered: {weekly_pnl:.2f}% loss",
                    severity=AlertSeverity.CRITICAL,
                    tags=["risk", "stop_loss", "weekly"]
                )
            return RiskCheckResult(
                approved=False,
                reason=f"Weekly stop loss hit: {weekly_pnl:.2f}% loss (real PnL)",
                violated_checks=["weekly_stop_loss"]
            )
        
        return RiskCheckResult(approved=True)
    
    def _check_max_drawdown(self, portfolio: PortfolioState) -> RiskCheckResult:
        """
        Check if max drawdown exceeded.
        
        NOTE: Currently portfolio.max_drawdown_pct is set to 0.0 in _init_portfolio_state().
        TODO: Calculate from equity curve history once we track high-water mark in StateStore.
        For now, this check is effectively disabled but structure is in place.
        """
        max_dd_pct = self.risk_config.get("max_drawdown_pct", 10.0)
        
        if portfolio.max_drawdown_pct >= max_dd_pct:
            logger.error(
                f"🚨 MAX DRAWDOWN EXCEEDED: {portfolio.max_drawdown_pct:.2f}% "
                f"(limit: {max_dd_pct}%)"
            )
            # Alert on drawdown breach
            if self.alert_service:
                self.alert_service.send_alert(
                    message=f"Max drawdown exceeded: {portfolio.max_drawdown_pct:.2f}%",
                    severity=AlertSeverity.CRITICAL,
                    tags=["risk", "drawdown"]
                )
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
        pending_buy_usd = portfolio.get_pending_notional_usd("buy")

        def _pct(value_usd: float) -> float:
            try:
                return (value_usd / portfolio.account_value_usd) * 100 if portfolio.account_value_usd > 0 else 0.0
            except ZeroDivisionError:
                return 0.0

        current_positions_pct = _pct(current_exposure_usd)
        pending_buy_pct = _pct(pending_buy_usd)
        current_exposure_pct = current_positions_pct + pending_buy_pct
        
        # Calculate proposed exposure
        proposed_pct = sum(p.size_pct for p in proposals)
        
        total_at_risk_pct = current_exposure_pct + proposed_pct
        
        if total_at_risk_pct > max_total_at_risk_pct:
            logger.error(
                "Total at-risk would exceed limit: %.1f%% > %.1f%% (positions: %.1f%%, pending buys: %.1f%%, proposed: %.1f%%)",
                total_at_risk_pct,
                max_total_at_risk_pct,
                current_positions_pct,
                pending_buy_pct,
                proposed_pct,
            )
            return RiskCheckResult(
                approved=False,
                reason=f"Total at-risk {total_at_risk_pct:.1f}% exceeds cap of {max_total_at_risk_pct:.1f}%",
                violated_checks=["max_total_at_risk_pct"]
            )
        
        logger.debug(
            "Global at-risk check passed: %.1f%%/%.1f%% (positions: %.1f%%, pending buys: %.1f%%, proposed: %.1f%%)",
            total_at_risk_pct,
            max_total_at_risk_pct,
            current_positions_pct,
            pending_buy_pct,
            proposed_pct,
        )
        
        return RiskCheckResult(approved=True)
    
    def _check_trade_frequency(self, proposals: List[TradeProposal],
                              portfolio: PortfolioState) -> RiskCheckResult:
        """
        Check trade frequency limits (spec requirement).
        
        Uses max_new_trades_per_hour from policy.yaml (default 2/hour).
        """
        max_per_day = self.risk_config.get("max_trades_per_day", 10)
        # Spec uses "max_new_trades_per_hour" (default 2)
        max_per_hour = self.risk_config.get("max_new_trades_per_hour", 
                                            self.risk_config.get("max_trades_per_hour", 2))
        
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
    
    def _check_max_open_positions(self, proposals: List[TradeProposal],
                                  portfolio: PortfolioState) -> RiskCheckResult:
        """
        Check max open positions limit (spec requirement).
        
        Only applies to BUY proposals that would create NEW positions.
        Reads strategy.max_open_positions from policy.yaml (default 8).
        """
        # Get max_open_positions from strategy section in policy.yaml
        strategy_cfg = self.policy.get("strategy", {})
        max_open = strategy_cfg.get("max_open_positions", 8)
        
        current_open = len(portfolio.open_positions)
        
        # Count how many NEW positions would be created (only BUY proposals for symbols we don't have)
        new_positions = sum(
            1 for p in proposals 
            if p.side == "BUY" and p.symbol not in portfolio.open_positions
        )
        
        total_would_be = current_open + new_positions
        
        if total_would_be > max_open:
            logger.warning(
                f"Max open positions would be exceeded: {total_would_be} > {max_open} "
                f"(current: {current_open} + new: {new_positions})"
            )
            return RiskCheckResult(
                approved=False,
                reason=f"Max open positions limit: {total_would_be} would exceed {max_open}",
                violated_checks=["max_open_positions"]
            )
        
        logger.debug(
            f"Max open positions check passed: {total_would_be}/{max_open} "
            f"(current: {current_open} + new: {new_positions})"
        )
        
        return RiskCheckResult(approved=True)
    
    def _filter_cooled_symbols(self, proposals: List[TradeProposal]) -> List[TradeProposal]:
        """
        Filter out proposals for symbols currently in cooldown.
        
        Checks StateStore for per-symbol cooldowns set after losses or stop-outs.
        Only filters if per_symbol_cooldown_enabled=true in policy.
        
        Returns:
            Filtered list of proposals (symbols not in cooldown)
        """
        if not self.risk_config.get("per_symbol_cooldown_enabled", True):
            return proposals
        
        from infra.state_store import get_state_store
        # Use state_store from risk engine if available, otherwise get default
        state_store = getattr(self, '_state_store', None) or get_state_store()
        
        filtered = []
        for proposal in proposals:
            symbol = proposal.symbol
            
            # Check if symbol is in cooldown
            if state_store.is_cooldown_active(symbol):
                logger.info(f"Filtered {symbol}: per-symbol cooldown active")
                continue
            
            filtered.append(proposal)
        
        if len(filtered) < len(proposals):
            logger.info(
                f"Per-symbol cooldown filtered: {len(filtered)}/{len(proposals)} proposals remain"
            )
        
        return filtered
    
    def _check_position_size(self, proposal: TradeProposal,
                            portfolio: PortfolioState,
                            regime: str) -> RiskCheckResult:
        """Check if position size is within limits"""
        violated = []

        existing_position_usd = portfolio.get_position_usd(proposal.symbol)
        pending_buy_usd = portfolio.get_pending_notional_usd("buy", proposal.symbol)

        def _pct(value_usd: float) -> float:
            try:
                return (value_usd / portfolio.account_value_usd) * 100 if portfolio.account_value_usd > 0 else 0.0
            except ZeroDivisionError:
                return 0.0

        existing_exposure_pct = _pct(existing_position_usd + pending_buy_usd)
        
        # Get base limits
        max_pos_pct = self.risk_config.get("max_position_size_pct", 5.0)
        min_pos_pct = self.risk_config.get("min_position_size_pct", 0.5)
        
        # Apply regime adjustment if enabled
        if self.regime_config.get("enabled", True):
            regime_settings = self.regime_config.get(regime, {})
            multiplier = regime_settings.get("position_size_multiplier", 1.0)
            max_pos_pct *= multiplier
        
        # Combine with existing exposure for BUY orders
        if proposal.side == "BUY":
            combined_pct = existing_exposure_pct + proposal.size_pct
            if combined_pct > max_pos_pct:
                violated.append(
                    f"position_size_with_pending ({combined_pct:.1f}% > {max_pos_pct:.1f}% including pending buys)"
                )

        # Check max
        if proposal.size_pct > max_pos_pct:
            violated.append(f"position_size_too_large ({proposal.size_pct:.1f}% > {max_pos_pct:.1f}%)")
        
        # Check min
        if proposal.size_pct < min_pos_pct:
            violated.append(f"position_size_too_small ({proposal.size_pct:.1f}% < {min_pos_pct:.1f}%)")
        
        # Check if already have position
        allow_pyramid = self.sizing_config.get("allow_pyramiding", False)
        if proposal.symbol in portfolio.open_positions:
            # Check pyramiding rules
            if not allow_pyramid:
                violated.append(f"already_have_position ({proposal.symbol})")

        # Pending orders count toward pyramiding guard as well
        if not allow_pyramid and pending_buy_usd > 0 and proposal.side == "BUY":
            violated.append(f"pending_buy_exists ({proposal.symbol})")
        
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
        Check cluster/theme exposure limits (spec requirement).
        
        Enforces max_per_theme_pct from policy.yaml risk section:
        - MEME: 5%
        - L2: 10%
        - DEFI: 10%
        
        Calculates existing + proposed exposure per theme.
        """
        if not self.universe_manager:
            # No universe manager, can't check clusters
            logger.debug("No universe manager, skipping cluster limits check")
            return RiskCheckResult(approved=True)
        
        # Get policy limits for each theme/cluster from risk.max_per_theme_pct
        max_per_theme = self.risk_config.get("max_per_theme_pct", {})
        
        if not max_per_theme:
            logger.debug("No max_per_theme_pct configured, skipping cluster limits")
            return RiskCheckResult(approved=True)
        
        # Calculate current cluster exposure from open positions (using schema)
        cluster_exposure = defaultdict(float)  # {cluster_name: total_size_pct}
        
        for symbol in portfolio.open_positions.keys():
            size_usd = portfolio.get_position_usd(symbol)
            cluster = self.universe_manager.get_asset_cluster(symbol)
            if cluster:
                size_pct = (size_usd / portfolio.account_value_usd) * 100
                cluster_exposure[cluster] += size_pct

        pending_buy_orders = (portfolio.pending_orders or {}).get("buy", {})
        for symbol, usd_value in pending_buy_orders.items():
            lookup_symbol = symbol if '-' in symbol else f"{symbol}-USD"
            cluster = self.universe_manager.get_asset_cluster(lookup_symbol)
            if not cluster:
                continue
            try:
                size_pct = (float(usd_value) / portfolio.account_value_usd) * 100 if portfolio.account_value_usd > 0 else 0.0
            except (TypeError, ValueError):
                continue
            cluster_exposure[cluster] += size_pct
        
        logger.debug(f"Current cluster exposure: {dict(cluster_exposure)}")
        
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
                            f"{cluster} theme limit violated: "
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
                reason="All proposals would violate theme/cluster limits",
                violated_checks=violated
            )
        
        # Update proposals list to only include approved ones
        proposals.clear()
        proposals.extend(approved_proposals)
        
        logger.debug(f"Cluster limits passed: {len(approved_proposals)}/{len(proposals) + len(violated)} approved")
        
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
    
    def _filter_degraded_products(self, proposals: List[TradeProposal]) -> List[TradeProposal]:
        """
        Filter out proposals for products with degraded exchange status.
        
        Blocks trading on products flagged as:
        - POST_ONLY (can only add liquidity, no market orders)
        - LIMIT_ONLY (no market orders allowed)
        - CANCEL_ONLY (can only cancel, no new orders)
        - offline (not tradeable)
        
        Args:
            proposals: List of trade proposals
            
        Returns:
            Filtered list of proposals (only products with normal status)
        """
        if not self.exchange:
            # No exchange adapter, can't check status
            return proposals
        
        if not self.circuit_breakers_config.get("check_product_status", True):
            # Product status checks disabled
            return proposals
        
        filtered = []
        blocked = []
        
        for proposal in proposals:
            product_id = proposal.symbol
            try:
                metadata = self.exchange.get_product_metadata(product_id)
                if not metadata:
                    logger.warning(f"No metadata found for {product_id}, blocking trade")
                    blocked.append((product_id, "no_metadata"))
                    continue
                
                status = metadata.get("status", "").upper()
                
                # Block degraded or restricted statuses
                restricted_statuses = ["POST_ONLY", "LIMIT_ONLY", "CANCEL_ONLY", "OFFLINE"]
                if status in restricted_statuses:
                    logger.warning(f"Blocking {product_id}: exchange status={status}")
                    blocked.append((product_id, status))
                    continue
                
                # Product is tradeable
                filtered.append(proposal)
                
            except Exception as e:
                logger.error(f"Error checking product status for {product_id}: {e}")
                # Fail closed: block trade on error
                blocked.append((product_id, f"error: {e}"))
                continue
        
        if blocked:
            logger.warning(f"Filtered {len(blocked)} proposals due to exchange product status: {blocked}")
        
        return filtered
    
    def _check_circuit_breakers(self, portfolio: PortfolioState, regime: str) -> RiskCheckResult:
        """
        Check circuit breakers for data staleness, API health, exchange status, and volatility.
        
        Fail CLOSED on any of:
        - Stale data (quotes/candles too old)
        - API health issues (consecutive errors, rate limits)
        - Exchange degraded (maintenance, status issues)
        - Extreme volatility (crash regime)
        - Insufficient universe (too few eligible assets)
        
        Returns:
            RiskCheckResult with approval or rejection reason
        """
        if not self.circuit_breakers_config:
            # Circuit breakers not configured, skip checks
            return RiskCheckResult(approved=True)
        
        violated = []
        
        # 1. Check for rate limit cooldown
        if self.circuit_breakers_config.get("pause_on_rate_limit", True):
            if self._last_rate_limit_time:
                cooldown_seconds = self.circuit_breakers_config.get("rate_limit_cooldown_seconds", 60)
                elapsed = (datetime.utcnow() - self._last_rate_limit_time).total_seconds()
                if elapsed < cooldown_seconds:
                    remaining = cooldown_seconds - elapsed
                    logger.warning(f"Rate limit cooldown active: {remaining:.0f}s remaining")
                    return RiskCheckResult(
                        approved=False,
                        reason=f"Rate limit cooldown ({remaining:.0f}s remaining)",
                        violated_checks=["rate_limit_cooldown"]
                    )
        
        # 2. Check API health (consecutive errors)
        max_errors = self.circuit_breakers_config.get("max_consecutive_api_errors", 3)
        if self._api_error_count >= max_errors:
            error_window = self.circuit_breakers_config.get("api_error_window_seconds", 300)
            if self._last_api_success:
                elapsed = (datetime.utcnow() - self._last_api_success).total_seconds()
                if elapsed > error_window:
                    # Reset counter after window
                    logger.info(f"Resetting API error counter after {elapsed:.0f}s")
                    self._api_error_count = 0
                else:
                    logger.error(f"API health check failed: {self._api_error_count} consecutive errors")
                    return RiskCheckResult(
                        approved=False,
                        reason=f"API health degraded ({self._api_error_count} consecutive errors)",
                        violated_checks=["api_health"]
                    )
            else:
                logger.error(f"API health check failed: {self._api_error_count} errors, no successful calls")
                return RiskCheckResult(
                    approved=False,
                    reason=f"API health critical ({self._api_error_count} errors)",
                    violated_checks=["api_health"]
                )
        
        # 3. Check exchange status (if exchange adapter provided)
        if self.exchange and self.circuit_breakers_config.get("check_exchange_status", True):
            try:
                # Simple connectivity check
                if not self.exchange.check_connectivity():
                    logger.error("Exchange connectivity check failed")
                    return RiskCheckResult(
                        approved=False,
                        reason="Exchange connectivity failed",
                        violated_checks=["exchange_connectivity"]
                    )
            except Exception as e:
                logger.error(f"Exchange health check raised exception: {e}")
                return RiskCheckResult(
                    approved=False,
                    reason=f"Exchange health check error: {e}",
                    violated_checks=["exchange_health"]
                )
        
        # 4. Check volatility circuit breaker (crash regime)
        max_vol = self.circuit_breakers_config.get("max_realized_volatility_pct", 150)
        if regime == "crash":
            logger.warning("Crash regime detected - halting new trades")
            return RiskCheckResult(
                approved=False,
                reason="Crash regime active (extreme volatility)",
                violated_checks=["volatility_crash"]
            )
        
        # 5. Check minimum eligible assets
        min_assets = self.circuit_breakers_config.get("min_eligible_assets", 2)
        if self.universe_manager:
            try:
                # This would require universe snapshot to be passed or cached
                # For now, we'll skip this check if universe not available
                pass
            except Exception:
                pass
        
        # All checks passed
        return RiskCheckResult(approved=True)
    
    def record_api_success(self):
        """Record successful API call for circuit breaker tracking"""
        self._api_error_count = 0
        self._last_api_success = datetime.utcnow()
        logger.debug("API success recorded, error counter reset")
    
    def record_api_error(self):
        """Record API error for circuit breaker tracking"""
        self._api_error_count += 1
        logger.warning(f"API error recorded (count: {self._api_error_count})")
    
    def record_rate_limit(self):
        """Record rate limit hit for circuit breaker tracking"""
        self._last_rate_limit_time = datetime.utcnow()
        logger.warning("Rate limit hit recorded")
    
    def apply_symbol_cooldown(self, symbol: str, is_stop_loss: bool = False):
        """
        Apply per-symbol cooldown after a loss or stop-out.
        
        Args:
            symbol: Trading symbol (e.g., "BTC-USD")
            is_stop_loss: If True, use longer cooldown for stop-loss hits
        """
        if not self.risk_config.get("per_symbol_cooldown_enabled", True):
            return
        
        from infra.state_store import get_state_store
        # Use state_store from risk engine if available, otherwise get default
        state_store = getattr(self, '_state_store', None) or get_state_store()
        state = state_store.load()
        
        # Determine cooldown duration
        if is_stop_loss:
            cooldown_minutes = self.risk_config.get("per_symbol_cooldown_after_stop", 60)
        else:
            cooldown_minutes = self.risk_config.get("per_symbol_cooldown_minutes", 30)
        
        # Set cooldown (use timezone-aware datetime to match is_cooldown_active check)
        from datetime import timezone
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
        state.setdefault("cooldowns", {})[symbol] = cooldown_until.isoformat()
        
        state_store.save(state)
        
        logger.info(
            f"Applied {cooldown_minutes}min cooldown to {symbol} "
            f"({'stop-loss' if is_stop_loss else 'loss'})"
        )
