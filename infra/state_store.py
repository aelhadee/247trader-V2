"""
247trader-v2 Infrastructure: State Store

Persistent state management with atomic writes.
Ported from v1 with enhancements for v2 architecture.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
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
    "last_reset_date": None,
    "last_reset_hour": None,
    "events": [],  # Recent events log
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
