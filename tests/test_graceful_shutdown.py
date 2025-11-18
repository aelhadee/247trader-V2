"""
Tests for graceful shutdown handler.

Validates that SIGTERM/SIGINT triggers proper cleanup:
- Cancel all active orders
- Flush StateStore
- Clean exit without data loss
"""

import pytest
from unittest.mock import Mock, patch

from runner.main_loop import TradingLoop
from core.order_state import OrderStatus, OrderState


class TestGracefulShutdown:
    """Test graceful shutdown handler"""
    
    @pytest.fixture(autouse=True)
    def reset_metrics(self):
        """Reset Prometheus metrics between tests to avoid registry conflicts"""
        from infra.metrics import MetricsRecorder
        # Clean up BEFORE test (in case previous test didn't have fixture)
        MetricsRecorder._reset_for_testing()
        yield
        # Clean up AFTER test
        MetricsRecorder._reset_for_testing()
    
    @pytest.fixture(autouse=True)
    def cleanup_lock(self):
        """Clean up instance lock before and after each test"""
        import os
        lock_file = "data/247trader-v2.pid"
        # Clean up before test
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except:
                pass
        yield
        # Clean up after test
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except:
                pass
    
    @pytest.fixture
    def mock_components(self):
        """Mock all external dependencies"""
        with patch('runner.main_loop.CoinbaseExchange') as mock_exchange_cls, \
             patch('infra.state_store.StateStore') as mock_state_store_cls, \
             patch('runner.main_loop.AuditLogger') as mock_audit_cls, \
             patch('runner.main_loop.AlertService') as mock_alert_cls, \
             patch('runner.main_loop.UniverseManager') as mock_universe_cls, \
             patch('runner.main_loop.TriggerEngine') as mock_trigger_cls, \
             patch('runner.main_loop.RulesEngine') as mock_rules_cls, \
             patch('runner.main_loop.RiskEngine') as mock_risk_cls, \
             patch('runner.main_loop.ExecutionEngine') as mock_exec_cls:
            
            # Configure exchange mock
            mock_exchange = Mock()
            mock_exchange.read_only = False
            mock_exchange.cancel_order = Mock(return_value={"success": True})
            mock_exchange.cancel_orders = Mock(return_value={"success": True})
            # get_accounts must return iterable list for portfolio init
            mock_exchange.get_accounts = Mock(return_value=[
                {"currency": "USD", "available_balance": {"value": "10000"}}
            ])
            mock_exchange_cls.return_value = mock_exchange
            
            # Configure state store mock
            mock_state_store = Mock()
            mock_state_store.load = Mock(return_value={
                "pnl_today": 0.0,
                "trades_today": 0,
                "positions": {},
                "open_orders": {},
            })
            mock_state_store.save = Mock(return_value=None)
            mock_state_store_cls.return_value = mock_state_store
            
            # Configure audit logger mock
            mock_audit = Mock()
            mock_audit_cls.return_value = mock_audit
            
            # Configure alert service mock
            mock_alert_cls.from_config = Mock(return_value=Mock(is_enabled=Mock(return_value=False)))
            
            # Configure other mocks
            mock_universe_cls.return_value = Mock()
            mock_trigger_cls.return_value = Mock()
            mock_rules_cls.return_value = Mock()
            mock_risk_cls.return_value = Mock()
            
            # Configure execution engine mock with order state machine
            mock_executor = Mock()
            mock_executor.order_state_machine = Mock()
            mock_executor.order_state_machine.get_active_orders = Mock(return_value=[])
            mock_executor._close_order_in_state_store = Mock()
            mock_exec_cls.return_value = mock_executor
            
            yield {
                "exchange": mock_exchange,
                "state_store": mock_state_store,
                "audit": mock_audit,
                "executor": mock_executor,
            }
    
    def test_dry_run_skips_order_cancellation(self, mock_components):
        """DRY_RUN mode should skip order cancellation"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "DRY_RUN"},
                "exchange": {"read_only": True},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="DRY_RUN")
            
            # Trigger shutdown
            loop._handle_stop()
            
            # Should set _running to False
            assert loop._running is False
            
            # Should NOT cancel any orders
            mock_components["exchange"].cancel_order.assert_not_called()
            mock_components["exchange"].cancel_orders.assert_not_called()
    
    def test_no_active_orders_to_cancel(self, mock_components):
        """Shutdown with no active orders should complete cleanly"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "LIVE"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # No active orders
            mock_components["executor"].order_state_machine.get_active_orders.return_value = []
            
            # Trigger shutdown
            loop._handle_stop()
            
            # Should set _running to False
            assert loop._running is False
            
            # Should not attempt cancellation (no orders)
            mock_components["exchange"].cancel_order.assert_not_called()
            mock_components["exchange"].cancel_orders.assert_not_called()
            
            # Should still flush state store
            mock_components["state_store"].save.assert_called_once()
    
    def test_cancel_single_active_order(self, mock_components):
        """Shutdown with single active order should cancel it"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "PAPER"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # One active order
            active_order = OrderState(
                order_id="order_123",
                client_order_id="client_abc",
                symbol="BTC-USD",
                side="BUY",
                size_usd=100.0,
                status=OrderStatus.OPEN.value,
            )
            mock_components["executor"].order_state_machine.get_active_orders.return_value = [active_order]
            mock_components["executor"].order_state_machine.transition = Mock()
            
            # Exchange returns success
            mock_components["exchange"].cancel_order.return_value = {"success": True}
            
            # Trigger shutdown
            loop._handle_stop()
            
            # Should cancel the order
            mock_components["exchange"].cancel_order.assert_called_once_with("order_123")
            
            # Should transition to CANCELED
            mock_components["executor"].order_state_machine.transition.assert_called_once_with(
                "client_abc",
                OrderStatus.CANCELED
            )
            
            # Should close in state store
            mock_components["executor"]._close_order_in_state_store.assert_called_once()
            
            # Should flush state store
            mock_components["state_store"].save.assert_called_once()
    
    def test_cancel_multiple_active_orders_batch(self, mock_components):
        """Shutdown with multiple active orders should batch cancel"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "LIVE"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # Three active orders
            active_orders = [
                OrderState(
                    order_id="order_1",
                    client_order_id="client_1",
                    symbol="BTC-USD",
                    side="BUY",
                    size_usd=100.0,
                    status=OrderStatus.OPEN.value,
                ),
                OrderState(
                    order_id="order_2",
                    client_order_id="client_2",
                    symbol="ETH-USD",
                    side="BUY",
                    size_usd=50.0,
                    status=OrderStatus.PARTIAL_FILL.value,
                ),
                OrderState(
                    order_id="order_3",
                    client_order_id="client_3",
                    symbol="SOL-USD",
                    side="SELL",
                    size_usd=75.0,
                    status=OrderStatus.OPEN.value,
                ),
            ]
            mock_components["executor"].order_state_machine.get_active_orders.return_value = active_orders
            mock_components["executor"].order_state_machine.transition = Mock()
            
            # Exchange returns success
            mock_components["exchange"].cancel_orders.return_value = {"success": True}
            
            # Trigger shutdown
            loop._handle_stop()
            
            # Should batch cancel all orders
            mock_components["exchange"].cancel_orders.assert_called_once_with(
                ["order_1", "order_2", "order_3"]
            )
            
            # Should not call single cancel
            mock_components["exchange"].cancel_order.assert_not_called()
            
            # Should transition all to CANCELED
            assert mock_components["executor"].order_state_machine.transition.call_count == 3
            
            # Should close all in state store
            assert mock_components["executor"]._close_order_in_state_store.call_count == 3
            
            # Should flush state store
            mock_components["state_store"].save.assert_called_once()
    
    def test_skip_orders_without_exchange_id(self, mock_components):
        """Orders without exchange order_id should be skipped"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "LIVE"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # Two orders: one with order_id, one without
            active_orders = [
                OrderState(
                    order_id="order_123",
                    client_order_id="client_1",
                    symbol="BTC-USD",
                    side="BUY",
                    size_usd=100.0,
                    status=OrderStatus.OPEN.value,
                ),
                OrderState(
                    order_id=None,  # No exchange order_id
                    client_order_id="client_2",
                    symbol="ETH-USD",
                    side="BUY",
                    size_usd=50.0,
                    status=OrderStatus.NEW.value,  # Never submitted
                ),
            ]
            mock_components["executor"].order_state_machine.get_active_orders.return_value = active_orders
            mock_components["executor"].order_state_machine.transition = Mock()
            
            # Trigger shutdown
            loop._handle_stop()
            
            # Should only cancel the order with exchange ID
            mock_components["exchange"].cancel_order.assert_called_once_with("order_123")
            
            # Should only transition the canceled order
            mock_components["executor"].order_state_machine.transition.assert_called_once()
    
    def test_cancel_failure_continues_cleanup(self, mock_components):
        """Exchange cancel failure should not stop cleanup"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "LIVE"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # One active order
            active_order = OrderState(
                order_id="order_123",
                client_order_id="client_abc",
                symbol="BTC-USD",
                side="BUY",
                size_usd=100.0,
                status=OrderStatus.OPEN.value,
            )
            mock_components["executor"].order_state_machine.get_active_orders.return_value = [active_order]
            
            # Exchange returns failure
            mock_components["exchange"].cancel_order.return_value = {
                "success": False,
                "error": "Order not found"
            }
            
            # Trigger shutdown
            loop._handle_stop()
            
            # Should attempt cancel
            mock_components["exchange"].cancel_order.assert_called_once_with("order_123")
            
            # Should still flush state store (cleanup continues)
            mock_components["state_store"].save.assert_called_once()
    
    def test_exchange_exception_continues_cleanup(self, mock_components):
        """Exchange exception should not stop cleanup"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "LIVE"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # One active order
            active_order = OrderState(
                order_id="order_123",
                client_order_id="client_abc",
                symbol="BTC-USD",
                side="BUY",
                size_usd=100.0,
                status=OrderStatus.OPEN.value,
            )
            mock_components["executor"].order_state_machine.get_active_orders.return_value = [active_order]
            
            # Exchange raises exception
            mock_components["exchange"].cancel_order.side_effect = Exception("Network error")
            
            # Trigger shutdown (should not raise)
            loop._handle_stop()
            
            # Should still flush state store (cleanup continues)
            mock_components["state_store"].save.assert_called_once()
    
    def test_state_store_flush_failure_logged(self, mock_components):
        """StateStore flush failure should be logged but not crash"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "LIVE"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # No active orders
            mock_components["executor"].order_state_machine.get_active_orders.return_value = []
            
            # StateStore raises exception
            mock_components["state_store"].save.side_effect = Exception("Disk full")
            
            # Trigger shutdown (should not raise)
            loop._handle_stop()
            
            # Should attempt save
            mock_components["state_store"].save.assert_called_once()
    
    def test_paper_mode_cancels_orders(self, mock_components):
        """PAPER mode should cancel orders (paper account)"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "PAPER"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # One active order
            active_order = OrderState(
                order_id="paper_order_123",
                client_order_id="client_abc",
                symbol="BTC-USD",
                side="BUY",
                size_usd=100.0,
                status=OrderStatus.OPEN.value,
            )
            mock_components["executor"].order_state_machine.get_active_orders.return_value = [active_order]
            mock_components["executor"].order_state_machine.transition = Mock()
            
            # Trigger shutdown
            loop._handle_stop()
            
            # Should cancel the order (PAPER mode allows cancellation)
            mock_components["exchange"].cancel_order.assert_called_once_with("paper_order_123")
            
            # Should flush state store
            mock_components["state_store"].save.assert_called_once()
    
    def test_transition_failure_continues_cleanup(self, mock_components):
        """Transition failure should not stop other orders from being canceled"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load:
            mock_load.return_value = {
                "app": {"mode": "LIVE"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # Two active orders
            active_orders = [
                OrderState(
                    order_id="order_1",
                    client_order_id="client_1",
                    symbol="BTC-USD",
                    side="BUY",
                    size_usd=100.0,
                    status=OrderStatus.OPEN.value,
                ),
                OrderState(
                    order_id="order_2",
                    client_order_id="client_2",
                    symbol="ETH-USD",
                    side="BUY",
                    size_usd=50.0,
                    status=OrderStatus.OPEN.value,
                ),
            ]
            mock_components["executor"].order_state_machine.get_active_orders.return_value = active_orders
            
            # First transition succeeds, second fails
            mock_components["executor"].order_state_machine.transition = Mock(
                side_effect=[None, Exception("Transition error")]
            )
            
            # Trigger shutdown (should not raise)
            loop._handle_stop()
            
            # Should attempt both transitions
            assert mock_components["executor"].order_state_machine.transition.call_count == 2
            
            # Should still flush state store
            mock_components["state_store"].save.assert_called_once()
    
    def test_cleanup_summary_logged(self, mock_components):
        """Cleanup summary should be logged with counts"""
        with patch('runner.main_loop.TradingLoop._load_yaml') as mock_load, \
             patch('runner.main_loop.logger') as mock_logger:
            mock_load.return_value = {
                "app": {"mode": "LIVE"},
                "exchange": {"read_only": False},
                "logging": {"level": "INFO", "file": "logs/test.log"},
                "monitoring": {},
            }
            
            loop = TradingLoop(mode_override="PAPER")
            
            # Two active orders
            active_orders = [
                OrderState(
                    order_id="order_1",
                    client_order_id="client_1",
                    symbol="BTC-USD",
                    side="BUY",
                    size_usd=100.0,
                    status=OrderStatus.OPEN.value,
                ),
                OrderState(
                    order_id="order_2",
                    client_order_id="client_2",
                    symbol="ETH-USD",
                    side="BUY",
                    size_usd=50.0,
                    status=OrderStatus.OPEN.value,
                ),
            ]
            mock_components["executor"].order_state_machine.get_active_orders.return_value = active_orders
            mock_components["executor"].order_state_machine.transition = Mock()
            
            # Trigger shutdown
            loop._handle_stop()
            
            # Should log summary with counts
            # Check that warning was called with "Orders canceled: 2"
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("Orders canceled: 2" in str(call) for call in warning_calls)
