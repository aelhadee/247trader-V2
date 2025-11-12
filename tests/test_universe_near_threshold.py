"""Tests for near-threshold overrides in UniverseManager."""

from datetime import datetime, timezone

from core.exchange_coinbase import OrderbookSnapshot, Quote
from core.universe import UniverseManager


def _build_manager(tolerance: float = 0.10, cap: int = 2) -> UniverseManager:
    manager = UniverseManager.__new__(UniverseManager)
    manager.config = {
        "universe": {
            "near_threshold": {
                "override_enabled": True,
                "tolerance_pct": tolerance,
                "max_overrides_per_tier": cap,
            }
        }
    }
    manager._near_threshold_cfg = manager.config["universe"]["near_threshold"]
    manager._near_threshold_usage = {"tier1": 0, "tier2": 0, "tier3": 0}
    return manager


def test_volume_near_threshold_override_allows_asset():
    manager = _build_manager()

    quote = Quote(
        symbol="TEST-USD",
        bid=100.0,
        ask=100.2,
        mid=100.1,
        spread_bps=25.0,
        last=100.0,
        volume_24h=9_500_000.0,
        timestamp=datetime.now(timezone.utc),
    )
    orderbook = OrderbookSnapshot(
        symbol="TEST-USD",
        bid_depth_usd=150_000.0,
        ask_depth_usd=150_000.0,
        total_depth_usd=150_000.0,
        bid_levels=5,
        ask_levels=5,
        timestamp=datetime.now(timezone.utc),
    )

    global_cfg = {
        "min_24h_volume_usd": 10_000_000.0,
        "max_spread_bps": 80,
        "min_orderbook_depth_usd_t1": 100_000.0,
    }
    tier_cfg = {}

    eligible, reason = manager._check_liquidity(quote, orderbook, global_cfg, tier_cfg, tier=1)

    assert eligible
    assert reason is None
    assert manager._near_threshold_usage["tier1"] == 1


def test_depth_near_threshold_override_allows_asset():
    manager = _build_manager()

    quote = Quote(
        symbol="DEPTH-USD",
        bid=50.0,
        ask=50.1,
        mid=50.05,
        spread_bps=20.0,
        last=50.0,
        volume_24h=20_000_000.0,
        timestamp=datetime.now(timezone.utc),
    )
    orderbook = OrderbookSnapshot(
        symbol="DEPTH-USD",
        bid_depth_usd=95_000.0,
        ask_depth_usd=95_000.0,
        total_depth_usd=95_000.0,
        bid_levels=4,
        ask_levels=4,
        timestamp=datetime.now(timezone.utc),
    )

    global_cfg = {
        "min_24h_volume_usd": 5_000_000.0,
        "max_spread_bps": 80,
        "min_orderbook_depth_usd_t1": 100_000.0,
    }
    tier_cfg = {}

    eligible, reason = manager._check_liquidity(quote, orderbook, global_cfg, tier_cfg, tier=1)

    assert eligible
    assert reason is None
    assert manager._near_threshold_usage["tier1"] == 1
