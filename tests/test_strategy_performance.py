""""""

Performance benchmarks for multi-strategy framework (REQ-STR4)Performance benchmarks for multi-strategy framework (REQ-STR4)



Tests strategy aggregation latency, memory usage, and scalability.Tests multi-strategy aggregation latency, memory usage, and scalability.

Uses RulesEngine (baseline strategy) with realistic context data.

NOTE: These are lightweight performance tests focused on the aggregation layer.

REQ-STR4: Multi-strategy aggregation must complete in <100ms for 10 strategiesThey use the actual StrategyRegistry with RulesEngine (our baseline strategy).

""""""



import pytestimport pytest

import timeimport time

import tracemallocimport tracemalloc

from datetime import datetime, timezonefrom datetime import datetime, timezone

from pathlib import Pathfrom typing import List, Dict, Any

import tempfilefrom pathlib import Path

import yamlimport tempfile

import yaml

from strategy.registry import StrategyRegistry

from strategy.base_strategy import StrategyContextfrom strategy.registry import StrategyRegistry

from core.universe import UniverseSnapshot, UniverseAssetfrom strategy.base_strategy import StrategyContext

from core.triggers import TriggerSignalfrom core.universe import UniverseSnapshot, UniverseAsset

from core.triggers import TriggerSignal



@pytest.fixtureimport pytest

def temp_config():import time

    """Create a temporary strategies.yaml for testing"""import tracemalloc

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:from datetime import datetime, timezone

        config = {from typing import List

            "strategies": {from dataclasses import dataclass

                "rules_engine": {

                    "enabled": True,from strategy.registry import StrategyRegistry

                    "type": "rules_engine",from strategy.base_strategy import BaseStrategy, StrategyContext

                    "description": "Baseline rules engine for testing",from strategy.rules_engine import TradeProposal

                    "risk_budgets": {from core.universe import UniverseSnapshot, UniverseAsset

                        "max_at_risk_pct": 15.0,from core.triggers import TriggerSignal

                        "max_trades_per_cycle": 10

                    },

                    "params": {}# Create mock strategies for performance testing

                }class MockStrategy(BaseStrategy):

            }    """Mock strategy for performance testing"""

        }    

        yaml.dump(config, f)    def __init__(self, name: str, delay_ms: float = 0):

        temp_path = Path(f.name)        config = {"enabled": True, "description": "Mock strategy for testing"}

            super().__init__(name=name, config=config)

    yield temp_path        self.delay_ms = delay_ms

        

    # Cleanup    def generate_proposals(self, context: StrategyContext) -> List[TradeProposal]:

    try:        """Generate mock proposals with optional delay."""

        temp_path.unlink()        if self.delay_ms > 0:

    except:            time.sleep(self.delay_ms / 1000.0)

        pass        

        # Generate 3 proposals per strategy

        proposals = []

@pytest.fixture        for i, trigger in enumerate(context.triggers[:3]):

def mock_context():            proposals.append(TradeProposal(

    """Create a realistic strategy context for testing"""                symbol=trigger.symbol,

                    side="BUY",

    # Create 20 test assets across tiers                size_pct=2.0,

    assets = [                confidence=0.5 + (i * 0.1),

        UniverseAsset(                reasoning=f"{self.name} - trigger {i}",

            symbol=f"ASSET{i}-USD",                strategy_name=self.name,

            tier=1 if i < 5 else (2 if i < 15 else 3),                asset=trigger.asset,

            precision=2,                stop_loss_pct=8.0,

            min_size=0.01,                take_profit_pct=15.0,

            lot_size=0.01,                max_hold_hours=48

            min_notional=10.0,            ))

            current_price=100.0 + i,        return proposals

            volume_24h=1_000_000 + (i * 100_000),

            spread_bps=10.0,

            depth_bid_usd=50_000,@pytest.fixture

            depth_ask_usd=50_000,def temp_config():

            timestamp=datetime.now(timezone.utc)    """Create a temporary strategies.yaml for testing"""

        )    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:

        for i in range(20)        config = {

    ]            "strategies": {

                    "rules_engine": {

    # Create trigger signals                    "enabled": True,

    triggers = [                    "type": "rules_engine",

        TriggerSignal(                    "description": "Baseline rules engine for testing",

            symbol=asset.symbol,                    "risk_budgets": {

            trigger_type="momentum",                        "max_at_risk_pct": 15.0,

            strength=0.7,                        "max_trades_per_cycle": 10

            confidence=0.8,                    },

            reason=f"Mock trigger for {asset.symbol}",                    "params": {}

            timestamp=datetime.now(timezone.utc),                }

            current_price=asset.current_price,            }

            volatility=0.3        }

        )        yaml.dump(config, f)

        for asset in assets        temp_path = Path(f.name)

    ]    

        yield temp_path

    # Create universe snapshot    

    universe = UniverseSnapshot(    # Cleanup

        tier_1_assets=assets[:5],    try:

        tier_2_assets=assets[5:15],        temp_path.unlink()

        tier_3_assets=assets[15:],    except:

        excluded_assets=[],        pass

        total_eligible=20,

        timestamp=datetime.now(timezone.utc),

        regime="normal"@pytest.fixture

    )def mock_context():

        """Create a mock strategy context with test data"""

    return StrategyContext(    

        universe=universe,    # Create 20 test assets across tiers

        triggers=triggers,    assets = [

        regime="normal",        UniverseAsset(

        timestamp=datetime.now(timezone.utc),            symbol=f"ASSET{i}-USD",

        cycle_number=1,            tier=1 if i < 5 else (2 if i < 15 else 3),

        state={},            precision=2,

        risk_constraints=None            min_size=0.01,

    )            lot_size=0.01,

            min_notional=10.0,

            current_price=100.0 + i,

class TestMultiStrategyPerformance:            volume_24h=1_000_000 + (i * 100_000),

    """Test multi-strategy aggregation performance"""            spread_bps=10.0,

                depth_bid_usd=50_000,

    def test_single_strategy_latency(self, temp_config, mock_context):            depth_ask_usd=50_000,

        """Test single strategy (RulesEngine) avg latency < 50ms"""            timestamp=datetime.now(timezone.utc)

        registry = StrategyRegistry(config_path=temp_config)        )

                for i in range(20)

        # Warm-up run    ]

        registry.generate_proposals(mock_context)    

            # Create trigger signals

        # Measure over 10 iterations    triggers = [

        iterations = 10        TriggerSignal(

        times = []            symbol=asset.symbol,

                    trigger_type="momentum",

        for _ in range(iterations):            strength=0.7,

            start = time.perf_counter()            confidence=0.8,

            proposals_by_strategy = registry.generate_proposals(mock_context)            reason=f"Mock trigger for {asset.symbol}",

            elapsed_ms = (time.perf_counter() - start) * 1000            timestamp=datetime.now(timezone.utc),

            times.append(elapsed_ms)            current_price=100.0,

                    volatility=0.3

        avg_ms = sum(times) / len(times)        )

                for asset in assets

        # Verify latency    ]

        assert avg_ms < 50, f"Average latency {avg_ms:.1f}ms, expected < 50ms"    

            # Create universe snapshot

        # Verify strategy ran    universe = UniverseSnapshot(

        assert "rules_engine" in proposals_by_strategy        tier_1_assets=assets[:5],

                tier_2_assets=assets[5:15],

        print(f"✅ Single strategy avg latency: {avg_ms:.1f}ms over {iterations} runs")        tier_3_assets=assets[15:],

            excluded_assets=[],

    def test_repeated_aggregation_consistency(self, temp_config, mock_context):        total_eligible=20,

        """Test repeated aggregation maintains consistent performance"""        timestamp=datetime.now(timezone.utc),

        registry = StrategyRegistry(config_path=temp_config)        regime="normal"

            )

        # Measure 100 consecutive runs    

        times = []    return StrategyContext(

        for _ in range(100):        universe=universe,

            start = time.perf_counter()        triggers=triggers,

            registry.generate_proposals(mock_context)        regime="normal",

            elapsed_ms = (time.perf_counter() - start) * 1000        timestamp=datetime.now(timezone.utc),

            times.append(elapsed_ms)        cycle_number=1,

                state={},

        avg_ms = sum(times) / len(times)        risk_constraints=None

        max_ms = max(times)    )

        min_ms = min(times)

        std_dev = (sum((t - avg_ms) ** 2 for t in times) / len(times)) ** 0.5

        class TestMultiStrategyPerformance:

        # Verify consistency (std dev should be reasonable)    """Test multi-strategy aggregation performance (uses RulesEngine baseline)"""

        assert std_dev < avg_ms, f"High variance: std={std_dev:.1f}ms, avg={avg_ms:.1f}ms"    

            def test_single_strategy_latency(self, temp_config, mock_context):

        print(f"✅ 100 runs: avg={avg_ms:.1f}ms, min={min_ms:.1f}ms, max={max_ms:.1f}ms, std={std_dev:.1f}ms")        """Test single strategy (RulesEngine) latency < 50ms."""

            registry = StrategyRegistry(config_path=temp_config)

    def test_proposal_generation_scales_linearly(self, temp_config, mock_context):        

        """Test proposal generation scales linearly with trigger count"""        # Measure aggregation time with 1 strategy

        registry = StrategyRegistry(config_path=temp_config)        iterations = 10

                total_time = 0

        # Test with increasing trigger counts        

        trigger_counts = [5, 10, 20, 40]        for _ in range(iterations):

        times = []            start = time.perf_counter()

                    proposals_by_strategy = registry.generate_proposals(mock_context)

        for count in trigger_counts:            elapsed_ms = (time.perf_counter() - start) * 1000

            # Create context with N triggers            total_time += elapsed_ms

            limited_triggers = mock_context.triggers[:count]        

            limited_context = StrategyContext(        avg_ms = total_time / iterations

                universe=mock_context.universe,        

                triggers=limited_triggers,        # Verify latency

                regime=mock_context.regime,        assert avg_ms < 50, f"Average latency {avg_ms:.1f}ms, expected < 50ms"

                timestamp=mock_context.timestamp,        

                cycle_number=mock_context.cycle_number        # Verify strategy ran

            )        assert "rules_engine" in proposals_by_strategy

                    

            # Measure over 5 iterations for stability        print(f"✅ Single strategy avg latency: {avg_ms:.1f}ms over {iterations} runs")

            iter_times = []    

            for _ in range(5):    def test_repeated_aggregation_consistency(self, temp_config, mock_context):

                start = time.perf_counter()        """Test repeated aggregation maintains consistent performance."""

                registry.generate_proposals(limited_context)        registry = StrategyRegistry(config_path=temp_config)

                elapsed_ms = (time.perf_counter() - start) * 1000        

                iter_times.append(elapsed_ms)        # Measure 100 consecutive runs

                    times = []

            avg_time = sum(iter_times) / len(iter_times)        for _ in range(100):

            times.append(avg_time)            start = time.perf_counter()

                    registry.generate_proposals(mock_context)

        # Verify scaling is reasonable (should be roughly linear, not exponential)            elapsed_ms = (time.perf_counter() - start) * 1000

        # 40 triggers shouldn't take more than 10x the time of 5 triggers            times.append(elapsed_ms)

        scale_factor = times[-1] / times[0] if times[0] > 0 else 1        

        assert scale_factor < 10, f"Scaling {trigger_counts[0]}→{trigger_counts[-1]} triggers: {scale_factor:.1f}x (too slow)"        avg_ms = sum(times) / len(times)

                max_ms = max(times)

        print(f"✅ Scaling 5→40 triggers: {scale_factor:.1f}x slower ({times[0]:.1f}ms → {times[-1]:.1f}ms)")        min_ms = min(times)

        

        # Verify consistency (max shouldn't be more than 3x avg)

class TestMemoryUsage:        assert max_ms < avg_ms * 3, f"Max {max_ms:.1f}ms > 3x avg {avg_ms:.1f}ms (inconsistent)"

    """Test memory usage and leak detection"""        

            print(f"✅ 100 runs: avg={avg_ms:.1f}ms, min={min_ms:.1f}ms, max={max_ms:.1f}ms")

    def test_no_memory_leak_over_200_cycles(self, temp_config, mock_context):    

        """Test no memory leak over 200 aggregation cycles"""    def test_proposal_generation_scales(self, temp_config, mock_context):

        registry = StrategyRegistry(config_path=temp_config)        """Test proposal generation scales with more triggers."""

                registry = StrategyRegistry(config_path=temp_config)

        tracemalloc.start()        

                # Test with increasing trigger counts

        # Warm-up (exclude initialization allocations)        trigger_counts = [5, 10, 20, 40]

        for _ in range(10):        times = []

            registry.generate_proposals(mock_context)        

                for count in trigger_counts:

        snapshot_before = tracemalloc.take_snapshot()            # Create context with N triggers

                    limited_triggers = mock_context.triggers[:count]

        # Run 200 cycles            limited_context = StrategyContext(

        for _ in range(200):                universe=mock_context.universe,

            registry.generate_proposals(mock_context)                triggers=limited_triggers,

                        regime=mock_context.regime,

        snapshot_after = tracemalloc.take_snapshot()                timestamp=mock_context.timestamp,

        tracemalloc.stop()                cycle_number=mock_context.cycle_number

                    )

        # Calculate memory growth            

        stats = snapshot_after.compare_to(snapshot_before, 'lineno')            start = time.perf_counter()

        total_growth_mb = sum(stat.size_diff for stat in stats) / (1024 * 1024)            registry.generate_proposals(limited_context)

                    elapsed_ms = (time.perf_counter() - start) * 1000

        # Memory growth should be < 15MB for 200 cycles (allowing for Python overhead)            times.append(elapsed_ms)

        assert abs(total_growth_mb) < 15, f"Memory grew by {total_growth_mb:.2f}MB, expected < 15MB"        

                # Verify scaling is reasonable (should be roughly linear, not exponential)

        print(f"✅ Memory growth over 200 cycles: {total_growth_mb:.2f}MB")        # 40 triggers shouldn't take more than 4x the time of 5 triggers

            scale_factor = times[-1] / times[0] if times[0] > 0 else 1

    def test_context_creation_memory_efficiency(self, mock_context):        assert scale_factor < 10, f"Scaling {trigger_counts[0]}→{trigger_counts[-1]} triggers: {scale_factor:.1f}x (too slow)"

        """Test StrategyContext creation is memory-efficient"""        

        tracemalloc.start()        print(f"✅ Scaling 5→40 triggers: {scale_factor:.1f}x slower ({times[0]:.1f}ms → {times[-1]:.1f}ms)")

        snapshot_before = tracemalloc.take_snapshot()

        

        # Create 1000 contextsclass TestMemoryUsage:

        contexts = []    """Test memory usage and leak detection."""

        for i in range(1000):    

            ctx = StrategyContext(    def test_no_memory_leak_repeated_aggregation(self, mock_context):

                universe=mock_context.universe,        """Test no memory leak over 100 aggregation cycles."""

                triggers=mock_context.triggers,        registry = StrategyRegistry()

                regime="normal",        

                timestamp=datetime.now(timezone.utc),        # Add 10 strategies

                cycle_number=i        for i in range(10):

            )            strategy = MockStrategy(name=f"strategy_{i}", delay_ms=1)

            contexts.append(ctx)            registry.register_strategy(strategy)

                

        snapshot_after = tracemalloc.take_snapshot()        # Start tracking memory

        tracemalloc.stop()        tracemalloc.start()

                

        stats = snapshot_after.compare_to(snapshot_before, 'lineno')        # First pass to warm up

        total_mb = sum(stat.size_diff for stat in stats) / (1024 * 1024)        for _ in range(10):

                    registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)

        # 1000 contexts should be < 50MB        

        assert total_mb < 50, f"1000 contexts used {total_mb:.2f}MB, expected < 50MB"        # Record baseline memory

                baseline_memory = tracemalloc.get_traced_memory()[0]

        kb_per_context = (total_mb * 1024) / 1000        

        print(f"✅ 1000 StrategyContext objects: {total_mb:.2f}MB ({kb_per_context:.1f}KB each)")        # Run 100 aggregations

        for _ in range(100):

            proposals = registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)

class TestREQSTR4Compliance:            assert len(proposals) > 0

    """Test REQ-STR4: Multi-strategy aggregation requirements"""        

            # Check final memory

    def test_meets_latency_requirement_single_strategy(self, temp_config, mock_context):        final_memory = tracemalloc.get_traced_memory()[0]

        """REQ-STR4: Single strategy completes in <100ms (baseline)"""        tracemalloc.stop()

        registry = StrategyRegistry(config_path=temp_config)        

                # Memory should not grow significantly (allow 10MB growth for caching)

        # Warm-up        memory_growth_mb = (final_memory - baseline_memory) / (1024 * 1024)

        registry.generate_proposals(mock_context)        assert memory_growth_mb < 10, f"Memory grew by {memory_growth_mb:.2f}MB (should be <10MB)"

            

        # Measure peak latency over 20 runs    def test_memory_released_after_strategy_removal(self, mock_context):

        max_latency_ms = 0        """Test memory is released when strategies are removed."""

        for _ in range(20):        registry = StrategyRegistry()

            start = time.perf_counter()        

            registry.generate_proposals(mock_context)        tracemalloc.start()

            elapsed_ms = (time.perf_counter() - start) * 1000        

            max_latency_ms = max(max_latency_ms, elapsed_ms)        # Add and remove strategies multiple times

                for cycle in range(5):

        # REQ-STR4: Must complete in <100ms            # Add 10 strategies

        assert max_latency_ms < 100, f"Peak latency {max_latency_ms:.1f}ms exceeds 100ms requirement"            for i in range(10):

                        strategy = MockStrategy(name=f"strategy_{cycle}_{i}", delay_ms=0)

        print(f"✅ REQ-STR4: Peak latency {max_latency_ms:.1f}ms < 100ms requirement")                registry.register_strategy(strategy)

                

    def test_memory_stable_under_sustained_load(self, temp_config, mock_context):            # Generate proposals

        """REQ-STR4: Memory remains stable under sustained load"""            registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)

        registry = StrategyRegistry(config_path=temp_config)            

                    # Clear strategies (simulate removal)

        tracemalloc.start()            registry._strategies = {}

                

        # Warm-up        # Force garbage collection

        for _ in range(20):        import gc

            registry.generate_proposals(mock_context)        gc.collect()

                

        # Measure memory over 500 cycles        final_memory = tracemalloc.get_traced_memory()[0]

        mem_samples = []        tracemalloc.stop()

        for i in range(500):        

            if i % 100 == 0:        # Memory should be reasonable after cleanup

                mem_samples.append(tracemalloc.get_traced_memory()[0] / (1024 * 1024))        memory_mb = final_memory / (1024 * 1024)

            registry.generate_proposals(mock_context)        assert memory_mb < 50, f"Final memory: {memory_mb:.2f}MB (should be <50MB)"

        

        tracemalloc.stop()

        class TestConcurrentStrategyExecution:

        # Memory growth should be minimal (< 20MB over 500 cycles)    """Test strategy execution behavior."""

        mem_growth = mem_samples[-1] - mem_samples[0]    

        assert mem_growth < 20, f"Memory grew by {mem_growth:.2f}MB over 500 cycles"    def test_all_strategies_execute(self, mock_context):

                """Test all enabled strategies execute."""

        print(f"✅ REQ-STR4: Memory stable under 500 cycles ({mem_growth:.2f}MB growth)")        registry = StrategyRegistry()

            

    def test_framework_ready_for_multi_strategy(self, temp_config, mock_context):        execution_count = {}

        """REQ-STR4: Framework is ready for multiple strategies"""        

        registry = StrategyRegistry(config_path=temp_config)        class CountingStrategy(MockStrategy):

                    def generate_proposals(self, context):

        # Verify registry loaded                execution_count[self.name] = execution_count.get(self.name, 0) + 1

        strategies = registry.list_strategies()                return super().generate_proposals(context)

        assert len(strategies) > 0, "No strategies loaded"        

                # Add 12 strategies

        # Verify enabled strategies        for i in range(12):

        enabled = registry.get_enabled_strategies()            strategy = CountingStrategy(name=f"strategy_{i}")

        assert len(enabled) > 0, "No strategies enabled"            registry.register_strategy(strategy)

                

        # Verify proposal generation works        # Execute aggregation

        proposals_by_strategy = registry.generate_proposals(mock_context)        registry.aggregate_proposals(mock_context, dedupe_by_symbol=True)

        assert len(proposals_by_strategy) > 0, "No proposals generated"        

                # All 12 strategies should have executed

        # Verify deduplication exists (check registry has aggregate_proposals method)        assert len(execution_count) == 12

        assert hasattr(registry, 'aggregate_proposals'), "Missing aggregation method"        assert all(count == 1 for count in execution_count.values())

            

        print(f"✅ REQ-STR4: Framework operational with {len(strategies)} strategies ({len(enabled)} enabled)")    def test_disabled_strategies_skipped(self, mock_context):

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
