"""Tests for REQ-SCH1: Jittered scheduling to prevent lockstep behavior."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys

from runner.main_loop import TradingLoop


@pytest.fixture(autouse=True)
def reset_metrics_and_cleanup():
    """Reset Prometheus metrics and clean up sys.modules mocking"""
    # Save original metrics module if it exists
    original_metrics = sys.modules.get('infra.metrics')
    
    # Clean up BEFORE test
    if original_metrics and hasattr(original_metrics, 'MetricsRecorder'):
        try:
            original_metrics.MetricsRecorder._reset_for_testing()
        except:
            pass
    
    yield
    
    # Clean up AFTER test
    if original_metrics and hasattr(original_metrics, 'MetricsRecorder'):
        try:
            original_metrics.MetricsRecorder._reset_for_testing()
        except:
            pass
    
    # Remove any mock from sys.modules to prevent polluting other tests
    if 'infra.metrics' in sys.modules and isinstance(sys.modules['infra.metrics'], MagicMock):
        if original_metrics:
            sys.modules['infra.metrics'] = original_metrics
        else:
            del sys.modules['infra.metrics']


@patch('infra.instance_lock.check_single_instance', return_value=True)
def test_jitter_config_loaded(mock_lock):
    """Test jitter configuration loads from policy.yaml (default 10%)."""
    loop = TradingLoop(config_dir="config", mode_override="DRY_RUN")
    assert hasattr(loop, 'loop_jitter_pct')
    assert isinstance(loop.loop_jitter_pct, float)
    assert loop.loop_jitter_pct == 10.0
    print(f"✅ Jitter configured: {loop.loop_jitter_pct}%")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
