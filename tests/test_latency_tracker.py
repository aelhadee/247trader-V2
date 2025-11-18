"""
Tests for latency tracking infrastructure.
"""

import time
import pytest
from datetime import datetime, timezone
from infra.latency_tracker import LatencyTracker


class TestLatencyTracker:
    """Test suite for LatencyTracker"""
    
    def test_basic_measurement(self):
        """Test basic latency measurement recording"""
        tracker = LatencyTracker(retention_per_operation=10)
        
        # Record some measurements
        tracker.record("test_op", 100.5, {"status": "success"})
        tracker.record("test_op", 150.0, {"status": "success"})
        tracker.record("test_op", 200.0, {"status": "success"})
        
        stats = tracker.get_stats("test_op")
        assert stats is not None
        assert stats.count == 3
        assert stats.min_ms == 100.5
        assert stats.max_ms == 200.0
        assert abs(stats.mean_ms - 150.166) < 0.1
    
    def test_context_manager(self):
        """Test context manager for automatic timing"""
        tracker = LatencyTracker()
        
        with tracker.measure("sleep_test"):
            time.sleep(0.01)  # 10ms
        
        stats = tracker.get_stats("sleep_test")
        assert stats is not None
        assert stats.count == 1
        assert stats.mean_ms >= 10.0  # At least 10ms
        assert stats.mean_ms < 100.0   # But not absurdly long
    
    def test_context_manager_with_exception(self):
        """Test context manager records timing even on exception"""
        tracker = LatencyTracker()
        
        with pytest.raises(ValueError):
            with tracker.measure("error_test"):
                raise ValueError("Test error")
        
        stats = tracker.get_stats("error_test")
        assert stats is not None
        assert stats.count == 1
        
        # Check that exception flag was set in metadata
        measurements = tracker.get_recent_measurements("error_test", limit=1)
        assert len(measurements) == 1
        assert measurements[0].metadata.get("exception") is True
    
    def test_multiple_operations(self):
        """Test tracking multiple different operations"""
        tracker = LatencyTracker()
        
        tracker.record("op_a", 50.0)
        tracker.record("op_b", 100.0)
        tracker.record("op_c", 150.0)
        
        all_stats = tracker.get_all_stats()
        assert len(all_stats) == 3
        assert "op_a" in all_stats
        assert "op_b" in all_stats
        assert "op_c" in all_stats
        assert all_stats["op_a"].mean_ms == 50.0
        assert all_stats["op_b"].mean_ms == 100.0
        assert all_stats["op_c"].mean_ms == 150.0
    
    def test_retention_limit(self):
        """Test that retention limit is enforced"""
        tracker = LatencyTracker(retention_per_operation=5)
        
        # Record 10 measurements
        for i in range(10):
            tracker.record("limited_op", float(i * 10))
        
        stats = tracker.get_stats("limited_op")
        assert stats is not None
        # Should only keep last 5
        assert stats.count == 5
        # Should be measurements 5-9 (50ms to 90ms)
        assert stats.min_ms == 50.0
        assert stats.max_ms == 90.0
    
    def test_percentiles(self):
        """Test percentile calculations"""
        tracker = LatencyTracker()
        
        # Create measurements with known distribution
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        for val in values:
            tracker.record("percentile_test", float(val))
        
        stats = tracker.get_stats("percentile_test")
        assert stats is not None
        assert stats.p50_ms == 55.0  # Median
        assert stats.p95_ms == 95.5  # 95th percentile
        assert stats.p99_ms == 99.1  # 99th percentile
    
    def test_get_recent_measurements(self):
        """Test retrieving recent measurements"""
        tracker = LatencyTracker()
        
        for i in range(5):
            tracker.record("recent_test", float(i * 10), {"index": i})
            time.sleep(0.001)  # Ensure different timestamps
        
        recent = tracker.get_recent_measurements("recent_test", limit=3)
        assert len(recent) == 3
        # Should be in reverse chronological order (newest first)
        assert recent[0].metadata["index"] == 4
        assert recent[1].metadata["index"] == 3
        assert recent[2].metadata["index"] == 2
    
    def test_clear_operation(self):
        """Test clearing measurements for specific operation"""
        tracker = LatencyTracker()
        
        tracker.record("op1", 10.0)
        tracker.record("op2", 20.0)
        
        tracker.clear("op1")
        
        assert tracker.get_stats("op1") is None
        assert tracker.get_stats("op2") is not None
    
    def test_clear_all(self):
        """Test clearing all measurements"""
        tracker = LatencyTracker()
        
        tracker.record("op1", 10.0)
        tracker.record("op2", 20.0)
        tracker.record("op3", 30.0)
        
        tracker.clear()
        
        all_stats = tracker.get_all_stats()
        assert len(all_stats) == 0
    
    def test_check_threshold(self):
        """Test threshold checking"""
        tracker = LatencyTracker()
        
        tracker.record("threshold_test", 50.0)
        tracker.record("threshold_test", 100.0)
        tracker.record("threshold_test", 150.0)
        
        # Mean is 100ms, should not exceed 150ms threshold
        assert tracker.check_threshold("threshold_test", 150.0) is None
        
        # Should exceed 80ms threshold
        result = tracker.check_threshold("threshold_test", 80.0)
        assert result is not None
        assert abs(result - 100.0) < 0.1
    
    def test_summarize(self):
        """Test summary generation"""
        tracker = LatencyTracker()
        
        tracker.record("api_call", 100.0)
        tracker.record("db_query", 50.0)
        tracker.record("cache_hit", 5.0)
        
        summary = tracker.summarize()
        assert "api_call" in summary
        assert "db_query" in summary
        assert "cache_hit" in summary
        assert "Mean" in summary
        assert "P95" in summary
    
    def test_to_state_dict(self):
        """Test state dict export for persistence"""
        tracker = LatencyTracker(retention_per_operation=100)
        
        tracker.record("op1", 10.0)
        tracker.record("op2", 20.0)
        
        state = tracker.to_state_dict()
        assert "operations" in state
        assert "total_operations" in state
        assert "retention_per_operation" in state
        assert state["total_operations"] == 2
        assert state["retention_per_operation"] == 100
        assert "op1" in state["operations"]
        assert "op2" in state["operations"]
    
    def test_stats_to_dict(self):
        """Test LatencyStats serialization"""
        tracker = LatencyTracker()
        tracker.record("test", 100.0)
        
        stats = tracker.get_stats("test")
        assert stats is not None
        
        stats_dict = stats.to_dict()
        assert "operation" in stats_dict
        assert "count" in stats_dict
        assert "mean_ms" in stats_dict
        assert "p95_ms" in stats_dict
        assert "last_timestamp" in stats_dict
        assert stats_dict["operation"] == "test"
    
    def test_no_measurements(self):
        """Test behavior when no measurements exist"""
        tracker = LatencyTracker()
        
        stats = tracker.get_stats("nonexistent")
        assert stats is None
        
        recent = tracker.get_recent_measurements("nonexistent", limit=5)
        assert len(recent) == 0
        
        summary = tracker.summarize()
        assert "No latency measurements" in summary
    
    def test_metadata_preserved(self):
        """Test that metadata is preserved in measurements"""
        tracker = LatencyTracker()
        
        metadata = {"endpoint": "/api/v1/orders", "status": 200, "method": "GET"}
        tracker.record("api_test", 150.0, metadata)
        
        measurements = tracker.get_recent_measurements("api_test", limit=1)
        assert len(measurements) == 1
        assert measurements[0].metadata == metadata
    
    def test_timestamp_recording(self):
        """Test that timestamps are recorded correctly"""
        tracker = LatencyTracker()
        
        before = datetime.now(timezone.utc)
        tracker.record("time_test", 10.0)
        after = datetime.now(timezone.utc)
        
        measurements = tracker.get_recent_measurements("time_test", limit=1)
        assert len(measurements) == 1
        timestamp = measurements[0].timestamp
        assert before <= timestamp <= after
    
    def test_concurrent_operations(self):
        """Test tracking multiple concurrent operation types"""
        tracker = LatencyTracker()
        
        # Simulate concurrent API calls of different types
        for i in range(10):
            tracker.record("api_read", 50.0 + i)
            tracker.record("api_write", 100.0 + i)
            tracker.record("api_delete", 150.0 + i)
        
        read_stats = tracker.get_stats("api_read")
        write_stats = tracker.get_stats("api_write")
        delete_stats = tracker.get_stats("api_delete")
        
        assert read_stats.count == 10
        assert write_stats.count == 10
        assert delete_stats.count == 10
        
        # Check means are in expected ranges
        assert 50 <= read_stats.mean_ms <= 60
        assert 100 <= write_stats.mean_ms <= 110
        assert 150 <= delete_stats.mean_ms <= 160


class TestGlobalTracker:
    """Test global tracker singleton"""
    
    def test_get_global_tracker(self):
        """Test getting global tracker instance"""
        from infra.latency_tracker import get_global_tracker, reset_global_tracker
        
        # Reset first
        reset_global_tracker()
        
        tracker1 = get_global_tracker()
        tracker2 = get_global_tracker()
        
        # Should be same instance
        assert tracker1 is tracker2
        
        # Test it works
        tracker1.record("global_test", 100.0)
        stats = tracker2.get_stats("global_test")
        assert stats is not None
        assert stats.count == 1
    
    def test_reset_global_tracker(self):
        """Test resetting global tracker"""
        from infra.latency_tracker import get_global_tracker, reset_global_tracker
        
        tracker1 = get_global_tracker()
        tracker1.record("test", 100.0)
        
        reset_global_tracker()
        
        tracker2 = get_global_tracker()
        # Should be new instance
        assert tracker2 is not tracker1
        # Should be empty
        assert tracker2.get_stats("test") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
