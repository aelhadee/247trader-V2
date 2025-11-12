"""Tests for conviction boosting and canary behaviour in the rules engine."""

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.universe import UniverseAsset, UniverseSnapshot  # noqa
from core.triggers import TriggerSignal  # noqa
from strategy.rules_engine import RulesEngine  # noqa


def _make_snapshot(asset: UniverseAsset) -> UniverseSnapshot:
    return UniverseSnapshot(
        timestamp=datetime.now(timezone.utc),
        regime="chop",
        tier_1_assets=[asset] if asset.tier == 1 else [],
        tier_2_assets=[asset] if asset.tier == 2 else [],
        tier_3_assets=[] if asset.tier in (1, 2) else [asset],
        excluded_assets=[],
        total_eligible=1,
    )


def _make_asset(symbol: str, tier: int) -> UniverseAsset:
    return UniverseAsset(
        symbol=symbol,
        tier=tier,
        allocation_min_pct=2.0 if tier == 2 else 5.0,
        allocation_max_pct=20.0 if tier == 2 else 40.0,
        volume_24h=35_000_000.0,
        spread_bps=25.0 if tier == 2 else 15.0,
        depth_usd=75_000.0,
        eligible=True,
    )


def _make_trigger(symbol: str, strength: float, confidence: float, qualifiers=None) -> TriggerSignal:
    qualifiers = qualifiers or {}
    return TriggerSignal(
        symbol=symbol,
        trigger_type="reversal",
        strength=strength,
        confidence=confidence,
        reason="test reversal",
        timestamp=datetime.now(timezone.utc),
        current_price=10.0,
        volatility=50.0,
        qualifiers=qualifiers,
    )


def test_reversal_requires_confirmations_for_conviction():
    asset = _make_asset("WLFI-USD", tier=2)
    snapshot = _make_snapshot(asset)

    weak_trigger = _make_trigger(symbol=asset.symbol, strength=0.25, confidence=0.60)

    strong_trigger = _make_trigger(
        symbol=asset.symbol,
        strength=0.25,
        confidence=0.60,
        qualifiers={
            "reversal_close_above_vwap_5m": True,
            "reversal_higher_low": True,
            "reversal_rsi_cross_50": True,
            "reversal_bounce_confirmed": True,
        },
    )

    engine = RulesEngine(config={})
    engine.policy.setdefault("triggers", {})["min_score"] = 0.1

    proposals_weak = engine.propose_trades(universe=snapshot, triggers=[weak_trigger], regime="chop")
    assert proposals_weak == [], "Weak reversal without confirmations should be rejected"

    proposals_strong = engine.propose_trades(universe=snapshot, triggers=[strong_trigger], regime="chop")
    assert len(proposals_strong) == 1, "Confirmed reversal should produce a proposal"
    proposal = proposals_strong[0]
    assert proposal.confidence >= 0.34
    boosts = proposal.metadata.get("conviction_boosts", [])
    assert "reversal_close_above_vwap_5m" in boosts
    assert "reversal_higher_low" in boosts
    assert "reversal_rsi_cross_50" in boosts
    assert "reversal_bounce_confirmed" in boosts


def test_canary_trade_emits_for_single_low_conviction_trigger():
    asset = _make_asset("BTC-USD", tier=1)
    snapshot = _make_snapshot(asset)

    trigger = _make_trigger(symbol=asset.symbol, strength=0.26, confidence=0.60)

    engine = RulesEngine(config={})
    engine.policy.setdefault("triggers", {})["min_score"] = 0.1

    proposals = engine.propose_trades(universe=snapshot, triggers=[trigger], regime="chop")
    assert len(proposals) == 1, "Canary trade should be emitted for single trigger in conviction window"
    proposal = proposals[0]
    assert "canary" in proposal.tags
    assert proposal.metadata.get("canary") is True
    assert proposal.metadata.get("order_type_override") == "limit_post_only"
    assert proposal.confidence < proposal.metadata.get("conviction_threshold", 0.34)
    assert 0.32 <= proposal.confidence < 0.34
