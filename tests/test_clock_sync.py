"""
Tests for Clock Sync Validation (REQ-TIME1)

Verifies NTP-based clock sync validation with <100ms drift requirement.
"""

import pytest
import struct
import time
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

from infra.clock_sync import ClockSyncValidator


@pytest.fixture
def validator():
    """Create clock sync validator with default config."""
    return ClockSyncValidator()


@pytest.fixture
def validator_strict():
    """Create validator with stricter tolerance for testing."""
    return ClockSyncValidator(max_drift_ms=50.0, timeout=2.0)


class TestNTPQuery:
    """Test NTP query and offset calculation."""
    
    def test_query_ntp_calculates_offset(self, validator):
        """Test NTP query calculates time offset correctly."""
        # Mock socket to simulate NTP response
        with patch('socket.socket') as mock_socket_class:
            mock_sock = MagicMock()
            mock_socket_class.return_value = mock_sock
            
            # Simulate NTP response packet
            # NTP timestamps are 64-bit fixed-point (seconds since 1900)
            ntp_epoch_offset = validator.NTP_EPOCH_OFFSET
            current_unix_time = time.time()
            current_ntp_time = current_unix_time + ntp_epoch_offset
            
            # Server receive time (T2) and transmit time (T3)
            t2_ntp = int(current_ntp_time * 2**32)
            t3_ntp = int((current_ntp_time + 0.01) * 2**32)  # 10ms processing delay
            
            # Build fake NTP response
            ntp_response = b'\x00' * 32  # Header
            ntp_response += struct.pack('!Q', t2_ntp)  # T2 at offset 32-39
            ntp_response += struct.pack('!Q', t3_ntp)  # T3 at offset 40-47
            
            mock_sock.recvfrom.return_value = (ntp_response, ('127.0.0.1', 123))
            
            # Query NTP
            offset, round_trip = validator._query_ntp("pool.ntp.org")
            
            # Offset should be small (within reasonable bounds)
            assert abs(offset) < 1.0  # Within 1 second
            assert round_trip > -0.1  # Allow small negative due to clock jitter (should be close to 0)
    
    def test_query_ntp_handles_timeout(self, validator):
        """Test NTP query handles timeout gracefully."""
        with patch('socket.socket') as mock_socket_class:
            mock_sock = MagicMock()
            mock_socket_class.return_value = mock_sock
            mock_sock.recvfrom.side_effect = TimeoutError("Timeout")
            
            with pytest.raises(TimeoutError):
                validator._query_ntp("unreachable.example.com")
    
    def test_query_ntp_handles_network_error(self, validator):
        """Test NTP query handles network errors."""
        with patch('socket.socket') as mock_socket_class:
            mock_sock = MagicMock()
            mock_socket_class.return_value = mock_sock
            mock_sock.sendto.side_effect = OSError("Network unreachable")
            
            with pytest.raises(OSError):
                validator._query_ntp("pool.ntp.org")


class TestNTPFallback:
    """Test NTP server fallback logic."""
    
    def test_tries_multiple_servers_on_failure(self, validator):
        """Test validator tries multiple NTP servers if first fails."""
        # Mock NTP query to fail for first server, succeed for second
        with patch.object(validator, '_query_ntp') as mock_query:
            mock_query.side_effect = [
                TimeoutError("First server timeout"),
                (0.045, 0.023),  # Second server succeeds (45ms offset, 23ms RTT)
            ]
            
            result = validator.query_ntp_with_fallback()
            
            assert result is not None
            assert result["offset_ms"] == 45.0
            assert result["round_trip_ms"] == 23.0
            assert mock_query.call_count >= 2  # Tried at least 2 servers
    
    def test_returns_none_if_all_servers_fail(self, validator):
        """Test returns None if all NTP servers unreachable."""
        with patch.object(validator, '_query_ntp') as mock_query:
            mock_query.side_effect = TimeoutError("All servers timeout")
            
            result = validator.query_ntp_with_fallback()
            
            assert result is None
            assert mock_query.call_count == len(validator.NTP_SERVERS)
    
    def test_uses_first_successful_server(self, validator):
        """Test uses first successful server and doesn't query others."""
        with patch.object(validator, '_query_ntp') as mock_query:
            mock_query.return_value = (0.025, 0.015)  # First server succeeds
            
            result = validator.query_ntp_with_fallback()
            
            assert result is not None
            assert mock_query.call_count == 1  # Only tried first server


class TestClockSyncCheck:
    """Test check_sync method (non-blocking)."""
    
    def test_check_sync_returns_synced_when_within_tolerance(self, validator):
        """Test returns synced=True when drift <100ms."""
        with patch.object(validator, 'query_ntp_with_fallback') as mock_query:
            mock_query.return_value = {
                "server": "pool.ntp.org",
                "offset_ms": 45.2,  # Within 100ms tolerance
                "round_trip_ms": 23.1,
                "local_time": datetime.now(timezone.utc).isoformat(),
                "ntp_time": datetime.now(timezone.utc).isoformat()
            }
            
            status = validator.check_sync()
            
            assert status["synced"] is True
            assert status["drift_ms"] == 45.2
            assert status["within_tolerance"] is True
            assert status["max_drift_ms"] == 100.0
    
    def test_check_sync_returns_not_synced_when_exceeds_tolerance(self, validator):
        """Test returns synced=False when drift >100ms."""
        with patch.object(validator, 'query_ntp_with_fallback') as mock_query:
            mock_query.return_value = {
                "server": "pool.ntp.org",
                "offset_ms": 150.5,  # Exceeds 100ms tolerance
                "round_trip_ms": 23.1,
                "local_time": datetime.now(timezone.utc).isoformat(),
                "ntp_time": datetime.now(timezone.utc).isoformat()
            }
            
            status = validator.check_sync()
            
            assert status["synced"] is False
            assert status["drift_ms"] == 150.5
            assert status["within_tolerance"] is False
    
    def test_check_sync_uses_absolute_offset(self, validator):
        """Test uses absolute value of offset (negative offset = clock ahead)."""
        with patch.object(validator, 'query_ntp_with_fallback') as mock_query:
            mock_query.return_value = {
                "server": "pool.ntp.org",
                "offset_ms": -45.2,  # Negative offset (clock ahead)
                "round_trip_ms": 23.1,
                "local_time": datetime.now(timezone.utc).isoformat(),
                "ntp_time": datetime.now(timezone.utc).isoformat()
            }
            
            status = validator.check_sync()
            
            assert status["drift_ms"] == 45.2  # Absolute value
            assert status["synced"] is True  # Within tolerance
    
    def test_check_sync_returns_error_when_ntp_unreachable(self, validator):
        """Test returns error status when NTP servers unreachable."""
        with patch.object(validator, 'query_ntp_with_fallback') as mock_query:
            mock_query.return_value = None  # All servers failed
            
            status = validator.check_sync()
            
            assert status["synced"] is False
            assert status["drift_ms"] is None
            assert status["within_tolerance"] is False
            assert "unreachable" in status["error"].lower()


class TestValidateOrFail:
    """Test validate_or_fail method (mode-gated behavior)."""
    
    def test_dry_run_skips_validation(self, validator):
        """Test DRY_RUN mode skips validation entirely."""
        # Don't mock anything - should skip NTP query
        status = validator.validate_or_fail(mode="DRY_RUN")
        
        assert status["synced"] is True
        assert status["server"] == "SKIPPED (DRY_RUN)"
        assert status["error"] is None
    
    def test_paper_mode_warns_but_does_not_fail(self, validator):
        """Test PAPER mode validates but doesn't fail on excessive drift."""
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": False,
                "drift_ms": 150.0,  # Exceeds tolerance
                "within_tolerance": False,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None
            }
            
            # Should NOT raise, just log warning
            status = validator.validate_or_fail(mode="PAPER")
            
            assert status["synced"] is False
            assert status["drift_ms"] == 150.0
    
    def test_live_mode_passes_when_within_tolerance(self, validator):
        """Test LIVE mode passes when drift <100ms."""
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": True,
                "drift_ms": 45.0,
                "within_tolerance": True,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None
            }
            
            status = validator.validate_or_fail(mode="LIVE")
            
            assert status["synced"] is True
    
    def test_live_mode_fails_when_exceeds_tolerance(self, validator):
        """Test LIVE mode raises RuntimeError when drift >100ms."""
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": False,
                "drift_ms": 150.0,
                "within_tolerance": False,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None
            }
            
            with pytest.raises(RuntimeError) as exc_info:
                validator.validate_or_fail(mode="LIVE")
            
            assert "REQ-TIME1" in str(exc_info.value)
            assert "150.0ms" in str(exc_info.value)
            assert "100.0ms" in str(exc_info.value)
    
    def test_live_mode_fails_when_ntp_unreachable(self, validator):
        """Test LIVE mode raises RuntimeError when NTP servers unreachable."""
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": False,
                "drift_ms": None,
                "within_tolerance": False,
                "max_drift_ms": 100.0,
                "server": None,
                "error": "All NTP servers unreachable"
            }
            
            with pytest.raises(RuntimeError) as exc_info:
                validator.validate_or_fail(mode="LIVE")
            
            assert "unreachable" in str(exc_info.value).lower()


class TestCustomTolerance:
    """Test custom tolerance configuration."""
    
    def test_accepts_custom_max_drift(self):
        """Test validator accepts custom max drift tolerance."""
        validator = ClockSyncValidator(max_drift_ms=50.0)
        
        assert validator.max_drift_ms == 50.0
    
    def test_uses_custom_tolerance_in_checks(self):
        """Test custom tolerance used in sync checks."""
        validator = ClockSyncValidator(max_drift_ms=50.0)
        
        with patch.object(validator, 'query_ntp_with_fallback') as mock_query:
            mock_query.return_value = {
                "server": "pool.ntp.org",
                "offset_ms": 75.0,  # Would pass 100ms, fails 50ms
                "round_trip_ms": 20.0,
                "local_time": datetime.now(timezone.utc).isoformat(),
                "ntp_time": datetime.now(timezone.utc).isoformat()
            }
            
            status = validator.check_sync()
            
            assert status["synced"] is False
            assert status["max_drift_ms"] == 50.0
    
    def test_accepts_custom_timeout(self):
        """Test validator accepts custom timeout."""
        validator = ClockSyncValidator(timeout=2.0)
        
        assert validator.timeout == 2.0


class TestDiagnostics:
    """Test get_diagnostics method."""
    
    def test_diagnostics_includes_status(self, validator):
        """Test diagnostics includes sync status."""
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": True,
                "drift_ms": 45.0,
                "within_tolerance": True,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None
            }
            
            diagnostics = validator.get_diagnostics()
            
            assert "status" in diagnostics
            assert diagnostics["status"]["synced"] is True
    
    def test_diagnostics_includes_local_time(self, validator):
        """Test diagnostics includes local time."""
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": True,
                "drift_ms": 45.0,
                "within_tolerance": True,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None
            }
            
            diagnostics = validator.get_diagnostics()
            
            assert "local_time_utc" in diagnostics
            # Should be ISO format
            datetime.fromisoformat(diagnostics["local_time_utc"].replace("Z", "+00:00"))
    
    def test_diagnostics_includes_recommendations_on_failure(self, validator):
        """Test diagnostics includes recommendations when sync fails."""
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": False,
                "drift_ms": None,
                "within_tolerance": False,
                "max_drift_ms": 100.0,
                "server": None,
                "error": "All NTP servers unreachable"
            }
            
            diagnostics = validator.get_diagnostics()
            
            assert "recommendations" in diagnostics
            assert len(diagnostics["recommendations"]) > 0
            # Should mention network/firewall
            recommendations_str = " ".join(diagnostics["recommendations"]).lower()
            assert "network" in recommendations_str or "firewall" in recommendations_str
    
    def test_diagnostics_recommends_ntp_enable_on_excessive_drift(self, validator):
        """Test diagnostics recommends enabling NTP when drift excessive."""
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": False,
                "drift_ms": 250.0,  # Excessive drift
                "within_tolerance": False,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None
            }
            
            diagnostics = validator.get_diagnostics()
            
            recommendations_str = " ".join(diagnostics["recommendations"]).lower()
            assert "ntp" in recommendations_str
            assert "sync" in recommendations_str or "enable" in recommendations_str


class TestREQTIME1Compliance:
    """Test compliance with REQ-TIME1 specification."""
    
    def test_max_drift_is_100ms(self, validator):
        """Test default max drift is 100ms per REQ-TIME1."""
        assert validator.MAX_DRIFT_MS == 100.0
        assert validator.max_drift_ms == 100.0
    
    def test_validates_ntp_sync(self, validator):
        """Test performs NTP sync validation."""
        with patch.object(validator, 'query_ntp_with_fallback') as mock_query:
            mock_query.return_value = {
                "server": "pool.ntp.org",
                "offset_ms": 45.0,
                "round_trip_ms": 20.0,
                "local_time": datetime.now(timezone.utc).isoformat(),
                "ntp_time": datetime.now(timezone.utc).isoformat()
            }
            
            status = validator.check_sync()
            
            assert mock_query.called
            assert status["synced"] is True
    
    def test_refuses_to_start_in_live_mode_when_drift_excessive(self, validator):
        """Test refuses to start in LIVE mode when drift >100ms."""
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": False,
                "drift_ms": 150.0,
                "within_tolerance": False,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None
            }
            
            with pytest.raises(RuntimeError):
                validator.validate_or_fail(mode="LIVE")
    
    def test_meets_req_time1_specification(self, validator):
        """
        Comprehensive test that REQ-TIME1 is fully implemented.
        
        REQ-TIME1: The host clock SHALL be NTP-synced with drift <100ms
        relative to a trusted source; otherwise the app SHALL refuse to start.
        """
        # 1. Default tolerance is 100ms
        assert validator.max_drift_ms == 100.0
        
        # 2. Queries NTP servers
        with patch.object(validator, '_query_ntp') as mock_query:
            mock_query.return_value = (0.045, 0.020)
            result = validator.query_ntp_with_fallback()
            assert result is not None
            assert mock_query.called
        
        # 3. Validates drift
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": False,
                "drift_ms": 150.0,
                "within_tolerance": False,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None
            }
            
            # 4. Refuses to start in LIVE mode
            with pytest.raises(RuntimeError) as exc_info:
                validator.validate_or_fail(mode="LIVE")
            
            assert "REQ-TIME1" in str(exc_info.value)
        
        # 5. Allows start when within tolerance
        with patch.object(validator, 'check_sync') as mock_check:
            mock_check.return_value = {
                "synced": True,
                "drift_ms": 45.0,
                "within_tolerance": True,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None
            }
            
            status = validator.validate_or_fail(mode="LIVE")
            assert status["synced"] is True
