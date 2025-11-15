"""Tests for REQ-SCH1: Jittered scheduling to prevent lockstep behavior."""

import pytest
from unittest.mock import Mock, patch, MagicMock

import sys
sys.modules['infra.metrics'] = MagicMock()

from runner.main_loop import TradingLoop


@patch('infra.instance_lock.check_single_instance', return_value=True)
def test_jitter_config_loaded(mock_lock):
    """Test jitter configuration loads from policy.yaml (default 10%)."""
    loop = TradingLoop(config_dir="config")
    assert hasattr(loop, 'loop_jitter_pct')
    assert isinstance(loop.loop_jitter_pct, float)
    assert loop.loop_jitter_pct == 10.0
    print(f"✅ Jitter configured: {loop.loop_jitter_pct}%")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
