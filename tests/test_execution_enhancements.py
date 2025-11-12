import datetime
from unittest.mock import MagicMock

import requests

from core.execution import ExecutionEngine, ExecutionResult


def _make_policy(cooldown_seconds: int = 0):
    return {
        "risk": {"min_trade_notional_usd": 1},
        "execution": {"failed_order_cooldown_seconds": cooldown_seconds},
        "microstructure": {"max_expected_slippage_bps": 50, "max_quote_age_seconds": 30},
    }


def test_convert_route_disabled_sets_flag_and_skips_denylist():
    exchange = MagicMock()
    exchange.read_only = False

    response = requests.Response()
    response.status_code = 403
    response._content = b'{"message": "route is disabled"}'

    http_error = requests.exceptions.HTTPError("route disabled", response=response)
    exchange.create_convert_quote.side_effect = http_error

    engine = ExecutionEngine(mode="LIVE", exchange=exchange, policy=_make_policy())
    # Avoid touching real order state machine during test
    engine.order_state_machine = MagicMock()

    result = engine.convert_asset(
        from_currency="PEPE",
        to_currency="USDC",
        amount="1.0",
        from_account_uuid="from-uuid",
        to_account_uuid="to-uuid",
    )

    assert result["success"] is False
    assert result["error"] == "convert_api_disabled"
    assert engine._convert_api_disabled is True
    assert engine.can_convert("PEPE", "USDC") is False
    assert ("PEPE", "USDC") not in engine._convert_denylist
    assert "retry_after_seconds" in result


def test_execute_bypass_failed_order_cooldown():
    exchange = MagicMock()
    exchange.read_only = False

    engine = ExecutionEngine(mode="LIVE", exchange=exchange, policy=_make_policy(cooldown_seconds=60))
    engine.order_state_machine = MagicMock()
    engine._execute_live = MagicMock(
        return_value=ExecutionResult(
            success=True,
            order_id="test",
            filled_size=1.0,
            filled_price=1.0,
            fees=0.0,
            slippage_bps=0.0,
            route="live_limit_post_only",
        )
    )

    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    engine._last_fail["BTC"] = now_ts

    skipped = engine.execute("BTC-USD", "SELL", 10.0)
    assert skipped.success is False
    assert skipped.route == "skipped_cooldown"
    engine._execute_live.assert_not_called()

    engine._execute_live.reset_mock()

    result = engine.execute(
        "BTC-USD",
        "SELL",
        10.0,
        bypass_failed_order_cooldown=True,
        bypass_slippage_budget=True,
    )

    engine._execute_live.assert_called_once()
    assert result.success is True


def test_convert_route_retries_after_backoff():
    exchange = MagicMock()
    exchange.read_only = False

    engine = ExecutionEngine(mode="LIVE", exchange=exchange, policy=_make_policy())
    engine.order_state_machine = MagicMock()

    # Simulate prior disablement in the past
    engine._convert_api_disabled = True
    engine._convert_api_disabled_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=engine.convert_api_retry_seconds + 5
    )

    exchange.create_convert_quote.return_value = {
        "trade": {
            "id": "trade-1",
            "exchange_rate": {"value": "1"},
            "total_fee": {"amount": {"value": "0"}},
        }
    }
    exchange.commit_convert_trade.return_value = {"trade": {"status": "SETTLED"}}

    result = engine.convert_asset(
        from_currency="PEPE",
        to_currency="USDC",
        amount="1",
        from_account_uuid="from-uuid",
        to_account_uuid="to-uuid",
    )

    assert result["success"] is True
    assert engine._convert_api_disabled is False