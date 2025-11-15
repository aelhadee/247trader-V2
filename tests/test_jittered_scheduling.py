"""
Tests for REQ-SCH1: Jittered scheduling to prevent lockstep behavior.

Validates:
- Jitter configuration loading from policy.yaml
- Jitter applied within 0-jitter_pct% range
- Jitter telemetry tracked in StateStore
- Sleep duration includes jitter
"""

import pytest
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from runner.main_loop import TradingLoop


def test_jitter_config_loads_from_policy():
    """Test jitter configuration loads from policy.yaml."""
    loop = TradingLoop(config_dir="config")
    # Should load jitter_pct from policy.yaml loop section
    assert hasattr(loop, 'loop_jitter_pct')
    assert isinstance(loop.loop_jitter_pct, float)
    assert 0.0 <= loop.loop_jitter_pct <= 20.0  # Within clamped range


def test_jitter_config_clamping():
    """Test custom jitter percentage."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    # Configs with 5% jitter
    (config_dir / "app.yaml").write_text(yaml.dump({
        "app": {"mode": "DRY_RUN"},
        "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},
        "logging": {"level": "INFO"}
    }))
    
    (config_dir / "policy.yaml").write_text(yaml.dump({
        "loop": {"interval_seconds": 60, "jitter_pct": 5.0},
        "risk": {"max_total_at_risk_pct": 15.0},
        "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},
        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}
    }))
    
    (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))
    (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))
    
    loop = TradingLoop(config_dir=str(config_dir))
    assert loop.loop_jitter_pct == 5.0


def test_jitter_config_disabled(tmp_path):
    """Test jitter can be disabled with 0%."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    (config_dir / "app.yaml").write_text(yaml.dump({
        "app": {"mode": "DRY_RUN"},
        "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},
        "logging": {"level": "INFO"}
    }))
    
    (config_dir / "policy.yaml").write_text(yaml.dump({
        "loop": {"interval_seconds": 60, "jitter_pct": 0.0},
        "risk": {"max_total_at_risk_pct": 15.0},
        "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},
        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}
    }))
    
    (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))
    (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))
    
    loop = TradingLoop(config_dir=str(config_dir))
    assert loop.loop_jitter_pct == 0.0


def test_jitter_clamped_max(tmp_path):
    """Test jitter clamped to maximum 20%."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    (config_dir / "app.yaml").write_text(yaml.dump({
        "app": {"mode": "DRY_RUN"},
        "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},
        "logging": {"level": "INFO"}
    }))
    
    (config_dir / "policy.yaml").write_text(yaml.dump({
        "loop": {"interval_seconds": 60, "jitter_pct": 50.0},  # Try to set 50%
        "risk": {"max_total_at_risk_pct": 15.0},
        "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},
        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}
    }))
    
    (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))
    (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))
    
    loop = TradingLoop(config_dir=str(config_dir))
    assert loop.loop_jitter_pct == 20.0  # Clamped to 20%


def test_jitter_clamped_negative(tmp_path):
    """Test jitter clamped to minimum 0%."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    (config_dir / "app.yaml").write_text(yaml.dump({
        "app": {"mode": "DRY_RUN"},
        "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},
        "logging": {"level": "INFO"}
    }))
    
    (config_dir / "policy.yaml").write_text(yaml.dump({
        "loop": {"interval_seconds": 60, "jitter_pct": -5.0},  # Negative value
        "risk": {"max_total_at_risk_pct": 15.0},
        "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},
        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}
    }))
    
    (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))
    (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))
    
    loop = TradingLoop(config_dir=str(config_dir))
    assert loop.loop_jitter_pct == 0.0  # Clamped to 0%


@patch('runner.main_loop.time.sleep')
@patch('runner.main_loop.time.monotonic')
def test_jitter_applied_in_range(mock_monotonic, mock_sleep, temp_config_dir):
    """Test that jitter is applied within 0 to jitter_pct% range."""
    # Mock time progression
    mock_monotonic.side_effect = [0.0, 5.0, 5.0, 10.0, 10.0]  # Cycle takes 5s each
    
    loop = TradingLoop(config_dir=temp_config_dir)
    loop._running = True
    
    # Mock all the heavy lifting
    loop._acquire_lock = Mock(return_value=True)
    loop.run_cycle = Mock()
    loop.state_store = Mock()
    loop.state_store.load = Mock(return_value={})
    loop.state_store.save = Mock()
    
    # Run one cycle then stop
    def stop_after_first():
        loop._running = False
    loop.run_cycle.side_effect = stop_after_first
    
    # Capture sleep durations
    sleep_durations = []
    original_sleep = mock_sleep
    def capture_sleep(duration):
        sleep_durations.append(duration)
    mock_sleep.side_effect = capture_sleep
    
    loop.run(interval_seconds=60)
    
    # Verify sleep was called
    assert len(sleep_durations) == 1
    
    # With 10% jitter, base sleep should be 60 - 5 = 55s
    # Jitter adds 0 to 6s (10% of 60s)
    # So total sleep should be in range [55, 61]
    sleep_duration = sleep_durations[0]
    assert 55.0 <= sleep_duration <= 61.0, f"Sleep {sleep_duration}s outside expected range"


@patch('runner.main_loop.time.sleep')
@patch('runner.main_loop.time.monotonic')
def test_jitter_disabled_no_randomization(mock_monotonic, mock_sleep, tmp_path):
    """Test that 0% jitter results in deterministic sleep."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    (config_dir / "app.yaml").write_text(yaml.dump({
        "app": {"mode": "DRY_RUN"},
        "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},
        "logging": {"level": "INFO"}
    }))
    
    (config_dir / "policy.yaml").write_text(yaml.dump({
        "loop": {"interval_seconds": 60, "jitter_pct": 0.0},  # No jitter
        "risk": {"max_total_at_risk_pct": 15.0},
        "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},
        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}
    }))
    
    (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))
    (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))
    
    # Mock time: cycle takes exactly 5s
    mock_monotonic.side_effect = [0.0, 5.0]
    
    loop = TradingLoop(config_dir=str(config_dir))
    loop._running = True
    
    loop._acquire_lock = Mock(return_value=True)
    loop.run_cycle = Mock()
    loop.state_store = Mock()
    loop.state_store.load = Mock(return_value={})
    loop.state_store.save = Mock()
    
    def stop_after_first():
        loop._running = False
    loop.run_cycle.side_effect = stop_after_first
    
    sleep_durations = []
    def capture_sleep(duration):
        sleep_durations.append(duration)
    mock_sleep.side_effect = capture_sleep
    
    loop.run(interval_seconds=60)
    
    # With 0% jitter and 5s cycle, sleep should be exactly 55s
    assert len(sleep_durations) == 1
    assert sleep_durations[0] == 55.0


@patch('runner.main_loop.time.sleep')
@patch('runner.main_loop.time.monotonic')
def test_jitter_telemetry_saved(mock_monotonic, mock_sleep, temp_config_dir):
    """Test that jitter stats are saved to StateStore."""
    mock_monotonic.side_effect = [0.0, 5.0]  # 5s cycle
    
    loop = TradingLoop(config_dir=temp_config_dir)
    loop._running = True
    
    loop._acquire_lock = Mock(return_value=True)
    loop.run_cycle = Mock()
    
    # Mock state store
    mock_state = {}
    loop.state_store = Mock()
    loop.state_store.load = Mock(return_value=mock_state)
    
    saved_states = []
    def capture_save(state):
        saved_states.append(state.copy())
    loop.state_store.save = Mock(side_effect=capture_save)
    
    def stop_after_first():
        loop._running = False
    loop.run_cycle.side_effect = stop_after_first
    
    mock_sleep.return_value = None
    
    loop.run(interval_seconds=60)
    
    # Verify jitter stats were saved
    assert len(saved_states) >= 1
    final_state = saved_states[-1]
    assert "jitter_stats" in final_state
    
    jitter_stats = final_state["jitter_stats"]
    assert "last_jitter_pct" in jitter_stats
    assert "last_sleep_seconds" in jitter_stats
    assert "last_cycle_seconds" in jitter_stats
    assert "last_total_interval" in jitter_stats
    
    # Verify values are reasonable
    assert 0.0 <= jitter_stats["last_jitter_pct"] <= 10.0
    assert jitter_stats["last_cycle_seconds"] == 5.0
    assert 55.0 <= jitter_stats["last_sleep_seconds"] <= 61.0


@patch('runner.main_loop.time.sleep')
@patch('runner.main_loop.time.monotonic')
def test_jitter_distribution_over_multiple_cycles(mock_monotonic, mock_sleep, temp_config_dir):
    """Test that jitter produces varied sleep durations over multiple cycles."""
    # Simulate 10 cycles, each taking 5s
    times = []
    for i in range(10):
        times.extend([i * 60.0, i * 60.0 + 5.0])
    mock_monotonic.side_effect = times
    
    loop = TradingLoop(config_dir=temp_config_dir)
    loop._running = True
    
    loop._acquire_lock = Mock(return_value=True)
    
    # Track cycle count
    cycle_count = [0]
    def run_cycle_mock():
        cycle_count[0] += 1
        if cycle_count[0] >= 10:
            loop._running = False
    loop.run_cycle = Mock(side_effect=run_cycle_mock)
    
    loop.state_store = Mock()
    loop.state_store.load = Mock(return_value={})
    loop.state_store.save = Mock()
    
    sleep_durations = []
    def capture_sleep(duration):
        sleep_durations.append(duration)
    mock_sleep.side_effect = capture_sleep
    
    loop.run(interval_seconds=60)
    
    # Should have 10 sleep calls
    assert len(sleep_durations) == 10
    
    # All should be in valid range [55, 61]
    for duration in sleep_durations:
        assert 55.0 <= duration <= 61.0
    
    # Should have variation (not all identical)
    unique_durations = set(sleep_durations)
    assert len(unique_durations) > 1, "Jitter should produce varied sleep durations"


@patch('runner.main_loop.time.sleep')
@patch('runner.main_loop.time.monotonic')
def test_jitter_minimum_sleep_enforced(mock_monotonic, mock_sleep, temp_config_dir):
    """Test that minimum 1s sleep is enforced even with jitter."""
    # Simulate a very long cycle (58s out of 60s interval)
    mock_monotonic.side_effect = [0.0, 58.0]
    
    loop = TradingLoop(config_dir=temp_config_dir)
    loop._running = True
    
    loop._acquire_lock = Mock(return_value=True)
    loop.run_cycle = Mock()
    loop.state_store = Mock()
    loop.state_store.load = Mock(return_value={})
    loop.state_store.save = Mock()
    
    def stop_after_first():
        loop._running = False
    loop.run_cycle.side_effect = stop_after_first
    
    sleep_durations = []
    def capture_sleep(duration):
        sleep_durations.append(duration)
    mock_sleep.side_effect = capture_sleep
    
    loop.run(interval_seconds=60)
    
    # Even though base would be 2s + jitter, minimum 1s should be enforced
    assert len(sleep_durations) == 1
    assert sleep_durations[0] >= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
