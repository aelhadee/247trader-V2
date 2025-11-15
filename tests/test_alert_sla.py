"""
Alert deduplication and escalation SLA verification (REQ-AL1).

Verifies:
1. Identical alerts within 60s window are deduped
2. Dedupe expires after 60s allowing re-notification
3. Unresolved alerts escalate after 2m
4. Resolved alerts don't escalate
5. Different fingerprints are not deduped
6. Escalation boosts severity appropriately

NOTE: These tests use time mocking for deterministic timing.
Run: pytest tests/test_alert_sla.py -v
"""

import time
from typing import Dict, Any
from unittest.mock import Mock, patch, MagicMock
import pytest

from infra.alerting import AlertService, AlertConfig, AlertSeverity, AlertRecord


@pytest.fixture
def alert_config():
    """Create test alert configuration."""
    return AlertConfig(
        enabled=True,
        webhook_url="https://test.webhook.com/alert",
        min_severity=AlertSeverity.INFO,
        dry_run=False,
        timeout=5.0,
        dedupe_seconds=60.0,
        escalation_seconds=120.0,
        escalation_webhook_url="https://test.webhook.com/escalation",
        escalation_severity_boost=1,
    )


@pytest.fixture
def alert_service(alert_config):
    """Create AlertService instance for testing."""
    return AlertService(alert_config)


@pytest.fixture
def mock_urllib():
    """Mock urllib for testing webhook calls."""
    with patch('infra.alerting.urllib.request.urlopen') as mock_urlopen:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response
        yield mock_urlopen


def create_time_sequence(*phases):
    """
    Create a time generator for mocking time.monotonic().
    
    Each phase represents a time value that will be returned for ~15 calls.
    This handles the multiple time.monotonic() calls within each notify().
    
    Example: create_time_sequence(0.0, 30.0, 60.0) will return:
    - 0.0 for first ~15 calls
    - 30.0 for next ~15 calls
    - 60.0 for remaining calls
    """
    class TimeGenerator:
        def __init__(self, phases):
            self.phases = phases
            self.call_count = 0
            self.calls_per_phase = 15
        
        def __call__(self):
            self.call_count += 1
            phase_index = min((self.call_count - 1) // self.calls_per_phase, len(self.phases) - 1)
            return self.phases[phase_index]
    
    return TimeGenerator(phases)


class TestAlertDeduplication:
    """Test alert deduplication within 60s window (REQ-AL1.1)."""
    
    def test_dedupe_identical_alerts_within_60s(self, alert_service, mock_urllib):
        """Verify identical alerts within 60s are deduped."""
        # Create time generator that returns time values based on call count
        class TimeGenerator:
            def __init__(self):
                self.phase = 0  # Track which notify() we're in
                self.call_count = 0
            
            def __call__(self):
                self.call_count += 1
                # Each notify() makes ~5-10 calls to monotonic()
                # Return time based on which phase we're in
                if self.call_count <= 15:
                    return 0.0
                elif self.call_count <= 30:
                    return 30.0
                else:
                    return 59.0
        
        with patch('time.monotonic', TimeGenerator()):
            # First alert should go through
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test Alert",
                message="Test message",
            )
            
            # Second alert within 60s should be deduped
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test Alert",
                message="Test message",
            )
            
            # Third alert at 59s should still be deduped
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test Alert",
                message="Test message",
            )
        
        # Only first alert should have sent webhook
        assert mock_urllib.call_count == 1
        
        # Check alert record
        fingerprint = alert_service._generate_fingerprint(
            AlertSeverity.WARNING, "Test Alert", "Test message"
        )
        assert fingerprint in alert_service._alert_history
        assert alert_service._alert_history[fingerprint].count == 3  # All 3 recorded
    
    def test_dedupe_expires_after_60s(self, alert_service, mock_urllib):
        """Verify dedupe expires after 60s, allowing re-notification."""
        with patch('time.monotonic', side_effect=[0.0, 0.1, 61.0, 61.1]):
            # First alert at t=0
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test Alert",
                message="Test message",
            )
            
            # Second alert at t=0.1 (within 60s) - deduped
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test Alert",
                message="Test message",
            )
            
            # Third alert at t=61 (after 60s) - should go through
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test Alert",
                message="Test message",
            )
        
        # First and third alerts should send webhooks
        assert mock_urllib.call_count == 2
    
    def test_different_fingerprints_not_deduped(self, alert_service, mock_urllib):
        """Verify alerts with different content are not deduped."""
        with patch('time.monotonic', return_value=0.0):
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test Alert 1",
                message="Message 1",
            )
            
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test Alert 2",
                message="Message 2",
            )
            
            alert_service.notify(
                severity=AlertSeverity.CRITICAL,
                title="Test Alert 1",  # Same title but different severity
                message="Message 1",
            )
        
        # All 3 alerts should send (different fingerprints)
        assert mock_urllib.call_count == 3
    
    def test_severity_affects_fingerprint(self, alert_service, mock_urllib):
        """Verify severity is part of fingerprint."""
        with patch('time.monotonic', return_value=0.0):
            fp1 = alert_service._generate_fingerprint(
                AlertSeverity.WARNING, "Test", "Message"
            )
            fp2 = alert_service._generate_fingerprint(
                AlertSeverity.CRITICAL, "Test", "Message"
            )
        
        assert fp1 != fp2, "Different severities should have different fingerprints"


class TestAlertEscalation:
    """Test alert escalation after 2m unresolved (REQ-AL1.2)."""
    
    def test_escalation_after_2m_unresolved(self, alert_service, mock_urllib):
        """Verify alert escalates after 2m unresolved."""
        # Timeline: t=0 (first alert), t=121 (escalation check)
        with patch('time.monotonic', side_effect=[0.0, 0.1, 121.0, 121.1]):
            # First alert at t=0
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Unresolved Issue",
                message="This issue persists",
            )
            
            # Second identical alert at t=121 should escalate
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Unresolved Issue",
                message="This issue persists",
            )
        
        # First alert + escalation = 2 webhooks
        assert mock_urllib.call_count == 2
        
        # Check escalation webhook was called
        escalation_call = mock_urllib.call_args_list[1]
        request = escalation_call[0][0]
        
        # Should use escalation webhook URL
        assert request.full_url == "https://test.webhook.com/escalation"
        
        # Check escalated record
        fingerprint = alert_service._generate_fingerprint(
            AlertSeverity.WARNING, "Unresolved Issue", "This issue persists"
        )
        assert alert_service._alert_history[fingerprint].escalated
    
    def test_escalation_boosts_severity(self, alert_service, mock_urllib):
        """Verify escalation boosts severity by configured amount."""
        with patch('time.monotonic', side_effect=[0.0, 0.1, 121.0, 121.1]):
            # WARNING alert
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test",
                message="Message",
            )
            
            # Trigger escalation
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Test",
                message="Message",
            )
        
        # Check escalation call had boosted severity
        escalation_call = mock_urllib.call_args_list[1]
        request = escalation_call[0][0]
        payload = request.data.decode('utf-8')
        
        assert "CRITICAL" in payload, "WARNING should escalate to CRITICAL"
        assert "ESCALATED" in payload, "Title should indicate escalation"
    
    def test_escalation_not_triggered_if_resolved(self, alert_service, mock_urllib):
        """Verify resolved alerts don't escalate."""
        with patch('time.monotonic', side_effect=[0.0, 0.1, 60.0, 121.0, 121.1]):
            # First alert at t=0
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Issue",
                message="Problem occurred",
            )
            
            # Resolve at t=60
            alert_service.resolve_alert(
                severity=AlertSeverity.WARNING,
                title="Issue",
                message="Problem occurred",
            )
            
            # Alert at t=121 should NOT escalate (resolved)
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Issue",
                message="Problem occurred",
            )
        
        # Only first alert (no escalation)
        assert mock_urllib.call_count == 1
    
    def test_escalation_prevents_further_escalations(self, alert_service, mock_urllib):
        """Verify already-escalated alerts don't escalate again."""
        with patch('time.monotonic', side_effect=[0.0, 0.1, 121.0, 121.1, 250.0, 250.1]):
            # First alert
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Persistent Issue",
                message="Still happening",
            )
            
            # Trigger escalation at 121s
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Persistent Issue",
                message="Still happening",
            )
            
            # Alert at 250s should NOT escalate again
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Persistent Issue",
                message="Still happening",
            )
        
        # First alert + one escalation = 2 webhooks (no re-escalation)
        assert mock_urllib.call_count == 2
    
    def test_escalation_includes_metadata(self, alert_service, mock_urllib):
        """Verify escalation includes occurrence count and timing metadata."""
        with patch('time.monotonic', side_effect=[0.0, 0.1, 30.0, 60.0, 121.0, 121.1]):
            # Multiple occurrences before escalation
            for _ in range(3):
                alert_service.notify(
                    severity=AlertSeverity.WARNING,
                    title="Recurring Issue",
                    message="Problem",
                )
            
            # Trigger escalation
            alert_service.notify(
                severity=AlertSeverity.WARNING,
                title="Recurring Issue",
                message="Problem",
            )
        
        # Check escalation payload
        escalation_call = mock_urllib.call_args_list[1]  # Second webhook call
        request = escalation_call[0][0]
        payload_str = request.data.decode('utf-8')
        
        assert "occurrence_count" in payload_str or "121s" in payload_str, \
            "Escalation should include timing/occurrence metadata"


class TestAlertConfiguration:
    """Test alert configuration and setup."""
    
    def test_from_config_with_dedupe_escalation(self):
        """Verify AlertService.from_config loads dedupe/escalation settings."""
        config_dict = {
            "webhook_url": "https://test.com/webhook",
            "min_severity": "warning",
            "dry_run": False,
            "timeout_seconds": 10.0,
            "dedupe_seconds": 90.0,
            "escalation_seconds": 180.0,
            "escalation_webhook_url": "https://test.com/escalation",
            "escalation_severity_boost": 2,
        }
        
        service = AlertService.from_config(enabled=True, raw_config=config_dict)
        
        assert service._config.dedupe_seconds == 90.0
        assert service._config.escalation_seconds == 180.0
        assert service._config.escalation_webhook_url == "https://test.com/escalation"
        assert service._config.escalation_severity_boost == 2
    
    def test_default_dedupe_escalation_values(self):
        """Verify default values when not specified."""
        config_dict = {
            "webhook_url": "https://test.com/webhook",
        }
        
        service = AlertService.from_config(enabled=True, raw_config=config_dict)
        
        assert service._config.dedupe_seconds == 60.0  # Default
        assert service._config.escalation_seconds == 120.0  # Default
        assert service._config.escalation_severity_boost == 1  # Default


class TestAlertHistoryManagement:
    """Test alert history tracking and cleanup."""
    
    def test_alert_history_records_occurrences(self, alert_service):
        """Verify alert history tracks multiple occurrences."""
        with patch('time.monotonic', side_effect=[0.0, 10.0, 20.0, 30.0]):
            for i in range(4):
                alert_service.notify(
                    severity=AlertSeverity.INFO,
                    title="Test",
                    message="Message",
                )
        
        fingerprint = alert_service._generate_fingerprint(
            AlertSeverity.INFO, "Test", "Message"
        )
        record = alert_service._alert_history[fingerprint]
        
        assert record.count == 4
        assert record.first_seen == 0.0
        assert record.last_seen == 30.0
    
    def test_cleanup_removes_old_alerts(self, alert_service):
        """Verify old alerts (>5min) are cleaned up."""
        with patch('time.monotonic', side_effect=[0.0, 0.1, 400.0]):
            # Alert at t=0
            alert_service.notify(
                severity=AlertSeverity.INFO,
                title="Old Alert",
                message="Will be cleaned",
            )
            
            # Force cleanup at t=400 (>5min later)
            alert_service._last_cleanup = 0.0  # Reset cleanup timer
            alert_service._cleanup_old_alerts()
        
        # Alert should be removed
        fingerprint = alert_service._generate_fingerprint(
            AlertSeverity.INFO, "Old Alert", "Will be cleaned"
        )
        assert fingerprint not in alert_service._alert_history
    
    def test_cleanup_preserves_recent_alerts(self, alert_service):
        """Verify recent alerts (<5min) are not cleaned up."""
        with patch('time.monotonic', side_effect=[0.0, 0.1, 200.0]):
            # Alert at t=0
            alert_service.notify(
                severity=AlertSeverity.INFO,
                title="Recent Alert",
                message="Should remain",
            )
            
            # Cleanup at t=200 (3.3min later)
            alert_service._last_cleanup = 0.0
            alert_service._cleanup_old_alerts()
        
        # Alert should still be present
        fingerprint = alert_service._generate_fingerprint(
            AlertSeverity.INFO, "Recent Alert", "Should remain"
        )
        assert fingerprint in alert_service._alert_history


class TestSeverityBoosting:
    """Test severity boosting logic."""
    
    def test_boost_info_to_warning(self, alert_service):
        """Verify INFO boosts to WARNING."""
        boosted = alert_service._boost_severity(AlertSeverity.INFO, 1)
        assert boosted == AlertSeverity.WARNING
    
    def test_boost_warning_to_critical(self, alert_service):
        """Verify WARNING boosts to CRITICAL."""
        boosted = alert_service._boost_severity(AlertSeverity.WARNING, 1)
        assert boosted == AlertSeverity.CRITICAL
    
    def test_boost_critical_stays_critical(self, alert_service):
        """Verify CRITICAL can't be boosted higher."""
        boosted = alert_service._boost_severity(AlertSeverity.CRITICAL, 1)
        assert boosted == AlertSeverity.CRITICAL
    
    def test_boost_by_multiple_levels(self, alert_service):
        """Verify multi-level boosting."""
        boosted = alert_service._boost_severity(AlertSeverity.INFO, 2)
        assert boosted == AlertSeverity.CRITICAL


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
