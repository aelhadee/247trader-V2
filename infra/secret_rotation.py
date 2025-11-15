"""
Secret Rotation Tracking (REQ-SEC2)

Tracks API secret rotation dates and alerts when rotation is overdue (>90 days).
Does NOT perform automatic rotation (requires manual Coinbase API key regeneration).

Usage:
    tracker = SecretRotationTracker()
    
    # Check if rotation is due
    if tracker.rotation_due():
        logger.critical("API secrets overdue for rotation!")
    
    # After manually rotating secrets
    tracker.record_rotation("Quarterly rotation per policy")
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class SecretRotationTracker:
    """
    Tracks API secret rotation dates per REQ-SEC2 (90-day rotation policy).
    
    Metadata stored in data/secret_rotation.json:
    {
        "last_rotation_utc": "2024-11-15T10:30:00Z",
        "rotation_policy_days": 90,
        "rotations": [
            {"timestamp": "2024-11-15T10:30:00Z", "reason": "Initial setup"},
            {"timestamp": "2025-02-13T09:15:00Z", "reason": "Quarterly rotation"}
        ]
    }
    
    Security:
    - Never logs secret values
    - Tracks rotation events only (timestamps + reason)
    - Audit-friendly for compliance
    """
    
    DEFAULT_METADATA_PATH = "data/secret_rotation.json"
    ROTATION_POLICY_DAYS = 90
    WARNING_THRESHOLD_DAYS = 7  # Warn 7 days before due
    
    def __init__(self, metadata_path: Optional[str] = None):
        self.metadata_path = metadata_path or self.DEFAULT_METADATA_PATH
        self._ensure_metadata_exists()
    
    def _ensure_metadata_exists(self) -> None:
        """Create metadata file if missing (assumes initial setup = rotation event)."""
        if os.path.exists(self.metadata_path):
            return
        
        # Create directory if needed
        Path(self.metadata_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize with current timestamp as "initial setup"
        now = datetime.now(timezone.utc)
        initial_metadata = {
            "last_rotation_utc": now.isoformat(),
            "rotation_policy_days": self.ROTATION_POLICY_DAYS,
            "rotations": [
                {
                    "timestamp": now.isoformat(),
                    "reason": "Initial setup (first run)"
                }
            ]
        }
        
        with open(self.metadata_path, 'w') as f:
            json.dump(initial_metadata, f, indent=2)
        
        logger.info(f"Initialized secret rotation metadata at {self.metadata_path}")
    
    def load_metadata(self) -> Dict[str, Any]:
        """Load rotation metadata (safe - no secrets exposed)."""
        try:
            with open(self.metadata_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load rotation metadata: {e}")
            # Return safe default (treat as never rotated = overdue)
            return {
                "last_rotation_utc": datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(),
                "rotation_policy_days": self.ROTATION_POLICY_DAYS,
                "rotations": []
            }
    
    def save_metadata(self, metadata: Dict[str, Any]) -> None:
        """Persist rotation metadata."""
        try:
            with open(self.metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save rotation metadata: {e}")
    
    def get_last_rotation_date(self) -> datetime:
        """Get last rotation timestamp (UTC)."""
        metadata = self.load_metadata()
        last_rotation_str = metadata.get("last_rotation_utc")
        
        if not last_rotation_str:
            # Never rotated = ancient date
            return datetime(2020, 1, 1, tzinfo=timezone.utc)
        
        try:
            return datetime.fromisoformat(last_rotation_str.replace("Z", "+00:00"))
        except Exception as e:
            logger.warning(f"Invalid last_rotation_utc format: {e}")
            return datetime(2020, 1, 1, tzinfo=timezone.utc)
    
    def days_since_rotation(self) -> float:
        """Calculate days since last rotation."""
        last_rotation = self.get_last_rotation_date()
        now = datetime.now(timezone.utc)
        delta = now - last_rotation
        return delta.total_seconds() / 86400.0
    
    def rotation_due(self) -> bool:
        """Check if rotation is overdue (>90 days per REQ-SEC2)."""
        days_since = self.days_since_rotation()
        return days_since > self.ROTATION_POLICY_DAYS
    
    def rotation_warning(self) -> bool:
        """Check if rotation is approaching (within 7 days of due date)."""
        days_since = self.days_since_rotation()
        return days_since > (self.ROTATION_POLICY_DAYS - self.WARNING_THRESHOLD_DAYS)
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get rotation status summary (safe for logging/alerts).
        
        Returns:
            {
                "last_rotation_utc": "2024-11-15T10:30:00Z",
                "days_since_rotation": 85.3,
                "rotation_due": False,
                "rotation_warning": True,
                "days_until_due": 4.7,
                "policy_days": 90
            }
        """
        last_rotation = self.get_last_rotation_date()
        days_since = self.days_since_rotation()
        days_until_due = self.ROTATION_POLICY_DAYS - days_since
        
        return {
            "last_rotation_utc": last_rotation.isoformat(),
            "days_since_rotation": round(days_since, 1),
            "rotation_due": self.rotation_due(),
            "rotation_warning": self.rotation_warning(),
            "days_until_due": round(days_until_due, 1),
            "policy_days": self.ROTATION_POLICY_DAYS
        }
    
    def record_rotation(self, reason: str = "Manual rotation per policy") -> None:
        """
        Record a rotation event (call after manually rotating API keys).
        
        Args:
            reason: Human-readable reason (logged, no secrets)
        
        Example:
            tracker.record_rotation("Quarterly rotation - Q1 2025")
        """
        now = datetime.now(timezone.utc)
        metadata = self.load_metadata()
        
        # Add rotation event to history
        rotation_event = {
            "timestamp": now.isoformat(),
            "reason": reason
        }
        
        if "rotations" not in metadata:
            metadata["rotations"] = []
        
        metadata["rotations"].append(rotation_event)
        metadata["last_rotation_utc"] = now.isoformat()
        
        self.save_metadata(metadata)
        
        logger.info(f"Recorded secret rotation: {reason} (no secrets logged)")
        logger.info(f"Next rotation due: {(now + timedelta(days=self.ROTATION_POLICY_DAYS)).date()}")
    
    def get_rotation_history(self, limit: int = 10) -> list:
        """
        Get recent rotation events (safe - no secrets).
        
        Args:
            limit: Maximum number of events to return
        
        Returns:
            List of rotation events (most recent first)
        """
        metadata = self.load_metadata()
        rotations = metadata.get("rotations", [])
        return sorted(rotations, key=lambda x: x["timestamp"], reverse=True)[:limit]
    
    def check_and_alert(self, alert_service=None) -> None:
        """
        Check rotation status and fire alerts if needed (REQ-SEC2).
        
        Args:
            alert_service: Optional AlertService instance for notifications
        
        Alerts:
            - CRITICAL if rotation overdue (>90 days)
            - WARNING if rotation approaching (>83 days)
        """
        status = self.get_status()
        
        if status["rotation_due"]:
            msg = (
                f"🔐 API secrets OVERDUE for rotation! "
                f"Last rotation: {status['last_rotation_utc'][:10]} "
                f"({status['days_since_rotation']} days ago). "
                f"Policy requires rotation every {status['policy_days']} days. "
                f"IMMEDIATE ACTION REQUIRED: Rotate Coinbase API keys."
            )
            logger.critical(msg)
            
            if alert_service:
                alert_service.send(
                    level="CRITICAL",
                    title="Secret Rotation OVERDUE",
                    message=msg,
                    tags=["security", "compliance", "secret_rotation"]
                )
        
        elif status["rotation_warning"]:
            msg = (
                f"⚠️ API secrets rotation approaching. "
                f"Last rotation: {status['last_rotation_utc'][:10]} "
                f"({status['days_since_rotation']} days ago). "
                f"Rotation due in {status['days_until_due']} days. "
                f"Plan rotation before deadline."
            )
            logger.warning(msg)
            
            if alert_service:
                alert_service.send(
                    level="WARNING",
                    title="Secret Rotation Due Soon",
                    message=msg,
                    tags=["security", "compliance", "secret_rotation"]
                )
        else:
            logger.info(
                f"Secret rotation status: OK "
                f"(last rotated {status['days_since_rotation']} days ago, "
                f"due in {status['days_until_due']} days)"
            )
