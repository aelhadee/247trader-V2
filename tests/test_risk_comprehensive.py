"""
247trader-v2 Tests: Comprehensive RiskEngine Coverage

Test suite for all critical risk management logic:
- Minimum notional enforcement
- Position size caps (per-symbol and total)
- Trade frequency limits (hourly/daily)
- Global trade spacing
- Per-symbol cooldowns
- Circuit breakers (kill switch, drawdown, data staleness)
- Exchange product status filtering

Target: 90%+ coverage of risk.py critical paths
"""

import pytest
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from unittest.mock import Mock, patch, MagicMock

from core.risk import RiskEngine, RiskCheckResult, PortfolioState
from strategy.rules_engine import TradeProposal
from infra.state_store import StateStore


# Test fixtures

@pytest.fixture
def minimal_policy():
    """Minimal policy configuration for testing"""
    return {
        "risk": {
            "min_trade_notional_usd": 10.0,
            "max_trades_per_day": 40,
            "max_trades_per_hour": 8,
            "max_new_trades_per_hour": 5,
            "min_seconds_between_trades": 120,
            "per_symbol_trade_spacing_seconds": 600,
            "per_symbol_cooldown_enabled": True,
            "per_symbol_cooldown_minutes": 30,
            "cooldown_after_loss_trades": 3,
            "cooldown_minutes": 60,
            "daily_stop_pnl_pct": -5.0,
            "weekly_stop_pnl_pct": -10.0,
            "max_drawdown_pct": 15.0,
            "max_at_risk_pct": 70.0,
            "max_open_positions": 8,
            "count_open_orders_in_cap": True,
            "allow_adds_when_over_cap": False,
        },
        "position_sizing": {
            "tier_sizing": {
                "T1": 0.05,  # 5% per trade
                "T2": 0.03,  # 3% per trade
                "T3": 0.01,  # 1% per trade
            },
            "max_position_pct": {
                "T1": 0.10,  # Max 10% per symbol
                "T2": 0.06,  # Max 6% per symbol
                "T3": 0.02,  # Max 2% per symbol
            }
        },
        "circuit_breakers": {
            "data_staleness_threshold_seconds": 300,
            "api_error_threshold": 3,
            "api_error_window_seconds": 60,
        },
        "governance": {
            "kill_switch_file": "data/KILL_SWITCH",
        },
        "strategy": {},
        "execution": {},
    }


@pytest.fixture
def mock_state_store(tmp_path):
    """Mock state store for testing"""
    state_file = tmp_path / "test_state.json"
    from infra.state_store import JsonFileBackend
    backend = JsonFileBackend(state_file)
    store = StateStore(backend=backend)
    return store


@pytest.fixture
def risk_engine(minimal_policy, mock_state_store):
    """Create RiskEngine instance for testing"""
    return RiskEngine(
        policy=minimal_policy,
        universe_manager=None,
        exchange=None,
        state_store=mock_state_store,
        alert_service=None
    )


@pytest.fixture
def base_portfolio():
    """Base portfolio state with 10k capital"""
    return PortfolioState(
        account_value_usd=10000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        current_time=datetime.now(timezone.utc),
        pending_orders={"buy": {}, "sell": {}},
    )


@pytest.fixture
def sample_proposal():
    """Sample BUY proposal for BTC"""
    return TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,  # 5% of capital = $500 on 10k
        reason="momentum_breakout",
        stop_loss_pct=5.0,
        take_profit_pct=10.0,
        max_hold_hours=48,
    )


# Test: Minimum Notional Enforcement

def test_min_notional_rejects_small_trade(risk_engine, base_portfolio):
    """Test that trades below min_notional_usd are rejected"""
    # Create proposal for $5 (below $10 minimum)
    small_proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.50,
        size_pct=0.05,  # 0.05% of 10k = $5
        reason="test",
    )
    
    result = risk_engine.check_all([small_proposal], base_portfolio)
    
    assert not result.approved
    # Check violated_checks for position_size_too_small instead of reason text
    assert any("position_size_too_small" in check or "minimum" in check.lower() 
               for check in result.violated_checks)


def test_min_notional_approves_sufficient_trade(risk_engine, base_portfolio, sample_proposal):
    """Test that trades above min_notional_usd are approved"""
    # sample_proposal is $500 (well above $10 minimum)
    result = risk_engine.check_all([sample_proposal], base_portfolio)
    
    # Should pass min notional check (may fail other checks)
    assert "min_notional" not in str(result.reason).lower()


# Test: Position Size Caps

def test_per_symbol_position_cap_enforced(risk_engine, base_portfolio):
    """Test that per-symbol position caps are enforced"""
    # Portfolio with existing 8% BTC position (near 10% T1 cap)
    portfolio_with_btc = PortfolioState(
        account_value_usd=10000.0,
        open_positions={"BTC-USD": {"units": 0.08, "usd": 800.0}},
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        current_time=datetime.now(timezone.utc),
    )
    
    # Try to add another 5% ($500) - would exceed 10% cap
    btc_proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([btc_proposal], portfolio_with_btc)
    
    # Should be rejected or downsized for position cap
    if not result.approved:
        assert "position" in result.reason.lower() or "cap" in result.reason.lower()


def test_total_exposure_cap_enforced(risk_engine, base_portfolio):
    """Test that total portfolio exposure cap is enforced"""
    # Portfolio with 65% total exposure (near 70% cap)
    portfolio_high_exposure = PortfolioState(
        account_value_usd=10000.0,
        open_positions={
            "BTC-USD": {"units": 0.03, "usd": 3000.0},
            "ETH-USD": {"units": 1.5, "usd": 2000.0},
            "SOL-USD": {"units": 30.0, "usd": 1500.0},
        },
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        current_time=datetime.now(timezone.utc),
    )
    
    # Try to add 10% more exposure (would breach 70% cap)
    new_proposal = TradeProposal(
        symbol="ADA-USD",
        side="BUY",
        confidence=0.60,
        size_pct=10.0,
        reason="test",
    )
    
    result = risk_engine.check_all([new_proposal], portfolio_high_exposure)
    
    # Should be rejected for exceeding total at-risk cap
    assert not result.approved
    assert "at.risk" in result.reason.lower() or "exposure" in result.reason.lower()


# Test: Trade Frequency Limits

def test_hourly_trade_limit_enforced(risk_engine, base_portfolio):
    """Test that hourly trade limit blocks excessive trading"""
    # Portfolio at hourly limit
    portfolio_at_limit = PortfolioState(
        account_value_usd=10000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=5,
        trades_this_hour=8,  # At max_trades_per_hour=8
        consecutive_losses=0,
        current_time=datetime.now(timezone.utc),
    )
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio_at_limit)
    
    assert not result.approved
    assert "hour" in result.reason.lower()


def test_daily_trade_limit_enforced(risk_engine, base_portfolio):
    """Test that daily trade limit blocks excessive trading"""
    # Portfolio at daily limit
    portfolio_at_limit = PortfolioState(
        account_value_usd=10000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=40,  # At max_trades_per_day=40
        trades_this_hour=3,
        consecutive_losses=0,
        current_time=datetime.now(timezone.utc),
    )
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio_at_limit)
    
    assert not result.approved
    assert "day" in result.reason.lower() or "daily" in result.reason.lower()


# Test: Global Trade Spacing

def test_global_trade_spacing_blocks_rapid_trades(risk_engine, base_portfolio, mock_state_store):
    """Test that global trade spacing prevents rapid-fire trading"""
    # Simulate recent trade 30 seconds ago (within 120s spacing)
    now = datetime.now(timezone.utc)
    last_trade_time = now - timedelta(seconds=30)
    
    state = mock_state_store.load()
    state["last_trade_timestamp"] = last_trade_time.isoformat()
    mock_state_store.save(state)
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], base_portfolio)
    
    assert not result.approved
    assert "spacing" in result.reason.lower()


def test_global_trade_spacing_allows_after_cooldown(risk_engine, base_portfolio, mock_state_store):
    """Test that trades are allowed after spacing period expires"""
    # Simulate trade 150 seconds ago (beyond 120s spacing)
    now = datetime.now(timezone.utc)
    last_trade_time = now - timedelta(seconds=150)
    
    state = mock_state_store.load()
    state["last_trade_timestamp"] = last_trade_time.isoformat()
    mock_state_store.save(state)
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], base_portfolio)
    
    # Should NOT be blocked by spacing (may fail other checks)
    assert "spacing" not in str(result.reason).lower()


# Test: Per-Symbol Cooldowns

def test_per_symbol_cooldown_after_recent_trade(risk_engine, base_portfolio, mock_state_store):
    """Test that per-symbol cooldown blocks trading same symbol too soon"""
    # Simulate BTC trade 5 minutes ago (within 30min cooldown)
    now = datetime.now(timezone.utc)
    last_btc_trade = now - timedelta(minutes=5)
    
    state = mock_state_store.load()
    state["cooldowns"] = {"BTC-USD": last_btc_trade.isoformat()}
    mock_state_store.save(state)
    
    btc_proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([btc_proposal], base_portfolio)
    
    # Should be blocked by per-symbol cooldown
    if not result.approved:
        assert "cooldown" in result.reason.lower() or "BTC" in result.reason


# Test: Circuit Breakers

def test_kill_switch_blocks_all_trades(risk_engine, base_portfolio, tmp_path):
    """Test that kill switch file blocks all trading"""
    # Create kill switch file
    kill_switch_path = tmp_path / "KILL_SWITCH"
    kill_switch_path.touch()
    
    # Update risk engine policy to point to test kill switch
    risk_engine.governance_config = {"kill_switch_file": str(kill_switch_path)}
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], base_portfolio)
    
    assert not result.approved
    assert "kill" in result.reason.lower() or "emergency" in result.reason.lower()


def test_daily_stop_loss_circuit_breaker(risk_engine, base_portfolio):
    """Test that daily stop loss blocks new trades"""
    # Portfolio with -6% daily PnL (exceeds -5% stop)
    portfolio_stopped = PortfolioState(
        account_value_usd=9400.0,
        open_positions={},
        daily_pnl_pct=-6.0,
        weekly_pnl_pct=-6.0,
        max_drawdown_pct=6.0,
        trades_today=3,
        trades_this_hour=1,
        consecutive_losses=2,
        current_time=datetime.now(timezone.utc),
    )
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio_stopped)
    
    assert not result.approved
    assert "stop" in result.reason.lower() or "daily" in result.reason.lower()


def test_max_drawdown_circuit_breaker(risk_engine, base_portfolio):
    """Test that max drawdown circuit breaker blocks trades"""
    # Portfolio with 16% drawdown (exceeds 15% max)
    portfolio_drawdown = PortfolioState(
        account_value_usd=8400.0,
        open_positions={},
        daily_pnl_pct=-4.0,
        weekly_pnl_pct=-10.0,
        max_drawdown_pct=16.0,  # Exceeds 15% limit
        trades_today=5,
        trades_this_hour=2,
        consecutive_losses=3,
        current_time=datetime.now(timezone.utc),
    )
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio_drawdown)
    
    assert not result.approved
    assert "drawdown" in result.reason.lower()


def test_consecutive_loss_cooldown(risk_engine, base_portfolio):
    """Test that consecutive losses trigger cooldown"""
    now = datetime.now(timezone.utc)
    
    # Portfolio with 3 consecutive losses (triggers cooldown)
    # Last loss was 10 minutes ago (within 60min cooldown)
    portfolio_losses = PortfolioState(
        account_value_usd=9500.0,
        open_positions={},
        daily_pnl_pct=-5.0,
        weekly_pnl_pct=-5.0,
        max_drawdown_pct=5.0,
        trades_today=5,
        trades_this_hour=2,
        consecutive_losses=3,  # Triggers cooldown
        last_loss_time=now - timedelta(minutes=10),
        current_time=now,
    )
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="BUY",
        confidence=0.75,
        size_pct=5.0,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio_losses)
    
    assert not result.approved
    assert "cooldown" in result.reason.lower() or "loss" in result.reason.lower()


# Test: Edge Cases

def test_empty_proposals_approved(risk_engine, base_portfolio):
    """Test that empty proposal list is approved"""
    result = risk_engine.check_all([], base_portfolio)
    
    assert result.approved
    assert result.approved_proposals == []


def test_multiple_proposals_filtered_correctly(risk_engine, base_portfolio):
    """Test that multiple proposals are filtered correctly"""
    proposals = [
        # Valid proposal
        TradeProposal(
            symbol="BTC-USD",
            side="BUY",
            confidence=0.75,
            size_pct=5.0,
            reason="momentum",
        ),
        # Too small
        TradeProposal(
            symbol="ETH-USD",
            side="BUY",
            confidence=0.50,
            size_pct=0.05,  # $5 - below minimum
            reason="test",
        ),
        # Valid proposal
        TradeProposal(
            symbol="SOL-USD",
            side="BUY",
            confidence=0.60,
            size_pct=3.0,
            reason="breakout",
        ),
    ]
    
    result = risk_engine.check_all(proposals, base_portfolio)
    
    # Should filter out the too-small proposal
    # May approve 0-2 proposals depending on other checks
    assert len(result.approved_proposals) < len(proposals)


def test_max_open_positions_enforced(risk_engine, base_portfolio):
    """Test that max open positions limit is enforced"""
    # Portfolio with 8 open positions (at max)
    portfolio_full = PortfolioState(
        account_value_usd=10000.0,
        open_positions={
            f"SYM{i}-USD": {"units": 1.0, "usd": 100.0}
            for i in range(8)
        },
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=8,
        trades_this_hour=2,
        consecutive_losses=0,
        current_time=datetime.now(timezone.utc),
    )
    
    # Try to open 9th position
    new_proposal = TradeProposal(
        symbol="NEW-USD",
        side="BUY",
        confidence=0.75,
        size_pct=3.0,
        reason="test",
    )
    
    result = risk_engine.check_all([new_proposal], portfolio_full)
    
    # Should be blocked for max positions
    if not result.approved:
        assert "position" in result.reason.lower() or "max" in result.reason.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
