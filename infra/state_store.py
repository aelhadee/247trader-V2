"""
247trader-v2 Infrastructure: State Store

Persistent state management with atomic writes.
Ported from v1 with enhancements for v2 architecture.
"""

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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
    "managed_positions": {},  # asset -> {entry_price, entry_time, stop_loss_pct, take_profit_pct, max_hold_hours}
    "cash_balances": {},  # quote currency -> available
    "open_orders": {},  # client_order_id/order_id -> metadata
    "recent_orders": [],  # bounded history of closed/canceled orders
    "pending_markers": {},  # lightweight pending flags with TTL
    "last_fill_times": {},  # symbol:side -> ISO timestamp of last fill
    "fill_history": {},  # symbol:side -> bounded list of fill timestamps
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
    
    PENDING_TTL_SECONDS = 120
    MAX_PENDING_HISTORY = 200
    MAX_FILL_HISTORY = 100

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

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        if not symbol:
            return symbol
        return symbol if "-" in symbol else f"{symbol}-USD"

    @staticmethod
    def _fill_key(symbol: str, side: str) -> str:
        normalized = StateStore._normalize_symbol(symbol)
        return f"{normalized}:{(side or '').upper()}"
    
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

        managed = state.setdefault("managed_positions", {})
        for symbol in list(managed.keys()):
            if symbol not in positions:
                managed.pop(symbol, None)

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

        self._purge_expired_pending(state)

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

    def purge_expired_pending(self) -> None:
        state = self.load()
        removed = self._purge_expired_pending(state)
        if removed:
            self.save(state)

    def _pending_bucket(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return state.setdefault("pending_markers", {})

    def _purge_expired_pending(self, state: Dict[str, Any]) -> List[str]:
        bucket = self._pending_bucket(state)
        if not bucket:
            return []

        now = datetime.now(timezone.utc)
        removed: List[str] = []
        for key, record in list(bucket.items()):
            expires_at = record.get("expires_at")
            if not expires_at:
                continue
            try:
                expiry = datetime.fromisoformat(expires_at)
            except Exception:
                expiry = now - timedelta(seconds=1)
            if expiry <= now:
                bucket.pop(key, None)
                removed.append(key)

        return removed

    def set_pending(
        self,
        product_id: str,
        side: str,
        *,
        client_order_id: Optional[str] = None,
        order_id: Optional[str] = None,
        notional_usd: Optional[float] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        if not product_id or not side:
            return

        state = self.load()
        bucket = self._pending_bucket(state)
        self._purge_expired_pending(state)

        normalized = self._normalize_symbol(product_id)
        side_upper = side.upper()
        ttl = int(ttl_seconds if ttl_seconds else self.PENDING_TTL_SECONDS)
        ttl = max(ttl, 1)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl)

        key = f"{normalized}:{side_upper}:{client_order_id or order_id or uuid.uuid4().hex}"
        bucket[key] = {
            "product_id": normalized,
            "base": normalized.split("-", 1)[0],
            "side": side_upper,
            "client_order_id": client_order_id,
            "order_id": order_id,
            "notional_usd": float(notional_usd) if notional_usd else None,
            "since": now.isoformat(),
            "expires_at": expires.isoformat(),
        }

        if len(bucket) > self.MAX_PENDING_HISTORY:
            # Drop oldest entries to keep bounded size
            oldest = sorted(
                bucket.items(),
                key=lambda item: item[1].get("since", ""),
            )[:-self.MAX_PENDING_HISTORY]
            for pending_key, _ in oldest:
                bucket.pop(pending_key, None)

        self.save(state)

    def clear_pending(
        self,
        product_id: str,
        side: str,
        *,
        client_order_id: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> None:
        state = self.load()
        bucket = self._pending_bucket(state)
        if not bucket:
            return

        self._purge_expired_pending(state)

        normalized = self._normalize_symbol(product_id)
        base = normalized.split("-", 1)[0]
        side_upper = side.upper()

        removed = False
        for key, record in list(bucket.items()):
            if record.get("side") != side_upper:
                continue
            if record.get("product_id") == normalized or record.get("base") == base:
                if client_order_id and record.get("client_order_id") and record.get("client_order_id") != client_order_id:
                    continue
                if order_id and record.get("order_id") and record.get("order_id") != order_id:
                    continue
                bucket.pop(key, None)
                removed = True

        if removed:
            self.save(state)

    def has_pending(self, product_id: str, side: str) -> bool:
        state = self.load()
        bucket = self._pending_bucket(state)
        if not bucket:
            return False

        removed = self._purge_expired_pending(state)
        if removed:
            self.save(state)

        normalized = self._normalize_symbol(product_id)
        base = normalized.split("-", 1)[0]
        side_upper = side.upper()

        for record in bucket.values():
            if record.get("side") != side_upper:
                continue
            if record.get("product_id") == normalized or record.get("base") == base:
                return True
        return False

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
        
        Tracks:
        - Trade counters (today/hour)
        - Global last_trade_timestamp (for pacing)
        - Per-symbol last_trade_timestamp (for per-symbol pacing)
        
        Args:
            filled_orders: List of successfully filled orders
            portfolio: Current portfolio state
            
        Returns:
            Updated state
        """
        state = self.load()
        now = datetime.now(timezone.utc)
        
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
                
                # Update global last trade timestamp (for global pacing)
                state["last_trade_timestamp"] = now.isoformat()
                
                # Update per-symbol last trade timestamp (for per-symbol pacing)
                state.setdefault("per_symbol_last_trade", {})[symbol] = now.isoformat()
                
                # Log event
                state.setdefault("events", []).append({
                    "at": now.isoformat(),
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
        timestamp: datetime,
        notional_usd: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record a fill and update position/PnL state."""

        state = self.load()
        positions = state.setdefault("positions", {})
        managed_positions = state.setdefault("managed_positions", {})

        try:
            size_dec = Decimal(str(filled_size))
        except (InvalidOperation, TypeError, ValueError):
            size_dec = Decimal("0")

        try:
            price_dec = Decimal(str(fill_price))
        except (InvalidOperation, TypeError, ValueError):
            price_dec = Decimal("0")

        try:
            fees_dec = Decimal(str(fees))
        except (InvalidOperation, TypeError, ValueError):
            fees_dec = Decimal("0")

        if notional_usd is not None:
            try:
                notional_dec = Decimal(str(notional_usd))
            except (InvalidOperation, TypeError, ValueError):
                notional_dec = Decimal("0")
        else:
            notional_dec = size_dec * price_dec

        if size_dec <= 0 or price_dec <= 0 or notional_dec <= 0:
            logger.debug(
                "record_fill skipped for %s: size=%s price=%s notional=%s",
                symbol,
                size_dec,
                price_dec,
                notional_dec,
            )
            return state

        side_upper = (side or "").upper()
        total_pnl_dec = Decimal("0")

        size_float = float(size_dec)
        price_float = float(price_dec)
        notional_float = float(notional_dec)
        fees_float = float(fees_dec)

        if side_upper == "BUY":
            # Track managed position with metadata for exits
            if symbol not in managed_positions:
                managed_positions[symbol] = {
                    "entry_price": price_float,
                    "entry_time": timestamp.isoformat(),
                    "stop_loss_pct": None,  # Will be set from TradeProposal
                    "take_profit_pct": None,  # Will be set from TradeProposal
                    "max_hold_hours": None,  # Will be set from TradeProposal
                }

            if symbol in positions:
                pos = positions[symbol]

                old_qty_dec = Decimal(str(pos.get("quantity", 0.0) or 0.0))
                old_price_dec = Decimal(str(pos.get("entry_price", price_float) or price_float))
                old_fees_dec = Decimal(str(pos.get("fees_paid", 0.0) or 0.0))

                total_value_dec = (old_qty_dec * old_price_dec) + notional_dec
                new_qty_dec = old_qty_dec + size_dec
                new_entry_price_dec = total_value_dec / new_qty_dec if new_qty_dec > 0 else price_dec
                mark_value_dec = new_qty_dec * price_dec if price_dec > 0 else total_value_dec

                pos["quantity"] = float(new_qty_dec)
                pos["units"] = float(new_qty_dec)
                pos["base_qty"] = float(new_qty_dec)
                pos["entry_price"] = float(new_entry_price_dec)
                pos["entry_value_usd"] = float(total_value_dec)
                pos["usd_value"] = float(mark_value_dec)
                pos["usd"] = float(mark_value_dec)
                pos["fees_paid"] = float(old_fees_dec + fees_dec)
                pos["last_updated"] = timestamp.isoformat()
                pos["last_fill_price"] = price_float

                logger.debug(
                    "Added to %s position: %.8f @ $%.8f, new avg entry: $%.8f, total qty: %.8f",
                    symbol,
                    size_float,
                    price_float,
                    float(new_entry_price_dec),
                    float(new_qty_dec),
                )
            else:
                positions[symbol] = {
                    "side": "BUY",
                    "quantity": size_float,
                    "units": size_float,
                    "base_qty": size_float,
                    "entry_price": price_float,
                    "entry_value_usd": notional_float,
                    "usd_value": notional_float,
                    "usd": notional_float,
                    "fees_paid": fees_float,
                    "entry_time": timestamp.isoformat(),
                    "last_updated": timestamp.isoformat(),
                    "last_fill_price": price_float,
                }

                logger.info("Opened %s position: %.8f @ $%.8f", symbol, size_float, price_float)

        elif side_upper == "SELL":
            pos = positions.get(symbol)
            if not pos:
                logger.warning("SELL without open position for %s - possible gap", symbol)
                return state

            entry_price = float(pos.get("entry_price", price_float))
            current_qty = float(pos.get("quantity", 0.0) or 0.0)

            if current_qty <= 0:
                logger.warning("SELL for %s but tracked quantity is zero", symbol)
                return state

            if size_float > current_qty + 0.0001:
                logger.warning("SELL size %.8f > position size %.8f for %s", size_float, current_qty, symbol)
                size_float = current_qty
                size_dec = Decimal(str(size_float))

            entry_price_dec = Decimal(str(entry_price))
            current_qty_dec = Decimal(str(current_qty))

            price_diff_dec = price_dec - entry_price_dec
            realized_pnl_dec = (price_diff_dec * size_dec) - fees_dec

            proportion_sold = float(size_dec / current_qty_dec) if current_qty_dec > 0 else 1.0
            total_entry_fees = float(pos.get("fees_paid", 0.0) or 0.0)
            proportion_fees = total_entry_fees * proportion_sold

            total_pnl_dec = realized_pnl_dec - Decimal(str(proportion_fees))

            logger.info(
                "Closed %.8f/%.8f of %s: entry=$%.2f exit=$%.2f PnL=$%.2f",
                size_float,
                current_qty,
                symbol,
                entry_price,
                price_float,
                float(total_pnl_dec),
            )

            state["pnl_today"] = state.get("pnl_today", 0.0) + float(total_pnl_dec)
            state["pnl_week"] = state.get("pnl_week", 0.0) + float(total_pnl_dec)

            if total_pnl_dec > 0:
                state["consecutive_losses"] = 0
                state["last_win_time"] = timestamp.isoformat()
            elif total_pnl_dec < 0:
                state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
                state["last_loss_time"] = timestamp.isoformat()

            remaining_qty = current_qty - size_float
            if remaining_qty <= 0.0001:
                positions.pop(symbol, None)
                managed_positions.pop(symbol, None)
                logger.info("Fully closed %s position", symbol)
            else:
                mark_value_usd = remaining_qty * price_float if price_float > 0 else entry_value_usd
                pos["quantity"] = remaining_qty
                pos["units"] = remaining_qty
                pos["base_qty"] = remaining_qty
                entry_value_usd = remaining_qty * entry_price
                pos["entry_value_usd"] = entry_value_usd
                pos["usd_value"] = mark_value_usd
                pos["usd"] = mark_value_usd
                pos["fees_paid"] = max(total_entry_fees * (1 - proportion_sold), 0.0)
                pos["last_updated"] = timestamp.isoformat()
                pos["last_fill_price"] = price_float
                logger.debug("Reduced %s position to %.8f units", symbol, remaining_qty)

        else:
            logger.warning("Unknown fill side %s for %s", side, symbol)
            return state

        state.setdefault("events", []).append(
            {
                "at": timestamp.isoformat(),
                "event": "fill",
                "symbol": symbol,
                "side": side_upper,
                "quantity": size_float,
                "price": price_float,
                "notional_usd": notional_float,
                "fees": fees_float,
                "pnl": float(total_pnl_dec) if side_upper == "SELL" else None,
                "mark_price": price_float,
            }
        )

        fill_key = self._fill_key(symbol, side_upper)
        state.setdefault("last_fill_times", {})[fill_key] = timestamp.isoformat()

        history_bucket = state.setdefault("fill_history", {}).setdefault(fill_key, [])
        history_bucket.append(timestamp.isoformat())
        if len(history_bucket) > self.MAX_FILL_HISTORY:
            history_bucket[:] = history_bucket[-self.MAX_FILL_HISTORY :]

        if len(state["events"]) > 100:
            state["events"] = state["events"][-100:]

        self.save(state)
        return state

    def get_last_fill_time(self, product_id: str, side: str) -> Optional[datetime]:
        state = self.load()
        key = self._fill_key(product_id, side)
        last = state.get("last_fill_times", {}).get(key)
        if not last:
            return None
        try:
            ts = datetime.fromisoformat(last)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        except Exception:
            return None

    def get_fill_count_since(self, product_id: str, side: str, since: datetime) -> int:
        state = self.load()
        key = self._fill_key(product_id, side)
        history = state.get("fill_history", {}).get(key) or []
        if not history:
            return 0

        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        count = 0
        for iso_ts in history:
            try:
                ts = datetime.fromisoformat(iso_ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts >= since:
                count += 1
        return count

    def mark_position_managed(self, symbol: str) -> None:
        """Explicitly mark a position as managed by the bot."""

        normalized = symbol if "-" in symbol else f"{symbol}-USD"
        state = self.load()
        state.setdefault("managed_positions", {})[normalized] = True
        self.save(state)

    def get_managed_positions(self) -> Dict[str, bool]:
        """Return a copy of managed position flags."""

        state = self.load()
        managed = state.get("managed_positions", {})
        if not isinstance(managed, dict):
            return {}
        return dict(managed)
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a value from state by key.
        
        Args:
            key: State key
            default: Default value if key not found
            
        Returns:
            Value from state or default
        """
        state = self.load()
        return state.get(key, default)
    
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
    
    def update_managed_position_targets(
        self,
        symbol: str,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
        max_hold_hours: Optional[float] = None,
    ) -> None:
        """
        Update stop-loss, take-profit, and max hold time for a managed position.
        
        Called after order execution to set exit targets from TradeProposal.
        
        Args:
            symbol: Asset symbol (e.g., "BTC")
            stop_loss_pct: Stop-loss percentage
            take_profit_pct: Take-profit percentage
            max_hold_hours: Maximum hold time in hours
        """
        state = self.load()
        managed = state.setdefault("managed_positions", {})
        
        if symbol not in managed:
            logger.debug(f"Cannot update targets for non-managed position: {symbol}")
            return
        
        # Update targets (preserve existing if new value is None)
        if isinstance(managed[symbol], dict):
            if stop_loss_pct is not None:
                managed[symbol]["stop_loss_pct"] = stop_loss_pct
            if take_profit_pct is not None:
                managed[symbol]["take_profit_pct"] = take_profit_pct
            if max_hold_hours is not None:
                managed[symbol]["max_hold_hours"] = max_hold_hours
        else:
            # Old format (boolean) - upgrade to dict
            logger.debug(f"Upgrading managed_position {symbol} from boolean to dict format")
            managed[symbol] = {
                "entry_price": None,  # Will be set on next fill
                "entry_time": None,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "max_hold_hours": max_hold_hours,
            }
        
        self.save(state)
        logger.debug(
            f"Updated {symbol} targets: SL={stop_loss_pct}%, TP={take_profit_pct}%, "
            f"max_hold={max_hold_hours}h"
        )


# Singleton instance
_store = None


def get_state_store(state_file: Optional[str] = None) -> StateStore:
    """Get singleton state store instance"""
    global _store
    if _store is None:
        _store = StateStore(state_file=state_file)
    return _store
