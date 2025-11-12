"""Tests for global risk exposure handling with managed vs external positions."""

import copy
from datetime import datetime, timezone
from typing import Dict, Optional

import pytest

from core.risk import PortfolioState, RiskEngine
from strategy.rules_engine import TradeProposal


@pytest.fixture
def base_policy():
    return {
        "risk": {
            "max_total_at_risk_pct": 15.0,
            "max_position_size_pct": 5.0,
            "max_per_asset_pct": 5.0,
            "min_position_size_pct": 0.5,
            "max_per_theme_pct": {},
            "daily_stop_pnl_pct": -3.0,
            "weekly_stop_pnl_pct": -7.0,
            "max_drawdown_pct": 10.0,
            "max_trades_per_day": 10,
            "max_trades_per_hour": 4,
            "max_new_trades_per_hour": 4,
            "cooldown_after_loss_trades": 3,
            "cooldown_minutes": 60,
            "per_symbol_cooldown_enabled": False,
            "per_symbol_cooldown_minutes": 0,
            "per_symbol_cooldown_after_stop": 0,
            "min_trade_notional_usd": 5.0,
            "stop_loss_pct": 8.0,
            "take_profit_pct": 15.0,
            "count_external_positions": False,
            "managed_position_tag": "247trader",
            "external_exposure_buffer_pct": 5.0,
        },
        "position_sizing": {},
        "microstructure": {},
        "regime": {},
        "governance": {},
        "circuit_breakers": {},
    }


def _portfolio(account_value: float, positions: dict, managed: Optional[Dict[str, bool]] = None) -> PortfolioState:
    return PortfolioState(
        account_value_usd=account_value,
        open_positions=positions,
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=datetime.now(timezone.utc),
        weekly_pnl_pct=0.0,
        pending_orders={},
    managed_positions=managed or {},
    )


def _proposal(symbol: str, side: str, size_pct: float) -> TradeProposal:
    return TradeProposal(
        symbol=symbol,
        side=side,
        size_pct=size_pct,
        reason="test",
        confidence=0.5,
    )


def test_external_exposure_ignored_when_flag_disabled(base_policy):
    policy = copy.deepcopy(base_policy)
    policy["risk"]["count_external_positions"] = False

    risk = RiskEngine(policy=policy)
    portfolio = _portfolio(
        1000.0,
        {"WLFI-USD": {"usd": 900.0}},
        managed={},
    )
    proposal = _proposal("WLFI-USD", "BUY", 2.0)

    result = risk._check_global_at_risk([proposal], portfolio)
    assert result.approved, result.reason


def test_external_buffer_applied_when_enabled(base_policy):
    policy = copy.deepcopy(base_policy)
    policy["risk"].update({
        "count_external_positions": True,
        "external_exposure_buffer_pct": 5.0,
    })

    risk = RiskEngine(policy=policy)
    # External exposure of 4% should be ignored due to buffer
    portfolio = _portfolio(
        1000.0,
        {"WLFI-USD": {"usd": 40.0}},
        managed={},
    )
    proposal = _proposal("WLFI-USD", "BUY", 10.0)

    result = risk._check_global_at_risk([proposal], portfolio)
    assert result.approved, result.reason


def test_external_exposure_counts_beyond_buffer(base_policy):
    policy = copy.deepcopy(base_policy)
    policy["risk"].update({
        "count_external_positions": True,
        "external_exposure_buffer_pct": 5.0,
    })

    risk = RiskEngine(policy=policy)
    portfolio = _portfolio(
        1000.0,
        {"WLFI-USD": {"usd": 200.0}},  # 20% external exposure
        managed={},
    )
    proposal = _proposal("WLFI-USD", "BUY", 0.5)

    result = risk._check_global_at_risk([proposal], portfolio)
    assert not result.approved
    assert "max_total_at_risk_pct" in result.violated_checks
