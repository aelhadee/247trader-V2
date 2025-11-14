import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.risk import RiskEngine  # noqa: E402


def test_circuit_snapshot_flags_cooldown():
    with open(ROOT_DIR / "config" / "policy.yaml") as f:
        policy = yaml.safe_load(f)

    engine = RiskEngine(policy)

    engine._last_rate_limit_time = datetime.now(timezone.utc)
    snapshot = engine.circuit_snapshot()
    assert snapshot["rate_limit_cooldown_active"] is True
    assert snapshot["last_rate_limit_time"] is not None

    cooldown = snapshot["rate_limit_cooldown_seconds"] or 0
    engine._last_rate_limit_time = datetime.now(timezone.utc) - timedelta(seconds=float(cooldown) + 5)
    snapshot = engine.circuit_snapshot()
    assert snapshot["rate_limit_cooldown_active"] is False

