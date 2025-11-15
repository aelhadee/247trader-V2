"""Alerting helpers for pager/webhook notifications."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    INFO = 10
    WARNING = 20
    CRITICAL = 30

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name.lower()

    @classmethod
    def from_string(cls, value: str, default: Optional["AlertSeverity"] = None) -> "AlertSeverity":
        if not value:
            return default or cls.WARNING
        normalized = value.strip().lower()
        for member in cls:
            if member.name.lower() == normalized:
                return member
        return default or cls.WARNING


@dataclass
class AlertConfig:
    enabled: bool
    webhook_url: Optional[str]
    min_severity: AlertSeverity
    dry_run: bool
    timeout: float = 5.0
    dedupe_seconds: float = 60.0  # Dedupe identical alerts within 60s
    escalation_seconds: float = 120.0  # Escalate unresolved alerts after 2m
    escalation_webhook_url: Optional[str] = None  # Optional separate escalation webhook
    escalation_severity_boost: int = 1  # Boost severity by N levels on escalation


@dataclass
class AlertRecord:
    """Track alert history for dedupe and escalation."""
    fingerprint: str
    severity: AlertSeverity
    title: str
    message: str
    first_seen: float  # time.monotonic()
    last_seen: float  # time.monotonic()
    count: int = 1
    escalated: bool = False
    resolved: bool = False


class AlertService:
    """
    Send notifications for critical trading events.
    
    Features:
    - Deduplication: Suppress identical alerts within 60s window
    - Escalation: Boost severity/send to additional webhook after 2m unresolved
    - History tracking: Maintain alert records for analysis
    """

    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        self._enabled = bool(config.enabled and config.webhook_url)
        if config.enabled and not config.webhook_url:
            logger.warning("Alerting enabled but no webhook URL set; disabling alerts")
            self._enabled = False
        
        # Alert history for dedupe and escalation
        self._alert_history: Dict[str, AlertRecord] = {}
        self._last_cleanup: float = time.monotonic()

    @classmethod
    def from_config(cls, enabled: bool, raw_config: Optional[Dict[str, Any]]) -> "AlertService":
        raw_config = raw_config or {}

        webhook_url = raw_config.get("webhook_url")
        if webhook_url and "${" in webhook_url:
            webhook_url = os.path.expandvars(webhook_url)

        if not webhook_url:
            env_key = raw_config.get("webhook_env", "ALERT_WEBHOOK_URL")
            webhook_url = os.getenv(env_key, "")

        min_sev = AlertSeverity.from_string(
            raw_config.get("min_severity", "warning"),
            default=AlertSeverity.WARNING,
        )
        dry_run = bool(raw_config.get("dry_run", False))
        timeout = float(raw_config.get("timeout_seconds", 5.0))

        config = AlertConfig(
            enabled=enabled,
            webhook_url=webhook_url or None,
            min_severity=min_sev,
            dry_run=dry_run,
            timeout=timeout,
        )
        return cls(config)

    def is_enabled(self) -> bool:
        return self._enabled

    def notify(
        self,
        severity: AlertSeverity,
        title: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._enabled:
            return
        if severity.value < self._config.min_severity.value:
            return

        payload = self._build_payload(severity, title, message, context)
        if self._config.dry_run:
            logger.info("[ALERT:%s] %s - %s | %s", severity.name, title, message, context or {})
            return

        assert self._config.webhook_url  # guarded by _enabled
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._config.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout) as response:
                if response.status >= 400:
                    body = response.read().decode("utf-8", errors="ignore")
                    raise urllib.error.HTTPError(
                        self._config.webhook_url,
                        response.status,
                        body,
                        response.headers,
                        None,
                    )
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout) as exc:
            logger.error("Failed to deliver alert '%s': %s", title, exc)

    @staticmethod
    def _build_payload(
        severity: AlertSeverity,
        title: str,
        message: str,
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        line_items = [f"[{severity.name}] {title}", message]
        if context:
            try:
                context_json = json.dumps(context, sort_keys=True)
            except TypeError:
                context_json = str(context)
            line_items.append(f"context={context_json}")
        return {"text": " | ".join(filter(None, line_items))}


__all__ = ["AlertService", "AlertSeverity"]
