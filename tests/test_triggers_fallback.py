"""Tests for TriggerEngine fallback scan logic."""

from datetime import datetime, timedelta, timezone

from typing import List

from core.triggers import TriggerEngine
from core.exchange_coinbase import OHLCV
from core.universe import UniverseAsset


class _DummyExchange:
    def __init__(self, candles):
        self._candles = candles

    def get_ohlcv(self, symbol, interval="1h", limit=168):
        return list(self._candles)


def _build_candles(base_price: float = 100.0, move_pct: float = 1.2) -> List[OHLCV]:
    """Generate hourly candles with a controlled last-hour move."""
    candles: List[OHLCV] = []
    now = datetime.now(timezone.utc)
    for i in range(70):
        timestamp = now - timedelta(hours=69 - i)
        close = base_price
        if i == 69:
            close = base_price * (1 + move_pct / 100.0)
        candles.append(
            OHLCV(
                symbol="TEST-USD",
                timestamp=timestamp,
                open=base_price,
                high=close,
                low=base_price,
                close=close,
                volume=1_000_000.0,
            )
        )
    return candles


def test_fallback_scan_emits_relaxed_trigger(monkeypatch):
    candles = _build_candles()
    engine = TriggerEngine()
    engine.exchange = _DummyExchange(candles)
    engine.enable_atr_filter = False
    engine.fallback_config = {
        "enabled": True,
        "min_no_trigger_streak": 0,
        "relax_pct": 0.5,
        "max_new_positions_per_cycle": 1,
        "allow_downside": True,
    }

    # Silence other trigger types to isolate fallback behaviour
    monkeypatch.setattr(engine, "_check_volume_spike", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_check_breakout", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_check_momentum", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "_check_price_move", lambda *args, **kwargs: None)

    asset = UniverseAsset(
        symbol="TEST-USD",
        tier=1,
        allocation_min_pct=5.0,
        allocation_max_pct=40.0,
        volume_24h=50_000_000.0,
        spread_bps=12.0,
        depth_usd=200_000.0,
        eligible=True,
    )

    signals = engine.scan([asset], regime="chop")

    assert len(signals) == 1
    assert signals[0].symbol == "TEST-USD"
    assert "fallback" in signals[0].reason
    assert engine._no_trigger_streak == 0
