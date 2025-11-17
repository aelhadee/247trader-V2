import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import infra.instance_lock as instance_lock  # noqa: E402
from runner.main_loop import TradingLoop  # noqa: E402


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset Prometheus metrics between tests to avoid registry conflicts"""
    from infra.metrics import MetricsRecorder
    # Clean up BEFORE test (in case previous test didn't have fixture)
    MetricsRecorder._reset_for_testing()
    yield
    # Clean up AFTER test
    MetricsRecorder._reset_for_testing()


def test_health_status_snapshot(monkeypatch):
    class DummyLock(SimpleNamespace):
        def __init__(self):
            super().__init__(released=False)

        def release(self):
            self.released = True

    monkeypatch.setattr(instance_lock, "check_single_instance", lambda *args, **kwargs: DummyLock())

    loop = TradingLoop(config_dir="config", mode_override="DRY_RUN")
    snapshot = loop._health_status_snapshot()

    assert snapshot["mode"] == loop.mode
    assert "rate_usage" in snapshot
    assert "cycle" in snapshot
    assert "issues" in snapshot

    loop._stop_state_store_supervisor()
    loop._stop_health_server()
    if getattr(loop, "instance_lock", None):
        loop.instance_lock.release()

