"""
Tests for fill reconciliation with OrderStateMachine integration.

Verifies:
- Fill polling from exchange
- Match fills to tracked orders
- Update order states with fill details
- Calculate fees and positions
- Handle partial and complete fills
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone, timedelta
from core.execution import ExecutionEngine
from core.order_state import OrderStateMachine, OrderStatus


class TestReconcileFills:
    """Test fill reconciliation functionality"""
    
    def setup_method(self):
        """Create fresh instances for each test"""
        self.policy = {
            "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},
            "risk": {"min_trade_notional_usd": 10.0}
        }
        self.mock_exchange = Mock()
        self.mock_exchange.read_only = False
        self.mock_state_store = Mock()
        
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
    
    def test_dry_run_mode_skips_reconciliation(self):
        """Test that DRY_RUN mode skips fill reconciliation"""
        engine = ExecutionEngine(mode="DRY_RUN", policy=self.policy)
        
        result = engine.reconcile_fills()
        
        assert result["fills_processed"] == 0
        assert result["orders_updated"] == 0
        assert result["total_fees"] == 0.0
    
    def test_no_fills_to_reconcile(self):
        """Test when exchange returns no fills"""
        self.mock_exchange.list_fills.return_value = []
        
        result = self.engine.reconcile_fills()
        
        assert result["fills_processed"] == 0
        assert result["orders_updated"] == 0
        assert result["total_fees"] == 0.0
        self.mock_exchange.list_fills.assert_called_once()
    
    def test_reconcile_single_complete_fill(self):
        """Test reconciling a single complete fill"""
        # Create tracked order
        client_id = "coid_test123"
        order_id = "exchange_order_123"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        
        # Mock fill from exchange
        fill = {
            "order_id": order_id,
            "product_id": "BTC-USD",
            "price": "50000.0",
            "size": "0.02",
            "commission": "0.4",  # $0.40 fee
            "side": "BUY",
            "trade_time": datetime.now(timezone.utc).isoformat(),
            "liquidity_indicator": "MAKER"
        }
        self.mock_exchange.list_fills.return_value = [fill]
        
        # Reconcile
        result = self.engine.reconcile_fills(lookback_minutes=60)
        
        # Verify results
        assert result["fills_processed"] == 1
        assert result["orders_updated"] == 1
        assert result["total_fees"] == 0.4
        assert result["fills_by_symbol"]["BTC-USD"] == 1
        assert result["unmatched_fills"] == 0
        
        # Verify order state updated
        order_state = self.state_machine.get_order(client_id)
        assert order_state.status == OrderStatus.FILLED.value
        assert order_state.filled_size == 0.02
        assert order_state.filled_value == 1000.0  # 0.02 * 50000
        assert order_state.fees == 0.4
        assert len(order_state.fills) == 1
    
    def test_reconcile_partial_fill(self):
        """Test reconciling a partial fill (< 95% of order size)"""
        # Create tracked order
        client_id = "coid_partial"
        order_id = "exchange_order_456"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="ETH-USD",
            side="buy",
            size_usd=1000.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        
        # Mock partial fill (only $500 of $1000 order)
        fill = {
            "order_id": order_id,
            "product_id": "ETH-USD",
            "price": "2500.0",
            "size": "0.2",  # $500 worth
            "commission": "0.2",
            "side": "BUY",
            "trade_time": datetime.now(timezone.utc).isoformat()
        }
        self.mock_exchange.list_fills.return_value = [fill]
        
        # Reconcile
        result = self.engine.reconcile_fills()
        
        # Verify partial fill state
        order_state = self.state_machine.get_order(client_id)
        assert order_state.status == OrderStatus.PARTIAL_FILL.value
        assert order_state.filled_value == 500.0
        assert result["orders_updated"] == 1
    
    def test_reconcile_multiple_fills_same_order(self):
        """Test multiple fills for the same order (accumulates)"""
        # Create tracked order
        client_id = "coid_multi"
        order_id = "exchange_order_789"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="SOL-USD",
            side="buy",
            size_usd=1000.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        
        # Mock two fills for same order
        fills = [
            {
                "order_id": order_id,
                "product_id": "SOL-USD",
                "price": "100.0",
                "size": "3.0",  # $300
                "commission": "0.12",
                "side": "BUY",
                "trade_time": datetime.now(timezone.utc).isoformat()
            },
            {
                "order_id": order_id,
                "product_id": "SOL-USD",
                "price": "100.0",
                "size": "7.0",  # $700
                "commission": "0.28",
                "side": "BUY",
                "trade_time": datetime.now(timezone.utc).isoformat()
            }
        ]
        self.mock_exchange.list_fills.return_value = fills
        
        # Reconcile
        result = self.engine.reconcile_fills()
        
        # Verify accumulated fills
        order_state = self.state_machine.get_order(client_id)
        assert order_state.filled_size == 10.0  # 3.0 + 7.0
        assert order_state.filled_value == 1000.0  # 300 + 700
        assert order_state.fees == 0.4  # 0.12 + 0.28
        assert len(order_state.fills) == 2
        assert result["fills_processed"] == 2
        assert result["total_fees"] == 0.4
    
    def test_reconcile_fills_multiple_orders(self):
        """Test reconciling fills for multiple different orders"""
        # Create multiple tracked orders
        orders = []
        for i in range(3):
            client_id = f"coid_order_{i}"
            order_id = f"exchange_order_{i}"
            
            self.state_machine.create_order(
                client_order_id=client_id,
                symbol=f"COIN{i}-USD",
                side="buy",
                size_usd=100.0
            )
            self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
            orders.append((client_id, order_id))
        
        # Mock fills for all orders
        fills = [
            {
                "order_id": order_id,
                "product_id": f"COIN{i}-USD",
                "price": "10.0",
                "size": "10.0",
                "commission": "0.04",
                "side": "BUY",
                "trade_time": datetime.now(timezone.utc).isoformat()
            }
            for i, (_, order_id) in enumerate(orders)
        ]
        self.mock_exchange.list_fills.return_value = fills
        
        # Reconcile
        result = self.engine.reconcile_fills()
        
        # Verify all orders updated
        assert result["fills_processed"] == 3
        assert result["orders_updated"] == 3
        assert result["total_fees"] == 0.12  # 0.04 * 3
        
        # Verify each order
        for client_id, _ in orders:
            order_state = self.state_machine.get_order(client_id)
            assert order_state.status == OrderStatus.FILLED.value
            assert order_state.filled_size == 10.0
    
    def test_unmatched_fill_no_tracked_order(self):
        """Test handling fills with no matching tracked order"""
        # Don't create any tracked orders
        
        # Mock fill for unknown order
        fill = {
            "order_id": "unknown_order_999",
            "product_id": "UNKNOWN-USD",
            "price": "1.0",
            "size": "100.0",
            "commission": "0.04",
            "side": "BUY",
            "trade_time": datetime.now(timezone.utc).isoformat()
        }
        self.mock_exchange.list_fills.return_value = [fill]
        
        # Reconcile
        result = self.engine.reconcile_fills()
        
        # Verify unmatched fill tracked
        assert result["fills_processed"] == 1
        assert result["orders_updated"] == 0
        assert result["unmatched_fills"] == 1
        assert result["total_fees"] == 0.04  # Fee still counted
    
    def test_reconcile_with_lookback_window(self):
        """Test that lookback_minutes parameter is passed correctly"""
        self.mock_exchange.list_fills.return_value = []
        
        # Reconcile with custom lookback
        self.engine.reconcile_fills(lookback_minutes=30)
        
        # Verify list_fills called with correct start_time
        call_kwargs = self.mock_exchange.list_fills.call_args[1]
        assert "start_time" in call_kwargs
        
        # Start time should be ~30 minutes ago
        start_time = call_kwargs["start_time"]
        now = datetime.now(timezone.utc)
        age_minutes = (now - start_time).total_seconds() / 60
        assert 29 < age_minutes < 31  # Allow small margin
    
    def test_state_store_integration(self):
        """Test that StateStore is updated with filled order details"""
        # Create tracked order
        client_id = "coid_statestore"
        order_id = "exchange_order_ss"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="XRP-USD",
            side="buy",
            size_usd=100.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        
        # Mock fill
        fill = {
            "order_id": order_id,
            "product_id": "XRP-USD",
            "price": "0.50",
            "size": "200.0",
            "commission": "0.04",
            "side": "BUY",
            "trade_time": "2025-11-11T12:00:00Z"
        }
        self.mock_exchange.list_fills.return_value = [fill]
        
        # Reconcile
        self.engine.reconcile_fills()
        
        # Verify state store close_order was called
        assert self.mock_state_store.close_order.called
        call_args = self.mock_state_store.close_order.call_args
        
        # Verify details passed to state store
        assert call_args[0][0] == client_id  # First arg is key
        assert call_args[1]["status"] == "filled"
        details = call_args[1]["details"]
        assert details["product_id"] == "XRP-USD"
        assert details["filled_size"] == 200.0
        assert details["fees"] == 0.04
    
    def test_reconcile_error_handling(self):
        """Test error handling when exchange API fails"""
        # Mock exchange failure
        self.mock_exchange.list_fills.side_effect = Exception("API error")
        
        # Should not raise, should return error info
        result = self.engine.reconcile_fills()
        
        assert result["fills_processed"] == 0
        assert result["orders_updated"] == 0
        assert "error" in result
        assert "API error" in result["error"]
    
    def test_fills_by_symbol_grouping(self):
        """Test that fills are correctly grouped by symbol"""
        # Create orders for different symbols
        for i, symbol in enumerate(["BTC-USD", "ETH-USD", "BTC-USD"]):
            client_id = f"coid_symbol_{i}"
            order_id = f"exchange_order_sym_{i}"
            
            self.state_machine.create_order(
                client_order_id=client_id,
                symbol=symbol,
                side="buy",
                size_usd=100.0
            )
            self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        
        # Mock fills (2 BTC-USD, 1 ETH-USD)
        fills = [
            {
                "order_id": "exchange_order_sym_0",
                "product_id": "BTC-USD",
                "price": "50000.0",
                "size": "0.002",
                "commission": "0.04",
                "side": "BUY",
                "trade_time": datetime.now(timezone.utc).isoformat()
            },
            {
                "order_id": "exchange_order_sym_1",
                "product_id": "ETH-USD",
                "price": "2500.0",
                "size": "0.04",
                "commission": "0.04",
                "side": "BUY",
                "trade_time": datetime.now(timezone.utc).isoformat()
            },
            {
                "order_id": "exchange_order_sym_2",
                "product_id": "BTC-USD",
                "price": "50000.0",
                "size": "0.002",
                "commission": "0.04",
                "side": "BUY",
                "trade_time": datetime.now(timezone.utc).isoformat()
            }
        ]
        self.mock_exchange.list_fills.return_value = fills
        
        # Reconcile
        result = self.engine.reconcile_fills()
        
        # Verify symbol grouping
        assert result["fills_by_symbol"]["BTC-USD"] == 2
        assert result["fills_by_symbol"]["ETH-USD"] == 1
    
    def test_already_filled_order_ignores_duplicate_fills(self):
        """Test that already-filled orders don't get re-transitioned"""
        # Create order and mark as already filled
        client_id = "coid_already_filled"
        order_id = "exchange_order_dup"
        
        self.state_machine.create_order(
            client_order_id=client_id,
            symbol="ADA-USD",
            side="buy",
            size_usd=100.0
        )
        self.state_machine.transition(client_id, OrderStatus.OPEN, order_id=order_id)
        self.state_machine.transition(client_id, OrderStatus.FILLED, filled_size=100.0, filled_value=100.0)
        
        # Mock duplicate fill
        fill = {
            "order_id": order_id,
            "product_id": "ADA-USD",
            "price": "1.0",
            "size": "100.0",
            "commission": "0.04",
            "side": "BUY",
            "trade_time": datetime.now(timezone.utc).isoformat()
        }
        self.mock_exchange.list_fills.return_value = [fill]
        
        # Reconcile
        result = self.engine.reconcile_fills()
        
        # Should still process fill (update totals) but not re-transition
        assert result["fills_processed"] == 1
        assert result["orders_updated"] == 1
        
        # Order should still be FILLED (not double-transitioned)
        order_state = self.state_machine.get_order(client_id)
        assert order_state.status == OrderStatus.FILLED.value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
