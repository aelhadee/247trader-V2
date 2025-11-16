"""
Tests for modular signal system.

Coverage:
- Individual signal scanning (PriceMove, Momentum, MeanReversion)
- Signal strength/confidence calculations
- SignalManager regime filtering
- Signal registry pattern
"""

import pytest
from datetime import datetime, timezone, timedelta
from typing import List

from strategy.signals import (
    PriceMoveSignal,
    MomentumSignal,
    MeanReversionSignal,
    get_signal,
    SIGNAL_REGISTRY
)
from strategy.signal_manager import SignalManager
from core.exchange_coinbase import OHLCV
from core.universe import UniverseAsset


# Fixtures

@pytest.fixture
def sample_asset():
    """Sample T1 asset"""
    return UniverseAsset(
        symbol="BTC-USD",
        tier=1,
        allocation_min_pct=0.01,
        allocation_max_pct=0.05,
        volume_24h=1_000_000_000,
        spread_bps=10,
        depth_usd=500_000,
        eligible=True
    )


@pytest.fixture
def flat_candles() -> List[OHLCV]:
    """100 candles with flat price ($50000)"""
    base_time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = []
    
    for i in range(100):
        candles.append(OHLCV(
            symbol="BTC-USD",
            timestamp=base_time + timedelta(minutes=15*i),
            open=50000.0,
            high=50010.0,
            low=49990.0,
            close=50000.0,
            volume=1000.0
        ))
    
    return candles


@pytest.fixture
def uptrend_candles() -> List[OHLCV]:
    """100 candles with steady uptrend (>10% over 12h for momentum signal)"""
    base_time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = []
    
    for i in range(100):
        # Gradual increase: need >5% over last 48 candles (12h)
        # From 50000 to 53000+ over 12h = 6%
        # Make it steeper for last 48 candles
        if i < 52:
            price = 50000.0
        else:
            # +6% over 48 candles
            price = 50000.0 + ((i - 52) / 48.0) * 3000.0
        
        candles.append(OHLCV(
            symbol="BTC-USD",
            timestamp=base_time + timedelta(minutes=15*i),
            open=price,
            high=price + 10,
            low=price - 10,
            close=price,
            volume=1000.0 + (i * 10)  # Increasing volume
        ))
    
    return candles


@pytest.fixture
def spike_candles() -> List[OHLCV]:
    """100 candles with recent volume spike + price move"""
    base_time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = []
    
    for i in range(100):
        # Flat until last 2 candles
        if i < 98:
            price = 50000.0
            volume = 1000.0
        else:
            # Sharp 2.5% spike in last 15min (1 candle)
            price = 51250.0  # 2.5% up from 50000
            volume = 3000.0  # 3x volume
        
        candles.append(OHLCV(
            symbol="BTC-USD",
            timestamp=base_time + timedelta(minutes=15*i),
            open=price,
            high=price + 10,
            low=price - 10,
            close=price,
            volume=volume
        ))
    
    return candles


@pytest.fixture
def overextended_candles() -> List[OHLCV]:
    """100 candles with overextension for mean reversion"""
    base_time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = []
    
    for i in range(100):
        # Average at 50000, recent spike to 51500 (3% up)
        if i < 90:
            price = 50000.0
        elif i < 94:
            # Sharp move up
            price = 50000.0 + (i - 89) * 375.0
        else:
            # Exhaustion (slowing)
            price = 51500.0 + (i - 93) * 50.0
        
        # Volume declining in exhaustion phase
        volume = 2000.0 if i < 94 else 1000.0
        
        candles.append(OHLCV(
            symbol="BTC-USD",
            timestamp=base_time + timedelta(minutes=15*i),
            open=price,
            high=price + 10,
            low=price - 10,
            close=price,
            volume=volume
        ))
    
    return candles


# PriceMoveSignal Tests

def test_price_move_signal_no_move(sample_asset, flat_candles):
    """No signal on flat price"""
    signal = PriceMoveSignal(config={})
    result = signal.scan(sample_asset, flat_candles, regime="chop")
    
    assert result is None


def test_price_move_signal_detects_spike(sample_asset, spike_candles):
    """Detects volume spike + price move"""
    signal = PriceMoveSignal(config={})
    result = signal.scan(sample_asset, spike_candles, regime="chop")
    
    assert result is not None
    assert result.trigger_type == "price_move"
    assert result.symbol == "BTC-USD"
    assert result.strength > 0.5  # 3% move
    assert result.confidence > 0.5  # Volume confirmation
    assert "move" in result.reason.lower()
    assert result.volatility > 0


def test_price_move_signal_strength_scales(sample_asset, spike_candles):
    """Strength scales with move magnitude"""
    signal = PriceMoveSignal(config={})
    
    # Small move = lower strength
    small_move = spike_candles[:98]  # Only 2 candles of spike
    result_small = signal.strength(small_move, regime="chop")
    
    # Large move = higher strength
    result_large = signal.strength(spike_candles, regime="chop")
    
    assert result_large > result_small


def test_price_move_signal_confidence_volume(sample_asset, spike_candles):
    """Confidence includes volume confirmation"""
    signal = PriceMoveSignal(config={})
    result = signal.confidence(spike_candles, regime="chop")
    
    # Should have high confidence (volume spike + consistent direction)
    assert result > 0.6


# MomentumSignal Tests

def test_momentum_signal_no_trend(sample_asset, flat_candles):
    """No signal on flat trend"""
    signal = MomentumSignal(config={})
    result = signal.scan(sample_asset, flat_candles, regime="bull")
    
    assert result is None


def test_momentum_signal_detects_trend(sample_asset, uptrend_candles):
    """Detects sustained uptrend"""
    signal = MomentumSignal(config={})
    result = signal.scan(sample_asset, uptrend_candles, regime="bull")
    
    assert result is not None
    assert result.trigger_type == "momentum"
    assert result.strength > 0.4  # 10% move
    assert result.confidence > 0.7  # Consistent trend
    assert "trend" in result.reason.lower()


def test_momentum_signal_requires_volume(sample_asset, uptrend_candles):
    """Momentum requires increasing volume"""
    # Modify candles to have declining volume
    declining_vol = []
    for i, c in enumerate(uptrend_candles):
        new_c = OHLCV(
            symbol="BTC-USD",
            timestamp=c.time,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=2000.0 - (i * 10)  # Declining
        )
        declining_vol.append(new_c)
    
    signal = MomentumSignal(config={})
    result = signal.scan(sample_asset, declining_vol, regime="bull")
    
    # Should reject (no volume confirmation)
    assert result is None


def test_momentum_signal_confidence_consistency(sample_asset, uptrend_candles):
    """Confidence based on trend consistency"""
    signal = MomentumSignal(config={})
    result = signal.confidence(uptrend_candles, regime="bull")
    
    # Smooth uptrend = high confidence
    assert result > 0.8


# MeanReversionSignal Tests

def test_mean_reversion_signal_no_deviation(sample_asset, flat_candles):
    """No signal when price at average"""
    signal = MeanReversionSignal(config={})
    result = signal.scan(sample_asset, flat_candles, regime="chop")
    
    assert result is None


def test_mean_reversion_signal_detects_overextension(sample_asset, overextended_candles):
    """Detects overextension with exhaustion"""
    signal = MeanReversionSignal(config={})
    result = signal.scan(sample_asset, overextended_candles, regime="chop")
    
    assert result is not None
    assert result.trigger_type == "mean_reversion"
    assert result.strength > 0.5  # 3% deviation
    assert "mean" in result.reason.lower() or "exhaustion" in result.reason.lower()


def test_mean_reversion_signal_only_in_chop(sample_asset, overextended_candles):
    """Mean reversion only fires in chop regime"""
    signal = MeanReversionSignal(config={})
    
    # Should work in chop
    result_chop = signal.scan(sample_asset, overextended_candles, regime="chop")
    assert result_chop is not None
    
    # Should NOT work in bull/bear
    result_bull = signal.scan(sample_asset, overextended_candles, regime="bull")
    assert result_bull is None
    
    result_bear = signal.scan(sample_asset, overextended_candles, regime="bear")
    assert result_bear is None


def test_mean_reversion_confidence_exhaustion(sample_asset, overextended_candles):
    """Confidence increases with exhaustion signals"""
    signal = MeanReversionSignal(config={})
    result = signal.confidence(overextended_candles, regime="chop")
    
    # Should be high (volume + move declining)
    assert result >= 0.6


# Signal Registry Tests

def test_signal_registry_complete():
    """Registry contains all signal types"""
    assert "price_move" in SIGNAL_REGISTRY
    assert "momentum" in SIGNAL_REGISTRY
    assert "mean_reversion" in SIGNAL_REGISTRY


def test_get_signal_factory():
    """Factory function creates signal instances"""
    signal = get_signal("price_move", config={})
    assert isinstance(signal, PriceMoveSignal)
    
    signal = get_signal("momentum", config={})
    assert isinstance(signal, MomentumSignal)
    
    signal = get_signal("mean_reversion", config={})
    assert isinstance(signal, MeanReversionSignal)


def test_get_signal_unknown():
    """Factory raises on unknown signal"""
    with pytest.raises(ValueError, match="Unknown signal type"):
        get_signal("invalid_signal", config={})


# SignalManager Tests (requires config files - basic tests only)

def test_signal_manager_init(tmp_path):
    """SignalManager loads and initializes signals"""
    # Create minimal config files
    signals_config = tmp_path / "signals.yaml"
    signals_config.write_text("""
enabled_signals:
  - price_move
signals:
  price_move:
    enabled: true
""")
    
    policy_config = tmp_path / "policy.yaml"
    policy_config.write_text("""
regime:
  enabled: true
  chop:
    allowed_signals: [price_move]
  bull:
    allowed_signals: [momentum, price_move]
""")
    
    manager = SignalManager(
        signals_config_path=str(signals_config),
        policy_config_path=str(policy_config)
    )
    
    assert "price_move" in manager.signals
    assert len(manager.signals) == 1


def test_signal_manager_regime_filtering(tmp_path):
    """SignalManager filters signals by regime"""
    # Create config
    signals_config = tmp_path / "signals.yaml"
    signals_config.write_text("""
enabled_signals:
  - price_move
  - momentum
signals:
  price_move:
    enabled: true
  momentum:
    enabled: true
""")
    
    policy_config = tmp_path / "policy.yaml"
    policy_config.write_text("""
regime:
  enabled: true
  chop:
    allowed_signals: [price_move]
  bull:
    allowed_signals: [momentum, price_move]
  crash:
    allowed_signals: []
""")
    
    manager = SignalManager(
        signals_config_path=str(signals_config),
        policy_config_path=str(policy_config)
    )
    
    # Chop: only price_move
    allowed_chop = manager._get_allowed_signals("chop")
    assert allowed_chop == ["price_move"]
    
    # Bull: both signals
    allowed_bull = manager._get_allowed_signals("bull")
    assert set(allowed_bull) == {"momentum", "price_move"}
    
    # Crash: no signals
    allowed_crash = manager._get_allowed_signals("crash")
    assert allowed_crash == []


def test_signal_manager_stats(tmp_path):
    """SignalManager provides stats"""
    signals_config = tmp_path / "signals.yaml"
    signals_config.write_text("""
enabled_signals:
  - price_move
signals:
  price_move:
    enabled: true
""")
    
    policy_config = tmp_path / "policy.yaml"
    policy_config.write_text("""
regime:
  enabled: true
  chop:
    allowed_signals: [price_move]
""")
    
    manager = SignalManager(
        signals_config_path=str(signals_config),
        policy_config_path=str(policy_config)
    )
    
    stats = manager.get_signal_stats()
    
    assert stats["total_signals"] == 1
    assert "price_move" in stats["loaded_signals"]
    assert "regime_filters" in stats
