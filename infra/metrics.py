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
    """Expose trading loop stats via Prometheus if available."""

    def __init__(self, enabled: bool, port: int = 9100) -> None:
        self._prom_available = Counter is not None
        self._enabled = bool(enabled) and self._prom_available
        self._port = port
        self._started = False

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
            self._cycle_summary = None
            self._cycle_counter = None
            self._cycle_gauge = None
            self._stage_summary = None
            self._rate_limit_gauge = None
            self._rate_limit_counter = None
            self._api_latency_summary = None
            self._no_trade_counter = None
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

    def start(self) -> None:
        if not self._enabled or self._started:
            return
        if start_http_server is None:  # pragma: no cover - guarded above
            return
        try:
            start_http_server(self._port)
            self._started = True
            logger.info("Prometheus metrics exporter listening on 0.0.0.0:%s", self._port)
        except OSError as exc:
            self._enabled = False
            logger.error("Failed to start metrics exporter on port %s: %s", self._port, exc)

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


__all__ = ["MetricsRecorder", "CycleStats"]
