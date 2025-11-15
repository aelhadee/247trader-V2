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

        escalation_webhook_url = raw_config.get("escalation_webhook_url")
        if escalation_webhook_url and "${" in escalation_webhook_url:
            escalation_webhook_url = os.path.expandvars(escalation_webhook_url)

        min_sev = AlertSeverity.from_string(
            raw_config.get("min_severity", "warning"),
            default=AlertSeverity.WARNING,
        )
        dry_run = bool(raw_config.get("dry_run", False))
        timeout = float(raw_config.get("timeout_seconds", 5.0))
        dedupe_seconds = float(raw_config.get("dedupe_seconds", 60.0))
        escalation_seconds = float(raw_config.get("escalation_seconds", 120.0))
        escalation_severity_boost = int(raw_config.get("escalation_severity_boost", 1))

        config = AlertConfig(
            enabled=enabled,
            webhook_url=webhook_url or None,
            min_severity=min_sev,
            dry_run=dry_run,
            timeout=timeout,
            dedupe_seconds=dedupe_seconds,
            escalation_seconds=escalation_seconds,
            escalation_webhook_url=escalation_webhook_url or None,
            escalation_severity_boost=escalation_severity_boost,
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
        """
        Send alert notification with dedupe and escalation.
        
        Deduplication:
        - Identical alerts (same fingerprint) within 60s are suppressed
        - Dedupe window configurable via dedupe_seconds
        
        Escalation:
        - Alerts unresolved after 2m trigger escalation
        - Escalation boosts severity or sends to separate webhook
        - Escalation window configurable via escalation_seconds
        """
        if not self._enabled:
            return
        if severity.value < self._config.min_severity.value:
            return
        
        # Cleanup old alerts periodically
        self._cleanup_old_alerts()
        
        # Generate fingerprint for dedupe/escalation tracking
        fingerprint = self._generate_fingerprint(severity, title, message)
        
        # Check for deduplication
        if self._should_dedupe(fingerprint):
            self._update_alert_record(fingerprint)
            logger.debug(f"Alert deduped: {title} (fingerprint={fingerprint[:8]}...)")
            return
        
        # Record alert
        self._record_alert(fingerprint, severity, title, message)
        
        # Check for escalation
        if self._should_escalate(fingerprint):
            self._escalate_alert(fingerprint, severity, title, message, context)
            return
        
        # Send normal alert
        self._send_alert(severity, title, message, context, webhook_url=self._config.webhook_url)

    def resolve_alert(self, severity: AlertSeverity, title: str, message: str) -> None:
        """
        Mark an alert as resolved to prevent escalation.
        
        Call this when the condition that triggered the alert is cleared.
        """
        fingerprint = self._generate_fingerprint(severity, title, message)
        if fingerprint in self._alert_history:
            self._alert_history[fingerprint].resolved = True
            logger.debug(f"Alert resolved: {title} (fingerprint={fingerprint[:8]}...)")

    def _generate_fingerprint(self, severity: AlertSeverity, title: str, message: str) -> str:
        """Generate unique fingerprint for alert dedupe/escalation."""
        content = f"{severity.name}|{title}|{message}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def _should_dedupe(self, fingerprint: str) -> bool:
        """
        Check if alert should be deduped (within 60s window from first occurrence).
        
        Uses fixed window from first_seen, not sliding window from last_seen.
        This means alerts are deduped for 60s after first occurrence, then
        window resets and next alert goes through.
        """
        if fingerprint not in self._alert_history:
            return False
        
        record = self._alert_history[fingerprint]
        elapsed = time.monotonic() - record.first_seen
        
        # Don't dedupe if outside window (window expired, will reset on next _record_alert)
        if elapsed > self._config.dedupe_seconds:
            return False
        
        # Dedupe all alerts within window, including escalated ones
        # (escalation already provided visibility; no need to spam)
        return True

    def _should_escalate(self, fingerprint: str) -> bool:
        """Check if alert should be escalated (unresolved for >2m)."""
        if fingerprint not in self._alert_history:
            return False
        
        record = self._alert_history[fingerprint]
        
        # Don't escalate if already escalated
        if record.escalated:
            return False
        
        # Don't escalate if resolved
        if record.resolved:
            return False
        
        # Check if unresolved for escalation window
        elapsed = time.monotonic() - record.first_seen
        return elapsed >= self._config.escalation_seconds

    def _record_alert(self, fingerprint: str, severity: AlertSeverity, title: str, message: str) -> None:
        """
        Record new alert in history.
        
        Only resets first_seen after escalation or resolution, not just after
        dedupe window expires. This allows escalation (2m) to trigger even if
        dedupe window (60s) has expired.
        """
        now = time.monotonic()
        
        if fingerprint in self._alert_history:
            record = self._alert_history[fingerprint]
            elapsed = now - record.first_seen
            
            # Reset as new occurrence only if:
            # 1) Already escalated (alert lifecycle complete), OR
            # 2) Dedupe window expired AND escalation window also expired
            should_reset = (
                record.escalated or
                record.resolved or
                elapsed > max(self._config.dedupe_seconds, self._config.escalation_seconds)
            )
            
            if should_reset:
                record.first_seen = now
                record.count = 1
                record.escalated = False
                record.resolved = False
            else:
                # Within lifecycle, just update count
                record.count += 1
            
            record.last_seen = now
        else:
            # Create new record
            self._alert_history[fingerprint] = AlertRecord(
                fingerprint=fingerprint,
                severity=severity,
                title=title,
                message=message,
                first_seen=now,
                last_seen=now,
            )

    def _update_alert_record(self, fingerprint: str) -> None:
        """Update last_seen timestamp for deduped alert."""
        if fingerprint in self._alert_history:
            self._alert_history[fingerprint].last_seen = time.monotonic()
            self._alert_history[fingerprint].count += 1

    def _escalate_alert(
        self,
        fingerprint: str,
        severity: AlertSeverity,
        title: str,
        message: str,
        context: Optional[Dict[str, Any]],
    ) -> None:
        """Escalate unresolved alert with higher severity or separate webhook."""
        record = self._alert_history[fingerprint]
        
        # Mark as escalated
        record.escalated = True
        
        # Boost severity
        escalated_severity = self._boost_severity(severity, self._config.escalation_severity_boost)
        
        # Add escalation context
        escalation_context = {
            **(context or {}),
            "escalated": True,
            "first_seen_seconds_ago": int(time.monotonic() - record.first_seen),
            "occurrence_count": record.count,
        }
        
        # Escalated title
        escalated_title = f"🚨 ESCALATED: {title}"
        escalated_message = f"{message} (unresolved for {int(time.monotonic() - record.first_seen)}s, {record.count} occurrences)"
        
        logger.warning(f"Escalating alert: {title} (unresolved for {int(time.monotonic() - record.first_seen)}s)")
        
        # Send to escalation webhook if configured, otherwise use primary with boosted severity
        webhook_url = self._config.escalation_webhook_url or self._config.webhook_url
        self._send_alert(escalated_severity, escalated_title, escalated_message, escalation_context, webhook_url=webhook_url)

    def _boost_severity(self, severity: AlertSeverity, boost: int) -> AlertSeverity:
        """Boost alert severity by N levels."""
        levels = [AlertSeverity.INFO, AlertSeverity.WARNING, AlertSeverity.CRITICAL]
        try:
            current_index = levels.index(severity)
        except ValueError:
            return severity
        
        new_index = min(current_index + boost, len(levels) - 1)
        return levels[new_index]

    def _send_alert(
        self,
        severity: AlertSeverity,
        title: str,
        message: str,
        context: Optional[Dict[str, Any]],
        webhook_url: Optional[str],
    ) -> None:
        """Send alert to webhook."""
        payload = self._build_payload(severity, title, message, context)
        
        if self._config.dry_run:
            logger.info("[ALERT:%s] %s - %s | %s", severity.name, title, message, context or {})
            return

        if not webhook_url:
            logger.warning(f"No webhook URL for alert: {title}")
            return

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout) as response:
                if response.status >= 400:
                    body = response.read().decode("utf-8", errors="ignore")
                    raise urllib.error.HTTPError(
                        webhook_url,
                        response.status,
                        body,
                        response.headers,
                        None,
                    )
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout) as exc:
            logger.error("Failed to deliver alert '%s': %s", title, exc)

    def _cleanup_old_alerts(self) -> None:
        """Remove alerts older than 5 minutes to prevent memory leak."""
        now = time.monotonic()
        
        # Cleanup every 60s
        if now - self._last_cleanup < 60.0:
            return
        
        self._last_cleanup = now
        
        # Remove alerts older than 5 minutes
        max_age = 300.0
        to_remove = [
            fp for fp, record in self._alert_history.items()
            if (now - record.last_seen) > max_age
        ]
        
        for fp in to_remove:
            del self._alert_history[fp]
        
        if to_remove:
            logger.debug(f"Cleaned up {len(to_remove)} old alert records")

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
