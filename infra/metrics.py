"""Prometheus-backed metrics hooks for trading loop and execution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

try:
    from prometheus_client import Counter, Gauge, Summary, start_http_server
except ImportError:  # pragma: no cover - optional dependency
    Counter = Gauge = Summary = None  # type: ignore
    start_http_server = None  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class CycleStats:
    status: str
    proposals: int
    approved: int
    executed: int
    duration_seconds: float


class MetricsRecorder:
    """
    Expose trading loop stats via Prometheus if available.
    
    Singleton pattern to prevent duplicate metric registration errors.
    """
    _instance: Optional['MetricsRecorder'] = None
    _initialized: bool = False

    def __new__(cls, enabled: bool = True, port: int = 9100):
        """Ensure only one MetricsRecorder instance exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, enabled: bool = True, port: int = 9100) -> None:
        # Skip re-initialization if already initialized
        if self.__class__._initialized:
            return
            
        self._prom_available = Counter is not None
        self._enabled = bool(enabled) and self._prom_available
        self._port = port
        self._started = False
        self.__class__._initialized = True

        self._last_cycle_stats: Optional[CycleStats] = None
        self._last_stage_durations: Dict[str, float] = {}
        self._last_rate_usage: Dict[str, float] = {}
        self._last_api_event: Optional[Dict[str, str]] = None
        self._last_no_trade_reason: Optional[str] = None

        if not self._prom_available and enabled:
            logger.warning(
                "Prometheus client not installed; metrics exporter disabled. "
                "Install `prometheus-client` to enable metrics."
            )

        if not self._enabled:
            # Core cycle metrics
            self._cycle_summary = None
            self._cycle_counter = None
            self._cycle_gauge = None
            self._stage_summary = None
            # Rate limiting metrics
            self._rate_limit_gauge = None
            self._rate_limit_counter = None
            self._api_latency_summary = None
            # Trading metrics
            self._no_trade_counter = None
            self._exposure_gauge = None
            self._positions_gauge = None
            self._pending_orders_gauge = None
            # Execution quality metrics
            self._fill_ratio_gauge = None
            self._fills_counter = None
            self._order_rejections_counter = None
            # Circuit breaker metrics
            self._circuit_breaker_gauge = None
            self._circuit_breaker_trips_counter = None
            # API error tracking
            self._api_errors_counter = None
            self._api_consecutive_errors_gauge = None
            # Trim tracking
            self._trim_attempts_counter = None
            self._trim_consecutive_failures_gauge = None
            return

        self._cycle_summary = Summary(  # type: ignore[assignment]
            "trader_cycle_duration_seconds",
            "Duration of a full trading loop cycle",
        )
        self._cycle_counter = Counter(  # type: ignore[assignment]
            "trader_cycle_total",
            "Total trading cycles by status",
            labelnames=("status",),
        )
        self._cycle_gauge = Gauge(  # type: ignore[assignment]
            "trader_cycle_stage_count",
            "Per-cycle counts (proposals, approvals, executions)",
            labelnames=("stage",),
        )
        self._stage_summary = Summary(  # type: ignore[assignment]
            "trader_stage_duration_seconds",
            "Duration of major trading stages",
            labelnames=("stage",),
        )
        self._rate_limit_gauge = Gauge(  # type: ignore[assignment]
            "exchange_rate_limit_utilization",
            "Current rate limit usage (0-1)",
            labelnames=("channel",),
        )
        self._rate_limit_counter = Counter(  # type: ignore[assignment]
            "exchange_rate_limit_violations_total",
            "Number of times rate limit usage exceeded configured budget",
            labelnames=("channel",),
        )
        self._api_latency_summary = Summary(  # type: ignore[assignment]
            "exchange_api_latency_seconds",
            "Latency of exchange API calls",
            labelnames=("endpoint", "channel", "status"),
        )
        self._no_trade_counter = Counter(  # type: ignore[assignment]
            "trader_no_trade_total",
            "Number of cycles that resulted in no-trade outcomes, grouped by reason",
            labelnames=("reason",),
        )
        
        # NEW: Exposure and portfolio gauges
        self._exposure_gauge = Gauge(  # type: ignore[assignment]
            "trader_exposure_pct",
            "Current portfolio exposure as percentage of NAV",
            labelnames=("type",),  # Label values: "at_risk", "pending"
        )
        self._positions_gauge = Gauge(  # type: ignore[assignment]
            "trader_open_positions",
            "Number of currently open positions",
        )
        self._pending_orders_gauge = Gauge(  # type: ignore[assignment]
            "trader_pending_orders",
            "Number of pending orders",
        )
        
        # NEW: Execution quality metrics
        self._fill_ratio_gauge = Gauge(  # type: ignore[assignment]
            "trader_fill_ratio",
            "Ratio of filled orders to total orders (0-1)",
        )
        self._fills_counter = Counter(  # type: ignore[assignment]
            "trader_fills_total",
            "Total number of filled orders",
            labelnames=("side",),  # side: "buy", "sell"
        )
        self._order_rejections_counter = Counter(  # type: ignore[assignment]
            "trader_order_rejections_total",
            "Total number of rejected orders",
            labelnames=("reason",),
        )
        
        # NEW: Circuit breaker state
        self._circuit_breaker_gauge = Gauge(  # type: ignore[assignment]
            "trader_circuit_breaker_state",
            "Circuit breaker state (0=closed/safe, 1=open/tripped)",
            labelnames=("breaker",),  # breaker: "api_health", "rate_limit", "volatility", etc.
        )
        self._circuit_breaker_trips_counter = Counter(  # type: ignore[assignment]
            "trader_circuit_breaker_trips_total",
            "Total number of circuit breaker trips",
            labelnames=("breaker",),
        )
        
        # NEW: API error tracking
        self._api_errors_counter = Counter(  # type: ignore[assignment]
            "exchange_api_errors_total",
            "Total number of API errors",
            labelnames=("error_type",),
        )
        self._api_consecutive_errors_gauge = Gauge(  # type: ignore[assignment]
            "exchange_api_consecutive_errors",
            "Current count of consecutive API errors",
        )

    @classmethod
    def _reset_for_testing(cls) -> None:
        """
        Reset singleton state for testing.
        WARNING: Only call from test fixtures/teardown.
        """
        # Clear Prometheus collectors from global registry
        if cls._instance is not None and cls._instance._prom_available:
            try:
                from prometheus_client import REGISTRY
                # Unregister all our metrics
                collectors_to_remove = []
                for collector in list(REGISTRY._collector_to_names):
                    # Check if it's one of our metrics
                    names = REGISTRY._collector_to_names.get(collector, set())
                    if any('trader_' in name or 'exchange_' in name for name in names):
                        collectors_to_remove.append(collector)
                
                for collector in collectors_to_remove:
                    try:
                        REGISTRY.unregister(collector)
                    except Exception:
                        pass  # Already unregistered
            except Exception:
                pass  # Prometheus not available or registry issue
        
        cls._instance = None
        cls._initialized = False

    def start(self) -> None:
        
        # NEW: Auto-trim tracking
        self._trim_attempts_counter = Counter(  # type: ignore[assignment]
            "trader_trim_attempts_total",
            "Total number of auto-trim attempts",
            labelnames=("outcome",),  # outcome: "success", "no_candidates", "failed"
        )
        self._trim_consecutive_failures_gauge = Gauge(  # type: ignore[assignment]
            "trader_trim_consecutive_failures",
            "Number of consecutive trim failures due to no candidates",
        )
        self._trim_liquidated_usd_counter = Counter(  # type: ignore[assignment]
            "trader_trim_liquidated_usd_total",
            "Total USD value liquidated via auto-trim",
        )

    def start(self) -> None:
        if not self._enabled or self._started:
            return
        if start_http_server is None:  # pragma: no cover - guarded above
            return
        
        # Auto-retry on port conflict
        ports_to_try = [self._port, self._port + 1, self._port + 2, self._port + 3]
        last_error = None
        
        for port in ports_to_try:
            try:
                start_http_server(port)
                self._started = True
                if port != self._port:
                    logger.warning(
                        "Port %s in use, successfully bound to port %s instead",
                        self._port, port
                    )
                    self._port = port  # Update to actual port
                logger.info("Prometheus metrics exporter listening on 0.0.0.0:%s", self._port)
                return
            except OSError as exc:
                last_error = exc
                if port != ports_to_try[-1]:  # Not the last port
                    logger.debug("Port %s in use, trying next port...", port)
                continue
        
        # All ports exhausted
        self._enabled = False
        logger.error(
            "Failed to start metrics exporter after trying ports %s: %s",
            ports_to_try, last_error
        )

    def observe_cycle(self, stats: CycleStats) -> None:
        if self._enabled:
            assert self._cycle_summary and self._cycle_counter and self._cycle_gauge
            self._cycle_summary.observe(stats.duration_seconds)
            self._cycle_counter.labels(status=stats.status).inc()
            self._cycle_gauge.labels(stage="proposals").set(stats.proposals)
            self._cycle_gauge.labels(stage="approved").set(stats.approved)
            self._cycle_gauge.labels(stage="executed").set(stats.executed)

        self._last_cycle_stats = stats

    def is_enabled(self) -> bool:
        return self._enabled

    def record_stage_duration(self, stage: str, duration: float) -> None:
        self._last_stage_durations[stage] = duration
        if self._enabled and self._stage_summary:
            self._stage_summary.labels(stage=stage).observe(duration)

    def record_rate_limit_usage(self, channel: str, usage: float, *, violated: bool = False) -> None:
        self._last_rate_usage[channel] = usage
        if self._enabled and self._rate_limit_gauge:
            self._rate_limit_gauge.labels(channel=channel).set(max(usage, 0.0))
            if violated and self._rate_limit_counter:
                self._rate_limit_counter.labels(channel=channel).inc()

    def record_api_call(self, endpoint: str, channel: str, duration: float, status: str) -> None:
        self._last_api_event = {
            "endpoint": endpoint,
            "channel": channel,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self._enabled and self._api_latency_summary:
            self._api_latency_summary.labels(
                endpoint=endpoint,
                channel=channel,
                status=status,
            ).observe(duration)

    def record_no_trade_reason(self, reason: str) -> None:
        self._last_no_trade_reason = reason
        if self._enabled and self._no_trade_counter:
            # Keep label cardinality bounded by reusing the literal reason string
            self._no_trade_counter.labels(reason=reason).inc()

    def last_cycle(self) -> Optional[CycleStats]:
        return self._last_cycle_stats

    def stage_snapshot(self) -> Dict[str, float]:
        return dict(self._last_stage_durations)

    def rate_usage_snapshot(self) -> Dict[str, float]:
        return dict(self._last_rate_usage)

    def last_api_event(self) -> Optional[Dict[str, str]]:
        return dict(self._last_api_event) if self._last_api_event else None

    def last_no_trade_reason(self) -> Optional[str]:
        return self._last_no_trade_reason
    
    def record_exposure(self, at_risk_pct: float, pending_pct: float = 0.0) -> None:
        """Record portfolio exposure percentages"""
        if self._enabled and self._exposure_gauge:
            self._exposure_gauge.labels(type="at_risk").set(max(at_risk_pct, 0.0))
            self._exposure_gauge.labels(type="pending").set(max(pending_pct, 0.0))
    
    def record_open_positions(self, count: int) -> None:
        """Record number of open positions"""
        if self._enabled and self._positions_gauge:
            self._positions_gauge.set(max(count, 0))
    
    def record_pending_orders(self, count: int) -> None:
        """Record number of pending orders"""
        if self._enabled and self._pending_orders_gauge:
            self._pending_orders_gauge.set(max(count, 0))
    
    def record_fill_ratio(self, fills: int, total_orders: int) -> None:
        """Record fill ratio (execution quality metric)"""
        if self._enabled and self._fill_ratio_gauge:
            ratio = fills / total_orders if total_orders > 0 else 0.0
            self._fill_ratio_gauge.set(max(min(ratio, 1.0), 0.0))
    
    def record_fill(self, side: str) -> None:
        """Record a filled order"""
        if self._enabled and self._fills_counter:
            self._fills_counter.labels(side=side).inc()
    
    def record_order_rejection(self, reason: str) -> None:
        """Record an order rejection"""
        if self._enabled and self._order_rejections_counter:
            # Normalize reason to keep cardinality bounded
            normalized_reason = self._normalize_rejection_reason(reason)
            self._order_rejections_counter.labels(reason=normalized_reason).inc()
    
    def record_circuit_breaker_state(self, breaker_name: str, is_open: bool) -> None:
        """Record circuit breaker state (0=closed/safe, 1=open/tripped)"""
        if self._enabled and self._circuit_breaker_gauge:
            self._circuit_breaker_gauge.labels(breaker=breaker_name).set(1 if is_open else 0)
    
    def record_circuit_breaker_trip(self, breaker_name: str) -> None:
        """Record a circuit breaker trip event"""
        if self._enabled:
            if self._circuit_breaker_gauge:
                self._circuit_breaker_gauge.labels(breaker=breaker_name).set(1)
            if self._circuit_breaker_trips_counter:
                self._circuit_breaker_trips_counter.labels(breaker=breaker_name).inc()
    
    def record_api_error(self, error_type: str, consecutive_count: int) -> None:
        """Record API error and consecutive error count"""
        if self._enabled:
            if self._api_errors_counter:
                # Normalize error type to keep cardinality bounded
                normalized_type = self._normalize_error_type(error_type)
                self._api_errors_counter.labels(error_type=normalized_type).inc()
            if self._api_consecutive_errors_gauge:
                self._api_consecutive_errors_gauge.set(max(consecutive_count, 0))
    
    def reset_consecutive_api_errors(self) -> None:
        """Reset consecutive API error count (on successful API call)"""
        if self._enabled and self._api_consecutive_errors_gauge:
            self._api_consecutive_errors_gauge.set(0)
    
    @staticmethod
    def _normalize_rejection_reason(reason: str) -> str:
        """Normalize rejection reasons to keep label cardinality bounded"""
        reason_lower = reason.lower()
        
        # Map to canonical categories
        if "insufficient" in reason_lower or "balance" in reason_lower:
            return "insufficient_funds"
        elif "limit" in reason_lower or "max" in reason_lower:
            return "limit_exceeded"
        elif "cooldown" in reason_lower or "spacing" in reason_lower:
            return "cooldown_active"
        elif "exposure" in reason_lower or "cap" in reason_lower:
            return "exposure_cap"
        elif "size" in reason_lower or "notional" in reason_lower:
            return "size_constraint"
        elif "circuit" in reason_lower or "breaker" in reason_lower:
            return "circuit_breaker"
        elif "regime" in reason_lower or "volatility" in reason_lower:
            return "regime_block"
        elif "kill" in reason_lower or "stop" in reason_lower:
            return "kill_switch_or_stop"
        else:
            return "other"
    
    @staticmethod
    def _normalize_error_type(error_type: str) -> str:
        """Normalize API error types to keep label cardinality bounded"""
        error_lower = error_type.lower()
        
        # Map to canonical categories
        if "timeout" in error_lower:
            return "timeout"
        elif "429" in error_lower or "rate" in error_lower:
            return "rate_limit"
        elif "401" in error_lower or "403" in error_lower or "auth" in error_lower:
            return "auth_error"
        elif "404" in error_lower:
            return "not_found"
        elif "500" in error_lower or "502" in error_lower or "503" in error_lower:
            return "server_error"
        elif "connection" in error_lower or "network" in error_lower:
            return "connection_error"
        else:
            return "other"
    
    def record_trim_attempt(self, outcome: str, consecutive_failures: int = 0, liquidated_usd: float = 0.0) -> None:
        """Record auto-trim attempt and outcome"""
        if not self._enabled:
            return
        
        assert self._trim_attempts_counter and self._trim_consecutive_failures_gauge and self._trim_liquidated_usd_counter
        self._trim_attempts_counter.labels(outcome=outcome).inc()
        self._trim_consecutive_failures_gauge.set(consecutive_failures)
        
        if liquidated_usd > 0:
            self._trim_liquidated_usd_counter.inc(liquidated_usd)
    
    def record_ai_latency(self, latency_ms: float) -> None:
        """Record AI advisor call latency"""
        if not self._enabled:
            return
        # Use existing stage duration for now
        self.record_stage_duration("ai_advisor", latency_ms / 1000.0)


__all__ = ["MetricsRecorder", "CycleStats"]
