"""
Tests for Multi-Strategy Framework

Covers:
- REQ-STR1: Pure strategy interface (no direct exchange calls)
- REQ-STR2: Per-strategy feature flags (enable/disable toggles)
- REQ-STR3: Per-strategy risk budgets (enforced before global caps)

Tests BaseStrategy, StrategyRegistry, and RulesEngine compatibility.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from datetime import datetime, timezone
from typing import List

from strategy.base_strategy import BaseStrategy, StrategyContext
from strategy.registry import StrategyRegistry
from strategy.rules_engine import RulesEngine, TradeProposal
from core.universe import UniverseSnapshot, UniverseAsset
from core.triggers import TriggerSignal


# ==============================================================================
# Test Fixtures
# ==============================================================================

@pytest.fixture
def mock_universe():
    """Create mock universe snapshot."""
    tier_1 = [
        UniverseAsset(
            symbol="BTC-USD",
            tier=1,
            allocation_min_pct=2.0,
            allocation_max_pct=15.0,
            volume_24h=1000000000.0,
            spread_bps=5.0,
            depth_usd=500000.0,
            eligible=True
        )
    ]
    tier_2 = [
        UniverseAsset(
            symbol="ETH-USD",
            tier=2,
            allocation_min_pct=1.0,
            allocation_max_pct=10.0,
            volume_24h=500000000.0,
            spread_bps=8.0,
            depth_usd=300000.0,
            eligible=True
        )
    ]
    
    return UniverseSnapshot(
        timestamp=datetime.now(timezone.utc),
        regime="chop",
        tier_1_assets=tier_1,
        tier_2_assets=tier_2,
        tier_3_assets=[],
        excluded_assets=[],
        total_eligible=2
    )


@pytest.fixture
def mock_triggers():
    """Create mock trigger signals."""
    return [
        TriggerSignal(
            symbol="BTC-USD",
            trigger_type="breakout",
            strength=0.8,
            confidence=0.9,
            reason="Price broke resistance",
            timestamp=datetime.now(timezone.utc),
            current_price=50000.0,
            volatility=0.05
        ),
        TriggerSignal(
            symbol="ETH-USD",
            trigger_type="volume_spike",
            strength=0.7,
            confidence=0.8,
            reason="Volume 2x average",
            timestamp=datetime.now(timezone.utc),
            current_price=3000.0,
            volatility=0.06
        )
    ]


@pytest.fixture
def strategy_context(mock_universe, mock_triggers):
    """Create strategy context."""
    return StrategyContext(
        universe=mock_universe,
        triggers=mock_triggers,
        regime="chop",
        timestamp=datetime.now(timezone.utc),
        cycle_number=1
    )


@pytest.fixture
def temp_strategy_config(tmp_path):
    """Create temporary strategy config file."""
    config_path = tmp_path / "strategies.yaml"
    config_content = """
strategies:
  rules_engine:
    enabled: true
    type: rules_engine
    description: Test strategy
    risk_budgets:
      max_at_risk_pct: 10.0
      max_trades_per_cycle: 3
    params: {}
  
  disabled_strategy:
    enabled: false
    type: rules_engine
    description: Disabled for testing
    risk_budgets:
      max_at_risk_pct: 5.0
      max_trades_per_cycle: 2
    params: {}
"""
    config_path.write_text(config_content)
    return config_path


# ==============================================================================
# Mock Strategy for Testing
# ==============================================================================

class MockStrategy(BaseStrategy):
    """Mock strategy for testing BaseStrategy interface."""
    
    def generate_proposals(self, context: StrategyContext) -> List[TradeProposal]:
        """Generate mock proposals."""
        if not context.triggers:
            return []
        
        proposals = []
        for trigger in context.triggers[:2]:  # Limit to 2
            proposals.append(TradeProposal(
                symbol=trigger.symbol,
                side="BUY",
                size_pct=2.0,
                reason=f"Mock strategy: {trigger.trigger_type}",
                confidence=trigger.confidence,
                trigger=trigger
            ))
        
        return proposals


# ==============================================================================
# Test BaseStrategy Interface (REQ-STR1)
# ==============================================================================

class TestBaseStrategyInterface:
    """Test REQ-STR1: Pure strategy interface."""
    
    def test_base_strategy_is_abstract(self):
        """Verify BaseStrategy cannot be instantiated directly."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BaseStrategy(name="test", config={})
    
    def test_base_strategy_requires_generate_proposals(self):
        """Verify generate_proposals() must be implemented."""
        # Create incomplete strategy class
        class IncompleteStrategy(BaseStrategy):
            pass
        
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteStrategy(name="incomplete", config={})
    
    def test_base_strategy_init(self):
        """Test BaseStrategy initialization."""
        config = {
            "enabled": True,
            "description": "Test strategy",
            "risk_budgets": {
                "max_at_risk_pct": 10.0,
                "max_trades_per_cycle": 5
            }
        }
        
        strategy = MockStrategy(name="test_strategy", config=config)
        
        assert strategy.name == "test_strategy"
        assert strategy.enabled is True
        assert strategy.description == "Test strategy"
        assert strategy.max_at_risk_pct == 10.0
        assert strategy.max_trades_per_cycle == 5
    
    def test_base_strategy_defaults_disabled(self):
        """Test REQ-STR2: New strategies default to disabled."""
        config = {"description": "Test"}  # No 'enabled' key
        
        strategy = MockStrategy(name="new_strategy", config=config)
        
        assert strategy.enabled is False
    
    def test_strategy_generate_proposals(self, strategy_context):
        """Test strategy generates proposals via interface."""
        config = {"enabled": True}
        strategy = MockStrategy(name="test", config=config)
        
        proposals = strategy.generate_proposals(strategy_context)
        
        assert len(proposals) == 2
        assert all(isinstance(p, TradeProposal) for p in proposals)
        assert proposals[0].symbol == "BTC-USD"
        assert proposals[1].symbol == "ETH-USD"
    
    def test_strategy_validate_proposals(self, strategy_context):
        """Test proposal validation and tagging."""
        config = {"enabled": True}
        strategy = MockStrategy(name="test_strat", config=config)
        
        proposals = strategy.run(strategy_context)
        
        # Check tags
        assert all("test_strat" in p.tags for p in proposals)
        
        # Check metadata
        assert all(p.metadata.get("strategy") == "test_strat" for p in proposals)
        assert all(p.metadata.get("strategy_enabled") is True for p in proposals)
    
    def test_strategy_max_trades_enforcement(self, strategy_context):
        """Test REQ-STR3: max_trades_per_cycle enforcement."""
        config = {
            "enabled": True,
            "risk_budgets": {
                "max_trades_per_cycle": 1  # Limit to 1
            }
        }
        strategy = MockStrategy(name="limited", config=config)
        
        proposals = strategy.run(strategy_context)
        
        # Should be limited to 1 despite generating 2
        assert len(proposals) == 1
    
    def test_strategy_disabled_returns_empty(self, strategy_context):
        """Test disabled strategies return no proposals."""
        config = {"enabled": False}
        strategy = MockStrategy(name="disabled", config=config)
        
        proposals = strategy.run(strategy_context)
        
        assert proposals == []
    
    def test_strategy_error_handling(self, strategy_context):
        """Test strategy errors are caught and logged."""
        class BrokenStrategy(BaseStrategy):
            def generate_proposals(self, context):
                raise ValueError("Intentional test error")
        
        config = {"enabled": True}
        strategy = BrokenStrategy(name="broken", config=config)
        
        # Should not raise, should return empty list
        proposals = strategy.run(strategy_context)
        assert proposals == []
    
    def test_invalid_proposal_side_rejected(self, strategy_context):
        """Test proposals with invalid side are rejected."""
        class InvalidSideStrategy(BaseStrategy):
            def generate_proposals(self, context):
                return [TradeProposal(
                    symbol="BTC-USD",
                    side="SHORT",  # Invalid (not BUY or SELL)
                    size_pct=2.0,
                    reason="Test",
                    confidence=0.8
                )]
        
        config = {"enabled": True}
        strategy = InvalidSideStrategy(name="invalid", config=config)
        
        proposals = strategy.run(strategy_context)
        assert len(proposals) == 0  # Invalid proposal rejected
    
    def test_invalid_confidence_rejected(self, strategy_context):
        """Test proposals with invalid confidence are rejected."""
        class InvalidConfidenceStrategy(BaseStrategy):
            def generate_proposals(self, context):
                return [TradeProposal(
                    symbol="BTC-USD",
                    side="BUY",
                    size_pct=2.0,
                    reason="Test",
                    confidence=1.5  # Invalid (> 1.0)
                )]
        
        config = {"enabled": True}
        strategy = InvalidConfidenceStrategy(name="invalid", config=config)
        
        proposals = strategy.run(strategy_context)
        assert len(proposals) == 0  # Invalid proposal rejected


# ==============================================================================
# Test StrategyRegistry (REQ-STR2)
# ==============================================================================

class TestStrategyRegistry:
    """Test REQ-STR2: Strategy registry and feature flags."""
    
    def test_registry_loads_strategies(self, temp_strategy_config):
        """Test registry loads strategies from config."""
        registry = StrategyRegistry(config_path=temp_strategy_config)
        
        assert len(registry.strategies) == 2
        assert "rules_engine" in registry.strategies
        assert "disabled_strategy" in registry.strategies
    
    def test_registry_respects_enabled_flag(self, temp_strategy_config):
        """Test registry respects enabled/disabled flags."""
        registry = StrategyRegistry(config_path=temp_strategy_config)
        
        enabled = registry.get_enabled_strategies()
        
        assert len(enabled) == 1
        assert enabled[0].name == "rules_engine"
    
    def test_registry_get_strategy_by_name(self, temp_strategy_config):
        """Test getting strategy by name."""
        registry = StrategyRegistry(config_path=temp_strategy_config)
        
        strategy = registry.get_strategy("rules_engine")
        
        assert strategy is not None
        assert strategy.name == "rules_engine"
        assert strategy.enabled is True
    
    def test_registry_list_strategies(self, temp_strategy_config):
        """Test listing all strategies with status."""
        registry = StrategyRegistry(config_path=temp_strategy_config)
        
        strategies = registry.list_strategies()
        
        assert len(strategies) == 2
        assert strategies["rules_engine"]["enabled"] is True
        assert strategies["disabled_strategy"]["enabled"] is False
    
    def test_registry_creates_default_config(self, tmp_path):
        """Test registry creates default config if missing."""
        nonexistent_path = tmp_path / "nonexistent.yaml"
        
        registry = StrategyRegistry(config_path=nonexistent_path)
        
        # Should have created default config with rules_engine
        assert nonexistent_path.exists()
        assert len(registry.strategies) >= 1
        assert "rules_engine" in registry.strategies
    
    def test_registry_generate_proposals_from_multiple(
        self,
        temp_strategy_config,
        strategy_context
    ):
        """Test generating proposals from multiple enabled strategies."""
        # Mock RulesEngine to avoid dependencies
        with patch('strategy.registry.RulesEngine') as MockRulesEngine:
            # Create mock strategy instance
            mock_strategy = MagicMock(spec=BaseStrategy)
            mock_strategy.name = "rules_engine"
            mock_strategy.enabled = True
            mock_strategy.run.return_value = [
                TradeProposal(
                    symbol="BTC-USD",
                    side="BUY",
                    size_pct=2.0,
                    reason="Test",
                    confidence=0.8
                )
            ]
            
            MockRulesEngine.return_value = mock_strategy
            
            registry = StrategyRegistry(config_path=temp_strategy_config)
            proposals_by_strategy = registry.generate_proposals(strategy_context)
            
            assert "rules_engine" in proposals_by_strategy
            assert len(proposals_by_strategy["rules_engine"]) > 0
    
    def test_registry_aggregate_proposals(self, temp_strategy_config, strategy_context):
        """Test proposal aggregation across strategies."""
        with patch('strategy.registry.RulesEngine') as MockRulesEngine:
            mock_strategy = MagicMock(spec=BaseStrategy)
            mock_strategy.name = "rules_engine"
            mock_strategy.enabled = True
            mock_strategy.run.return_value = [
                TradeProposal(
                    symbol="BTC-USD",
                    side="BUY",
                    size_pct=2.0,
                    reason="Test",
                    confidence=0.8,
                    metadata={}
                )
            ]
            
            MockRulesEngine.return_value = mock_strategy
            
            registry = StrategyRegistry(config_path=temp_strategy_config)
            aggregated = registry.aggregate_proposals(strategy_context)
            
            assert len(aggregated) > 0
            # Check strategy metadata added
            assert all(
                "strategy_source" in p.metadata
                for p in aggregated
            )
    
    def test_registry_dedupe_by_symbol(self, temp_strategy_config, strategy_context):
        """Test deduplication of proposals by symbol."""
        # Create registry with two strategies proposing same symbol
        registry = StrategyRegistry(config_path=temp_strategy_config)
        
        # Mock proposals from different strategies for same symbol
        proposals = [
            TradeProposal(
                symbol="BTC-USD",
                side="BUY",
                size_pct=2.0,
                reason="Strategy 1",
                confidence=0.8,
                metadata={"strategy_source": "strat1"}
            ),
            TradeProposal(
                symbol="BTC-USD",
                side="BUY",
                size_pct=3.0,
                reason="Strategy 2",
                confidence=0.9,  # Higher confidence
                metadata={"strategy_source": "strat2"}
            )
        ]
        
        deduped = registry._dedupe_proposals(proposals)
        
        # Should keep only highest confidence
        assert len(deduped) == 1
        assert deduped[0].confidence == 0.9
        assert deduped[0].metadata["strategy_source"] == "strat2"


# ==============================================================================
# Test RulesEngine Compatibility
# ==============================================================================

class TestRulesEngineCompatibility:
    """Test backward compatibility of RulesEngine with BaseStrategy."""
    
    def test_rules_engine_inherits_base_strategy(self):
        """Test RulesEngine is a BaseStrategy."""
        rules = RulesEngine(name="test", config={"enabled": True})
        
        assert isinstance(rules, BaseStrategy)
    
    def test_rules_engine_implements_generate_proposals(self, strategy_context):
        """Test RulesEngine implements generate_proposals()."""
        rules = RulesEngine(name="test", config={"enabled": True})
        
        # Should not raise
        proposals = rules.generate_proposals(strategy_context)
        
        assert isinstance(proposals, list)
    
    def test_rules_engine_backward_compat_propose_trades(self, mock_universe, mock_triggers):
        """Test existing propose_trades() still works."""
        rules = RulesEngine(name="test", config={"enabled": True})
        
        # Old interface should still work
        proposals = rules.propose_trades(
            universe=mock_universe,
            triggers=mock_triggers,
            regime="chop"
        )
        
        assert isinstance(proposals, list)
    
    def test_rules_engine_old_init_signature(self):
        """Test RulesEngine supports old __init__ signature."""
        # Old signature: __init__(config: Dict)
        config = {"some_key": "some_value"}
        
        # Should not raise
        rules = RulesEngine(config=config)
        
        assert rules.config == config


# ==============================================================================
# Test StrategyContext
# ==============================================================================

class TestStrategyContext:
    """Test StrategyContext dataclass."""
    
    def test_context_creation(self, mock_universe, mock_triggers):
        """Test creating valid context."""
        context = StrategyContext(
            universe=mock_universe,
            triggers=mock_triggers,
            regime="bull",
            timestamp=datetime.now(timezone.utc),
            cycle_number=5
        )
        
        assert context.universe == mock_universe
        assert context.triggers == mock_triggers
        assert context.regime == "bull"
        assert context.cycle_number == 5
    
    def test_context_timestamp_timezone_aware(self, mock_universe, mock_triggers):
        """Test context ensures timezone-aware timestamps."""
        naive_ts = datetime.now()  # No timezone
        
        context = StrategyContext(
            universe=mock_universe,
            triggers=mock_triggers,
            timestamp=naive_ts
        )
        
        # Should be converted to UTC
        assert context.timestamp.tzinfo is not None
    
    def test_context_validates_universe_type(self, mock_triggers):
        """Test context validates universe type."""
        with pytest.raises(TypeError, match="universe must be UniverseSnapshot"):
            StrategyContext(
                universe="invalid",  # Wrong type
                triggers=mock_triggers
            )
    
    def test_context_validates_triggers_type(self, mock_universe):
        """Test context validates triggers type."""
        with pytest.raises(TypeError, match="triggers must be list"):
            StrategyContext(
                universe=mock_universe,
                triggers="invalid"  # Wrong type
            )


# ==============================================================================
# Test REQ-STR1: No Exchange API Calls
# ==============================================================================

class TestStrategyIsolation:
    """Test REQ-STR1: Strategies don't call exchange APIs."""
    
    def test_base_strategy_no_exchange_dependency(self):
        """Test BaseStrategy doesn't import exchange modules."""
        import strategy.base_strategy as base_module
        
        # Check that exchange modules are not imported
        assert not hasattr(base_module, 'CoinbaseExchange')
        assert not hasattr(base_module, 'exchange')
    
    def test_mock_strategy_no_exchange_access(self, strategy_context):
        """Test strategies cannot access exchange via context."""
        config = {"enabled": True}
        strategy = MockStrategy(name="test", config=config)
        
        # Context should not have exchange
        assert not hasattr(strategy_context, 'exchange')
        assert not hasattr(strategy_context, 'place_order')
        
        # Strategy should generate proposals without exchange
        proposals = strategy.generate_proposals(strategy_context)
        assert isinstance(proposals, list)
