"""
Clock Sync Validation (REQ-TIME1)

Validates host clock is NTP-synced with drift <100ms before trading.
Critical for timestamp-based operations (order timestamps, fill reconciliation, etc.).

Usage:
    validator = ClockSyncValidator()
    
    # Check on startup
    validator.validate_or_fail(mode="LIVE")  # Raises if drift >100ms
    
    # Or check without failing
    status = validator.check_sync()
    if not status["synced"]:
        logger.warning(f"Clock drift: {status['drift_ms']}ms")
"""

import logging
import socket
import struct
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class ClockSyncValidator:
    """
    Validates clock sync via NTP per REQ-TIME1 (<100ms drift requirement).
    
    NTP Query:
    - Queries public NTP servers (pool.ntp.org)
    - Calculates round-trip time and offset
    - Fails startup if drift >100ms in LIVE mode
    
    Mode Gating:
    - DRY_RUN: Skip validation (no real orders)
    - PAPER: Validate with warning only
    - LIVE: Fail fast on >100ms drift
    
    Compliance:
    - Meets REQ-TIME1 specification
    - Prevents stale order timestamps
    - Ensures accurate fill reconciliation
    """
    
    # NTP time begins 1900-01-01, Unix time begins 1970-01-01
    NTP_EPOCH_OFFSET = 2208988800  # Seconds between 1900 and 1970
    
    MAX_DRIFT_MS = 100.0  # Per REQ-TIME1
    TIMEOUT_SECONDS = 5.0
    
    # Public NTP servers (fallback list)
    NTP_SERVERS = [
        "pool.ntp.org",
        "time.apple.com",
        "time.google.com",
        "time.cloudflare.com",
    ]
    
    def __init__(self, max_drift_ms: float = MAX_DRIFT_MS, timeout: float = TIMEOUT_SECONDS):
        self.max_drift_ms = max_drift_ms
        self.timeout = timeout
    
    def _query_ntp(self, server: str) -> Tuple[float, float]:
        """
        Query NTP server and calculate offset.
        
        Returns:
            (offset_seconds, round_trip_seconds)
        
        Raises:
            Exception on network/timeout errors
        """
        # Create NTP request packet (48 bytes, mode 3 = client)
        ntp_request = b'\x1b' + 47 * b'\0'
        
        # Record client transmit time (T1)
        t1 = time.time()
        
        # Send request
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        
        try:
            sock.sendto(ntp_request, (server, 123))
            
            # Receive response
            data, _ = sock.recvfrom(1024)
            
            # Record client receive time (T4)
            t4 = time.time()
            
            # Parse NTP timestamps from response
            # Server receive time (T2) at byte offset 32-39
            # Server transmit time (T3) at byte offset 40-47
            t2 = struct.unpack('!Q', data[32:40])[0] / 2**32 - self.NTP_EPOCH_OFFSET
            t3 = struct.unpack('!Q', data[40:48])[0] / 2**32 - self.NTP_EPOCH_OFFSET
            
            # Calculate offset and round-trip delay
            # Offset = ((T2 - T1) + (T3 - T4)) / 2
            # Round-trip = (T4 - T1) - (T3 - T2)
            offset = ((t2 - t1) + (t3 - t4)) / 2
            round_trip = (t4 - t1) - (t3 - t2)
            
            return offset, round_trip
        
        finally:
            sock.close()
    
    def query_ntp_with_fallback(self) -> Optional[Dict[str, Any]]:
        """
        Query NTP servers with fallback (try multiple servers).
        
        Returns:
            {
                "server": "pool.ntp.org",
                "offset_ms": 45.2,
                "round_trip_ms": 23.1,
                "local_time": "2024-11-15T10:30:00Z",
                "ntp_time": "2024-11-15T10:30:00.045Z"
            }
            
            Or None if all servers fail
        """
        for server in self.NTP_SERVERS:
            try:
                logger.debug(f"Querying NTP server: {server}")
                offset, round_trip = self._query_ntp(server)
                
                offset_ms = offset * 1000
                round_trip_ms = round_trip * 1000
                
                local_time = datetime.now(timezone.utc)
                ntp_time = datetime.fromtimestamp(local_time.timestamp() + offset, tz=timezone.utc)
                
                result = {
                    "server": server,
                    "offset_ms": round(offset_ms, 1),
                    "round_trip_ms": round(round_trip_ms, 1),
                    "local_time": local_time.isoformat(),
                    "ntp_time": ntp_time.isoformat()
                }
                
                logger.info(
                    f"NTP sync check: server={server}, "
                    f"offset={offset_ms:.1f}ms, round_trip={round_trip_ms:.1f}ms"
                )
                
                return result
            
            except Exception as e:
                logger.warning(f"NTP query failed for {server}: {e}")
                continue
        
        logger.error("All NTP servers failed to respond")
        return None
    
    def check_sync(self) -> Dict[str, Any]:
        """
        Check clock sync status (non-blocking).
        
        Returns:
            {
                "synced": True/False,
                "drift_ms": 45.2,
                "within_tolerance": True,
                "max_drift_ms": 100.0,
                "server": "pool.ntp.org",
                "error": None or "error message"
            }
        """
        ntp_result = self.query_ntp_with_fallback()
        
        if not ntp_result:
            return {
                "synced": False,
                "drift_ms": None,
                "within_tolerance": False,
                "max_drift_ms": self.max_drift_ms,
                "server": None,
                "error": "All NTP servers unreachable"
            }
        
        drift_ms = abs(ntp_result["offset_ms"])
        within_tolerance = drift_ms <= self.max_drift_ms
        
        return {
            "synced": within_tolerance,
            "drift_ms": drift_ms,
            "within_tolerance": within_tolerance,
            "max_drift_ms": self.max_drift_ms,
            "server": ntp_result["server"],
            "round_trip_ms": ntp_result["round_trip_ms"],
            "error": None
        }
    
    def validate_or_fail(self, mode: str = "LIVE") -> Dict[str, Any]:
        """
        Validate clock sync and fail startup if drift >100ms (REQ-TIME1).
        
        Args:
            mode: Trading mode (DRY_RUN/PAPER/LIVE)
        
        Returns:
            Sync status dict
        
        Raises:
            RuntimeError: If drift >100ms in LIVE mode
        
        Mode Behavior:
            - DRY_RUN: Skip validation (log only)
            - PAPER: Validate but don't fail (warning only)
            - LIVE: Fail fast on >100ms drift
        """
        # DRY_RUN: Skip validation (no real orders)
        if mode == "DRY_RUN":
            logger.info("Clock sync check skipped (DRY_RUN mode)")
            return {
                "synced": True,
                "drift_ms": 0.0,
                "within_tolerance": True,
                "max_drift_ms": self.max_drift_ms,
                "server": "SKIPPED (DRY_RUN)",
                "error": None
            }
        
        # Query NTP
        status = self.check_sync()
        
        # PAPER mode: Warn but don't fail
        if mode == "PAPER":
            if not status["synced"]:
                logger.warning(
                    f"Clock drift exceeds tolerance in PAPER mode: "
                    f"{status['drift_ms']:.1f}ms > {self.max_drift_ms}ms. "
                    f"Continuing with paper trading (would fail in LIVE mode)."
                )
            else:
                logger.info(
                    f"Clock sync validated (PAPER mode): "
                    f"drift={status['drift_ms']:.1f}ms, tolerance={self.max_drift_ms}ms"
                )
            return status
        
        # LIVE mode: Fail fast on excessive drift
        if not status["synced"]:
            drift_str = f"{status['drift_ms']:.1f}ms" if status['drift_ms'] is not None else "unknown"
            error_msg = (
                f"Clock drift exceeds tolerance (REQ-TIME1): "
                f"{drift_str} > {self.max_drift_ms}ms. "
                f"LIVE trading requires accurate timestamps. "
                f"Fix: Ensure NTP sync is enabled and working. "
                f"Server: {status['server'] or 'unreachable'}"
            )
            logger.critical(error_msg)
            raise RuntimeError(error_msg)
        
        logger.info(
            f"✅ Clock sync validated (LIVE mode): "
            f"drift={status['drift_ms']:.1f}ms < {self.max_drift_ms}ms, "
            f"server={status['server']}"
        )
        
        return status
    
    def get_diagnostics(self) -> Dict[str, Any]:
        """
        Get detailed clock sync diagnostics (for troubleshooting).
        
        Returns:
            {
                "status": {...},  # Same as check_sync()
                "local_time_utc": "2024-11-15T10:30:00Z",
                "system_uptime": 123456,  # Seconds (if available)
                "ntp_servers_tested": ["pool.ntp.org", "time.apple.com"],
                "recommendations": ["Enable NTP sync", "Check firewall UDP 123"]
            }
        """
        status = self.check_sync()
        
        diagnostics = {
            "status": status,
            "local_time_utc": datetime.now(timezone.utc).isoformat(),
            "ntp_servers_tested": self.NTP_SERVERS,
            "recommendations": []
        }
        
        # Add recommendations based on status
        if not status["synced"]:
            if status["error"]:
                diagnostics["recommendations"].append(
                    "Check network connectivity to NTP servers (UDP port 123)"
                )
                diagnostics["recommendations"].append(
                    "Verify firewall allows outbound NTP traffic"
                )
            
            if status["drift_ms"] and status["drift_ms"] > self.max_drift_ms:
                diagnostics["recommendations"].append(
                    f"Enable NTP sync: sudo systemctl enable --now systemd-timesyncd (Linux) "
                    f"or System Preferences → Date & Time → Set time automatically (macOS)"
                )
                diagnostics["recommendations"].append(
                    "Verify NTP service is running: timedatectl status (Linux) or sudo sntp -d pool.ntp.org (macOS)"
                )
        
        return diagnostics
