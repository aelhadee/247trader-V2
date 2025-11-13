"""Unit tests for RiskEngine exposure cap resizing helpers."""

from datetime import datetime, timezone

import pytest

from core.risk import PortfolioState, RiskEngine
from strategy.rules_engine import TradeProposal


@pytest.fixture
def portfolio() -> PortfolioState:
    return PortfolioState(
        account_value_usd=1_000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        weekly_pnl_pct=0.0,
        current_time=datetime.now(timezone.utc),
        pending_orders={},
    )


@pytest.fixture
def base_policy():
    return {
        "risk": {
            "max_position_size_pct": 5.0,
            "max_total_at_risk_pct": 100.0,
            "min_trade_notional_usd": 0.0,
        },
        "execution": {
            "min_notional_usd": 0.0,
            "allow_min_bump_in_risk": True,
        },
    }


def test_apply_caps_degrades_to_per_asset_limit(portfolio, base_policy):
    risk = RiskEngine(policy=base_policy)
    proposal = TradeProposal(
        symbol="AERO-USD",
        side="BUY",
        size_pct=10.0,
        reason="oversized",
        confidence=0.8,
    )

    kept, rejections, degraded = risk._apply_caps_to_proposals([proposal], portfolio, {})

    assert len(kept) == 1
    adjusted = kept[0]
    assert pytest.approx(adjusted.size_pct, rel=1e-3) == 5.0
    assert adjusted.metadata.get("risk_degraded") is True
    assert adjusted.metadata.get("risk_assigned_usd") == pytest.approx(50.0, rel=1e-3)
    assert degraded == 1
    assert rejections == {}
    assert risk.last_caps_snapshot.get("total_used_usd") == pytest.approx(50.0, rel=1e-3)


def test_apply_caps_bumps_to_min_notional_when_allowed(portfolio, base_policy):
    policy = dict(base_policy)
    policy["risk"] = dict(base_policy["risk"], min_trade_notional_usd=10.0)
    risk = RiskEngine(policy=policy)

    proposal = TradeProposal(
        symbol="WLFI-USD",
        side="BUY",
        size_pct=0.3,
        reason="tiny",
        confidence=0.9,
    )

    kept, rejections, degraded = risk._apply_caps_to_proposals([proposal], portfolio, {})

    assert len(kept) == 1
    adjusted = kept[0]
    assert adjusted.metadata.get("risk_min_bump") is True
    assert adjusted.metadata.get("risk_assigned_usd") == pytest.approx(10.0, rel=1e-3)
    assert pytest.approx(adjusted.size_pct, rel=1e-3) == 1.0
    assert degraded == 0
    assert rejections == {}


def test_apply_caps_rejects_when_no_capacity(portfolio, base_policy):
    policy = dict(base_policy)
    risk = RiskEngine(policy=policy)

    # Fully consume the per-asset cap with an existing position
    portfolio.open_positions["XLM-USD"] = {"usd": 50.0, "units": 100.0}

    proposal = TradeProposal(
        symbol="XLM-USD",
        side="BUY",
        size_pct=1.0,
        reason="no_capacity",
        confidence=0.4,
    )

    kept, rejections, degraded = risk._apply_caps_to_proposals([proposal], portfolio, {})

    assert kept == []
    assert rejections == {"XLM-USD": ["no_capacity"]}
    assert degraded == 0
    assert risk.last_caps_snapshot.get("per_asset_remaining_usd", {}).get("XLM-USD") == pytest.approx(0.0)


def test_apply_caps_skips_sells_and_preserves_size(portfolio, base_policy):
    risk = RiskEngine(policy=base_policy)

    proposal = TradeProposal(
        symbol="ADA-USD",
        side="SELL",
        size_pct=7.5,
        reason="reduce",
        confidence=0.2,
    )

    kept, rejections, degraded = risk._apply_caps_to_proposals([proposal], portfolio, {})

    assert len(kept) == 1
    assert kept[0].size_pct == 7.5
    assert degraded == 0
    assert rejections == {}
*** End Patch