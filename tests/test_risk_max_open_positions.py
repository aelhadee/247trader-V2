"""Tests for max-open-position enforcement with pending orders counted."""

from datetime import datetime, timezone

import pytest

from core.risk import PortfolioState, RiskEngine
from strategy.rules_engine import TradeProposal


@pytest.fixture
def base_policy():
    return {
        "risk": {
            "min_trade_notional_usd": 10.0,
        },
        "strategy": {
            "max_open_positions": 1,
            "prefer_add_to_existing": True,
        },
    }


def _portfolio(pending_buy: dict, open_positions: dict | None = None) -> PortfolioState:
    return PortfolioState(
        account_value_usd=10_000.0,
        open_positions=open_positions or {},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        weekly_pnl_pct=0.0,
        current_time=datetime.now(timezone.utc),
        pending_orders={"buy": pending_buy, "sell": {}},
    )


def test_pending_orders_consume_capacity(base_policy):
    risk_engine = RiskEngine(policy=base_policy)
    portfolio = _portfolio({"SOL-USD": 250.0})

    proposals = [
        TradeProposal(symbol="SOL-USD", side="buy", size_pct=2.0, reason="", confidence=0.6),
        TradeProposal(symbol="ADA-USD", side="buy", size_pct=2.0, reason="", confidence=0.7),
    ]

    result = risk_engine._check_max_open_positions(proposals, portfolio)

    assert result.approved
    assert result.filtered_proposals is not None
    kept_symbols = {proposal.symbol for proposal in result.filtered_proposals}
    assert kept_symbols == {"SOL-USD"}


def test_capacity_respects_pending_new_slots(base_policy):
    policy = {**base_policy, "strategy": {**base_policy["strategy"], "max_open_positions": 3, "max_new_positions_per_cycle": 1}}
    risk_engine = RiskEngine(policy=policy)

    open_positions = {"ETH-USD": {"usd": 500.0}}
    pending = {"SOL-USD": 200.0}
    portfolio = _portfolio(pending, open_positions)

    proposals = [
        TradeProposal(symbol="ADA-USD", side="buy", size_pct=2.0, reason="", confidence=0.9),
        TradeProposal(symbol="DOGE-USD", side="buy", size_pct=2.0, reason="", confidence=0.2),
        TradeProposal(symbol="SOL-USD", side="buy", size_pct=1.0, reason="", confidence=0.8),
    ]

    result = risk_engine._check_max_open_positions(proposals, portfolio)

    assert result.approved
    assert result.filtered_proposals is not None
    # Only one new symbol (highest confidence) should remain besides existing/pending additions
    kept_symbols = {proposal.symbol for proposal in result.filtered_proposals}
    assert "ADA-USD" in kept_symbols
    assert "DOGE-USD" not in kept_symbols