"""Regression tests for per-symbol position sizing checks."""

from datetime import datetime, timezone

import pytest

from core.risk import PortfolioState, RiskEngine
from strategy.rules_engine import TradeProposal


def _portfolio(open_usd: float, pending_buy: float = 0.0) -> PortfolioState:
    open_positions = {
        "XLM-USD": {
            "usd": open_usd,
            "units": open_usd / 0.28 if open_usd else 0.0,
        }
    }
    pending_orders = {"buy": {}, "sell": {}}
    if pending_buy:
        pending_orders["buy"]["XLM-USD"] = pending_buy

    return PortfolioState(
        account_value_usd=10_000.0,
        open_positions=open_positions,
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        weekly_pnl_pct=0.0,
        current_time=datetime.now(timezone.utc),
        pending_orders=pending_orders,
    )


@pytest.fixture
def risk_engine():
    policy = {
        "risk": {
            "min_trade_notional_usd": 5.0,
            "allow_adds_when_over_cap": True,
        },
        "position_sizing": {
            "allow_pyramiding": False,
        },
    }
    return RiskEngine(policy=policy)


def test_existing_position_can_be_topped_up_when_no_pending(risk_engine):
    portfolio = _portfolio(open_usd=500.0, pending_buy=0.0)
    proposal = TradeProposal(symbol="XLM-USD", side="BUY", size_pct=1.0, confidence=0.6, reason="test")

    result = risk_engine._check_position_size(proposal, portfolio, "chop")

    assert result.approved, result.reason


def test_pending_buy_blocks_add_when_pyramiding_disabled(risk_engine):
    portfolio = _portfolio(open_usd=500.0, pending_buy=100.0)
    proposal = TradeProposal(symbol="XLM-USD", side="BUY", size_pct=1.0, confidence=0.6, reason="test")

    result = risk_engine._check_position_size(proposal, portfolio, "chop")

    assert not result.approved
    assert any("pending_buy_exists" in check for check in result.violated_checks)


def test_position_size_guard_respects_combined_exposure(risk_engine):
    """Adds should still obey max position caps when exposure would exceed limits."""
    # Existing exposure already at 5% of NAV
    existing_value = 500.0
    portfolio = _portfolio(open_usd=existing_value)

    proposal = TradeProposal(symbol="XLM-USD", side="BUY", size_pct=1.0, confidence=0.6, reason="test")

    result = risk_engine._check_position_size(proposal, portfolio, "chop")

    assert not result.approved
    assert any("position_size_with_pending" in check for check in result.violated_checks)
