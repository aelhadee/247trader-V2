"""
Tests for TradeLimits module.

Coverage:
- Global trade spacing
- Per-symbol spacing
- Frequency limits (hourly/daily)
- Consecutive loss cooldown
- Per-symbol cooldowns (win/loss/stop differentiated)
- Cooldown status queries
"""

import pytest
from datetime import datetime, timedelta, timezone
from typing import Dict

from core.trade_limits import TradeLimits, TradeTimingResult
from strategy.rules_engine import TradeProposal
from infra.state_store import StateStore, JsonFileBackend


# Fixtures

@pytest.fixture
def minimal_config():
    """Minimal trade limits configuration"""
    return {
        "min_seconds_between_trades": 180,  # 3min global spacing
        "per_symbol_trade_spacing_seconds": 900,  # 15min per-symbol
        "max_trades_per_hour": 5,
        "max_trades_per_day": 120,
        "cooldown_after_loss_trades": 3,
        "cooldown_minutes": 60,
        "per_symbol_cooldown_enabled": True,
        "per_symbol_cooldown_win_minutes": 10,
        "per_symbol_cooldown_loss_minutes": 60,
        "per_symbol_cooldown_after_stop": 120,
    }


@pytest.fixture
def mock_state_store(tmp_path):
    """Mock state store for testing"""
    state_file = tmp_path / "test_trade_limits_state.json"
    backend = JsonFileBackend(path=str(state_file))
    return StateStore(backend=backend)


@pytest.fixture
def trade_limits(minimal_config, mock_state_store):
    """TradeLimits instance"""
    return TradeLimits(config=minimal_config, state_store=mock_state_store)


@pytest.fixture
def sample_proposals():
    """Sample trade proposals"""
    return [
        TradeProposal(
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            confidence=0.7,
            reason="test",
            tier=1,
            stop_loss_pct=10.0,
            take_profit_pct=15.0
        ),
        TradeProposal(
            symbol="ETH-USD",
            side="buy",
            size_usd=500.0,
            confidence=0.6,
            reason="test",
            tier=1,
            stop_loss_pct=10.0,
            take_profit_pct=15.0
        )
    ]


# Test: Initialization

def test_trade_limits_init(minimal_config, mock_state_store):
    """TradeLimits initializes with config"""
    limits = TradeLimits(config=minimal_config, state_store=mock_state_store)
    
    assert limits.min_global_spacing_sec == 180
    assert limits.per_symbol_spacing_sec == 900
    assert limits.max_trades_per_hour == 5
    assert limits.max_trades_per_day == 120
    assert limits.cooldown_win_minutes == 10
    assert limits.cooldown_loss_minutes == 60
    assert limits.cooldown_stop_minutes == 120


# Test: Global Trade Spacing

def test_global_spacing_first_trade(trade_limits, sample_proposals):
    """First trade passes global spacing"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    result = trade_limits.check_all(
        proposals=sample_proposals,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=now
    )
    
    assert result.approved


def test_global_spacing_blocked(trade_limits, sample_proposals, mock_state_store):
    """Trade blocked by global spacing"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    # Record a trade 60s ago (less than 180s min spacing)
    last_trade = now - timedelta(seconds=60)
    state = mock_state_store.load()
    state["last_trade_timestamp"] = last_trade.isoformat()
    mock_state_store.save(state)
    
    result = trade_limits.check_all(
        proposals=sample_proposals,
        trades_today=1,
        trades_this_hour=1,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=now
    )
    
    assert not result.approved
    assert "global_trade_spacing" in result.violated_checks
    assert "120s remaining" in result.reason


def test_global_spacing_passes_after_delay(trade_limits, sample_proposals, mock_state_store):
    """Trade passes after global spacing delay"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    # Record a trade 200s ago (more than 180s min spacing)
    last_trade = now - timedelta(seconds=200)
    state = mock_state_store.load()
    state["last_trade_timestamp"] = last_trade.isoformat()
    mock_state_store.save(state)
    
    result = trade_limits.check_all(
        proposals=sample_proposals,
        trades_today=1,
        trades_this_hour=1,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=now
    )
    
    assert result.approved


# Test: Frequency Limits

def test_hourly_limit_not_reached(trade_limits, sample_proposals):
    """Trade passes when hourly limit not reached"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    result = trade_limits.check_all(
        proposals=sample_proposals,
        trades_today=10,
        trades_this_hour=4,  # Below limit of 5
        consecutive_losses=0,
        last_loss_time=None,
        current_time=now
    )
    
    assert result.approved


def test_hourly_limit_reached(trade_limits, sample_proposals):
    """Trade blocked when hourly limit reached"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    result = trade_limits.check_all(
        proposals=sample_proposals,
        trades_today=10,
        trades_this_hour=5,  # At limit
        consecutive_losses=0,
        last_loss_time=None,
        current_time=now
    )
    
    assert not result.approved
    assert "trade_frequency_hourly" in result.violated_checks
    assert "5/5" in result.reason


def test_daily_limit_reached(trade_limits, sample_proposals):
    """Trade blocked when daily limit reached"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    result = trade_limits.check_all(
        proposals=sample_proposals,
        trades_today=120,  # At daily limit
        trades_this_hour=4,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=now
    )
    
    assert not result.approved
    assert "trade_frequency_daily" in result.violated_checks
    assert "120/120" in result.reason


# Test: Consecutive Loss Cooldown

def test_loss_cooldown_not_triggered(trade_limits, sample_proposals):
    """Trade passes with fewer than 3 consecutive losses"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    result = trade_limits.check_all(
        proposals=sample_proposals,
        trades_today=5,
        trades_this_hour=2,
        consecutive_losses=2,  # Below threshold of 3
        last_loss_time=now - timedelta(minutes=10),
        current_time=now
    )
    
    assert result.approved


def test_loss_cooldown_active(trade_limits, sample_proposals):
    """Trade blocked during loss cooldown"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    last_loss = now - timedelta(minutes=30)  # 30min ago, cooldown is 60min
    
    result = trade_limits.check_all(
        proposals=sample_proposals,
        trades_today=5,
        trades_this_hour=2,
        consecutive_losses=3,  # At threshold
        last_loss_time=last_loss,
        current_time=now
    )
    
    assert not result.approved
    assert "consecutive_loss_cooldown" in result.violated_checks
    assert "30min left" in result.reason


def test_loss_cooldown_expired(trade_limits, sample_proposals):
    """Trade passes after loss cooldown expires"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    last_loss = now - timedelta(minutes=70)  # 70min ago, cooldown is 60min
    
    result = trade_limits.check_all(
        proposals=sample_proposals,
        trades_today=5,
        trades_this_hour=2,
        consecutive_losses=3,
        last_loss_time=last_loss,
        current_time=now
    )
    
    assert result.approved


# Test: Per-Symbol Cooldowns

def test_apply_win_cooldown(trade_limits, mock_state_store):
    """Apply win cooldown (10min)"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    trade_limits.apply_cooldown("BTC-USD", outcome="win", current_time=now)
    
    state = mock_state_store.load()
    assert "BTC-USD" in state["cooldowns"]
    
    # Check cooldown expires in 10min
    status = trade_limits.get_cooldown_status("BTC-USD", current_time=now)
    assert status["on_cooldown"]
    assert status["last_outcome"] == "win"
    assert 9.9 < status["minutes_remaining"] <= 10.0


def test_apply_loss_cooldown(trade_limits, mock_state_store):
    """Apply loss cooldown (60min)"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    trade_limits.apply_cooldown("ETH-USD", outcome="loss", current_time=now)
    
    status = trade_limits.get_cooldown_status("ETH-USD", current_time=now)
    assert status["on_cooldown"]
    assert status["last_outcome"] == "loss"
    assert 59.9 < status["minutes_remaining"] <= 60.0


def test_apply_stop_loss_cooldown(trade_limits, mock_state_store):
    """Apply stop-loss cooldown (120min)"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    trade_limits.apply_cooldown("SOL-USD", outcome="stop_loss", current_time=now)
    
    status = trade_limits.get_cooldown_status("SOL-USD", current_time=now)
    assert status["on_cooldown"]
    assert status["last_outcome"] == "stop_loss"
    assert 119.9 < status["minutes_remaining"] <= 120.0


def test_cooldown_blocks_proposal(trade_limits, mock_state_store):
    """Proposal blocked by symbol cooldown"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    # Apply cooldown to BTC-USD
    trade_limits.apply_cooldown("BTC-USD", outcome="loss", current_time=now)
    
    proposals = [
        TradeProposal(
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            confidence=0.7,
            reason="test",
            tier=1,
            stop_loss_pct=10.0,
            take_profit_pct=15.0
        )
    ]
    
    approved, rejections = trade_limits.filter_proposals_by_timing(proposals, current_time=now)
    
    assert len(approved) == 0
    assert "BTC-USD" in rejections
    assert "per_symbol_cooldown" in rejections["BTC-USD"]


def test_cooldown_expires(trade_limits, mock_state_store):
    """Proposal passes after cooldown expires"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    # Apply 10min win cooldown
    trade_limits.apply_cooldown("BTC-USD", outcome="win", current_time=now)
    
    # Check 15min later (after cooldown)
    later = now + timedelta(minutes=15)
    
    proposals = [
        TradeProposal(
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            confidence=0.7,
            reason="test",
            tier=1,
            stop_loss_pct=10.0,
            take_profit_pct=15.0
        )
    ]
    
    approved, rejections = trade_limits.filter_proposals_by_timing(proposals, current_time=later)
    
    assert len(approved) == 1
    assert "BTC-USD" not in rejections


# Test: Per-Symbol Spacing

def test_symbol_spacing_first_trade(trade_limits, mock_state_store):
    """First trade on symbol passes spacing"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    proposals = [
        TradeProposal(
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            confidence=0.7,
            reason="test",
            tier=1,
            stop_loss_pct=10.0,
            take_profit_pct=15.0
        )
    ]
    
    approved, rejections = trade_limits.filter_proposals_by_timing(proposals, current_time=now)
    
    assert len(approved) == 1
    assert len(rejections) == 0


def test_symbol_spacing_blocked(trade_limits, mock_state_store):
    """Trade blocked by per-symbol spacing (15min)"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    # Record a trade on BTC-USD 5min ago (less than 15min)
    trade_limits.record_trade("BTC-USD", current_time=now - timedelta(minutes=5))
    
    proposals = [
        TradeProposal(
            symbol="BTC-USD",
            side="buy",
            size_usd=1000.0,
            confidence=0.7,
            reason="test",
            tier=1,
            stop_loss_pct=10.0,
            take_profit_pct=15.0
        )
    ]
    
    approved, rejections = trade_limits.filter_proposals_by_timing(proposals, current_time=now)
    
    assert len(approved) == 0
    assert "BTC-USD" in rejections
    assert "per_symbol_spacing" in rejections["BTC-USD"]


def test_symbol_spacing_different_symbols(trade_limits, mock_state_store):
    """Different symbols don't interfere with spacing"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    # Record trade on BTC-USD
    trade_limits.record_trade("BTC-USD", current_time=now - timedelta(minutes=5))
    
    # Try to trade ETH-USD (different symbol)
    proposals = [
        TradeProposal(
            symbol="ETH-USD",
            side="buy",
            size_usd=500.0,
            confidence=0.6,
            reason="test",
            tier=1,
            stop_loss_pct=10.0,
            take_profit_pct=15.0
        )
    ]
    
    approved, rejections = trade_limits.filter_proposals_by_timing(proposals, current_time=now)
    
    assert len(approved) == 1
    assert len(rejections) == 0


# Test: Record Trade

def test_record_trade_updates_state(trade_limits, mock_state_store):
    """Recording trade updates state"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    trade_limits.record_trade("BTC-USD", current_time=now)
    
    state = mock_state_store.load()
    assert state["last_trade_timestamp"] == now.isoformat()
    assert state["last_trade_time_by_symbol"]["BTC-USD"] == now.isoformat()


# Test: Mixed Scenarios

def test_multiple_proposals_mixed_outcomes(trade_limits, mock_state_store):
    """Filter multiple proposals with different timing states"""
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    # BTC: on cooldown (loss)
    trade_limits.apply_cooldown("BTC-USD", outcome="loss", current_time=now)
    
    # ETH: violates spacing
    trade_limits.record_trade("ETH-USD", current_time=now - timedelta(minutes=5))
    
    # SOL: clean (no history)
    
    proposals = [
        TradeProposal(symbol="BTC-USD", side="buy", size_usd=1000, confidence=0.7, 
                     reason="test", tier=1, stop_loss_pct=10, take_profit_pct=15),
        TradeProposal(symbol="ETH-USD", side="buy", size_usd=500, confidence=0.6,
                     reason="test", tier=1, stop_loss_pct=10, take_profit_pct=15),
        TradeProposal(symbol="SOL-USD", side="buy", size_usd=300, confidence=0.5,
                     reason="test", tier=2, stop_loss_pct=10, take_profit_pct=15),
    ]
    
    approved, rejections = trade_limits.filter_proposals_by_timing(proposals, current_time=now)
    
    # Only SOL should pass
    assert len(approved) == 1
    assert approved[0].symbol == "SOL-USD"
    
    # BTC and ETH rejected
    assert "BTC-USD" in rejections
    assert "ETH-USD" in rejections
    assert "per_symbol_cooldown" in rejections["BTC-USD"]
    assert "per_symbol_spacing" in rejections["ETH-USD"]
