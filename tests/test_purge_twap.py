import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.execution import ExecutionResult
from runner.main_loop import TradingLoop


def _make_quote(price: float = 1.0):
    """Helper to build a simple quote namespace."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return SimpleNamespace(mid=price, bid=price, ask=price, timestamp=now)


def test_purge_liquidation_uses_maker_limit_orders():
    """Purge liquidation should route via maker TWAP with slippage guardrails."""
    loop = TradingLoop.__new__(TradingLoop)

    loop.mode = "LIVE"
    loop.policy_config = {
        "portfolio_management": {
            "min_liquidation_value_usd": 0,
            "max_liquidations_per_cycle": 1,
            "purge_execution": {
                "slice_usd": 15,
                "replace_seconds": 1,
                "max_duration_seconds": 5,
                "poll_interval_seconds": 0.1,
            },
        },
        "risk": {"min_trade_notional_usd": 5},
        "execution": {},
    }
    loop.universe_config = {
        "tiers": {
            "tier_1_core": {"symbols": ["BAD-USD"]},
            "tier_2_rotational": {"symbols": []},
            "tier_3_event_driven": {"symbols": []},
        }
    }
    loop.state_store = MagicMock()
    loop._init_portfolio_state = MagicMock(return_value={})
    loop.portfolio = {}

    loop._require_accounts = MagicMock(
        return_value=[
            {
                "currency": "BAD",
                "available_balance": {"value": "30"},
                "hold": {"value": "0"},
            }
        ]
    )

    exchange = MagicMock()
    exchange.get_quote.return_value = _make_quote(1.0)
    exchange.get_order_status.return_value = {
        "status": "FILLED",
        "filled_size": "1.0",
        "average_filled_price": "1.0",
        "filled_value": "1.0",
    }
    exchange.list_fills.return_value = [
        {
            "size": "1.0",
            "price": "1.0",
            "commission": "0.0",
            "size_in_quote": "1.0",
            "side": "SELL",
            "product_id": "BAD-USD",
        }
    ]
    loop.exchange = exchange

    order_state_machine = MagicMock()
    loop.executor = MagicMock()
    loop.executor.min_notional_usd = 15
    loop.executor.generate_client_order_id = MagicMock(return_value="coid_test")
    loop.executor.order_state_machine = order_state_machine
    loop.executor._close_order_in_state_store = MagicMock()
    loop.executor.exchange = exchange

    execution_result = ExecutionResult(
        success=True,
        order_id="order-123",
        symbol="BAD-USD",
        side="SELL",
        filled_size=0.0,
        filled_price=0.0,
        fees=0.0,
        slippage_bps=0.0,
        route="live_limit_post_only",
    )
    loop.executor.execute.return_value = execution_result

    universe = SimpleNamespace(
        excluded_assets=["BAD-USD"],
        get_all_eligible=lambda: [],
    )

    with patch("runner.main_loop.time.sleep", return_value=None):
        loop._purge_ineligible_holdings(universe)

    assert loop.executor.execute.call_count >= 1
    for call in loop.executor.execute.call_args_list:
        kwargs = call.kwargs
        assert kwargs.get("force_order_type") == "limit_post_only"
        assert kwargs.get("skip_liquidity_checks", False) is False
        assert kwargs.get("tier") == 1
    assert kwargs.get("bypass_slippage_budget") is True
    assert kwargs.get("bypass_failed_order_cooldown") is True


def test_purge_retries_after_cancelled_slice():
    """TWAP liquidation should retry when a slice is cancelled with zero fills."""
    loop = TradingLoop.__new__(TradingLoop)

    loop.mode = "LIVE"
    loop.policy_config = {
        "portfolio_management": {
            "min_liquidation_value_usd": 0,
            "max_liquidations_per_cycle": 1,
            "purge_execution": {
                "slice_usd": 15,
                "replace_seconds": 1,
                "max_duration_seconds": 5,
                "poll_interval_seconds": 0.1,
                "max_residual_usd": 5,
                "max_consecutive_no_fill": 3,
            },
        },
        "risk": {"min_trade_notional_usd": 5},
        "execution": {},
    }
    loop.universe_config = {
        "tiers": {
            "tier_1_core": {"symbols": ["BAD-USD"]},
            "tier_2_rotational": {"symbols": []},
            "tier_3_event_driven": {"symbols": []},
        }
    }
    loop.state_store = MagicMock()
    loop._init_portfolio_state = MagicMock(return_value={})
    loop.portfolio = {}

    loop._require_accounts = MagicMock(
        return_value=[
            {
                "currency": "BAD",
                "available_balance": {"value": "1000"},
                "hold": {"value": "0"},
            }
        ]
    )

    exchange = MagicMock()
    exchange.get_quote.return_value = _make_quote(0.175)
    loop.exchange = exchange

    loop.executor = MagicMock()
    loop.executor.min_notional_usd = 15
    loop.executor.order_state_machine = MagicMock()
    loop.executor._close_order_in_state_store = MagicMock()
    loop.executor.exchange = exchange

    loop.executor.execute.side_effect = [
        ExecutionResult(
            success=True,
            order_id="order-1",
            symbol="BAD-USD",
            side="SELL",
            filled_size=0.0,
            filled_price=0.0,
            fees=0.0,
            slippage_bps=0.0,
            route="live_limit_post_only",
        ),
        ExecutionResult(
            success=True,
            order_id="order-2",
            symbol="BAD-USD",
            side="SELL",
            filled_size=0.0,
            filled_price=0.0,
            fees=0.0,
            slippage_bps=0.0,
            route="live_limit_post_only",
        ),
    ]

    loop._await_twap_slice = MagicMock(
        side_effect=[
            (0.0, 0.0, 0.0, [], "CANCELED"),
            (15.0, 86.0, 0.05, [], "FILLED"),
        ]
    )

    with patch("runner.main_loop.time.sleep", return_value=None):
        result = loop._sell_via_market_order(
            currency="BAD",
            balance=1000,
            usd_target=15,
            tier=1,
            preferred_pair="BAD-USD",
        )

    assert result is True
    assert loop.executor.execute.call_count == 2
    assert loop._await_twap_slice.call_count == 2
    exchange.get_quote.assert_called()
