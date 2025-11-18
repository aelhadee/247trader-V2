"""
Tests for order state machine.

Ensures proper lifecycle transitions, fill tracking, and querying.
"""

import pytest
from datetime import datetime, timezone, timedelta
from core.order_state import (
    OrderStateMachine,
    OrderStatus,
    OrderState,
    get_order_state_machine
)


class TestOrderState:
    """Test OrderState dataclass"""
    
    def test_order_state_creation(self):
        """Test basic order state creation"""
        order = OrderState(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            size_base=0.01
        )
        
        assert order.client_order_id == "test_123"
        assert order.symbol == "BTC-USD"
        assert order.side == "buy"
        assert order.status == OrderStatus.NEW.value
        assert order.created_at is not None
    
    def test_order_state_requires_symbol(self):
        """Test that symbol is required"""
        with pytest.raises(ValueError, match="symbol is required"):
            OrderState(symbol="", side="buy", size_usd=100.0)
    
    def test_order_state_requires_size(self):
        """Test that size must be positive"""
        with pytest.raises(ValueError, match="size must be positive"):
            OrderState(symbol="BTC-USD", side="buy", size_usd=0.0)
    
    def test_is_terminal(self):
        """Test terminal state detection"""
        order = OrderState(
            symbol="BTC-USD",
            side="buy",
            size_usd=100.0,
            status=OrderStatus.FILLED.value
        )
        assert order.is_terminal() is True
        
        order.status = OrderStatus.OPEN.value
        assert order.is_terminal() is False
    
    def test_is_active(self):
        """Test active state detection"""
        order = OrderState(
            symbol="BTC-USD",
            side="buy",
            size_usd=100.0,
            status=OrderStatus.OPEN.value
        )
        assert order.is_active() is True
        
        order.status = OrderStatus.FILLED.value
        assert order.is_active() is False
    
    def test_fill_percentage(self):
        """Test fill percentage calculation"""
        order = OrderState(
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            size_base=0.01
        )
        
        assert order.fill_percentage() == 0.0
        
        order.filled_size = 0.005
        assert abs(order.fill_percentage() - 50.0) < 0.01
        
        order.filled_size = 0.01
        assert abs(order.fill_percentage() - 100.0) < 0.01


class TestOrderStateMachine:
    """Test OrderStateMachine class"""
    
    def setup_method(self):
        """Create fresh state machine for each test"""
        self.machine = OrderStateMachine()
    
    def test_create_order(self):
        """Test order creation"""
        order = self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            size_base=0.01,
            route="live"
        )
        
        assert order.client_order_id == "test_123"
        assert order.status == OrderStatus.NEW.value
        assert self.machine.get_order("test_123") is not None
    
    def test_create_duplicate_order(self):
        """Test that creating duplicate order returns existing"""
        order1 = self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        
        order2 = self.machine.create_order(
            client_order_id="test_123",
            symbol="ETH-USD",
            side="sell",
            size_usd=500.0
        )
        
        assert order1 is order2
        assert order2.symbol == "BTC-USD"  # Original values preserved
    
    def test_valid_transition_new_to_open(self):
        """Test NEW → OPEN transition"""
        self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        
        success = self.machine.transition(
            "test_123",
            OrderStatus.OPEN,
            order_id="exchange_456"
        )
        
        assert success is True
        order = self.machine.get_order("test_123")
        assert order.status == OrderStatus.OPEN.value
        assert order.order_id == "exchange_456"
        assert order.submitted_at is not None
    
    def test_valid_transition_open_to_partial(self):
        """Test OPEN → PARTIAL_FILL transition"""
        self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.machine.transition("test_123", OrderStatus.OPEN)
        
        success = self.machine.transition("test_123", OrderStatus.PARTIAL_FILL)
        
        assert success is True
        order = self.machine.get_order("test_123")
        assert order.status == OrderStatus.PARTIAL_FILL.value
    
    def test_valid_transition_partial_to_filled(self):
        """Test PARTIAL_FILL → FILLED transition"""
        self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.machine.transition("test_123", OrderStatus.OPEN)
        self.machine.transition("test_123", OrderStatus.PARTIAL_FILL)
        
        success = self.machine.transition("test_123", OrderStatus.FILLED)
        
        assert success is True
        order = self.machine.get_order("test_123")
        assert order.status == OrderStatus.FILLED.value
        assert order.completed_at is not None
    
    def test_invalid_transition(self):
        """Test that invalid transitions are rejected"""
        self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        
        # Can't go from NEW directly to FILLED
        success = self.machine.transition("test_123", OrderStatus.FILLED)
        
        assert success is False
        order = self.machine.get_order("test_123")
        assert order.status == OrderStatus.NEW.value  # Unchanged
    
    def test_terminal_state_no_transitions(self):
        """Test that terminal states cannot transition"""
        self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.machine.transition("test_123", OrderStatus.OPEN)
        self.machine.transition("test_123", OrderStatus.FILLED)
        
        # Try to transition from FILLED (terminal)
        success = self.machine.transition("test_123", OrderStatus.CANCELED)
        
        assert success is False
        order = self.machine.get_order("test_123")
        assert order.status == OrderStatus.FILLED.value
    
    def test_late_fill_allows_canceled_to_filled(self):
        """Late fills from the exchange should upgrade canceled orders to filled."""
        self.machine.create_order(
            client_order_id="late_fill",
            symbol="ETH-USD",
            side="buy",
            size_usd=500.0,
        )
        self.machine.transition("late_fill", OrderStatus.OPEN)
        self.machine.transition("late_fill", OrderStatus.CANCELED)

        success = self.machine.transition("late_fill", OrderStatus.FILLED)

        assert success is True
        order = self.machine.get_order("late_fill")
        assert order.status == OrderStatus.FILLED.value
        assert order.completed_at is not None

    def test_update_fill_promotes_canceled_to_filled(self):
        """update_fill should tolerate late fills even after local cancellation."""
        self.machine.create_order(
            client_order_id="late_fill_update",
            symbol="SOL-USD",
            side="buy",
            size_usd=200.0,
            size_base=5.0,
        )
        self.machine.transition("late_fill_update", OrderStatus.OPEN)
        self.machine.transition("late_fill_update", OrderStatus.CANCELED)

        self.machine.update_fill(
            client_order_id="late_fill_update",
            filled_size=5.0,
            filled_value=200.0,
            fees=1.0,
        )

        order = self.machine.get_order("late_fill_update")
        assert order.status == OrderStatus.FILLED.value
        assert order.filled_size == 5.0

    def test_transition_nonexistent_order(self):
        """Test transitioning nonexistent order returns False"""
        success = self.machine.transition("nonexistent", OrderStatus.OPEN)
        assert success is False
    
    def test_update_fill(self):
        """Test fill details update"""
        self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            size_base=0.01
        )
        self.machine.transition("test_123", OrderStatus.OPEN)
        
        success = self.machine.update_fill(
            client_order_id="test_123",
            filled_size=0.01,
            filled_value=1000.0,
            fees=4.0
        )
        
        assert success is True
        order = self.machine.get_order("test_123")
        assert order.filled_size == 0.01
        assert order.filled_value == 1000.0
        assert order.fees == 4.0
        assert abs(order.average_price - 100000.0) < 0.01
    
    def test_update_fill_auto_transition_to_filled(self):
        """Test that update_fill auto-transitions to FILLED when 100% filled"""
        self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            size_base=0.01
        )
        self.machine.transition("test_123", OrderStatus.OPEN)
        
        # Fill 100%
        self.machine.update_fill(
            client_order_id="test_123",
            filled_size=0.01,
            filled_value=1000.0,
            fees=4.0
        )
        
        order = self.machine.get_order("test_123")
        assert order.status == OrderStatus.FILLED.value
    
    def test_update_fill_auto_transition_to_partial(self):
        """Test that update_fill auto-transitions to PARTIAL_FILL when partially filled"""
        self.machine.create_order(
            client_order_id="test_123",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            size_base=0.01
        )
        self.machine.transition("test_123", OrderStatus.OPEN)
        
        # Fill 50%
        self.machine.update_fill(
            client_order_id="test_123",
            filled_size=0.005,
            filled_value=500.0,
            fees=2.0
        )
        
        order = self.machine.get_order("test_123")
        assert order.status == OrderStatus.PARTIAL_FILL.value
    
    def test_get_active_orders(self):
        """Test getting active orders"""
        self.machine.create_order("order1", "BTC-USD", "buy", 1000.0)
        self.machine.create_order("order2", "ETH-USD", "buy", 500.0)
        self.machine.create_order("order3", "SOL-USD", "sell", 200.0)
        
        self.machine.transition("order1", OrderStatus.OPEN)
        self.machine.transition("order2", OrderStatus.OPEN)
        self.machine.transition("order2", OrderStatus.FILLED)
        
        active = self.machine.get_active_orders()
        
        assert len(active) == 2  # order1 (OPEN) and order3 (NEW)
        client_ids = [o.client_order_id for o in active]
        assert "order1" in client_ids
        assert "order3" in client_ids
    
    def test_get_terminal_orders(self):
        """Test getting terminal orders"""
        self.machine.create_order("order1", "BTC-USD", "buy", 1000.0)
        self.machine.create_order("order2", "ETH-USD", "buy", 500.0)
        
        self.machine.transition("order1", OrderStatus.OPEN)
        self.machine.transition("order1", OrderStatus.FILLED)
        
        terminal = self.machine.get_terminal_orders()
        
        assert len(terminal) == 1
        assert terminal[0].client_order_id == "order1"
    
    def test_get_orders_by_status(self):
        """Test filtering orders by status"""
        self.machine.create_order("order1", "BTC-USD", "buy", 1000.0)
        self.machine.create_order("order2", "ETH-USD", "buy", 500.0)
        self.machine.create_order("order3", "SOL-USD", "sell", 200.0)
        
        self.machine.transition("order1", OrderStatus.OPEN)
        self.machine.transition("order2", OrderStatus.OPEN)
        
        open_orders = self.machine.get_orders_by_status(OrderStatus.OPEN)
        
        assert len(open_orders) == 2
        client_ids = [o.client_order_id for o in open_orders]
        assert "order1" in client_ids
        assert "order2" in client_ids
    
    def test_get_stale_orders(self):
        """Test getting stale orders"""
        self.machine.create_order("order1", "BTC-USD", "buy", 1000.0)
        self.machine.create_order("order2", "ETH-USD", "buy", 500.0)
        
        # Make order1 old
        order1 = self.machine.get_order("order1")
        order1.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        
        stale = self.machine.get_stale_orders(max_age_seconds=60)
        
        assert len(stale) == 1
        assert stale[0].client_order_id == "order1"
    
    def test_cleanup_old_orders(self):
        """Test cleaning up old terminal orders"""
        # Create 5 orders, all terminal
        for i in range(5):
            self.machine.create_order(f"order{i}", "BTC-USD", "buy", 1000.0)
            self.machine.transition(f"order{i}", OrderStatus.OPEN)
            self.machine.transition(f"order{i}", OrderStatus.FILLED)
        
        # Make them have different completion times
        for i, order in enumerate(self.machine.orders.values()):
            order.completed_at = datetime.now(timezone.utc) - timedelta(seconds=i)
        
        # Keep only last 3
        self.machine.cleanup_old_orders(keep_last_n=3)
        
        assert len(self.machine.orders) == 3
        terminal = self.machine.get_terminal_orders()
        assert len(terminal) == 3
    
    def test_get_summary(self):
        """Test getting summary statistics"""
        self.machine.create_order("order1", "BTC-USD", "buy", 1000.0)
        self.machine.create_order("order2", "ETH-USD", "buy", 500.0)
        self.machine.create_order("order3", "SOL-USD", "sell", 200.0)
        
        self.machine.transition("order1", OrderStatus.OPEN)
        self.machine.transition("order2", OrderStatus.OPEN)
        self.machine.transition("order2", OrderStatus.FILLED)
        
        summary = self.machine.get_summary()
        
        assert summary["total_orders"] == 3
        assert summary["active_orders"] == 2  # order1 (OPEN), order3 (NEW)
        assert summary["terminal_orders"] == 1  # order2 (FILLED)
        assert summary["status_breakdown"][OrderStatus.NEW.value] == 1
        assert summary["status_breakdown"][OrderStatus.OPEN.value] == 1
        assert summary["status_breakdown"][OrderStatus.FILLED.value] == 1


class TestOrderStateMachineSingleton:
    """Test singleton pattern"""
    
    def test_singleton_same_instance(self):
        """Test that get_order_state_machine returns same instance"""
        machine1 = get_order_state_machine()
        machine2 = get_order_state_machine()
        
        assert machine1 is machine2
    
    def test_singleton_persists_state(self):
        """Test that state persists across singleton calls"""
        machine1 = get_order_state_machine()
        machine1.create_order("test_singleton", "BTC-USD", "buy", 1000.0)
        
        machine2 = get_order_state_machine()
        order = machine2.get_order("test_singleton")
        
        assert order is not None
        assert order.symbol == "BTC-USD"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
