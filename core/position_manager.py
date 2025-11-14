"""
Position Management: Exit Logic for Stop-Loss and Take-Profit

Evaluates open positions against configured targets and generates SELL proposals.
Implements trailing stops, max hold time, and regime-aware exits.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from strategy.rules_engine import TradeProposal

logger = logging.getLogger(__name__)


@dataclass
class PositionExitSignal:
    """Signal to exit a position"""
    symbol: str
    reason: str  # "stop_loss", "take_profit", "max_hold", "trailing_stop"
    current_price: float
    entry_price: float
    pnl_pct: float
    hold_hours: float
    confidence: float = 1.0  # Always high confidence for exits


class PositionManager:
    """
    Manages position exits based on stop-loss, take-profit, and max hold time.
    
    Responsibilities:
    - Check open positions against exit targets
    - Generate SELL proposals for positions meeting exit criteria
    - Support trailing stops (future enhancement)
    - Respect regime-aware exit adjustments
    """
    
    def __init__(self, policy: Dict, state_store=None):
        """
        Initialize PositionManager.
        
        Args:
            policy: Policy config dict
            state_store: StateStore instance for tracking position metadata
        """
        self.policy = policy
        self.state_store = state_store
        self.exit_config = policy.get("exits", {})
        
        # Exit feature flags
        self.enabled = self.exit_config.get("enabled", True)
        self.check_stop_loss = self.exit_config.get("check_stop_loss", True)
        self.check_take_profit = self.exit_config.get("check_take_profit", True)
        self.check_max_hold = self.exit_config.get("check_max_hold", True)
        
        # Trailing stop (future enhancement)
        self.use_trailing_stop = self.exit_config.get("use_trailing_stop", False)
        self.trailing_stop_pct = self.exit_config.get("trailing_stop_pct", 5.0)
        
        logger.info(
            f"PositionManager initialized: enabled={self.enabled}, "
            f"stop_loss={self.check_stop_loss}, take_profit={self.check_take_profit}, "
            f"max_hold={self.check_max_hold}"
        )
    
    def evaluate_positions(
        self,
        positions: Dict[str, Dict],
        managed_positions: Dict[str, Dict],
        current_prices: Dict[str, float],
    ) -> List[TradeProposal]:
        """
        Evaluate all open positions and generate SELL proposals for exits.
        
        Args:
            positions: Current positions from state store (symbol -> {total, usd_value, ...})
            managed_positions: Managed position metadata (symbol -> {entry_price, entry_time, ...})
            current_prices: Current market prices (symbol -> price)
        
        Returns:
            List of SELL TradeProposal objects
        """
        if not self.enabled:
            logger.debug("Position exits disabled in config")
            return []
        
        proposals = []
        now = datetime.now(timezone.utc)
        
        for symbol, pos_data in positions.items():
            quantity = pos_data.get("total", 0.0)
            if quantity <= 0:
                continue
            
            # Get managed position metadata
            managed = managed_positions.get(symbol, {})
            if not managed:
                logger.debug(f"No managed position metadata for {symbol}, skipping exit check")
                continue
            
            entry_price = managed.get("entry_price")
            entry_time_str = managed.get("entry_time")
            stop_loss_pct = managed.get("stop_loss_pct")
            take_profit_pct = managed.get("take_profit_pct")
            max_hold_hours = managed.get("max_hold_hours")
            
            if not entry_price or not entry_time_str:
                logger.debug(f"Missing entry_price or entry_time for {symbol}, skipping")
                continue
            
            # Get current price
            current_price = current_prices.get(symbol)
            if not current_price or current_price <= 0:
                logger.warning(f"No valid current price for {symbol}, skipping exit check")
                continue
            
            # Calculate PnL and hold time
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            entry_time = datetime.fromisoformat(entry_time_str)
            hold_hours = (now - entry_time).total_seconds() / 3600
            
            # Check exit conditions
            exit_signal = self._check_exit_conditions(
                symbol=symbol,
                current_price=current_price,
                entry_price=entry_price,
                pnl_pct=pnl_pct,
                hold_hours=hold_hours,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                max_hold_hours=max_hold_hours,
            )
            
            if exit_signal:
                # Generate SELL proposal
                proposal = self._create_sell_proposal(
                    symbol=symbol,
                    quantity=quantity,
                    current_price=current_price,
                    exit_signal=exit_signal,
                )
                proposals.append(proposal)
                
                logger.info(
                    f"EXIT SIGNAL: {symbol} {exit_signal.reason.upper()} - "
                    f"PnL: {pnl_pct:+.2f}%, Hold: {hold_hours:.1f}h, "
                    f"Price: ${entry_price:.4f} → ${current_price:.4f}"
                )
        
        if proposals:
            logger.info(f"Generated {len(proposals)} SELL proposals from position exits")
        else:
            logger.debug("No positions met exit criteria")
        
        return proposals
    
    def _check_exit_conditions(
        self,
        symbol: str,
        current_price: float,
        entry_price: float,
        pnl_pct: float,
        hold_hours: float,
        stop_loss_pct: Optional[float],
        take_profit_pct: Optional[float],
        max_hold_hours: Optional[float],
    ) -> Optional[PositionExitSignal]:
        """
        Check if position meets any exit condition.
        
        Priority order: stop_loss > take_profit > max_hold
        
        Returns:
            PositionExitSignal if exit condition met, None otherwise
        """
        # 1. Check stop-loss (highest priority - protect capital)
        if self.check_stop_loss and stop_loss_pct is not None:
            if pnl_pct <= -abs(stop_loss_pct):
                return PositionExitSignal(
                    symbol=symbol,
                    reason="stop_loss",
                    current_price=current_price,
                    entry_price=entry_price,
                    pnl_pct=pnl_pct,
                    hold_hours=hold_hours,
                    confidence=1.0,
                )
        
        # 2. Check take-profit (lock in gains)
        if self.check_take_profit and take_profit_pct is not None:
            if pnl_pct >= take_profit_pct:
                return PositionExitSignal(
                    symbol=symbol,
                    reason="take_profit",
                    current_price=current_price,
                    entry_price=entry_price,
                    pnl_pct=pnl_pct,
                    hold_hours=hold_hours,
                    confidence=1.0,
                )
        
        # 3. Check max hold time (forced exit)
        if self.check_max_hold and max_hold_hours is not None:
            if hold_hours >= max_hold_hours:
                return PositionExitSignal(
                    symbol=symbol,
                    reason="max_hold",
                    current_price=current_price,
                    entry_price=entry_price,
                    pnl_pct=pnl_pct,
                    hold_hours=hold_hours,
                    confidence=0.8,  # Slightly lower confidence for time-based exits
                )
        
        return None
    
    def _create_sell_proposal(
        self,
        symbol: str,
        quantity: float,
        current_price: float,
        exit_signal: PositionExitSignal,
    ) -> TradeProposal:
        """
        Create a SELL TradeProposal from exit signal.
        
        Args:
            symbol: Asset symbol (e.g., "BTC")
            quantity: Position size to sell
            current_price: Current market price
            exit_signal: Exit signal details
        
        Returns:
            TradeProposal for SELL order
        """
        notional_usd = quantity * current_price
        
        return TradeProposal(
            symbol=f"{symbol}-USD",
            side="sell",
            size_pct=0.0,  # Size determined by quantity, not %
            reason=f"exit_{exit_signal.reason}",
            confidence=exit_signal.confidence,
            stop_loss_pct=None,  # N/A for exit orders
            take_profit_pct=None,  # N/A for exit orders
            max_hold_hours=None,  # N/A for exit orders
            metadata={
                "exit_reason": exit_signal.reason,
                "entry_price": exit_signal.entry_price,
                "current_price": exit_signal.current_price,
                "pnl_pct": exit_signal.pnl_pct,
                "hold_hours": exit_signal.hold_hours,
                "quantity": quantity,
                "notional_usd": notional_usd,
            },
        )
    
    def update_trailing_stops(
        self,
        positions: Dict[str, Dict],
        managed_positions: Dict[str, Dict],
        current_prices: Dict[str, float],
    ) -> Dict[str, float]:
        """
        Update trailing stop prices for positions (future enhancement).
        
        Args:
            positions: Current positions
            managed_positions: Managed position metadata
            current_prices: Current market prices
        
        Returns:
            Updated trailing stop prices (symbol -> stop_price)
        """
        if not self.use_trailing_stop:
            return {}
        
        # TODO: Implement trailing stop logic
        # - Track highest price since entry
        # - Set stop at highest_price * (1 - trailing_stop_pct/100)
        # - Only trigger if current_price drops below stop
        
        logger.debug("Trailing stop not yet implemented")
        return {}


def get_position_manager(
    policy: Dict,
    state_store=None,
) -> PositionManager:
    """Factory function to get PositionManager instance"""
    return PositionManager(policy=policy, state_store=state_store)
