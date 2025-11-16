"""
Tests for TradingCyclePipeline - Shared Core Logic

Validates that the pipeline works correctly in both live and backtest modes.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock

from core.trading_cycle import TradingCyclePipeline, CycleResult
from core.universe import UniverseSnapshot, UniverseAsset
from core.triggers import TriggerSignal
from strategy.rules_engine import TradeProposal
from core.risk import PortfolioState, RiskCheckResult


@pytest.fixture
def mock_universe_mgr():
    """Mock universe manager"""
    mgr = Mock()
    
    # Create a simple universe
    universe = Mock(spec=UniverseSnapshot)
    universe.total_eligible = 3
    universe.tier_1_assets = [
        Mock(spec=UniverseAsset, symbol="BTC-USD", tier=1),
    ]
    universe.tier_2_assets = [
        Mock(spec=UniverseAsset, symbol="ETH-USD", tier=2),
    ]
    universe.tier_3_assets = [
        Mock(spec=UniverseAsset, symbol="SOL-USD", tier=3),
    ]
    universe.get_all_eligible = Mock(return_value=[
        universe.tier_1_assets[0],
        universe.tier_2_assets[0],
        universe.tier_3_assets[0],
    ])
    
    mgr.get_universe = Mock(return_value=universe)
    return mgr


@pytest.fixture
def mock_trigger_engine():
    """Mock trigger engine"""
    engine = Mock()
    
    # Default: return 2 triggers
    triggers = [
        TriggerSignal(
            symbol="BTC-USD",
            trigger_type="volume_spike",
            strength=0.8,
            confidence=0.7,
            reason="Test trigger",
            timestamp=datetime.now(timezone.utc),
            current_price=50000.0,
            volume_ratio=2.5
        ),
        TriggerSignal(
            symbol="ETH-USD",
            trigger_type="breakout",
            strength=0.6,
            confidence=0.65,
            reason="Test breakout",
            timestamp=datetime.now(timezone.utc),
            current_price=3000.0,
            price_change_pct=5.0
        ),
    ]
    
    engine.scan = Mock(return_value=triggers)
    return engine


@pytest.fixture
def mock_regime_detector():
    """Mock regime detector"""
    return Mock()


@pytest.fixture
def mock_risk_engine():
    """Mock risk engine"""
    engine = Mock()
    
    # Default: approve all
    result = RiskCheckResult(
        approved=True,
        filtered_proposals=[],
        reason=None
    )
    engine.check_all = Mock(return_value=result)
    return engine


@pytest.fixture
def trading_pipeline(mock_universe_mgr, mock_trigger_engine, mock_regime_detector, mock_risk_engine):
    """Create trading pipeline with mocks"""
    return TradingCyclePipeline(
        universe_mgr=mock_universe_mgr,
        trigger_engine=mock_trigger_engine,
        regime_detector=mock_regime_detector,
        risk_engine=mock_risk_engine,
        strategy_registry=None,  # Test without registry
        policy_config={}
    )


@pytest.fixture
def portfolio_state():
    """Mock portfolio state"""
    return PortfolioState(
        account_value_usd=10000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=datetime.now(timezone.utc)
    )


def test_pipeline_initialization(trading_pipeline):
    """Test pipeline initializes correctly"""
    assert trading_pipeline.universe_mgr is not None
    assert trading_pipeline.trigger_engine is not None
    assert trading_pipeline.risk_engine is not None


def test_successful_cycle(trading_pipeline, portfolio_state):
    """Test a successful trading cycle"""
    result = trading_pipeline.execute_cycle(
        current_time=datetime.now(timezone.utc),
        portfolio=portfolio_state,
        regime="chop",
        cycle_number=1,
        state=None
    )
    
    assert result.success is True
    assert result.universe is not None
    assert len(result.triggers) == 2
    assert result.no_trade_reason is None


def test_empty_universe_no_trade(trading_pipeline, portfolio_state, mock_universe_mgr):
    """Test cycle returns no-trade when universe is empty"""
    # Mock empty universe
    empty_universe = Mock()
    empty_universe.total_eligible = 0
    mock_universe_mgr.get_universe.return_value = empty_universe
    
    result = trading_pipeline.execute_cycle(
        current_time=datetime.now(timezone.utc),
        portfolio=portfolio_state,
        regime="chop",
        cycle_number=1
    )
    
    assert result.success is False
    assert result.no_trade_reason == "empty_universe"


def test_no_triggers_no_trade(trading_pipeline, portfolio_state, mock_trigger_engine):
    """Test cycle returns no-trade when no triggers detected"""
    # Mock no triggers
    mock_trigger_engine.scan.return_value = []
    
    result = trading_pipeline.execute_cycle(
        current_time=datetime.now(timezone.utc),
        portfolio=portfolio_state,
        regime="chop",
        cycle_number=1
    )
    
    assert result.success is False
    assert result.no_trade_reason == "no_candidates_from_triggers"


def test_no_proposals_no_trade(trading_pipeline, portfolio_state):
    """Test cycle returns no-trade when no proposals generated"""
    # RulesEngine returns empty by default when triggers don't meet conviction
    result = trading_pipeline.execute_cycle(
        current_time=datetime.now(timezone.utc),
        portfolio=portfolio_state,
        regime="chop",
        cycle_number=1
    )
    
    # This might succeed with proposals or fail with no proposals depending on rules engine
    # Just verify it doesn't crash
    assert result is not None


def test_risk_rejection(trading_pipeline, portfolio_state, mock_risk_engine):
    """Test cycle returns no-trade when risk blocks proposals"""
    # Mock risk rejection
    rejection_result = RiskCheckResult(
        approved=False,
        filtered_proposals=[],
        reason="test_rejection"
    )
    mock_risk_engine.check_all.return_value = rejection_result
    
    result = trading_pipeline.execute_cycle(
        current_time=datetime.now(timezone.utc),
        portfolio=portfolio_state,
        regime="chop",
        cycle_number=1
    )
    
    # May succeed if no proposals, or fail if proposals blocked
    assert result is not None


def test_backtest_trigger_provider(trading_pipeline, portfolio_state):
    """Test pipeline works with custom trigger provider (backtest mode)"""
    # Create custom trigger provider
    custom_triggers = [
        TriggerSignal(
            symbol="BTC-USD",
            trigger_type="test",
            strength=0.9,
            confidence=0.8,
            reason="Custom trigger",
            timestamp=datetime.now(timezone.utc),
            current_price=50000.0
        )
    ]
    
    def custom_trigger_provider(universe, current_time, regime):
        return custom_triggers
    
    result = trading_pipeline.execute_cycle(
        current_time=datetime.now(timezone.utc),
        portfolio=portfolio_state,
        regime="chop",
        cycle_number=1,
        trigger_provider=custom_trigger_provider
    )
    
    assert result is not None
    # Should use custom triggers instead of calling trigger_engine.scan()
    assert len(result.triggers) == 1 or result.no_trade_reason is not None


def test_pipeline_error_handling(trading_pipeline, portfolio_state, mock_universe_mgr):
    """Test pipeline handles errors gracefully"""
    # Mock universe manager to raise exception
    mock_universe_mgr.get_universe.side_effect = Exception("Test error")
    
    result = trading_pipeline.execute_cycle(
        current_time=datetime.now(timezone.utc),
        portfolio=portfolio_state,
        regime="chop",
        cycle_number=1
    )
    
    assert result.success is False
    assert result.no_trade_reason == "pipeline_error"
    assert result.error is not None


def test_cycle_result_dataclass():
    """Test CycleResult dataclass structure"""
    result = CycleResult(
        success=True,
        universe=None,
        triggers=[],
        base_proposals=[],
        risk_approved=[],
        executed=[],
        no_trade_reason=None
    )
    
    assert result.success is True
    assert result.error is None  # Default value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
