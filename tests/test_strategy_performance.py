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
def mock_context():
    """Create mock StrategyContext for testing."""
    # Create mock assets
    assets = [
        UniverseAsset(
            symbol=f"SYMBOL{i}-USD",
            tier=1 if i < 5 else (2 if i < 15 else 3),
            allocation_min_pct=1.0,
            allocation_max_pct=10.0,
            volume_24h=1000000,
            spread_bps=10,
            depth_usd=50000,
            eligible=True
        )
        for i in range(20)
    ]
    
    # Create mock triggers
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
        for i, asset in enumerate(assets)
    ]
    
    # Create mock universe
    universe = UniverseSnapshot(
        tier_1_assets=[a for a in assets if a.tier == 1],
        tier_2_assets=[a for a in assets if a.tier == 2],
        tier_3_assets=[a for a in assets if a.tier == 3],
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
        state={}
    )


class TestMultiStrategyPerformance:
    """Test performance with 10+ strategies."""
    
    def test_aggregation_latency_10_strategies(self, mock_context):
        """Test aggregation latency with 10 strategies < 100ms."""
        registry = StrategyRegistry()
        
        # Add 10 strategies
        for i in range(10):
            strategy = MockStrategy(name=f"strategy_{i}", delay_ms=5)
            registry.register_strategy(strategy)
        
        # Measure aggregation time
        start = time.perf_counter()
        proposals = registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
        duration_ms = (time.perf_counter() - start) * 1000
        
        assert duration_ms < 100, f"Aggregation took {duration_ms:.2f}ms (should be <100ms)"
        assert len(proposals) > 0
        assert len(proposals) <= 20  # Should dedupe to max 20 symbols
    
    def test_aggregation_latency_20_strategies(self, mock_context):
        """Test aggregation latency with 20 strategies < 200ms."""
        registry = StrategyRegistry()
        
        # Add 20 strategies
        for i in range(20):
            strategy = MockStrategy(name=f"strategy_{i}", delay_ms=3)
            registry.register_strategy(strategy)
        
        # Measure aggregation time
        start = time.perf_counter()
        proposals = registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
        duration_ms = (time.perf_counter() - start) * 1000
        
        assert duration_ms < 200, f"Aggregation took {duration_ms:.2f}ms (should be <200ms)"
        assert len(proposals) > 0
    
    def test_deduplication_performance(self, mock_context):
        """Test deduplication performance with many duplicate symbols."""
        registry = StrategyRegistry()
        
        # Add 15 strategies (will create many duplicates for same symbols)
        for i in range(15):
            strategy = MockStrategy(name=f"strategy_{i}", delay_ms=1)
            registry.register_strategy(strategy)
        
        # Measure deduplication time
        start = time.perf_counter()
        proposals = registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)
        duration_ms = (time.perf_counter() - start) * 1000
        
        # Should dedupe to unique symbols only
        symbols = {p.symbol for p in proposals}
        assert len(symbols) == len(proposals), "Deduplication failed - duplicate symbols found"
        assert len(proposals) <= 20, "Should have at most 20 unique proposals"
        
        # Deduplication should be fast
        assert duration_ms < 150, f"Deduplication took {duration_ms:.2f}ms (should be <150ms)"


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
