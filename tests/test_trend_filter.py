from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.exchange_coinbase import OHLCV
from core.triggers import TriggerEngine, TriggerSignal
from core.universe import UniverseAsset
from strategy.rules_engine import RulesEngine, TradeProposal


def _build_candles(prices, symbol="WLFI-USD"):
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = []
    for idx, price in enumerate(prices):
        timestamp = base_time + timedelta(hours=idx)
        candles.append(
            OHLCV(
                symbol=symbol,
                timestamp=timestamp,
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=1_000_000.0,
            )
        )
    return candles


def _make_asset(symbol="WLFI-USD"):
    return UniverseAsset(
        symbol=symbol,
        tier=1,
        allocation_min_pct=5.0,
        allocation_max_pct=15.0,
        volume_24h=1_000_000_000.0,
        spread_bps=5.0,
        depth_usd=5_000_000.0,
        eligible=True,
    )


def _make_trigger_engine(trend_cfg):
    base_regime = {
        "pct_change_15m": 2.0,
        "pct_change_60m": 4.0,
        "volume_ratio_1h": 1.9,
        "atr_filter_min_mult": 1.1,
    }
    signals_payload = {
        "triggers": {
            "regime_thresholds": {
                "chop": base_regime,
                "bull": base_regime,
                "bear": base_regime,
            },
            "reversal_confirm": {},
            "trend_filter": trend_cfg,
        }
    }
    policy_payload = {"triggers": {}, "circuit_breakers": {}}

    exchange = MagicMock()

    with patch("core.triggers.get_exchange", return_value=exchange):
        with patch("yaml.safe_load", side_effect=[signals_payload, policy_payload]):
            engine = TriggerEngine()

    return engine, exchange


def test_reversal_trend_filter_blocks_negative_slope():
    trend_cfg = {
        "enabled": True,
        "ema_period_hours": 21,
        "slope_lookback_hours": 3,
        "min_slope_pct_per_hour": 0.05,
    }
    engine, _ = _make_trigger_engine(trend_cfg)
    asset = _make_asset()

    prices = [
        120.0,
        118.0,
        115.0,
        112.0,
        108.0,
        104.0,
        100.0,
        96.0,
        94.0,
        93.0,
        92.0,
        91.0,
        90.0,
        91.0,
        93.0,
        96.0,
        99.0,
        101.0,
        100.0,
        99.0,
        98.0,
        97.0,
        96.0,
        95.0,
    ]
    candles = _build_candles(prices)

    signal = engine._check_breakout(asset, candles)

    assert signal is None, "Trend filter should block reversal when EMA slope is negative"


def test_reversal_trend_filter_allows_positive_slope_and_exposes_metrics():
    trend_cfg = {
        "enabled": True,
        "ema_period_hours": 21,
        "slope_lookback_hours": 3,
        "min_slope_pct_per_hour": 0.05,
    }
    engine, _ = _make_trigger_engine(trend_cfg)
    asset = _make_asset()

    prices = [
        105.0,
        104.0,
        103.0,
        102.0,
        100.0,
        98.0,
        96.0,
        94.0,
        92.0,
        91.0,
        90.0,
        90.5,
        91.0,
        92.0,
        93.0,
        94.0,
        95.0,
        96.0,
        97.0,
        97.5,
        98.0,
        98.5,
        98.75,
        99.0,
    ]
    candles = _build_candles(prices)

    signal = engine._check_breakout(asset, candles)

    assert signal is not None, "Reversal trigger should fire with positive slope"
    assert signal.qualifiers.get("trend_filter_passed") is True
    slope = signal.metrics.get("trend_filter_slope_pct_per_hr")
    assert slope is not None and slope >= trend_cfg["min_slope_pct_per_hour"]
    assert signal.metrics.get("trend_filter_passed") == 1.0


def test_conviction_breakdown_includes_weights():
    engine = RulesEngine(config={})
    asset = _make_asset()

    trigger = TriggerSignal(
        symbol=asset.symbol,
        trigger_type="reversal",
        strength=0.4,
        confidence=0.6,
        reason="test",
        timestamp=datetime.now(timezone.utc),
        current_price=100.0,
    )

    proposal = TradeProposal(
        symbol=asset.symbol,
        side="BUY",
        size_pct=2.5,
        reason="test",
        confidence=0.0,
        trigger=trigger,
        asset=asset,
    )

    conviction, breakdown = engine._calculate_conviction(trigger, asset, proposal)

    assert 0.0 <= conviction <= 1.0
    assert "strength_weight" in breakdown
    assert "confidence_weight" in breakdown
    assert pytest.approx(breakdown["strength_component"], rel=1e-6) == (
        breakdown["strength_weight"] * breakdown["strength_value"]
    )
    assert pytest.approx(breakdown["confidence_component"], rel=1e-6) == (
        breakdown["confidence_weight"] * breakdown["confidence_value"]
    )
