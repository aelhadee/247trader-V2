"""
247trader-v2 Infrastructure: State Store

Persistent state management with atomic writes.
Ported from v1 with enhancements for v2 architecture.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, List
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


DEFAULT_STATE = {
    "pnl_today": 0.0,
    "pnl_week": 0.0,
    "trades_today": 0,
    "trades_this_hour": 0,
    "consecutive_losses": 0,
    "last_loss_time": None,
    "last_win_time": None,
    "cooldowns": {},  # asset -> ISO time when eligible again
    "positions": {},  # asset -> position info
    "cash_balances": {},  # quote currency -> available
    "open_orders": {},  # client_order_id/order_id -> metadata
    "recent_orders": [],  # bounded history of closed/canceled orders
    "last_reconcile_at": None,
    "last_reset_date": None,
    "last_reset_hour": None,
    "events": [],  # Recent events log
    "high_water_mark": 0.0,  # Peak NAV for drawdown calculation
    "zero_trigger_cycles": 0,  # Counter for consecutive cycles with 0 triggers (bounded auto-tune)
    "auto_tune_applied": False,  # Flag to prevent repeated auto-tune adjustments
}


class StateStore:
    """
    Persistent state storage using JSON file.
    
    Features:
    - Atomic writes (temp file + rename)
    - Daily/hourly counter resets
    - Cooldown tracking
    - Trade history
    - Thread-safe operations
    """
    
    def __init__(self, state_file: Optional[str] = None):
        """
        Initialize state store.
        
        Args:
            state_file: Path to state JSON file (default: data/.state.json)
        """
        if state_file:
            self.state_file = Path(state_file)
        else:
            state_file = os.getenv("STATE_FILE", "data/.state.json")
            self.state_file = Path(state_file)
        
        # Ensure directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        self._state = None
        logger.info(f"Initialized StateStore at {self.state_file}")
    
    def load(self) -> Dict[str, Any]:
        """
        Load state from file.
        
        Returns:
            State dict with defaults merged
        """
        if not self.state_file.exists():
            logger.debug("No state file found, using defaults")
            return dict(DEFAULT_STATE)
        
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            if isinstance(data, dict):
                # Merge with defaults
                state = {**DEFAULT_STATE, **data}
                
                # Auto-reset counters if needed
                state = self._auto_reset(state)
                
                self._state = state
                logger.debug("Loaded state from file")
                return state
            else:
                logger.warning("Invalid state file format, using defaults")
                return dict(DEFAULT_STATE)
                
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return dict(DEFAULT_STATE)
    
    def save(self, state: Dict[str, Any]) -> None:
        """
        Save state to file atomically.
        
        Args:
            state: State dict to save
        """
        try:
            # Write to temp file first
            temp_fd, temp_path = tempfile.mkstemp(
                dir=self.state_file.parent,
                prefix=".state_",
                suffix=".json.tmp"
            )
            
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            
            # Atomic rename
            os.replace(temp_path, self.state_file)
            
            self._state = state
            logger.debug("Saved state to file")
            
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def update(self, event: str, **kwargs) -> Dict[str, Any]:
        """
        Update state with event.
        
        Args:
            event: Event type ("trade", "no_trade", "win", "loss", etc.)
            **kwargs: Event-specific data
            
        Returns:
            Updated state
        """
        state = self.load()
        now = datetime.now(timezone.utc)
        
        # Add event to history
        state.setdefault("events", []).append({
            "at": now.isoformat(),
            "event": event,
            **kwargs
        })
        
        # Trim events (keep last 100)
        if len(state["events"]) > 100:
            state["events"] = state["events"][-100:]
        
        # Handle specific events
        if event == "trade":
            state["trades_today"] = state.get("trades_today", 0) + 1
            state["trades_this_hour"] = state.get("trades_this_hour", 0) + 1
            
            # Update PnL if provided
            if "pnl_delta" in kwargs:
                try:
                    state["pnl_today"] = float(state.get("pnl_today", 0.0)) + float(kwargs["pnl_delta"])
                    state["pnl_week"] = float(state.get("pnl_week", 0.0)) + float(kwargs["pnl_delta"])
                except Exception:
                    pass
            
            # Apply cooldown
            asset = kwargs.get("asset")
            cooldown_minutes = kwargs.get("cooldown_minutes")
            if asset and cooldown_minutes:
                ts = (now + timedelta(minutes=int(cooldown_minutes))).isoformat()
                state.setdefault("cooldowns", {})[asset] = ts
        
        elif event == "loss":
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            state["last_loss_time"] = now.isoformat()
            
        elif event == "win":
            state["consecutive_losses"] = 0
            state["last_win_time"] = now.isoformat()
        
        self.save(state)
        return state

    def reconcile_exchange_snapshot(
        self,
        *,
        positions: Dict[str, Dict[str, float]],
        cash_balances: Dict[str, float],
        open_orders: Dict[str, Dict[str, Any]],
        timestamp: datetime,
    ) -> Dict[str, Any]:
        """Replace portfolio snapshot with authoritative data from the exchange."""

        state = self.load()
        state["positions"] = positions
        state["cash_balances"] = cash_balances
        state["last_reconcile_at"] = timestamp.isoformat()

        # Sync open orders against active set
        closed, created = self.sync_open_orders(open_orders, timestamp)

        state.setdefault("events", []).append(
            {
                "at": timestamp.isoformat(),
                "event": "reconcile",
                "positions": len(positions),
                "open_orders": len(open_orders),
                "orders_closed": closed,
                "orders_seen": created,
            }
        )

        if len(state["events"]) > 100:
            state["events"] = state["events"][-100:]

        self.save(state)
        return state

    def record_open_order(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Persist metadata for a newly submitted order."""

        state = self.load()
        now = datetime.now(timezone.utc).isoformat()
        entry = {**payload}
        entry.setdefault("status", "open")
        entry.setdefault("first_seen", now)
        entry["updated_at"] = now
        state.setdefault("open_orders", {})[key] = entry
        state.setdefault("events", []).append(
            {
                "at": now,
                "event": "order_opened",
                "order_key": key,
                "product_id": entry.get("product_id"),
                "side": entry.get("side"),
                "quote_size_usd": entry.get("quote_size_usd"),
            }
        )
        if len(state["events"]) > 100:
            state["events"] = state["events"][-100:]
        self.save(state)
        return state

    def close_order(
        self,
        key: str,
        *,
        status: str = "closed",
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Mark an order as closed and archive it."""

        state = self.load()
        open_orders = state.setdefault("open_orders", {})
        entry = open_orders.pop(key, None)
        if entry is None:
            return False, {}

        ts = (timestamp or datetime.now(timezone.utc)).isoformat()
        entry.update(details or {})
        entry["status"] = status
        entry["closed_at"] = ts
        entry["updated_at"] = ts
        state.setdefault("recent_orders", []).append(entry)
        # Trim history to last 50 entries
        if len(state["recent_orders"]) > 50:
            state["recent_orders"] = state["recent_orders"][-50:]

        state.setdefault("events", []).append(
            {
                "at": ts,
                "event": "order_closed",
                "order_key": key,
                "status": status,
            }
        )
        if len(state["events"]) > 100:
            state["events"] = state["events"][-100:]
        self.save(state)
        return True, entry

    def has_open_order(self, key: str) -> bool:
        """Return True if an order key is currently tracked as open."""

        state = self.load()
        return key in state.get("open_orders", {})

    def sync_open_orders(
        self,
        active_orders: Dict[str, Dict[str, Any]],
        timestamp: Optional[datetime] = None,
    ) -> Tuple[List[str], List[str]]:
        """Synchronize the open-order cache with authoritative exchange data."""

        state = self.load()
        now = (timestamp or datetime.now(timezone.utc)).isoformat()
        open_orders = state.setdefault("open_orders", {})

        created = []
        for key, order in active_orders.items():
            existing = key in open_orders
            entry = open_orders.get(key, {})
            order.setdefault("status", "open")
            order.setdefault("first_seen", entry.get("first_seen", now))
            order["updated_at"] = now
            open_orders[key] = {**entry, **order}
            if not existing:
                created.append(key)

        closed = []
        for key in list(open_orders.keys()):
            if key not in active_orders:
                entry = open_orders.pop(key)
                entry["status"] = "closed"
                entry["closed_at"] = now
                state.setdefault("recent_orders", []).append(entry)
                closed.append(key)

        if len(state.get("recent_orders", [])) > 50:
            state["recent_orders"] = state["recent_orders"][-50:]

        state.setdefault("events", []).append(
            {
                "at": now,
                "event": "open_orders_sync",
                "open_orders": len(open_orders),
                "closed": closed,
                "created": created,
            }
        )
        if len(state["events"]) > 100:
            state["events"] = state["events"][-100:]

        state["last_open_orders_sync"] = now
        self.save(state)
        return closed, created
    
    def _auto_reset(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Auto-reset daily/hourly counters.
        
        Args:
            state: Current state
            
        Returns:
            State with counters reset if needed
        """
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        current_hour = now.hour
        
        # Daily reset
        last_reset_date = state.get("last_reset_date")
        if last_reset_date != today:
            logger.info(f"Resetting daily counters (last reset: {last_reset_date})")
            state["trades_today"] = 0
            state["pnl_today"] = 0.0
            state["last_reset_date"] = today
        
        # Hourly reset
        last_reset_hour = state.get("last_reset_hour")
        if last_reset_hour != current_hour:
            logger.debug(f"Resetting hourly counters (last reset: {last_reset_hour}h)")
            state["trades_this_hour"] = 0
            state["last_reset_hour"] = current_hour
        
        # Clean expired cooldowns
        cooldowns = state.get("cooldowns", {})
        if cooldowns:
            expired = []
            for asset, ts_str in cooldowns.items():
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts <= now:
                        expired.append(asset)
                except Exception:
                    expired.append(asset)
            
            for asset in expired:
                del cooldowns[asset]
            
            if expired:
                logger.debug(f"Cleared expired cooldowns for: {expired}")
        
        return state
    
    def update_from_fills(self, filled_orders: list, portfolio: Any) -> Dict[str, Any]:
        """
        Update state after order fills.
        
        Args:
            filled_orders: List of successfully filled orders
            portfolio: Current portfolio state
            
        Returns:
            Updated state
        """
        state = self.load()
        
        for order in filled_orders:
            # Extract order details (handle both dict and object)
            if isinstance(order, dict):
                symbol = order.get('symbol')
                side = order.get('side')
                success = order.get('success', False)
            else:
                symbol = getattr(order, 'symbol', None)
                side = getattr(order, 'side', None)
                success = getattr(order, 'success', False)
            
            if success and symbol:
                # Increment trade counter
                state["trades_today"] = state.get("trades_today", 0) + 1
                state["trades_this_hour"] = state.get("trades_this_hour", 0) + 1
                
                # Log event
                state.setdefault("events", []).append({
                    "at": datetime.now(timezone.utc).isoformat(),
                    "event": "fill",
                    "symbol": symbol,
                    "side": side
                })
        
        # Trim events (keep last 100)
        if len(state.get("events", [])) > 100:
            state["events"] = state["events"][-100:]
        
        self.save(state)
        return state
    
    def is_cooldown_active(self, asset: str) -> bool:
        """
        Check if asset is in cooldown.
        
        Args:
            asset: Asset symbol
            
        Returns:
            True if cooldown active
        """
        state = self.load()
        cooldowns = state.get("cooldowns", {})
        ts_str = cooldowns.get(asset)
        
        if not ts_str:
            return False
        
        try:
            ts = datetime.fromisoformat(ts_str)
            now = datetime.now(timezone.utc)
            return ts > now
        except Exception:
            return False
    
    def record_fill(
        self,
        symbol: str,
        side: str,
        filled_size: float,
        fill_price: float,
        fees: float,
        timestamp: datetime
    ) -> Dict[str, Any]:
        """
        Record a fill and update positions/PnL.
        
        Strategy:
        - BUY: Create/add to position with weighted average entry price
        - SELL: Close/reduce position and calculate realized PnL
        
        Args:
            symbol: Trading pair (e.g., "BTC-USD")
            side: "BUY" or "SELL"
            filled_size: Filled quantity in base currency
            fill_price: Fill price
            fees: Trading fees paid
            timestamp: Fill timestamp
            
        Returns:
            Updated state with positions and PnL
        """
        state = self.load()
        positions = state.setdefault("positions", {})
        
        side_upper = side.upper()
        total_pnl = 0.0  # Initialize for event logging
        
        if side_upper == "BUY":
            # BUY: Create or add to position
            if symbol in positions:
                # Add to existing position - weighted average entry price
                pos = positions[symbol]
                old_qty = pos["quantity"]
                old_price = pos["entry_price"]
                old_fees = pos.get("fees_paid", 0.0)
                
                # Weighted average entry price
                total_value = (old_qty * old_price) + (filled_size * fill_price)
                new_qty = old_qty + filled_size
                new_entry_price = total_value / new_qty if new_qty > 0 else fill_price
                
                pos["quantity"] = new_qty
                pos["entry_price"] = new_entry_price
                pos["entry_value_usd"] = new_qty * new_entry_price
                pos["fees_paid"] = old_fees + fees
                pos["last_updated"] = timestamp.isoformat()
                
                logger.debug(f"Added to {symbol} position: {filled_size} @ ${fill_price:.2f}, "
                           f"new avg entry: ${new_entry_price:.2f}, total qty: {new_qty}")
            else:
                # Create new position
                positions[symbol] = {
                    "side": "BUY",
                    "quantity": filled_size,
                    "entry_price": fill_price,
                    "entry_value_usd": filled_size * fill_price,
                    "fees_paid": fees,
                    "entry_time": timestamp.isoformat(),
                    "last_updated": timestamp.isoformat()
                }
                
                logger.info(f"Opened {symbol} position: {filled_size} @ ${fill_price:.2f}")
        
        elif side_upper == "SELL":
            # SELL: Close or reduce position and calculate realized PnL
            if symbol not in positions:
                logger.warning(f"SELL without open position for {symbol} - possible short or tracking gap")
                # Could be a short position or we missed the BUY
                # For now, just log and skip PnL calculation
                return state
            
            pos = positions[symbol]
            entry_price = pos["entry_price"]
            current_qty = pos["quantity"]
            
            if filled_size > current_qty + 0.0001:  # Allow tiny rounding
                logger.warning(f"SELL size {filled_size} > position size {current_qty} for {symbol}")
                # Sell what we have
                filled_size = current_qty
            
            # Calculate realized PnL for the sold portion
            # PnL = (exit_price - entry_price) * quantity_sold - fees
            price_diff = fill_price - entry_price
            realized_pnl = (price_diff * filled_size) - fees
            
            # Proportion of position sold
            proportion_sold = filled_size / current_qty if current_qty > 0 else 1.0
            proportion_fees = pos.get("fees_paid", 0.0) * proportion_sold
            
            # Total PnL includes proportional entry fees
            total_pnl = realized_pnl - proportion_fees
            
            logger.info(f"Closed {filled_size}/{current_qty} of {symbol}: "
                       f"entry=${entry_price:.2f}, exit=${fill_price:.2f}, "
                       f"PnL=${total_pnl:.2f}")
            
            # Update PnL accumulators
            state["pnl_today"] = state.get("pnl_today", 0.0) + total_pnl
            state["pnl_week"] = state.get("pnl_week", 0.0) + total_pnl
            
            # Update win/loss streaks
            if total_pnl > 0:
                state["consecutive_losses"] = 0
                state["last_win_time"] = timestamp.isoformat()
            elif total_pnl < 0:
                state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
                state["last_loss_time"] = timestamp.isoformat()
            
            # Update or remove position
            remaining_qty = current_qty - filled_size
            if remaining_qty < 0.0001:  # Essentially zero
                # Position fully closed
                del positions[symbol]
                logger.info(f"Fully closed {symbol} position")
            else:
                # Partial close - reduce quantity and fees proportionally
                pos["quantity"] = remaining_qty
                pos["entry_value_usd"] = remaining_qty * entry_price
                pos["fees_paid"] = pos.get("fees_paid", 0.0) * (1 - proportion_sold)
                pos["last_updated"] = timestamp.isoformat()
                logger.debug(f"Reduced {symbol} position to {remaining_qty}")
        
        # Add event
        state.setdefault("events", []).append({
            "at": timestamp.isoformat(),
            "event": "fill",
            "symbol": symbol,
            "side": side_upper,
            "quantity": filled_size,
            "price": fill_price,
            "fees": fees,
            "pnl": total_pnl if side_upper == "SELL" and symbol in positions or side_upper == "SELL" else None
        })
        
        # Trim events
        if len(state["events"]) > 100:
            state["events"] = state["events"][-100:]
        
        self.save(state)
        return state
    
    def increment_zero_proposal_cycles(self) -> int:
        """
        Increment zero-proposal cycle counter.
        
        Returns:
            Current count after increment
        """
        state = self.load()
        state["zero_proposal_cycles"] = state.get("zero_proposal_cycles", 0) + 1
        count = state["zero_proposal_cycles"]
        self.save(state)
        logger.debug(f"Zero-proposal cycles: {count}")
        return count
    
    def reset_zero_proposal_cycles(self) -> None:
        """Reset zero-proposal cycle counter (proposals generated)"""
        state = self.load()
        state["zero_proposal_cycles"] = 0
        state["auto_loosen_applied"] = False  # Reset flag when proposals resume
        self.save(state)
        logger.debug("Reset zero-proposal cycle counter")
    
    def mark_auto_loosen_applied(self) -> None:
        """Mark that auto-loosening has been applied (prevent repeated adjustments)"""
        state = self.load()
        state["auto_loosen_applied"] = True
        self.save(state)
        logger.info("Marked auto-loosen as applied")
    
    def has_auto_loosen_applied(self) -> bool:
        """Check if auto-loosening has already been applied"""
        state = self.load()
        return state.get("auto_loosen_applied", False)
    
    def reset(self, full: bool = False) -> Dict[str, Any]:
        """
        Reset state counters.
        
        Args:
            full: If True, reset everything including positions and cooldowns
            
        Returns:
            Reset state
        """
        if full:
            logger.warning("Full state reset")
            state = dict(DEFAULT_STATE)
        else:
            state = self.load()
            logger.info("Resetting counters only")
            state["trades_today"] = 0
            state["trades_this_hour"] = 0
            state["pnl_today"] = 0.0
            state["consecutive_losses"] = 0
        
        self.save(state)
        return state


# Singleton instance
_store = None


def get_state_store(state_file: Optional[str] = None) -> StateStore:
    """Get singleton state store instance"""
    global _store
    if _store is None:
        _store = StateStore(state_file=state_file)
    return _store
