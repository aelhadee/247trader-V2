"""
247trader-v2 Infrastructure: Latency Tracker

Track and report API call latencies, decision cycle timings, and submission pipeline metrics.
Essential for watchdog timers and alerting accuracy.
"""

import time
import logging
from contextlib import contextmanager
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


@dataclass
class LatencyMeasurement:
    """Single latency measurement"""
    operation: str
    duration_ms: float
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LatencyStats:
    """Aggregated latency statistics for an operation"""
    operation: str
    count: int
    total_ms: float
    min_ms: float
    max_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    last_timestamp: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for serialization"""
        d = asdict(self)
        if self.last_timestamp:
            d['last_timestamp'] = self.last_timestamp.isoformat()
        return d


class LatencyTracker:
    """
    Latency tracking with per-operation statistics and configurable retention.
    
    Features:
    - Context manager for automatic timing
    - Per-operation metrics (count, min, max, mean, percentiles)
    - Rolling window for memory efficiency
    - Thread-safe operations
    - Alert integration for threshold violations
    """
    
    def __init__(self, retention_per_operation: int = 1000):
        """
        Args:
            retention_per_operation: Max measurements to keep per operation (rolling window)
        """
        self._measurements: Dict[str, deque] = defaultdict(lambda: deque(maxlen=retention_per_operation))
        self._operation_counts: Dict[str, int] = defaultdict(int)
        self._lock = __import__('threading').Lock()
        self.retention_per_operation = retention_per_operation
        
    @contextmanager
    def measure(self, operation: str, metadata: Optional[Dict[str, Any]] = None):
        """
        Context manager for timing operations.
        
        Usage:
            with tracker.measure("api_get_accounts"):
                result = exchange.get_accounts()
        
        Args:
            operation: Operation name/label (e.g., "api_list_products", "decision_cycle")
            metadata: Optional metadata dict (e.g., {"endpoint": "/accounts", "status": 200})
        """
        start = time.perf_counter()
        exception_raised = False
        try:
            yield
        except Exception:
            exception_raised = True
            raise
        finally:
            duration_s = time.perf_counter() - start
            duration_ms = duration_s * 1000.0
            
            # Add exception flag to metadata
            meta = metadata.copy() if metadata else {}
            if exception_raised:
                meta['exception'] = True
            
            self.record(operation, duration_ms, meta)
    
    def record(self, operation: str, duration_ms: float, metadata: Optional[Dict[str, Any]] = None):
        """
        Record a latency measurement.
        
        Args:
            operation: Operation name
            duration_ms: Duration in milliseconds
            metadata: Optional metadata dict
        """
        measurement = LatencyMeasurement(
            operation=operation,
            duration_ms=duration_ms,
            timestamp=datetime.now(timezone.utc),
            metadata=metadata or {},
        )
        
        with self._lock:
            self._measurements[operation].append(measurement)
            self._operation_counts[operation] += 1
    
    def get_stats(self, operation: str) -> Optional[LatencyStats]:
        """
        Get aggregated statistics for an operation.
        
        Args:
            operation: Operation name
            
        Returns:
            LatencyStats or None if no measurements exist
        """
        with self._lock:
            measurements = list(self._measurements.get(operation, []))
        
        if not measurements:
            return None
        
        durations = [m.duration_ms for m in measurements]
        durations_sorted = sorted(durations)
        count = len(durations)
        
        # Calculate percentiles
        def percentile(data: List[float], p: float) -> float:
            if not data:
                return 0.0
            k = (len(data) - 1) * p
            f = int(k)
            c = k - f
            if f + 1 < len(data):
                return data[f] * (1 - c) + data[f + 1] * c
            return data[f]
        
        return LatencyStats(
            operation=operation,
            count=count,
            total_ms=sum(durations),
            min_ms=min(durations),
            max_ms=max(durations),
            mean_ms=sum(durations) / count,
            p50_ms=percentile(durations_sorted, 0.50),
            p95_ms=percentile(durations_sorted, 0.95),
            p99_ms=percentile(durations_sorted, 0.99),
            last_timestamp=measurements[-1].timestamp,
        )
    
    def get_all_stats(self) -> Dict[str, LatencyStats]:
        """
        Get statistics for all tracked operations.
        
        Returns:
            Dict mapping operation name to LatencyStats
        """
        with self._lock:
            operations = list(self._measurements.keys())
        
        return {
            op: stats
            for op in operations
            if (stats := self.get_stats(op)) is not None
        }
    
    def get_recent_measurements(self, operation: str, limit: int = 10) -> List[LatencyMeasurement]:
        """
        Get recent measurements for an operation.
        
        Args:
            operation: Operation name
            limit: Maximum number of measurements to return
            
        Returns:
            List of recent LatencyMeasurement objects (newest first)
        """
        with self._lock:
            measurements = list(self._measurements.get(operation, []))
        
        return measurements[-limit:][::-1]
    
    def clear(self, operation: Optional[str] = None):
        """
        Clear measurements.
        
        Args:
            operation: If specified, clear only this operation. Otherwise clear all.
        """
        with self._lock:
            if operation:
                self._measurements.pop(operation, None)
                self._operation_counts.pop(operation, None)
            else:
                self._measurements.clear()
                self._operation_counts.clear()
    
    def check_threshold(self, operation: str, threshold_ms: float) -> Optional[float]:
        """
        Check if mean latency exceeds threshold.
        
        Args:
            operation: Operation name
            threshold_ms: Threshold in milliseconds
            
        Returns:
            Mean latency if threshold exceeded, None otherwise
        """
        stats = self.get_stats(operation)
        if stats and stats.mean_ms > threshold_ms:
            return stats.mean_ms
        return None
    
    def summarize(self) -> str:
        """
        Generate human-readable summary of all tracked operations.
        
        Returns:
            Multi-line summary string
        """
        all_stats = self.get_all_stats()
        if not all_stats:
            return "No latency measurements recorded"
        
        lines = ["Latency Summary:"]
        lines.append("=" * 80)
        lines.append(f"{'Operation':<40} {'Count':>8} {'Mean':>10} {'P95':>10} {'P99':>10}")
        lines.append("-" * 80)
        
        # Sort by operation name
        for operation in sorted(all_stats.keys()):
            stats = all_stats[operation]
            lines.append(
                f"{operation:<40} {stats.count:>8} "
                f"{stats.mean_ms:>9.2f}ms {stats.p95_ms:>9.2f}ms {stats.p99_ms:>9.2f}ms"
            )
        
        lines.append("=" * 80)
        return "\n".join(lines)
    
    def to_state_dict(self) -> Dict[str, Any]:
        """
        Export state for persistence in StateStore.
        
        Returns:
            Dict with serializable latency statistics
        """
        all_stats = self.get_all_stats()
        return {
            "operations": {
                op: stats.to_dict()
                for op, stats in all_stats.items()
            },
            "total_operations": len(all_stats),
            "retention_per_operation": self.retention_per_operation,
        }


# Global singleton instance for convenience
_global_tracker: Optional[LatencyTracker] = None


def get_global_tracker() -> LatencyTracker:
    """Get or create global latency tracker instance"""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = LatencyTracker()
    return _global_tracker


def reset_global_tracker():
    """Reset global tracker (mainly for tests)"""
    global _global_tracker
    _global_tracker = None
