"""Prometheus-backed metrics hooks for trading loop and execution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

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

        if not self._prom_available and enabled:
            logger.warning(
                "Prometheus client not installed; metrics exporter disabled. "
                "Install `prometheus-client` to enable metrics."
            )

        if not self._enabled:
            self._cycle_summary = None
            self._cycle_counter = None
            self._cycle_gauge = None
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
        if not self._enabled:
            return
        assert self._cycle_summary and self._cycle_counter and self._cycle_gauge

        self._cycle_summary.observe(stats.duration_seconds)
        self._cycle_counter.labels(status=stats.status).inc()
        self._cycle_gauge.labels(stage="proposals").set(stats.proposals)
        self._cycle_gauge.labels(stage="approved").set(stats.approved)
        self._cycle_gauge.labels(stage="executed").set(stats.executed)

    def is_enabled(self) -> bool:
        return self._enabled


__all__ = ["MetricsRecorder", "CycleStats"]
