"""
247trader-v2 Core: Order State Machine

Explicit order lifecycle management with state transitions and telemetry.

States: NEW → OPEN → PARTIAL_FILL → (FILLED | CANCELED | EXPIRED | REJECTED | FAILED)

Provides:
- State transition validation
- Lifecycle timestamps
- Status checking
- Telemetry hooks
"""

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    """Order lifecycle states"""
    NEW = "new"                      # Order created, not yet submitted
    OPEN = "open"                    # Order submitted to exchange
    PARTIAL_FILL = "partial_fill"    # Partially filled
    FILLED = "filled"                # Completely filled
    CANCELED = "canceled"            # Canceled by user/system
    EXPIRED = "expired"              # Expired (time-in-force limit)
    REJECTED = "rejected"            # Rejected by exchange
    FAILED = "failed"                # Failed to submit


class OrderSide(Enum):
    """Order side"""
    BUY = "buy"
    SELL = "sell"


@dataclass
class OrderState:
    """
    Order state with lifecycle tracking.
    
    Tracks complete order lifecycle from creation through terminal state,
    including timestamps for each transition and execution metrics.
    """
    # Identifiers
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    
    # Order details
    symbol: str = ""
    side: str = "buy"  # "buy" or "sell"
    size_usd: float = 0.0
    size_base: float = 0.0
    
    # State
    status: str = OrderStatus.NEW.value
    
    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    submitted_at: Optional[datetime] = None
    first_fill_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Execution details
    filled_size: float = 0.0
    filled_value: float = 0.0
    average_price: float = 0.0
    fees: float = 0.0
    fills: List[Dict[str, Any]] = field(default_factory=list)
    
    # Metadata
    route: str = "unknown"
    error: Optional[str] = None
    rejection_reason: Optional[str] = None
    
    def __post_init__(self):
        """Validate initial state"""
        if not self.symbol:
            raise ValueError("Order symbol is required")
        if self.size_usd <= 0 and self.size_base <= 0:
            raise ValueError("Order size must be positive")
    
    def age_seconds(self) -> float:
        """Return order age in seconds"""
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()
    
    def is_terminal(self) -> bool:
        """Check if order is in terminal state"""
        terminal_states = {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELED.value,
            OrderStatus.EXPIRED.value,
            OrderStatus.REJECTED.value,
            OrderStatus.FAILED.value
        }
        return self.status in terminal_states
    
    def is_active(self) -> bool:
        """Check if order is actively working"""
        active_states = {
            OrderStatus.NEW.value,
            OrderStatus.OPEN.value,
            OrderStatus.PARTIAL_FILL.value
        }
        return self.status in active_states
    
    def fill_percentage(self) -> float:
        """Return fill percentage (0-100)"""
        if self.size_base > 0:
            return (self.filled_size / self.size_base) * 100.0
        elif self.size_usd > 0 and self.filled_value > 0:
            return (self.filled_value / self.size_usd) * 100.0
        return 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "size_usd": self.size_usd,
            "size_base": self.size_base,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "first_fill_at": self.first_fill_at.isoformat() if self.first_fill_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "filled_size": self.filled_size,
            "filled_value": self.filled_value,
            "average_price": self.average_price,
            "fees": self.fees,
            "fills_count": len(self.fills),
            "route": self.route,
            "error": self.error,
            "rejection_reason": self.rejection_reason,
            "age_seconds": self.age_seconds(),
            "fill_percentage": self.fill_percentage()
        }


class OrderStateMachine:
    """
    Order state machine with transition validation and telemetry.
    
    Manages order lifecycle transitions and ensures only valid state changes occur.
    Tracks all orders and provides querying/filtering capabilities.
    """
    
    # Valid state transitions
    VALID_TRANSITIONS = {
        OrderStatus.NEW: {OrderStatus.OPEN, OrderStatus.FAILED, OrderStatus.REJECTED},
        OrderStatus.OPEN: {OrderStatus.PARTIAL_FILL, OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED},
        OrderStatus.PARTIAL_FILL: {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED},
        # Terminal states have no outbound transitions
        OrderStatus.FILLED: set(),
        OrderStatus.CANCELED: set(),
        OrderStatus.EXPIRED: set(),
        OrderStatus.REJECTED: set(),
        OrderStatus.FAILED: set(),
    }
    
    def __init__(self):
        """Initialize order state machine"""
        self.orders: Dict[str, OrderState] = {}
        logger.info("OrderStateMachine initialized")
    
    def create_order(
        self,
        client_order_id: str,
        symbol: str,
        side: str,
        size_usd: float,
        size_base: float = 0.0,
        route: str = "unknown"
    ) -> OrderState:
        """
        Create new order in NEW state.
        
        Args:
            client_order_id: Client order ID (deterministic)
            symbol: Trading pair (e.g., "BTC-USD")
            side: "buy" or "sell"
            size_usd: USD size
            size_base: Base asset size (optional)
            route: Execution route
            
        Returns:
            OrderState object
        """
        if client_order_id in self.orders:
            logger.warning(f"Order {client_order_id} already exists")
            return self.orders[client_order_id]
        
        order = OrderState(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side.lower(),
            size_usd=size_usd,
            size_base=size_base,
            route=route,
            status=OrderStatus.NEW.value
        )
        
        self.orders[client_order_id] = order
        logger.info(f"Created order {client_order_id}: {symbol} {side} ${size_usd:.2f}")
        return order
    
    def transition(
        self,
        client_order_id: str,
        new_status: OrderStatus,
        order_id: Optional[str] = None,
        error: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        allow_override: bool = False,
    ) -> bool:
        """
        Transition order to new state.
        
        Args:
            client_order_id: Client order ID
            new_status: Target status
            order_id: Exchange order ID (if available)
            error: Error message (for FAILED state)
            rejection_reason: Rejection reason (for REJECTED state)
            
        Returns:
            True if transition succeeded, False otherwise
        """
        if client_order_id not in self.orders:
            logger.error(f"Order {client_order_id} not found")
            return False
        
        order = self.orders[client_order_id]
        current_status = OrderStatus(order.status)
        
        # Check if transition is valid or requires override (late fill reconciliation, etc.)
        valid_next_states = self.VALID_TRANSITIONS.get(current_status, set())
        override_allowed = allow_override or self._should_allow_override(current_status, new_status)

        if current_status == OrderStatus.CANCELED and new_status == OrderStatus.FILLED and not override_allowed:
            logger.warning(
                "Late fill detected after cancel for %s; forcing idempotent upgrade to FILLED",
                client_order_id,
            )
            override_allowed = True

        if new_status not in valid_next_states:
            if not override_allowed:
                logger.warning(
                    f"Invalid transition for {client_order_id}: "
                    f"{current_status.value} → {new_status.value}"
                )
                return False
            logger.info(
                "Override transition for %s: %s → %s",
                client_order_id,
                current_status.value,
                new_status.value,
            )
        
        # Perform transition
        old_status = order.status
        order.status = new_status.value
        now = datetime.now(timezone.utc)
        
        # Update timestamps
        if new_status == OrderStatus.OPEN:
            order.submitted_at = now
            if order_id:
                order.order_id = order_id
        
        elif new_status == OrderStatus.PARTIAL_FILL:
            if not order.first_fill_at:
                order.first_fill_at = now
        
        elif new_status in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED, OrderStatus.FAILED}:
            order.completed_at = now
            if error:
                order.error = error
            if rejection_reason:
                order.rejection_reason = rejection_reason
        
        logger.info(
            f"Order {client_order_id} transitioned: "
            f"{old_status} → {new_status.value}"
        )
        return True

    @staticmethod
    def _should_allow_override(current: OrderStatus, new: OrderStatus) -> bool:
        """Allow late fill upgrades from canceled/expired paths."""

        if new == OrderStatus.FILLED and current in {OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.FAILED}:
            return True

        return False
    
    def update_fill(
        self,
        client_order_id: str,
        filled_size: float,
        filled_value: float,
        fees: float,
        fills: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """
        Update order fill details.
        
        Args:
            client_order_id: Client order ID
            filled_size: Filled size in base asset
            filled_value: Filled value in USD
            fees: Total fees
            fills: List of fill events
            
        Returns:
            True if update succeeded
        """
        if client_order_id not in self.orders:
            logger.error(f"Order {client_order_id} not found")
            return False
        
        order = self.orders[client_order_id]
        order.filled_size = filled_size
        order.filled_value = filled_value
        order.fees = fees
        
        if filled_size > 0:
            order.average_price = filled_value / filled_size
        
        if fills:
            order.fills = fills
        
        # Auto-transition based on fill status (allow late fill upgrades)
        if filled_size > 0:
            target_status = None

            if order.size_base > 0:
                try:
                    fill_pct = (filled_size / order.size_base) * 100.0
                except ZeroDivisionError:
                    fill_pct = 0.0
            elif order.size_usd > 0:
                try:
                    fill_pct = (filled_value / order.size_usd) * 100.0
                except ZeroDivisionError:
                    fill_pct = 0.0
            else:
                fill_pct = 0.0

            if fill_pct >= 99.9:
                target_status = OrderStatus.FILLED
            elif fill_pct > 0:
                target_status = OrderStatus.PARTIAL_FILL

            if target_status is not None:
                current_status = OrderStatus(order.status)

                if not order.first_fill_at:
                    order.first_fill_at = datetime.now(timezone.utc)

                if current_status != target_status:
                    self.transition(
                        client_order_id,
                        target_status,
                        allow_override=True,
                    )
        
        return True
    
    def get_order(self, client_order_id: str) -> Optional[OrderState]:
        """Get order by client_order_id"""
        return self.orders.get(client_order_id)
    
    def get_active_orders(self) -> List[OrderState]:
        """Get all active (non-terminal) orders"""
        return [order for order in self.orders.values() if order.is_active()]
    
    def get_terminal_orders(self) -> List[OrderState]:
        """Get all terminal orders"""
        return [order for order in self.orders.values() if order.is_terminal()]
    
    def get_orders_by_status(self, status: OrderStatus) -> List[OrderState]:
        """Get orders in specific status"""
        return [order for order in self.orders.values() if order.status == status.value]
    
    def get_stale_orders(self, max_age_seconds: float) -> List[OrderState]:
        """Get active orders older than max_age_seconds"""
        return [
            order for order in self.get_active_orders()
            if order.age_seconds() > max_age_seconds
        ]
    
    def cleanup_old_orders(self, keep_last_n: int = 100):
        """
        Remove old terminal orders, keeping only last N.
        
        Args:
            keep_last_n: Number of terminal orders to keep
        """
        terminal = sorted(
            self.get_terminal_orders(),
            key=lambda o: o.completed_at or o.created_at,
            reverse=True
        )
        
        if len(terminal) > keep_last_n:
            to_remove = terminal[keep_last_n:]
            for order in to_remove:
                if order.client_order_id:
                    del self.orders[order.client_order_id]
            logger.info(f"Cleaned up {len(to_remove)} old terminal orders")
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics"""
        orders_list = list(self.orders.values())
        active = self.get_active_orders()
        terminal = self.get_terminal_orders()
        
        status_counts = {}
        for status in OrderStatus:
            status_counts[status.value] = len(self.get_orders_by_status(status))
        
        return {
            "total_orders": len(orders_list),
            "active_orders": len(active),
            "terminal_orders": len(terminal),
            "status_breakdown": status_counts,
            "oldest_active_age": max([o.age_seconds() for o in active], default=0),
        }


# Singleton instance
_state_machine: Optional[OrderStateMachine] = None


def get_order_state_machine() -> OrderStateMachine:
    """Get singleton order state machine"""
    global _state_machine
    if _state_machine is None:
        _state_machine = OrderStateMachine()
    return _state_machine
