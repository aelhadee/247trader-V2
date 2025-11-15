"""
Tests for Secret Rotation Tracking (REQ-SEC2)

Verifies 90-day rotation policy enforcement, tracking, and alerting.
"""

import json
import os
import pytest
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from infra.secret_rotation import SecretRotationTracker


@pytest.fixture
def temp_metadata_file():
    """Create temporary metadata file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_path = f.name
    yield temp_path
    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def tracker(temp_metadata_file):
    """Create tracker with temporary metadata file."""
    return SecretRotationTracker(metadata_path=temp_metadata_file)


@pytest.fixture
def mock_alert_service():
    """Create mock alert service."""
    return Mock()


class TestSecretRotationTracker:
    """Test secret rotation tracking and alerting."""
    
    def test_initializes_metadata_on_first_run(self, temp_metadata_file):
        """Test metadata file created with initial rotation event."""
        tracker = SecretRotationTracker(metadata_path=temp_metadata_file)
        
        assert os.path.exists(temp_metadata_file)
        
        metadata = tracker.load_metadata()
        assert "last_rotation_utc" in metadata
        assert "rotation_policy_days" in metadata
        assert metadata["rotation_policy_days"] == 90
        assert "rotations" in metadata
        assert len(metadata["rotations"]) == 1
        assert metadata["rotations"][0]["reason"] == "Initial setup (first run)"
    
    def test_days_since_rotation_calculated_correctly(self, tracker):
        """Test days since rotation calculation."""
        # Set last rotation to 30 days ago
        metadata = tracker.load_metadata()
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        metadata["last_rotation_utc"] = thirty_days_ago.isoformat()
        tracker.save_metadata(metadata)
        
        days_since = tracker.days_since_rotation()
        
        # Allow small tolerance for test execution time
        assert 29.9 < days_since < 30.1
    
    def test_rotation_not_due_when_recent(self, tracker):
        """Test rotation not due when last rotation was recent."""
        # Set last rotation to 30 days ago (< 90 days)
        metadata = tracker.load_metadata()
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        metadata["last_rotation_utc"] = thirty_days_ago.isoformat()
        tracker.save_metadata(metadata)
        
        assert not tracker.rotation_due()
    
    def test_rotation_due_when_overdue(self, tracker):
        """Test rotation due when >90 days since last rotation."""
        # Set last rotation to 100 days ago (> 90 days)
        metadata = tracker.load_metadata()
        hundred_days_ago = datetime.now(timezone.utc) - timedelta(days=100)
        metadata["last_rotation_utc"] = hundred_days_ago.isoformat()
        tracker.save_metadata(metadata)
        
        assert tracker.rotation_due()
    
    def test_rotation_warning_when_approaching(self, tracker):
        """Test warning triggered when rotation approaching (within 7 days)."""
        # Set last rotation to 85 days ago (> 83 days, within warning threshold)
        metadata = tracker.load_metadata()
        eighty_five_days_ago = datetime.now(timezone.utc) - timedelta(days=85)
        metadata["last_rotation_utc"] = eighty_five_days_ago.isoformat()
        tracker.save_metadata(metadata)
        
        assert tracker.rotation_warning()
        assert not tracker.rotation_due()  # Not overdue yet
    
    def test_no_warning_when_not_approaching(self, tracker):
        """Test no warning when rotation not approaching."""
        # Set last rotation to 50 days ago (< 83 days)
        metadata = tracker.load_metadata()
        fifty_days_ago = datetime.now(timezone.utc) - timedelta(days=50)
        metadata["last_rotation_utc"] = fifty_days_ago.isoformat()
        tracker.save_metadata(metadata)
        
        assert not tracker.rotation_warning()
        assert not tracker.rotation_due()
    
    def test_get_status_returns_complete_info(self, tracker):
        """Test get_status returns all required fields."""
        status = tracker.get_status()
        
        assert "last_rotation_utc" in status
        assert "days_since_rotation" in status
        assert "rotation_due" in status
        assert "rotation_warning" in status
        assert "days_until_due" in status
        assert "policy_days" in status
        assert status["policy_days"] == 90
    
    def test_record_rotation_updates_metadata(self, tracker):
        """Test recording rotation updates last_rotation_utc."""
        # Set initial rotation to 100 days ago
        metadata = tracker.load_metadata()
        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        metadata["last_rotation_utc"] = old_date.isoformat()
        tracker.save_metadata(metadata)
        
        # Record new rotation
        tracker.record_rotation("Test rotation")
        
        # Verify updated
        status = tracker.get_status()
        assert status["days_since_rotation"] < 1.0  # Should be very recent
        assert not status["rotation_due"]
    
    def test_record_rotation_appends_to_history(self, tracker):
        """Test rotation events appended to history."""
        initial_count = len(tracker.get_rotation_history())
        
        tracker.record_rotation("Quarterly rotation Q1")
        tracker.record_rotation("Quarterly rotation Q2")
        
        history = tracker.get_rotation_history()
        assert len(history) == initial_count + 2
        assert history[0]["reason"] == "Quarterly rotation Q2"  # Most recent first
        assert history[1]["reason"] == "Quarterly rotation Q1"
    
    def test_check_and_alert_fires_critical_when_overdue(self, tracker, mock_alert_service):
        """Test CRITICAL alert fired when rotation overdue."""
        # Set last rotation to 100 days ago
        metadata = tracker.load_metadata()
        hundred_days_ago = datetime.now(timezone.utc) - timedelta(days=100)
        metadata["last_rotation_utc"] = hundred_days_ago.isoformat()
        tracker.save_metadata(metadata)
        
        tracker.check_and_alert(alert_service=mock_alert_service)
        
        # Verify CRITICAL alert sent
        mock_alert_service.send.assert_called_once()
        call_kwargs = mock_alert_service.send.call_args[1]
        assert call_kwargs["level"] == "CRITICAL"
        assert "OVERDUE" in call_kwargs["title"]
        assert "secret_rotation" in call_kwargs["tags"]
    
    def test_check_and_alert_fires_warning_when_approaching(self, tracker, mock_alert_service):
        """Test WARNING alert fired when rotation approaching."""
        # Set last rotation to 85 days ago
        metadata = tracker.load_metadata()
        eighty_five_days_ago = datetime.now(timezone.utc) - timedelta(days=85)
        metadata["last_rotation_utc"] = eighty_five_days_ago.isoformat()
        tracker.save_metadata(metadata)
        
        tracker.check_and_alert(alert_service=mock_alert_service)
        
        # Verify WARNING alert sent
        mock_alert_service.send.assert_called_once()
        call_kwargs = mock_alert_service.send.call_args[1]
        assert call_kwargs["level"] == "WARNING"
        assert "Due Soon" in call_kwargs["title"]
    
    def test_check_and_alert_no_alert_when_ok(self, tracker, mock_alert_service):
        """Test no alert when rotation status OK."""
        # Set last rotation to 30 days ago
        metadata = tracker.load_metadata()
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        metadata["last_rotation_utc"] = thirty_days_ago.isoformat()
        tracker.save_metadata(metadata)
        
        tracker.check_and_alert(alert_service=mock_alert_service)
        
        # Verify no alert sent
        mock_alert_service.send.assert_not_called()
    
    def test_handles_missing_metadata_file(self, temp_metadata_file):
        """Test tracker handles missing metadata file gracefully."""
        # Delete metadata file
        if os.path.exists(temp_metadata_file):
            os.unlink(temp_metadata_file)
        
        tracker = SecretRotationTracker(metadata_path=temp_metadata_file)
        
        # Should create new file with initial rotation
        assert os.path.exists(temp_metadata_file)
        status = tracker.get_status()
        assert status["days_since_rotation"] < 1.0
    
    def test_handles_corrupted_metadata_file(self, temp_metadata_file):
        """Test tracker handles corrupted metadata file."""
        # Write invalid JSON
        with open(temp_metadata_file, 'w') as f:
            f.write("{ invalid json }")
        
        tracker = SecretRotationTracker(metadata_path=temp_metadata_file)
        
        # Should reinitialize with current date (not ancient default)
        # Because _ensure_metadata_exists detects invalid JSON and reinitializes
        status = tracker.get_status()
        assert status["days_since_rotation"] < 1.0  # Should be recent (reinitialized)
    
    def test_handles_missing_last_rotation_field(self, tracker):
        """Test tracker handles missing last_rotation_utc field."""
        # Corrupt metadata
        metadata = tracker.load_metadata()
        del metadata["last_rotation_utc"]
        tracker.save_metadata(metadata)
        
        # Should treat as never rotated
        assert tracker.rotation_due()
        days_since = tracker.days_since_rotation()
        assert days_since > 1000  # Very old (2020 default)
    
    def test_get_rotation_history_respects_limit(self, tracker):
        """Test rotation history respects limit parameter."""
        # Add 15 rotation events
        for i in range(15):
            tracker.record_rotation(f"Rotation {i}")
        
        # Request only 5 most recent
        history = tracker.get_rotation_history(limit=5)
        
        assert len(history) == 5
        assert history[0]["reason"] == "Rotation 14"  # Most recent
    
    def test_never_logs_secret_values(self, tracker, caplog):
        """Test that secret values are NEVER logged."""
        import logging
        caplog.set_level(logging.DEBUG)
        
        # Perform various operations
        tracker.get_status()
        tracker.record_rotation("Test rotation")
        tracker.get_rotation_history()
        
        # Verify no logs contain anything that looks like a secret
        for record in caplog.records:
            message = record.message.lower()
            # Check for common secret patterns
            assert "privatekey" not in message
            assert "api_secret" not in message
            assert "-----begin" not in message
            # Rotation metadata should be logged, but safely
            if "rotation" in message:
                assert "no secrets logged" in message or "timestamp" in message


class TestSecretRotationMetadataPersistence:
    """Test metadata file persistence and recovery."""
    
    def test_metadata_survives_tracker_recreation(self, temp_metadata_file):
        """Test metadata persists across tracker instances."""
        # Create tracker and record rotation
        tracker1 = SecretRotationTracker(metadata_path=temp_metadata_file)
        tracker1.record_rotation("First rotation")
        status1 = tracker1.get_status()
        
        # Create new tracker instance
        tracker2 = SecretRotationTracker(metadata_path=temp_metadata_file)
        status2 = tracker2.get_status()
        
        # Status should match
        assert abs(status1["days_since_rotation"] - status2["days_since_rotation"]) < 0.01
    
    def test_metadata_directory_created_if_missing(self):
        """Test metadata directory created if doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "subdir", "nested", "rotation.json")
            
            tracker = SecretRotationTracker(metadata_path=metadata_path)
            
            assert os.path.exists(metadata_path)
            assert os.path.exists(os.path.dirname(metadata_path))


class TestSecretRotationCompliance:
    """Test compliance with REQ-SEC2 specification."""
    
    def test_rotation_policy_is_90_days(self, tracker):
        """Test rotation policy is exactly 90 days per REQ-SEC2."""
        assert tracker.ROTATION_POLICY_DAYS == 90
        
        status = tracker.get_status()
        assert status["policy_days"] == 90
    
    def test_rotation_events_logged_without_secrets(self, tracker, caplog):
        """Test rotation events logged without exposing secret values."""
        import logging
        caplog.set_level(logging.INFO)
        
        tracker.record_rotation("Quarterly rotation per policy")
        
        # Verify event logged
        logged_messages = [record.message for record in caplog.records]
        assert any("Recorded secret rotation" in msg for msg in logged_messages)
        
        # Verify no secrets in logs
        for msg in logged_messages:
            assert "no secrets logged" in msg.lower() or "rotation" in msg.lower()
    
    def test_meets_req_sec2_specification(self, tracker):
        """
        Comprehensive test that REQ-SEC2 is fully implemented.
        
        REQ-SEC2: Secrets SHALL be rotated at least every 90 days,
        and the rotation event SHALL be logged (without exposing secret values).
        """
        # 1. Policy is 90 days
        assert tracker.ROTATION_POLICY_DAYS == 90
        
        # 2. Tracking works
        tracker.record_rotation("Test rotation")
        status = tracker.get_status()
        assert "last_rotation_utc" in status
        assert "days_since_rotation" in status
        
        # 3. Detection works
        metadata = tracker.load_metadata()
        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        metadata["last_rotation_utc"] = old_date.isoformat()
        tracker.save_metadata(metadata)
        assert tracker.rotation_due()
        
        # 4. Alerting works
        mock_alerts = Mock()
        tracker.check_and_alert(alert_service=mock_alerts)
        assert mock_alerts.send.called
        
        # 5. No secrets exposed in metadata
        metadata = tracker.load_metadata()
        metadata_str = json.dumps(metadata)
        assert "privateKey" not in metadata_str
        assert "api_secret" not in metadata_str
        assert "-----BEGIN" not in metadata_str
