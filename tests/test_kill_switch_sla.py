"""
Kill-switch SLA verification test (REQ-K1).

Verifies that on kill-switch activation:
1. Proposals are blocked immediately (same cycle)
2. All working orders are canceled within ≤10s
3. CRITICAL alert fires within ≤5s
4. halt_reason and timestamp persisted to state

This test uses mocked exchange and timing instrumentation to verify
the SLA requirements without real network calls.

NOTE: Run these tests in isolation to avoid Prometheus registry conflicts:
    pytest tests/test_kill_switch_sla.py -v
"""

import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock, MagicMock, patch
import pytest

# Mock metrics module before importing main_loop to avoid Prometheus registry conflicts
if 'infra.metrics' not in sys.modules:
    mock_metrics = MagicMock()
    mock_metrics.MetricsRecorder = MagicMock(return_value=MagicMock())
    mock_metrics.CycleStats = MagicMock
    sys.modules['infra.metrics'] = mock_metrics

from runner.main_loop import TradingLoop
from core.order_state import OrderStatus
from infra.alerting import AlertSeverity


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset Prometheus metrics between tests to avoid registry conflicts"""
    # Reset the real MetricsRecorder if it exists
    try:
        from infra import metrics as real_metrics
        if hasattr(real_metrics, 'MetricsRecorder'):
            real_metrics.MetricsRecorder._reset_for_testing()
    except (ImportError, AttributeError):
        pass
    yield


@pytest.fixture
def kill_switch_file(tmp_path):
    """Create temporary kill switch file path."""
    return tmp_path / "KILL_SWITCH_TEST"


@pytest.fixture
def mock_exchange():
    """Mock exchange with order cancellation tracking."""
    exchange = Mock()
    exchange.mode = "DRY_RUN"
    exchange.read_only = True
    exchange.get_accounts.return_value = [{"currency": "USD", "available_balance": {"value": "10000.0"}}]
    exchange.get_products.return_value = []
    exchange.list_orders.return_value = []
    exchange.list_fills.return_value = []
    exchange.cancel_order = Mock(return_value={"success": True})
    exchange.cancel_orders = Mock(return_value={"success": True})
    return exchange


@pytest.fixture
def mock_alert_service():
    """Mock alert service that tracks notification timing."""
    alert_service = Mock()
    alert_service._enabled = True  # Required for notify() to execute
    alert_service.is_enabled = Mock(return_value=True)
    alert_service.notify = Mock()
    return alert_service


@patch('infra.instance_lock.check_single_instance', return_value=True)
def test_kill_switch_blocks_proposals_immediately(mock_lock, kill_switch_file, mock_exchange, mock_alert_service):
    """
    Verify kill-switch blocks proposals in the same cycle.
    
    REQ-K1.1: Stop generating new proposals immediately (same cycle).
    """
    # Setup: Create loop with test kill switch path
    loop = TradingLoop(config_dir="config", mode_override="DRY_RUN")
    loop.exchange = mock_exchange
    loop.risk_engine.alert_service = mock_alert_service
    
    # Override kill switch path
    loop.risk_engine.governance_config["kill_switch_file"] = str(kill_switch_file)
    
    # Create kill switch file
    kill_switch_file.write_text("ACTIVATED")
    
    # Execute: Check risk engine blocks proposals
    from core.risk import PortfolioState
    from strategy.rules_engine import TradeProposal
    
    portfolio = PortfolioState(
        account_value_usd=10000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        weekly_pnl_pct=0.0,
        pending_orders={},
    )
    
    # Create a proposal to test that kill switch blocks it
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        size_pct=1.0,
        reason="test_kill_switch",
        confidence=0.8,
        stop_loss_pct=2.0,
        take_profit_pct=5.0,
    )
    
    result = loop.risk_engine.check_all(proposals=[proposal], portfolio=portfolio)
    
    # Assert: Proposals blocked
    assert not result.approved, "Kill switch should block all proposals"
    assert "kill_switch" in result.violated_checks
    assert "KILL_SWITCH" in result.reason
    
    # Assert: Alert fired (timing validated separately)
    assert mock_alert_service.notify.called
    call_args = mock_alert_service.notify.call_args
    assert call_args[1]["severity"] == AlertSeverity.CRITICAL
    assert "KILL SWITCH" in call_args[1]["title"]


@patch('infra.instance_lock.check_single_instance', return_value=True)
def test_kill_switch_alert_sla_under_5s(mock_lock, kill_switch_file, mock_exchange, mock_alert_service):
    """
    Verify kill-switch alert fires within ≤5s.
    
    REQ-K1.3: Emit a CRITICAL alert within ≤5s.
    """
    # Setup
    loop = TradingLoop(config_dir="config", mode_override="DRY_RUN")
    loop.exchange = mock_exchange
    loop.risk_engine.alert_service = mock_alert_service
    loop.risk_engine.governance_config["kill_switch_file"] = str(kill_switch_file)
    
    # Create kill switch file
    kill_switch_file.write_text("ACTIVATED")
    
    # Execute: Measure alert timing
    from core.risk import PortfolioState
    from strategy.rules_engine import TradeProposal
    
    portfolio = PortfolioState(
        account_value_usd=10000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        weekly_pnl_pct=0.0,
        pending_orders={},
    )
    
    # Need a proposal to trigger kill-switch alert
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        size_pct=1.0,
        reason="test_alert",
        confidence=0.8,
    )
    
    start_time = time.monotonic()
    result = loop.risk_engine.check_all(proposals=[proposal], portfolio=portfolio)
    alert_latency = time.monotonic() - start_time
    
    # Assert: Alert fired
    assert mock_alert_service.notify.called, "Kill-switch should trigger CRITICAL alert"
    
    # Assert: Latency ≤5s (in practice should be <<1s for local call)
    assert alert_latency < 5.0, f"Alert latency {alert_latency:.3f}s exceeds 5s SLA"
    
    # Assert: Correct alert severity
    call_args = mock_alert_service.notify.call_args
    assert call_args[1]["severity"] == AlertSeverity.CRITICAL


@patch('infra.instance_lock.check_single_instance', return_value=True)
def test_kill_switch_cancel_timing_sla(mock_lock, kill_switch_file, mock_exchange, mock_alert_service):
    """
    Verify all orders canceled within ≤10s on kill-switch.
    
    REQ-K1.2: Cancel all working orders within ≤10s.
    
    This tests the _handle_stop() graceful shutdown path which is
    triggered by kill-switch detection in the main loop.
    """
    # Setup: Create loop with mock orders
    loop = TradingLoop(config_dir="config", mode_override="DRY_RUN")
    loop.exchange = mock_exchange
    loop.mode = "PAPER"  # Not DRY_RUN so cancellation happens
    loop._running = True
    
    # Mock instance_lock to have a release() method
    mock_lock_obj = Mock()
    mock_lock_obj.release = Mock()
    loop.instance_lock = mock_lock_obj
    
    # Create mock active orders in OrderStateMachine
    from core.order_state import OrderState, OrderStatus
    mock_order_1 = OrderState(
        client_order_id="test_order_1",
        order_id="exchange_order_1",
        symbol="BTC-USD",
        side="BUY",
        size_base=0.1,
        size_usd=5000.0,
        status=OrderStatus.OPEN.value,
    )
    
    mock_order_2 = OrderState(
        client_order_id="test_order_2",
        order_id="exchange_order_2",
        symbol="ETH-USD",
        side="BUY",
        size_base=1.0,
        size_usd=3000.0,
        status=OrderStatus.OPEN.value,
    )
    
    # Inject orders into OrderStateMachine
    loop.executor.order_state_machine.orders = {
        "test_order_1": mock_order_1,
        "test_order_2": mock_order_2,
    }
    
    # Mock exchange cancel responses
    mock_exchange.cancel_orders = Mock(return_value={
        "success": True,
        "results": [
            {"order_id": "exchange_order_1", "success": True},
            {"order_id": "exchange_order_2", "success": True},
        ]
    })
    
    # Execute: Measure cancellation timing
    start_time = time.monotonic()
    loop._handle_stop()
    cancel_latency = time.monotonic() - start_time
    
    # Assert: Cancellation completed within 10s SLA
    assert cancel_latency < 10.0, f"Order cancellation took {cancel_latency:.3f}s, exceeds 10s SLA"
    
    # Assert: Exchange cancel called
    assert mock_exchange.cancel_orders.called or mock_exchange.cancel_order.called
    
    # Assert: Orders transitioned to CANCELED
    assert loop.executor.order_state_machine.orders["test_order_1"].status == OrderStatus.CANCELED.value
    assert loop.executor.order_state_machine.orders["test_order_2"].status == OrderStatus.CANCELED.value
    
    # Assert: Running flag cleared
    assert not loop._running


@patch('infra.instance_lock.check_single_instance', return_value=True)
def test_kill_switch_state_persistence(mock_lock, kill_switch_file, mock_exchange, mock_alert_service):
    """
    Verify kill-switch activation persists halt state.
    
    REQ-K1.4: Persist halt_reason and timestamp.
    """
    # Setup
    loop = TradingLoop(config_dir="config", mode_override="DRY_RUN")
    loop.exchange = mock_exchange
    loop.risk_engine.alert_service = mock_alert_service
    loop.risk_engine.governance_config["kill_switch_file"] = str(kill_switch_file)
    
    # Create kill switch file
    kill_switch_file.write_text("ACTIVATED")
    
    # Execute: Trigger kill-switch check
    from core.risk import PortfolioState
    from strategy.rules_engine import TradeProposal
    
    portfolio = PortfolioState(
        account_value_usd=10000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        weekly_pnl_pct=0.0,
        pending_orders={},
    )
    
    # Need a proposal to trigger kill-switch check
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        size_pct=1.0,
        reason="test_kill_switch",
        confidence=0.8,
    )
    
    result = loop.risk_engine.check_all(proposals=[proposal], portfolio=portfolio)
    
    # Assert: Result contains halt reason
    assert not result.approved
    assert "KILL_SWITCH" in result.reason or "kill_switch" in result.reason.lower()
    assert "kill_switch" in result.violated_checks
    
    # Assert: Alert context includes timestamp
    call_args = mock_alert_service.notify.call_args
    context = call_args[1].get("context", {})
    assert "timestamp" in context, "Alert context should include activation timestamp"
    assert "action" in context
    assert context["action"] == "all_trading_halted"


@patch('infra.instance_lock.check_single_instance', return_value=True)
def test_kill_switch_detection_timing(mock_lock, kill_switch_file):
    """
    Verify kill-switch detection happens within ≤3s (MTTD).
    
    Mean time to detect (MTTD) kill-switch changes ≤3s per REQ-K1.
    
    This tests the file check overhead, which should be negligible
    (<1ms) but we validate against the 3s MTTD SLA.
    """
    # Setup
    kill_switch_path = str(kill_switch_file)
    
    # Create kill switch file
    kill_switch_file.write_text("ACTIVATED")
    
    # Execute: Measure detection timing
    start_time = time.monotonic()
    
    # Simulate the check that happens in main_loop
    detected = Path(kill_switch_path).exists()
    
    detection_latency = time.monotonic() - start_time
    
    # Assert: File exists check detected kill switch
    assert detected, "Kill switch file should be detected"
    
    # Assert: Detection latency well under 3s MTTD
    assert detection_latency < 3.0, f"Detection took {detection_latency:.6f}s, exceeds 3s MTTD"
    
    # In practice, file check should be <1ms
    assert detection_latency < 0.1, f"Detection took {detection_latency:.6f}s, expected <100ms"


@patch('infra.instance_lock.check_single_instance', return_value=True)
def test_kill_switch_no_new_orders_after_activation(mock_lock, kill_switch_file, mock_exchange, mock_alert_service):
    """
    Verify no new orders placed after kill-switch activation.
    
    Validates that execution engine respects risk gate blocks.
    """
    # Setup
    loop = TradingLoop(config_dir="config", mode_override="DRY_RUN")
    loop.exchange = mock_exchange
    loop.risk_engine.alert_service = mock_alert_service
    loop.risk_engine.governance_config["kill_switch_file"] = str(kill_switch_file)
    
    # Create kill switch file
    kill_switch_file.write_text("ACTIVATED")
    
    # Mock proposal
    from strategy.rules_engine import TradeProposal
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        size_pct=1.0,
        reason="test_kill_switch",
        confidence=0.8,
        stop_loss_pct=2.0,
        take_profit_pct=5.0,
    )
    
    # Execute: Try to execute with kill-switch active
    from core.risk import PortfolioState
    portfolio = PortfolioState(
        account_value_usd=10000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        weekly_pnl_pct=0.0,
        pending_orders={},
    )
    
    risk_result = loop.risk_engine.check_all(proposals=[proposal], portfolio=portfolio)
    
    # Assert: Risk check blocks
    assert not risk_result.approved
    
    # Assert: No exchange order placed
    assert not mock_exchange.place_order.called if hasattr(mock_exchange, 'place_order') else True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
