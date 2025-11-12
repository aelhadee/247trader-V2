"""Regression tests for execution fill reconciliation."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from core import order_state
from core.execution import ExecutionEngine
from infra.state_store import StateStore


@pytest.fixture
def fresh_order_state_machine():
    """Provide a clean singleton for each test."""
    order_state._state_machine = order_state.OrderStateMachine()
    yield order_state._state_machine
    order_state._state_machine = order_state.OrderStateMachine()


@pytest.fixture
def state_store(tmp_path):
    state_file = tmp_path / "fill_state.json"
    store = StateStore(state_file=str(state_file))
    store.reset(full=True)
    return store


@pytest.fixture
def execution_engine(state_store, fresh_order_state_machine):
    exchange = MagicMock()
    exchange.read_only = False
    exchange.list_open_orders.return_value = []
    exchange.get_order_status.return_value = {}
    engine = ExecutionEngine(mode="LIVE", exchange=exchange, policy={}, state_store=state_store)
    # Inject the fresh order state machine to avoid shared state across tests
    engine.order_state_machine = fresh_order_state_machine
    return engine


def test_reconcile_fills_uses_base_units(execution_engine, state_store):
    """Market fills expressed in quote currency should be converted to base units."""
    exchange = execution_engine.exchange

    price = Decimal("0.28")
    quote_notional = Decimal("4.970355")
    base_expected = quote_notional / price

    fill_payload = {
        "order_id": "abcd-1234",
        "product_id": "XLM-USD",
        "price": str(price),
        # Market orders on Coinbase Advanced often return quote size only.
        "size_in_quote": str(quote_notional),
        "commission": "0.01",
        "side": "BUY",
        "trade_time": "2025-11-12T14:00:00Z",
    }

    exchange.list_fills.return_value = [fill_payload]

    summary = execution_engine.reconcile_fills(lookback_minutes=5)

    state = state_store.load()
    positions = state.get("positions", {})
    assert "XLM-USD" in positions
    position = positions["XLM-USD"]

    assert position["quantity"] == pytest.approx(float(base_expected))
    assert position["base_qty"] == pytest.approx(float(base_expected))
    assert position["entry_price"] == pytest.approx(float(price))
    assert position["entry_value_usd"] == pytest.approx(float(quote_notional))
    assert summary["fills_processed"] == 1


def test_reconcile_fills_records_fees(execution_engine, state_store):
    """Recorded fills should carry commission values into state."""
    exchange = execution_engine.exchange

    fill_payload = {
        "order_id": "efgh-5678",
        "product_id": "ADA-USD",
        "price": "0.55",
        "size_in_quote": "5.00",
        "commission": "0.0125",
        "side": "BUY",
        "trade_time": "2025-11-12T15:00:00Z",
    }

    exchange.list_fills.return_value = [fill_payload]

    execution_engine.reconcile_fills(lookback_minutes=5)

    state = state_store.load()
    position = state["positions"]["ADA-USD"]
    assert position["fees_paid"] == pytest.approx(0.0125)
    assert position["base_qty"] == pytest.approx(5.0 / 0.55, rel=1e-6)
