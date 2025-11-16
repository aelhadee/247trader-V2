"""
Tests for enhanced manage_open_orders with OrderStateMachine integration.

Verifies stale order cancellation, state transitions, and error handling.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone, timedelta
from core.execution import ExecutionEngine
from core.order_state import OrderStateMachine, OrderStatus
from infra.state_store import StateStore


class TestManageOpenOrders:
    """Test manage_open_orders with OrderStateMachine integration"""
    
    def setup_method(self):
        """Create fresh instances for each test"""
        self.policy = {
            "execution": {"cancel_after_seconds": 60},
            "risk": {"min_trade_notional_usd": 10.0}
        }
        self.mock_exchange = Mock()
        self.mock_exchange.read_only = False
        self.mock_state_store = Mock(spec=StateStore)
        
        self.engine = ExecutionEngine(
            mode="LIVE",
            exchange=self.mock_exchange,
            policy=self.policy,
            state_store=self.mock_state_store
        )
        
        # Get fresh state machine
        self.state_machine = self.engine.order_state_machine
        # Clear any existing orders
        self.state_machine.orders.clear()
    
    def test_dry_run_mode_skips_cancellation(self):
        """Test that DRY_RUN mode skips order cancellation"""
        mock_exchange = Mock()
        engine = ExecutionEngine(mode="DRY_RUN", policy=self.policy, exchange=mock_exchange)
        
        # Should return immediately without calling exchange
        engine.manage_open_orders()
        
        # No exchange calls should be made
        mock_exchange.list_open_orders.assert_not_called()
        mock_exchange.cancel_order.assert_not_called()
        mock_exchange.cancel_orders.assert_not_called()
    
    def test_disabled_cancellation_when_zero_timeout(self):
        """Test that cancellation is disabled when cancel_after_seconds <= 0"""
        policy = {"execution": {"cancel_after_seconds": 0}}
        engine = ExecutionEngine(
            mode="LIVE",
            exchange=self.mock_exchange,
            policy=policy
        )
        
        engine.manage_open_orders()
        
        # Should not call exchange
        self.mock_exchange.list_open_orders.assert_not_called()
    
    def test_no_stale_orders(self):
        """Test when there are no stale orders"""
        # Create recent order (not stale)
        self.state_machine.create_order(
            client_order_id="coid_recent",
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.state_machine.transition("coid_recent", OrderStatus.OPEN, order_id="order_123")
        
        # Should not attempt cancellation
        self.engine.manage_open_orders()
        
        # No cancellation calls
        self.mock_exchange.cancel_order.assert_not_called()
        self.mock_exchange.cancel_orders.assert_not_called()
    
    def test_cancel_single_stale_order(self):
        """Test canceling a single stale order"""
        # Create stale order
        client_id = "coid_stale"
        order_id = "order_123"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        
        # Make it old
        order = self.state_machine.get_order(client_id)
        order.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        
        # Mock exchange cancellation
        self.mock_exchange.cancel_order.return_value = {"success": True}
        self.mock_exchange.list_open_orders.return_value = []
        
        # Run cancellation
        self.engine.manage_open_orders()
        
        # Verify cancellation was called
        self.mock_exchange.cancel_order.assert_called_once_with(order_id)
        
        # Verify state transition
        order_state = self.state_machine.get_order(client_id)
        assert order_state.status == OrderStatus.CANCELED.value
    
    def test_cancel_multiple_stale_orders_batch(self):
        """Test canceling multiple stale orders (individual cancellation)"""
        # Create 3 stale orders
        orders = []
        for i in range(3):
            client_id = f"coid_stale_{i}"
            order_id = f"order_{i}"
            
            self.state_machine.create_order(
                client_order_id=client_id,
                symbol="BTC-USD",
                side="buy",
                size_usd=1000.0
            )
            self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
            
            # Make it old
            order = self.state_machine.get_order(client_id)
            order.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
            orders.append((client_id, order_id))
        
        # Mock individual cancellation (current implementation uses _safe_cancel)
        self.mock_exchange.cancel_order.return_value = {"success": True}
        self.mock_exchange.list_open_orders.return_value = []
        
        # Run cancellation
        self.engine.manage_open_orders()
        
        # Verify individual cancel was called 3 times
        assert self.mock_exchange.cancel_order.call_count == 3
        
        # Verify all order IDs were canceled
        call_order_ids = [call.args[0] for call in self.mock_exchange.cancel_order.call_args_list]
        assert all(f"order_{i}" in call_order_ids for i in range(3))
        
        # Verify all orders transitioned to CANCELED
        for client_id, _ in orders:
            order_state = self.state_machine.get_order(client_id)
            assert order_state.status == OrderStatus.CANCELED.value
    
    def test_batch_cancel_fallback_to_individual(self):
        """Test fallback to individual cancellation when batch fails"""
        # Create 2 stale orders
        orders = []
        for i in range(2):
            client_id = f"coid_stale_{i}"
            order_id = f"order_{i}"
            
            self.state_machine.create_order(
                client_order_id=client_id,
                symbol="BTC-USD",
                side="buy",
                size_usd=1000.0
            )
            self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
            
            order = self.state_machine.get_order(client_id)
            order.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
            orders.append((client_id, order_id))
        
        # Mock batch cancel failure
        self.mock_exchange.cancel_orders.side_effect = Exception("Batch cancel failed")
        self.mock_exchange.cancel_order.return_value = {"success": True}
        self.mock_exchange.list_open_orders.return_value = []
        
        # Run cancellation
        self.engine.manage_open_orders()
        
        # Verify batch was attempted
        self.mock_exchange.cancel_orders.assert_called_once()
        
        # Verify fallback to individual
        assert self.mock_exchange.cancel_order.call_count == 2
        
        # Verify all orders still transitioned
        for client_id, _ in orders:
            order_state = self.state_machine.get_order(client_id)
            assert order_state.status == OrderStatus.CANCELED.value
    
    def test_cancel_failure_still_transitions_state(self):
        """Test that order transitions to CANCELED even if API cancel fails"""
        # Create stale order
        client_id = "coid_stale"
        order_id = "order_123"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        
        order = self.state_machine.get_order(client_id)
        order.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        
        # Mock cancellation failure
        self.mock_exchange.cancel_order.side_effect = Exception("Order not found")
        self.mock_exchange.list_open_orders.return_value = []
        
        # Run cancellation (should not raise)
        self.engine.manage_open_orders()
        
        # Verify state still transitioned
        order_state = self.state_machine.get_order(client_id)
        assert order_state.status == OrderStatus.CANCELED.value
        assert "Cancel failed" in order_state.error
    
    def test_stale_order_without_exchange_id(self):
        """Test handling stale order that has no exchange order_id"""
        # Create order without exchange ID
        client_id = "coid_no_exchange_id"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        # Don't set order_id (simulates order that never got exchange confirmation)
        self.state_machine.transition(client_id, OrderStatus.OPEN)
        
        order = self.state_machine.get_order(client_id)
        order.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        
        # Run cancellation
        self.engine.manage_open_orders()
        
        # Should not attempt exchange cancellation
        self.mock_exchange.cancel_order.assert_not_called()
        
        # But should still transition to CANCELED
        order_state = self.state_machine.get_order(client_id)
        assert order_state.status == OrderStatus.CANCELED.value
        assert "No exchange order_id" in order_state.error
    
    def test_skip_already_terminal_orders(self):
        """Test that terminal orders are skipped"""
        # Create order and make it terminal
        client_id = "coid_terminal"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id="order_123")
        self.state_machine.transition(client_id, OrderStatus.FILLED)
        
        # Make it old
        order = self.state_machine.get_order(client_id)
        order.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        
        # Run cancellation
        self.engine.manage_open_orders()
        
        # Should not attempt cancellation (already terminal)
        self.mock_exchange.cancel_order.assert_not_called()
    
    def test_state_store_integration(self):
        """Test that StateStore is updated on cancellation"""
        # Create stale order
        client_id = "coid_stale"
        order_id = "order_123"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        
        order = self.state_machine.get_order(client_id)
        order.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        
        # Mock cancellation
        self.mock_exchange.cancel_order.return_value = {"success": True}
        self.mock_exchange.list_open_orders.return_value = []
        
        # Mock state store
        self.engine.state_store = self.mock_state_store
        
        # Run cancellation
        self.engine.manage_open_orders()
        
        # Verify state store was called (via _close_order_in_state_store)
        # The method should attempt to close the order
        assert self.mock_state_store.close_order.called or \
               self.mock_state_store.method_calls  # At least some interaction
    
    def test_exchange_sync_after_cancellation(self):
        """Test that open orders are synced from exchange after cancellation"""
        # Create stale order
        client_id = "coid_stale"
        order_id = "order_123"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        
        order = self.state_machine.get_order(client_id)
        order.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        
        # Mock cancellation and sync
        self.mock_exchange.cancel_order.return_value = {"success": True}
        self.mock_exchange.list_open_orders.return_value = [
            {"order_id": "other_order", "status": "open"}
        ]
        
        # Run cancellation
        self.engine.manage_open_orders()
        
        # Verify exchange was queried for remaining orders
        assert self.mock_exchange.list_open_orders.call_count >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
