"""
247trader-v2 Core: Trade Pacing and Limits

Centralized trade pacing logic extracted from RiskEngine.
Handles timing constraints, frequency limits, and cooldowns.

Separation of Concerns:
- RiskEngine: Risk checks (exposure, position caps, circuit breakers)
- TradeLimits: Pacing/timing (spacing, cooldowns, frequency limits)

Pattern: Single Responsibility Principle
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set
import logging

from strategy.rules_engine import TradeProposal

logger = logging.getLogger(__name__)


@dataclass
class TradeTimingResult:
    """Result of trade timing check"""
    approved: bool
    reason: str = ""
    violated_checks: List[str] = None
    cooled_symbols: Set[str] = None  # Symbols on cooldown
    
    def __post_init__(self):
        if self.violated_checks is None:
            self.violated_checks = []
        if self.cooled_symbols is None:
            self.cooled_symbols = set()


class TradeLimits:
    """
    Manages trade pacing, frequency limits, and cooldowns.
    
    Enforces:
    - Global trade spacing (min time between any trades)
    - Per-symbol trade spacing (min time between trades on same symbol)
    - Hourly/daily trade frequency caps
    - Consecutive loss cooldowns
    - Per-symbol cooldowns (win/loss/stop-loss differentiated)
    
    Configuration from policy.yaml:
    - min_seconds_between_trades: Global spacing (e.g., 180s)
    - per_symbol_trade_spacing_seconds: Per-symbol spacing (e.g., 900s)
    - max_trades_per_hour: Hourly cap (e.g., 5)
    - max_trades_per_day: Daily cap (e.g., 120)
    - per_symbol_cooldown_minutes: Base cooldown (30min)
    - per_symbol_cooldown_after_stop: Stop-loss cooldown (120min)
    - per_symbol_cooldown_win_minutes: Win cooldown (10min, NEW)
    - per_symbol_cooldown_loss_minutes: Loss cooldown (60min, NEW)
    """
    
    def __init__(self, config: Dict, state_store=None):
        """
        Initialize trade limits manager.
        
        Args:
            config: Risk config from policy.yaml
            state_store: StateStore instance for persisting cooldowns/counters
        """
        self.config = config
        self.state_store = state_store
        
        # Validate config before proceeding
        self._validate_config(config)
        
        # Global spacing
        self.min_global_spacing_sec = config.get("min_seconds_between_trades", 180)
        
        # Per-symbol spacing
        self.per_symbol_spacing_sec = config.get("per_symbol_trade_spacing_seconds", 900)
        
        # Frequency limits
        self.max_trades_per_hour = config.get("max_new_trades_per_hour",
                                              config.get("max_trades_per_hour", 5))
        self.max_trades_per_day = config.get("max_trades_per_day", 120)
        
        # Consecutive loss cooldown
        self.cooldown_after_losses = config.get("cooldown_after_loss_trades", 3)
        self.loss_cooldown_minutes = config.get("cooldown_minutes", 60)
        
        # Per-symbol cooldowns (differentiated)
        self.cooldown_enabled = config.get("per_symbol_cooldown_enabled", True)
        self.cooldown_win_minutes = config.get("per_symbol_cooldown_win_minutes", 10)
        self.cooldown_loss_minutes = config.get("per_symbol_cooldown_loss_minutes", 60)
        self.cooldown_stop_minutes = config.get("per_symbol_cooldown_after_stop", 120)
        
        logger.info(
            f"TradeLimits initialized: global_spacing={self.min_global_spacing_sec}s, "
            f"per_symbol_spacing={self.per_symbol_spacing_sec}s, "
            f"max_per_hour={self.max_trades_per_hour}, max_per_day={self.max_trades_per_day}, "
            f"cooldowns_enabled={self.cooldown_enabled}"
        )
    
    def check_all(
        self,
        proposals: List[TradeProposal],
        trades_today: int,
        trades_this_hour: int,
        consecutive_losses: int,
        last_loss_time: Optional[datetime],
        current_time: Optional[datetime] = None
    ) -> TradeTimingResult:
        """
        Check all timing constraints.
        
        Args:
            proposals: Proposed trades
            trades_today: Trade count today
            trades_this_hour: Trade count this hour
            consecutive_losses: Consecutive loss count
            last_loss_time: Timestamp of last loss
            current_time: Current time (for backtesting)
            
        Returns:
            TradeTimingResult with approval status
        """
        if not proposals:
            return TradeTimingResult(approved=True)
        
        now = current_time or datetime.now(timezone.utc)
        
        # Check consecutive loss cooldown
        loss_check = self._check_loss_cooldown(
            consecutive_losses, last_loss_time, now
        )
        if not loss_check.approved:
            return loss_check
        
        # Check frequency limits
        freq_check = self._check_frequency_limits(
            trades_today, trades_this_hour
        )
        if not freq_check.approved:
            return freq_check
        
        # Check global trade spacing
        global_check = self._check_global_spacing(now)
        if not global_check.approved:
            return global_check
        
        # Check per-symbol spacing and cooldowns
        symbol_check = self._check_per_symbol_timing(proposals, now)
        if not symbol_check.approved:
            return symbol_check
        
        return TradeTimingResult(approved=True)
    
    def filter_proposals_by_timing(
        self,
        proposals: List[TradeProposal],
        current_time: Optional[datetime] = None
    ) -> tuple[List[TradeProposal], Dict[str, List[str]]]:
        """
        Filter proposals removing those violating per-symbol timing.
        
        Args:
            proposals: Trade proposals
            current_time: Current time (for backtesting)
            
        Returns:
            (approved_proposals, rejections_by_symbol)
        """
        now = current_time or datetime.now(timezone.utc)
        
        approved = []
        rejections = {}
        
        for proposal in proposals:
            symbol = proposal.symbol
            
            # Check per-symbol cooldown
            if self._is_symbol_on_cooldown(symbol, now):
                rejections.setdefault(symbol, []).append("per_symbol_cooldown")
                logger.debug(f"{symbol}: Blocked by cooldown")
                continue
            
            # Check per-symbol spacing
            if self._violates_symbol_spacing(symbol, now):
                rejections.setdefault(symbol, []).append("per_symbol_spacing")
                logger.debug(f"{symbol}: Blocked by spacing ({self.per_symbol_spacing_sec}s)")
                continue
            
            approved.append(proposal)
        
        return approved, rejections
    
    def _check_loss_cooldown(
        self,
        consecutive_losses: int,
        last_loss_time: Optional[datetime],
        now: datetime
    ) -> TradeTimingResult:
        """Check if in cooldown period after consecutive losses"""
        if consecutive_losses < self.cooldown_after_losses:
            return TradeTimingResult(approved=True)
        
        if not last_loss_time:
            return TradeTimingResult(approved=True)
        
        # Ensure timezone-aware
        if last_loss_time.tzinfo is None:
            last_loss_time = last_loss_time.replace(tzinfo=timezone.utc)
        
        cooldown_expires = last_loss_time + timedelta(minutes=self.loss_cooldown_minutes)
        
        if now < cooldown_expires:
            minutes_left = (cooldown_expires - now).total_seconds() / 60
            return TradeTimingResult(
                approved=False,
                reason=f"Cooldown: {consecutive_losses} consecutive losses ({minutes_left:.0f}min left)",
                violated_checks=["consecutive_loss_cooldown"]
            )
        
        return TradeTimingResult(approved=True)
    
    def _check_frequency_limits(
        self,
        trades_today: int,
        trades_this_hour: int
    ) -> TradeTimingResult:
        """Check hourly and daily trade frequency limits"""
        if trades_today >= self.max_trades_per_day:
            return TradeTimingResult(
                approved=False,
                reason=f"Daily trade limit reached ({trades_today}/{self.max_trades_per_day})",
                violated_checks=["trade_frequency_daily"]
            )
        
        if trades_this_hour >= self.max_trades_per_hour:
            return TradeTimingResult(
                approved=False,
                reason=f"Hourly trade limit reached ({trades_this_hour}/{self.max_trades_per_hour})",
                violated_checks=["trade_frequency_hourly"]
            )
        
        return TradeTimingResult(approved=True)
    
    def _check_global_spacing(self, now: datetime) -> TradeTimingResult:
        """Check minimum time between any trades (global pacing)"""
        if self.min_global_spacing_sec <= 0:
            return TradeTimingResult(approved=True)
        
        if not self.state_store:
            return TradeTimingResult(approved=True)
        
        state = self.state_store.load()
        last_trade_ts_str = state.get("last_trade_timestamp")
        
        if not last_trade_ts_str:
            return TradeTimingResult(approved=True)
        
        try:
            last_trade_ts = datetime.fromisoformat(last_trade_ts_str)
            if last_trade_ts.tzinfo is None:
                last_trade_ts = last_trade_ts.replace(tzinfo=timezone.utc)
            
            elapsed = (now - last_trade_ts).total_seconds()
            
            if elapsed < self.min_global_spacing_sec:
                remaining = self.min_global_spacing_sec - elapsed
                return TradeTimingResult(
                    approved=False,
                    reason=f"Global trade spacing active ({remaining:.0f}s remaining, min {self.min_global_spacing_sec}s)",
                    violated_checks=["global_trade_spacing"]
                )
        except Exception as e:
            logger.warning(f"Error checking global spacing: {e}")
        
        return TradeTimingResult(approved=True)
    
    def _check_per_symbol_timing(
        self,
        proposals: List[TradeProposal],
        now: datetime
    ) -> TradeTimingResult:
        """Check per-symbol spacing and cooldowns for all proposals"""
        cooled_symbols = set()
        
        for proposal in proposals:
            symbol = proposal.symbol
            
            # Check cooldown
            if self._is_symbol_on_cooldown(symbol, now):
                cooled_symbols.add(symbol)
            
            # Check spacing
            if self._violates_symbol_spacing(symbol, now):
                cooled_symbols.add(symbol)
        
        if cooled_symbols:
            return TradeTimingResult(
                approved=False,
                reason=f"Per-symbol timing violated for: {', '.join(sorted(cooled_symbols))}",
                violated_checks=["per_symbol_timing"],
                cooled_symbols=cooled_symbols
            )
        
        return TradeTimingResult(approved=True)
    
    def _is_symbol_on_cooldown(self, symbol: str, now: datetime) -> bool:
        """Check if symbol is on cooldown"""
        if not self.cooldown_enabled or not self.state_store:
            return False
        
        state = self.state_store.load()
        cooldowns = state.get("cooldowns", {})
        
        cooldown_until_str = cooldowns.get(symbol)
        if not cooldown_until_str:
            return False
        
        try:
            cooldown_until = datetime.fromisoformat(cooldown_until_str)
            if cooldown_until.tzinfo is None:
                cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
            
            return now < cooldown_until
        except Exception as e:
            logger.warning(f"Error checking cooldown for {symbol}: {e}")
            return False
    
    def _violates_symbol_spacing(self, symbol: str, now: datetime) -> bool:
        """Check if symbol violates minimum spacing"""
        if self.per_symbol_spacing_sec <= 0 or not self.state_store:
            return False
        
        state = self.state_store.load()
        last_trade_times = state.get("last_trade_time_by_symbol", {})
        
        last_trade_str = last_trade_times.get(symbol)
        if not last_trade_str:
            return False
        
        try:
            last_trade = datetime.fromisoformat(last_trade_str)
            if last_trade.tzinfo is None:
                last_trade = last_trade.replace(tzinfo=timezone.utc)
            
            elapsed = (now - last_trade).total_seconds()
            return elapsed < self.per_symbol_spacing_sec
        except Exception as e:
            logger.warning(f"Error checking spacing for {symbol}: {e}")
            return False
    
    def apply_cooldown(
        self,
        symbol: str,
        outcome: str = "loss",
        current_time: Optional[datetime] = None
    ):
        """
        Apply cooldown to symbol based on trade outcome.
        
        Args:
            symbol: Trading symbol
            outcome: Trade outcome ("win", "loss", "stop_loss")
            current_time: Current time (for backtesting)
        """
        if not self.cooldown_enabled or not self.state_store:
            return
        
        # Determine cooldown duration based on outcome
        if outcome == "stop_loss":
            cooldown_minutes = self.cooldown_stop_minutes
        elif outcome == "loss":
            cooldown_minutes = self.cooldown_loss_minutes
        elif outcome == "win":
            cooldown_minutes = self.cooldown_win_minutes
        else:
            logger.warning(f"Unknown outcome '{outcome}', defaulting to loss cooldown")
            cooldown_minutes = self.cooldown_loss_minutes
        
        now = current_time or datetime.now(timezone.utc)
        cooldown_until = now + timedelta(minutes=cooldown_minutes)
        
        state = self.state_store.load()
        state.setdefault("cooldowns", {})[symbol] = cooldown_until.isoformat()
        
        # Also store outcome for analytics
        state.setdefault("last_trade_result", {})[symbol] = {
            "outcome": outcome,
            "timestamp": now.isoformat(),
            "cooldown_until": cooldown_until.isoformat()
        }
        
        self.state_store.save(state)
        
        logger.info(
            f"Applied {cooldown_minutes}min cooldown to {symbol} "
            f"(outcome={outcome})"
        )
    
    def record_trade(
        self,
        symbol: str,
        current_time: Optional[datetime] = None
    ):
        """
        Record trade execution for spacing tracking.
        
        Args:
            symbol: Trading symbol
            current_time: Current time (for backtesting)
        """
        if not self.state_store:
            return
        
        now = current_time or datetime.now(timezone.utc)
        
        state = self.state_store.load()
        
        # Update global last trade time
        state["last_trade_timestamp"] = now.isoformat()
        
        # Update per-symbol last trade time
        state.setdefault("last_trade_time_by_symbol", {})[symbol] = now.isoformat()
        
        self.state_store.save(state)
        
        logger.debug(f"Recorded trade for {symbol} at {now.isoformat()}")
    
    def get_cooldown_status(self, symbol: str, current_time: Optional[datetime] = None) -> Dict:
        """
        Get cooldown status for symbol.
        
        Args:
            symbol: Trading symbol
            current_time: Current time (for backtesting)
            
        Returns:
            Dict with: on_cooldown (bool), cooldown_until (str), last_outcome (str), minutes_remaining (float)
        """
        if not self.state_store:
            return {"on_cooldown": False}
        
        now = current_time or datetime.now(timezone.utc)
        
        state = self.state_store.load()
        cooldowns = state.get("cooldowns", {})
        last_results = state.get("last_trade_result", {})
        
        cooldown_until_str = cooldowns.get(symbol)
        if not cooldown_until_str:
            return {"on_cooldown": False}
        
        try:
            cooldown_until = datetime.fromisoformat(cooldown_until_str)
            if cooldown_until.tzinfo is None:
                cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
            
            on_cooldown = now < cooldown_until
            minutes_remaining = max(0, (cooldown_until - now).total_seconds() / 60)
            
            last_result = last_results.get(symbol, {})
            
            return {
                "on_cooldown": on_cooldown,
                "cooldown_until": cooldown_until.isoformat(),
                "minutes_remaining": minutes_remaining,
                "last_outcome": last_result.get("outcome", "unknown"),
                "last_trade_time": last_result.get("timestamp", "")
            }
        except Exception as e:
            logger.warning(f"Error getting cooldown status for {symbol}: {e}")
            return {"on_cooldown": False}
    
    def _validate_config(self, config: Dict):
        """
        Validate TradeLimits configuration.
        
        Raises:
            ValueError: If config is invalid
        """
        errors = []
        
        # Global spacing validation
        global_spacing = config.get("min_seconds_between_trades")
        if global_spacing is not None:
            if not isinstance(global_spacing, (int, float)):
                errors.append("min_seconds_between_trades must be numeric")
            elif global_spacing < 0:
                errors.append("min_seconds_between_trades must be >= 0")
            elif global_spacing > 3600:
                errors.append("min_seconds_between_trades should be <= 3600s (1 hour)")
        
        # Per-symbol spacing validation
        symbol_spacing = config.get("per_symbol_trade_spacing_seconds")
        if symbol_spacing is not None:
            if not isinstance(symbol_spacing, (int, float)):
                errors.append("per_symbol_trade_spacing_seconds must be numeric")
            elif symbol_spacing < 0:
                errors.append("per_symbol_trade_spacing_seconds must be >= 0")
            elif symbol_spacing > 86400:
                errors.append("per_symbol_trade_spacing_seconds should be <= 86400s (24 hours)")
        
        # Frequency limits validation
        trades_per_hour = config.get("max_new_trades_per_hour") or config.get("max_trades_per_hour")
        if trades_per_hour is not None:
            if not isinstance(trades_per_hour, int):
                errors.append("max_trades_per_hour must be an integer")
            elif trades_per_hour < 1:
                errors.append("max_trades_per_hour must be >= 1")
            elif trades_per_hour > 100:
                errors.append("max_trades_per_hour should be <= 100")
        
        trades_per_day = config.get("max_trades_per_day")
        if trades_per_day is not None:
            if not isinstance(trades_per_day, int):
                errors.append("max_trades_per_day must be an integer")
            elif trades_per_day < 1:
                errors.append("max_trades_per_day must be >= 1")
            elif trades_per_day > 1000:
                errors.append("max_trades_per_day should be <= 1000")
            
            # Ensure daily limit >= hourly limit * 24
            if trades_per_hour is not None and trades_per_day < (trades_per_hour * 24):
                errors.append(
                    f"max_trades_per_day ({trades_per_day}) must be >= "
                    f"max_trades_per_hour ({trades_per_hour}) * 24 = {trades_per_hour * 24}"
                )
        
        # Cooldown validation
        cooldown_win = config.get("per_symbol_cooldown_win_minutes")
        if cooldown_win is not None:
            if not isinstance(cooldown_win, (int, float)):
                errors.append("per_symbol_cooldown_win_minutes must be numeric")
            elif cooldown_win < 0:
                errors.append("per_symbol_cooldown_win_minutes must be >= 0")
            elif cooldown_win > 1440:
                errors.append("per_symbol_cooldown_win_minutes should be <= 1440 (24 hours)")
        
        cooldown_loss = config.get("per_symbol_cooldown_loss_minutes")
        if cooldown_loss is not None:
            if not isinstance(cooldown_loss, (int, float)):
                errors.append("per_symbol_cooldown_loss_minutes must be numeric")
            elif cooldown_loss < 0:
                errors.append("per_symbol_cooldown_loss_minutes must be >= 0")
            elif cooldown_loss > 1440:
                errors.append("per_symbol_cooldown_loss_minutes should be <= 1440 (24 hours)")
        
        cooldown_stop = config.get("per_symbol_cooldown_after_stop")
        if cooldown_stop is not None:
            if not isinstance(cooldown_stop, (int, float)):
                errors.append("per_symbol_cooldown_after_stop must be numeric")
            elif cooldown_stop < 0:
                errors.append("per_symbol_cooldown_after_stop must be >= 0")
            elif cooldown_stop > 1440:
                errors.append("per_symbol_cooldown_after_stop should be <= 1440 (24 hours)")
        
        # Consecutive loss cooldown validation
        loss_streak = config.get("cooldown_after_loss_trades")
        if loss_streak is not None:
            if not isinstance(loss_streak, int):
                errors.append("cooldown_after_loss_trades must be an integer")
            elif loss_streak < 1:
                errors.append("cooldown_after_loss_trades must be >= 1")
            elif loss_streak > 20:
                errors.append("cooldown_after_loss_trades should be <= 20")
        
        loss_cooldown = config.get("cooldown_minutes")
        if loss_cooldown is not None:
            if not isinstance(loss_cooldown, (int, float)):
                errors.append("cooldown_minutes must be numeric")
            elif loss_cooldown < 0:
                errors.append("cooldown_minutes must be >= 0")
            elif loss_cooldown > 1440:
                errors.append("cooldown_minutes should be <= 1440 (24 hours)")
        
        # If there are errors, raise with all issues
        if errors:
            error_msg = "TradeLimits configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.debug("TradeLimits configuration validated successfully")
