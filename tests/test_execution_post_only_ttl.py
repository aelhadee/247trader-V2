"""Tests for post-only TTL enforcement in the execution engine."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from core.execution import ExecutionEngine
from core.order_state import OrderStatus, get_order_state_machine


@pytest.fixture(autouse=True)
def reset_order_state_machine():
    """Ensure the shared order state machine is clean between tests."""
    osm = get_order_state_machine()
    osm.orders.clear()
    yield
    osm.orders.clear()


def _build_engine(exchange: Mock, ttl_seconds: int = 1) -> ExecutionEngine:
    policy = {
        "risk": {"min_trade_notional_usd": 10.0},
        "execution": {
            "default_order_type": "limit_post_only",
            "maker_fee_bps": 40.0,
            "taker_fee_bps": 60.0,
            "preferred_quote_currencies": ["USDC", "USD"],
            "auto_convert_preferred_quote": False,
            "clamp_small_trades": True,
            "small_order_market_threshold_usd": 0.0,
            "failed_order_cooldown_seconds": 0,
            "cancel_after_seconds": 60,
            "post_only_ttl_seconds": ttl_seconds,
            "post_trade_reconcile_wait_seconds": 0.5,
        },
        "microstructure": {
            "max_expected_slippage_bps": 50.0,
            "max_quote_age_seconds": 30,
        },
        "portfolio_management": {},
    }

    exchange.read_only = False
    engine = ExecutionEngine(mode="LIVE", exchange=exchange, policy=policy)
    engine.enforce_product_constraints = MagicMock(
        return_value={"success": True, "adjusted_size_usd": 20.0, "fee_adjusted": False}
    )
    return engine


def _patch_time(monkeypatch: pytest.MonkeyPatch, sequence: list[float]) -> None:
    """Patch time.monotonic to return deterministic values and disable sleep."""

    values = iter(sequence)

    def fake_monotonic() -> float:
        try:
            return next(values)
        except StopIteration:
            return sequence[-1]

    monkeypatch.setattr("core.execution.time.monotonic", fake_monotonic)
    monkeypatch.setattr("core.execution.time.sleep", lambda _x: None)


def _default_preview():
    return {
        "success": True,
        "estimated_price": 10.0,
        "estimated_size": 2.0,
        "estimated_slippage_bps": 5.0,
        "expected_fill_price": 10.0,
    }


def _quote() -> SimpleNamespace:
    return SimpleNamespace(
        bid=9.9,
        ask=10.1,
        mid=10.0,
        spread_bps=20.0,
        timestamp=datetime.now(timezone.utc),
    )


def test_post_only_ttl_cancels_unfilled_order(monkeypatch: pytest.MonkeyPatch):
    exchange = Mock()
    exchange.get_quote.return_value = _quote()
    exchange.preview_order.return_value = _default_preview()
    exchange.place_order.return_value = {
        "success": True,
        "order_id": "ttl-test-1",
        "status": "OPEN",
        "fills": [],
    }

    status_sequence = [
        {"status": "OPEN", "filled_size": "0"},
        {"status": "OPEN", "filled_size": "0"},
    ]
    exchange.get_order_status.side_effect = lambda _order_id: (
        status_sequence.pop(0) if status_sequence else {"status": "OPEN", "filled_size": "0"}
    )
    exchange.cancel_order.return_value = {"success": True}
    exchange.list_fills.return_value = []

    engine = _build_engine(exchange, ttl_seconds=1)
    engine.preview_order = MagicMock(return_value=_default_preview())

    _patch_time(monkeypatch, [0.0, 0.4, 0.9, 1.3])

    result = engine.execute(symbol="SOL-USD", side="SELL", size_usd=20.0)

    assert not result.success
    assert result.route == "live_limit_post_only_timeout"
    assert "TTL" in (result.error or "")
    exchange.cancel_order.assert_called_once_with("ttl-test-1")

    assert any(
        order.status == OrderStatus.CANCELED.value for order in engine.order_state_machine.orders.values()
    )


def test_post_only_ttl_skips_when_filled(monkeypatch: pytest.MonkeyPatch):
    exchange = Mock()
    exchange.get_quote.return_value = _quote()
    exchange.preview_order.return_value = _default_preview()
    exchange.place_order.return_value = {
        "success": True,
        "order_id": "ttl-test-2",
        "status": "OPEN",
        "fills": [],
    }

    exchange.get_order_status.side_effect = lambda _order_id: {
        "status": "FILLED",
        "filled_size": "2",
    }
    exchange.list_fills.return_value = [
        {"size": "2", "price": "10", "commission": "0.1"}
    ]

    engine = _build_engine(exchange, ttl_seconds=1)
    engine.preview_order = MagicMock(return_value=_default_preview())

    _patch_time(monkeypatch, [0.0, 0.3, 0.6])

    result = engine.execute(symbol="SOL-USD", side="SELL", size_usd=20.0)

    assert result.success
    assert result.route == "live_limit_post_only"
    assert result.filled_size == pytest.approx(2.0)
    assert result.filled_price == pytest.approx(10.0)
    exchange.cancel_order.assert_not_called()