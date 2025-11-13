"""Tests for execution plan helpers and adaptive TTL logic."""

from datetime import datetime, timezone

import pytest

from core.execution import ExecutionEngine
from core.exchange_coinbase import Quote


class DummyExchange:
    read_only = True


@pytest.fixture
def base_policy():
    return {
        "execution": {
            "default_order_type": "limit_post_only",
            "maker_first": True,
            "maker_max_reprices": 1,
            "maker_max_ttl_sec": 12,
            "maker_first_min_ttl_sec": 6,
            "maker_retry_min_ttl_sec": 3,
            "maker_reprice_decay": 0.7,
            "small_order_market_threshold_usd": 6.0,
            "taker_fallback": True,
            "prefer_ioc": True,
            "taker_max_slippage_bps": {"T1": 20, "default": 60},
            "allow_min_bump_in_risk": True,
        },
        "risk": {
            "min_trade_notional_usd": 5.0,
        },
        "microstructure": {},
        "portfolio_management": {},
    }


@pytest.fixture
def engine(base_policy):
    return ExecutionEngine(mode="DRY_RUN", exchange=DummyExchange(), policy=base_policy)


def test_plan_includes_maker_and_fallback(engine):
    plan = engine._build_execution_plan(force_order_type=None, size_usd=20.0)
    maker_steps = [step for step in plan if step["order_type"] == "limit_post_only"]
    fallback_steps = [step for step in plan if step.get("mode") == "fallback"]

    assert len(maker_steps) == 2  # initial attempt + single reprice
    assert len(fallback_steps) == 1


def test_plan_small_order_goes_market(engine):
    plan = engine._build_execution_plan(force_order_type=None, size_usd=5.0)
    assert len(plan) == 1
    assert plan[0]["order_type"] == "market"


def test_adaptive_ttl_respects_bounds(engine):
    quote = Quote(
        symbol="TEST-USD",
        bid=1.0000,
        ask=1.0002,
        mid=1.0001,
        spread_bps=2.0,
        last=1.0001,
        volume_24h=1_000_000.0,
        timestamp=datetime.now(timezone.utc),
    )

    first_ttl = engine._adaptive_maker_ttl(quote, attempt_index=0)
    retry_ttl = engine._adaptive_maker_ttl(quote, attempt_index=1)

    assert first_ttl >= engine.maker_first_min_ttl_seconds
    assert engine.maker_retry_min_ttl_seconds <= retry_ttl <= first_ttl


def test_taker_slippage_budget_enforced(engine):
    assert not engine._is_taker_slippage_allowed(25.0, tier=1)
    assert engine._is_taker_slippage_allowed(35.0, tier=2)
*** End Patch