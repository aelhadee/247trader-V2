from types import SimpleNamespace
from unittest.mock import MagicMock

from core.risk import PortfolioState
from runner.main_loop import TradingLoop


def test_auto_trim_to_risk_cap_converts_excess_exposure():
    loop = object.__new__(TradingLoop)
    loop.mode = "LIVE"
    loop.policy_config = {
        "risk": {"max_total_at_risk_pct": 15.0},
        "portfolio_management": {
            "auto_trim_to_risk_cap": True,
            "trim_target_buffer_pct": 1.0,
            "trim_tolerance_pct": 0.25,
            "trim_min_value_usd": 10.0,
            "trim_max_liquidations": 5,
            "trim_preferred_quotes": ["USDC", "USD", "USDT"],
            "trim_slippage_buffer_pct": 5.0,
        },
    }

    candidate = {
        "currency": "PEPE",
        "account_uuid": "pepe-uuid",
        "balance": 1000.0,
        "value_usd": 440.0,
        "price": 0.44,
        "pair": "PEPE-USD",
    }

    loop.executor = SimpleNamespace(
        min_notional_usd=5.0,
        get_liquidation_candidates=MagicMock(return_value=[candidate]),
        convert_asset=MagicMock(return_value={"success": True}),
        can_convert=MagicMock(return_value=True),
    )

    loop.portfolio = PortfolioState(
        account_value_usd=500.0,
        open_positions={"PEPE-USD": {"usd": 440.0, "units": 1000.0}},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        pending_orders={"buy": {}},
    )

    def _require_accounts(_):
        return [
            {
                "currency": "PEPE",
                "uuid": "pepe-uuid",
                "available_balance": {"value": 1000},
            },
            {
                "currency": "USDC",
                "uuid": "usdc-uuid",
                "available_balance": {"value": 50},
            },
        ]

    loop._require_accounts = MagicMock(side_effect=_require_accounts)
    loop._sell_via_market_order = MagicMock(return_value=False)
    loop._infer_tier_from_config = MagicMock(return_value=2)

    trimmed_portfolio = PortfolioState(
        account_value_usd=500.0,
        open_positions={"PEPE-USD": {"usd": 40.0, "units": 100.0}},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        pending_orders={"buy": {}},
    )

    loop._reconcile_exchange_state = MagicMock()
    loop._init_portfolio_state = MagicMock(return_value=trimmed_portfolio)

    result = TradingLoop._auto_trim_to_risk_cap(loop)

    assert result is True
    loop.executor.get_liquidation_candidates.assert_called_once()
    loop.executor.convert_asset.assert_called_once()
    loop._init_portfolio_state.assert_called_once()
    assert loop.portfolio is trimmed_portfolio
