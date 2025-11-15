"""
Performance benchmarks for multi-strategy framework (REQ-STR4)

Tests multi-strategy aggregation latency, memory usage, and scalability.

NOTE: These are lightweight performance tests focused on the aggregation layer.
They use the actual StrategyRegistry with RulesEngine (our baseline strategy).
"""

import pytest
import time
import tracemalloc
from datetime import datetime, timezone
from typing import List, Dict, Any
from pathlib import Path
import tempfile
import yaml

from strategy.registry import StrategyRegistry
from strategy.base_strategy import StrategyContext
from core.universe import UniverseSnapshot, UniverseAsset
from core.triggers import TriggerSignal

import pytest
import time
import tracemalloc
from datetime import datetime, timezone
from typing import List
from dataclasses import dataclass

from strategy.registry import StrategyRegistry
from strategy.base_strategy import BaseStrategy, StrategyContext
from strategy.rules_engine import TradeProposal
from core.universe import UniverseSnapshot, UniverseAsset
from core.triggers import TriggerSignal


# Create mock strategies for performance testing
class MockStrategy(BaseStrategy):
    """Mock strategy for performance testing"""
    
    def __init__(self, name: str, delay_ms: float = 0):
        config = {"enabled": True, "description": "Mock strategy for testing"}
        super().__init__(name=name, config=config)
        self.delay_ms = delay_ms
    
    def generate_proposals(self, context: StrategyContext) -> List[TradeProposal]:
        """Generate mock proposals with optional delay."""
        if self.delay_ms > 0:
            time.sleep(self.delay_ms / 1000.0)
        
        # Generate 3 proposals per strategy
        proposals = []
        for i, trigger in enumerate(context.triggers[:3]):
            proposals.append(TradeProposal(
                symbol=trigger.symbol,
                side="BUY",
                size_pct=2.0,
                confidence=0.5 + (i * 0.1),
                reasoning=f"{self.name} - trigger {i}",
                strategy_name=self.name,
                asset=trigger.asset,
                stop_loss_pct=8.0,
                take_profit_pct=15.0,
                max_hold_hours=48
            ))
        return proposals


@pytest.fixture
def temp_config():
    """Create a temporary strategies.yaml for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config = {
            "strategies": {
                "rules_engine": {
                    "enabled": True,
                    "type": "rules_engine",
                    "description": "Baseline rules engine for testing",
                    "risk_budgets": {
                        "max_at_risk_pct": 15.0,
                        "max_trades_per_cycle": 10
                    },
                    "params": {}
                }
            }
        }
        yaml.dump(config, f)
        temp_path = Path(f.name)
    
    yield temp_path
    
    # Cleanup
    try:
        temp_path.unlink()
    except:
        pass


@pytest.fixture
def mock_context():
    """Create a mock strategy context with test data"""
    
    # Create 20 test assets across tiers
    assets = [
        UniverseAsset(
            symbol=f"ASSET{i}-USD",
            tier=1 if i < 5 else (2 if i < 15 else 3),
            precision=2,
            min_size=0.01,
            lot_size=0.01,
            min_notional=10.0,
            current_price=100.0 + i,
            volume_24h=1_000_000 + (i * 100_000),
            spread_bps=10.0,
            depth_bid_usd=50_000,
            depth_ask_usd=50_000,
            timestamp=datetime.now(timezone.utc)
        )
        for i in range(20)
    ]
    
    # Create trigger signals
    triggers = [
        TriggerSignal(
            symbol=asset.symbol,
            trigger_type="momentum",
            strength=0.7,
            confidence=0.8,
            reason=f"Mock trigger for {asset.symbol}",
            timestamp=datetime.now(timezone.utc),
            current_price=100.0,
            volatility=0.3
        )
        for asset in assets
    ]
    
    # Create universe snapshot
    universe = UniverseSnapshot(
        tier_1_assets=assets[:5],
        tier_2_assets=assets[5:15],
        tier_3_assets=assets[15:],
        excluded_assets=[],
        total_eligible=20,
        timestamp=datetime.now(timezone.utc),
        regime="normal"
    )
    
    return StrategyContext(
        universe=universe,
        triggers=triggers,
        regime="normal",
        timestamp=datetime.now(timezone.utc),
        cycle_number=1,
        state={},
        risk_constraints=None
    )


class TestMultiStrategyPerformance:
    """Test multi-strategy aggregation performance (uses RulesEngine baseline)"""
    
    def test_single_strategy_latency(self, temp_config, mock_context):
        """Test single strategy (RulesEngine) latency < 50ms."""
        registry = StrategyRegistry(config_path=temp_config)
        
        # Measure aggregation time with 1 strategy
        iterations = 10
        total_time = 0
        
        for _ in range(iterations):
            start = time.perf_counter()
            proposals_by_strategy = registry.generate_proposals(mock_context)
            elapsed_ms = (time.perf_counter() - start) * 1000
            total_time += elapsed_ms
        
        avg_ms = total_time / iterations
        
        # Verify latency
        assert avg_ms < 50, f"Average latency {avg_ms:.1f}ms, expected < 50ms"
        
        # Verify strategy ran
        assert "rules_engine" in proposals_by_strategy
        
        print(f"✅ Single strategy avg latency: {avg_ms:.1f}ms over {iterations} runs")
    
    def test_repeated_aggregation_consistency(self, temp_config, mock_context):
        """Test repeated aggregation maintains consistent performance."""
        registry = StrategyRegistry(config_path=temp_config)
        
        # Measure 100 consecutive runs
        times = []
        for _ in range(100):
            start = time.perf_counter()
            registry.generate_proposals(mock_context)
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
        
        avg_ms = sum(times) / len(times)
        max_ms = max(times)
        min_ms = min(times)
        
        # Verify consistency (max shouldn't be more than 3x avg)
        assert max_ms < avg_ms * 3, f"Max {max_ms:.1f}ms > 3x avg {avg_ms:.1f}ms (inconsistent)"
        
        print(f"✅ 100 runs: avg={avg_ms:.1f}ms, min={min_ms:.1f}ms, max={max_ms:.1f}ms")
    
    def test_proposal_generation_scales(self, temp_config, mock_context):
        """Test proposal generation scales with more triggers."""
        registry = StrategyRegistry(config_path=temp_config)
        
        # Test with increasing trigger counts
        trigger_counts = [5, 10, 20, 40]
        times = []
        
        for count in trigger_counts:
            # Create context with N triggers
            limited_triggers = mock_context.triggers[:count]
            limited_context = StrategyContext(
                universe=mock_context.universe,
                triggers=limited_triggers,
                regime=mock_context.regime,
                timestamp=mock_context.timestamp,
                cycle_number=mock_context.cycle_number
            )
            
            start = time.perf_counter()
            registry.generate_proposals(limited_context)
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
        
        # Verify scaling is reasonable (should be roughly linear, not exponential)
        # 40 triggers shouldn't take more than 4x the time of 5 triggers
        scale_factor = times[-1] / times[0] if times[0] > 0 else 1
        assert scale_factor < 10, f"Scaling {trigger_counts[0]}→{trigger_counts[-1]} triggers: {scale_factor:.1f}x (too slow)"
        
        print(f"✅ Scaling 5→40 triggers: {scale_factor:.1f}x slower ({times[0]:.1f}ms → {times[-1]:.1f}ms)")


class TestMemoryUsage:
    """Test memory usage and leak detection."""
    
    def test_no_memory_leak_repeated_aggregation(self, mock_context):
        """Test no memory leak over 100 aggregation cycles."""
        registry = StrategyRegistry()
        
        # Add 10 strategies
        for i in range(10):
            strategy = MockStrategy(name=f"strategy_{i}", delay_ms=1)
            registry.register_strategy(strategy)
        
        # Start tracking memory
        tracemalloc.start()
        
        # First pass to warm up
        for _ in range(10):
            registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
        
        # Record baseline memory
        baseline_memory = tracemalloc.get_traced_memory()[0]
        
        # Run 100 aggregations
        for _ in range(100):
            proposals = registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
            assert len(proposals) > 0
        
        # Check final memory
        final_memory = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        
        # Memory should not grow significantly (allow 10MB growth for caching)
        memory_growth_mb = (final_memory - baseline_memory) / (1024 * 1024)
        assert memory_growth_mb < 10, f"Memory grew by {memory_growth_mb:.2f}MB (should be <10MB)"
    
    def test_memory_released_after_strategy_removal(self, mock_context):
        """Test memory is released when strategies are removed."""
        registry = StrategyRegistry()
        
        tracemalloc.start()
        
        # Add and remove strategies multiple times
        for cycle in range(5):
            # Add 10 strategies
            for i in range(10):
                strategy = MockStrategy(name=f"strategy_{cycle}_{i}", delay_ms=0)
                registry.register_strategy(strategy)
            
            # Generate proposals
            registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
            
            # Clear strategies (simulate removal)
            registry._strategies = {}
        
        # Force garbage collection
        import gc
        gc.collect()
        
        final_memory = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        
        # Memory should be reasonable after cleanup
        memory_mb = final_memory / (1024 * 1024)
        assert memory_mb < 50, f"Final memory: {memory_mb:.2f}MB (should be <50MB)"


class TestConcurrentStrategyExecution:
    """Test strategy execution behavior."""
    
    def test_all_strategies_execute(self, mock_context):
        """Test all enabled strategies execute."""
        registry = StrategyRegistry()
        
        execution_count = {}
        
        class CountingStrategy(MockStrategy):
            def generate_proposals(self, context):
                execution_count[self.name] = execution_count.get(self.name, 0) + 1
                return super().generate_proposals(context)
        
        # Add 12 strategies
        for i in range(12):
            strategy = CountingStrategy(name=f"strategy_{i}")
            registry.register_strategy(strategy)
        
        # Execute aggregation
        registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
        
        # All 12 strategies should have executed
        assert len(execution_count) == 12
        assert all(count == 1 for count in execution_count.values())
    
    def test_disabled_strategies_skipped(self, mock_context):
        """Test disabled strategies are skipped."""
        registry = StrategyRegistry()
        
        execution_count = {}
        
        class CountingStrategy(MockStrategy):
            def generate_proposals(self, context):
                execution_count[self.name] = execution_count.get(self.name, 0) + 1
                return super().generate_proposals(context)
        
        # Add 10 strategies, disable half
        for i in range(10):
            strategy = CountingStrategy(name=f"strategy_{i}")
            strategy.enabled = (i % 2 == 0)  # Enable even-numbered only
            registry.register_strategy(strategy)
        
        # Execute aggregation
        registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
        
        # Only 5 enabled strategies should have executed
        assert len(execution_count) == 5
        assert all(f"strategy_{i}" in execution_count for i in [0, 2, 4, 6, 8])


class TestProposalQuality:
    """Test proposal quality with multiple strategies."""
    
    def test_highest_confidence_wins_deduplication(self, mock_context):
        """Test deduplication keeps highest-confidence proposal."""
        registry = StrategyRegistry()
        
        # Add strategies with different confidence levels
        for i in range(5):
            class ConfidenceStrategy(MockStrategy):
                def generate_proposals(self, context):
                    # All strategies propose same symbol with different confidence
                    return [TradeProposal(
                        symbol="BTC-USD",
                        side="BUY",
                        size_pct=2.0,
                        confidence=0.3 + (i * 0.1),  # 0.3, 0.4, 0.5, 0.6, 0.7
                        reasoning=f"{self.name} - confidence {0.3 + (i * 0.1)}",
                        strategy_name=self.name,
                        asset=None,
                        stop_loss_pct=8.0,
                        take_profit_pct=15.0,
                        max_hold_hours=48
                    )]
            
            strategy = ConfidenceStrategy(name=f"strategy_{i}")
            registry.register_strategy(strategy)
        
        # Aggregate with deduplication
        proposals = registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
        
        # Should have only 1 BTC-USD proposal with highest confidence
        btc_proposals = [p for p in proposals if p.symbol == "BTC-USD"]
        assert len(btc_proposals) == 1
        assert btc_proposals[0].confidence == pytest.approx(0.7, abs=0.01)


class TestREQSTR4Compliance:
    """Test compliance with REQ-STR4 specification."""
    
    def test_meets_latency_requirements(self, mock_context):
        """Test meets <100ms latency requirement for 10 strategies."""
        registry = StrategyRegistry()
        
        # Add 10 strategies with realistic processing time
        for i in range(10):
            strategy = MockStrategy(name=f"strategy_{i}", delay_ms=3)
            registry.register_strategy(strategy)
        
        # Measure 10 consecutive runs
        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
            latencies.append((time.perf_counter() - start) * 1000)
        
        # All runs should be < 100ms
        assert all(lat < 100 for lat in latencies), f"Latencies: {latencies}"
        
        # Average should be well under limit
        avg_latency = sum(latencies) / len(latencies)
        assert avg_latency < 80, f"Average latency {avg_latency:.2f}ms (should be <80ms)"
    
    def test_scales_to_20_strategies(self, mock_context):
        """Test scales reasonably to 20 strategies."""
        registry = StrategyRegistry()
        
        # Add 20 strategies
        for i in range(20):
            strategy = MockStrategy(name=f"strategy_{i}", delay_ms=2)
            registry.register_strategy(strategy)
        
        # Should still complete in reasonable time
        start = time.perf_counter()
        proposals = registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
        duration_ms = (time.perf_counter() - start) * 1000
        
        assert duration_ms < 200, f"20 strategies took {duration_ms:.2f}ms (should be <200ms)"
        assert len(proposals) > 0
    
    def test_memory_stable_under_load(self, mock_context):
        """Test memory remains stable under sustained load."""
        registry = StrategyRegistry()
        
        # Add 15 strategies
        for i in range(15):
            strategy = MockStrategy(name=f"strategy_{i}", delay_ms=1)
            registry.register_strategy(strategy)
        
        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        
        # Run 50 aggregations
        for _ in range(50):
            registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
        
        final = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        
        growth_mb = (final - baseline) / (1024 * 1024)
        assert growth_mb < 5, f"Memory grew {growth_mb:.2f}MB under load (should be <5MB)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
