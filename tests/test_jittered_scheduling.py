"""""""""

Tests for REQ-SCH1: Jittered scheduling to prevent lockstep behavior.

Tests for REQ-SCH1: Jittered scheduling to prevent lockstep behavior.Tests for REQ-SCH1: Jittered scheduling to prevent lockstep behavior.

Validates:

- Jitter configuration loading from policy.yaml  

- Jitter clamping to safe bounds (0-20%)

- Jitter applied within specified rangeValidates:Validates:

- Jitter telemetry tracked in StateStore

"""- Jitter configuration loading from policy.yaml  - Jitter configuration loading from policy.yaml



import pytest- Jitter clamping to safe bounds (0-20%)- Jitter applied within 0-jitter_pct% range

from unittest.mock import Mock, patch

from runner.main_loop import TradingLoop- Jitter applied within specified range- Jitter telemetry tracked in StateStore



- Jitter telemetry tracked in StateStore- Sleep duration includes jitter

class TestJitterConfiguration:

    """Test jitter configuration loading and validation."""""""""

    

    def test_jitter_loads_from_policy(self):

        """Test jitter_pct loads from policy.yaml."""

        loop = TradingLoop(config_dir="config")import pytestimport pytest

        assert hasattr(loop, 'loop_jitter_pct')

        assert isinstance(loop.loop_jitter_pct, float)from unittest.mock import Mock, patchimport time

        assert loop.loop_jitter_pct == 10.0

    from runner.main_loop import TradingLoopfrom pathlib import Path

    def test_jitter_clamping_to_max(self):

        """Test jitter values are clamped to maximum 20%."""from unittest.mock import Mock, patch, MagicMock

        with patch.object(TradingLoop, '_load_yaml') as mock_load:

            def mock_yaml_loader(filename):from runner.main_loop import TradingLoop

                if 'policy.yaml' in filename:

                    return {class TestJitterConfiguration:

                        'loop': {'interval_seconds': 60, 'jitter_pct': 50.0},

                        'risk': {'max_total_at_risk_pct': 15.0},    """Test jitter configuration loading and validation."""

                        'execution': {'maker_fee_bps': 40, 'taker_fee_bps': 60},

                        'latency': {'total_seconds': 45.0}    def test_jitter_config_loads_from_policy():

                    }

                return {'triggers': {}} if 'signals' in filename else {'tiers': {}}    def test_jitter_loads_from_policy(self):    """Test jitter configuration loads from policy.yaml."""

            

            mock_load.side_effect = mock_yaml_loader        """Test jitter_pct loads from policy.yaml."""    loop = TradingLoop(config_dir="config")

            

            with patch('tools.config_validator.validate_all_configs', return_value=[]):        loop = TradingLoop(config_dir="config")    # Should load jitter_pct from policy.yaml loop section (default 10.0)

                loop = TradingLoop(config_dir="config")

                assert loop.loop_jitter_pct == 20.0        assert hasattr(loop, 'loop_jitter_pct')    assert hasattr(loop, 'loop_jitter_pct')

    

    def test_jitter_clamping_to_min(self):        assert isinstance(loop.loop_jitter_pct, float)    assert isinstance(loop.loop_jitter_pct, float)

        """Test jitter values are clamped to minimum 0%."""

        with patch.object(TradingLoop, '_load_yaml') as mock_load:        # Should be 10.0 from policy.yaml    assert loop.loop_jitter_pct == 10.0  # From policy.yaml

            def mock_yaml_loader(filename):

                if 'policy.yaml' in filename:        assert loop.loop_jitter_pct == 10.0

                    return {

                        'loop': {'interval_seconds': 60, 'jitter_pct': -10.0},    

                        'risk': {'max_total_at_risk_pct': 15.0},

                        'execution': {'maker_fee_bps': 40, 'taker_fee_bps': 60},    def test_jitter_clamping_to_max(self):def test_jitter_config_clamping_bounds():

                        'latency': {'total_seconds': 45.0}

                    }        """Test jitter values are clamped to maximum 20%."""    """Test custom jitter percentage."""

                return {'triggers': {}} if 'signals' in filename else {'tiers': {}}

                    # Mock config loading to inject extreme value    config_dir = tmp_path / "config"

            mock_load.side_effect = mock_yaml_loader

                    with patch.object(TradingLoop, '_load_yaml') as mock_load:    config_dir.mkdir()

            with patch('tools.config_validator.validate_all_configs', return_value=[]):

                loop = TradingLoop(config_dir="config")            def mock_yaml_loader(filename):    

                assert loop.loop_jitter_pct == 0.0

                if 'policy.yaml' in filename:    # Configs with 5% jitter



class TestJitterApplication:                    return {    (config_dir / "app.yaml").write_text(yaml.dump({

    """Test jitter is applied correctly during loop execution."""

                            'loop': {'interval_seconds': 60, 'jitter_pct': 50.0},  # Try 50%        "app": {"mode": "DRY_RUN"},

    @patch('runner.main_loop.time.sleep')

    @patch('runner.main_loop.time.monotonic')                        'risk': {'max_total_at_risk_pct': 15.0},        "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},

    def test_jitter_applied_in_valid_range(self, mock_monotonic, mock_sleep):

        """Test jitter adds 0 to jitter_pct% to sleep duration."""                        'execution': {'maker_fee_bps': 40, 'taker_fee_bps': 60},        "logging": {"level": "INFO"}

        mock_monotonic.side_effect = [0.0, 5.0]

                                'latency': {'total_seconds': 45.0}    }))

        loop = TradingLoop(config_dir="config")

        loop._running = True                    }    

        loop._acquire_lock = Mock(return_value=True)

        loop.run_cycle = Mock()                return {'triggers': {}} if 'signals' in filename else {'tiers': {}}    (config_dir / "policy.yaml").write_text(yaml.dump({

        loop.state_store = Mock()

        loop.state_store.load = Mock(return_value={})                    "loop": {"interval_seconds": 60, "jitter_pct": 5.0},

        loop.state_store.save = Mock()

                    mock_load.side_effect = mock_yaml_loader        "risk": {"max_total_at_risk_pct": 15.0},

        def stop_after_one():

            loop._running = False                    "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},

        loop.run_cycle.side_effect = stop_after_one

                    # Bypass validation for test        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}

        sleep_durations = []

        def capture_sleep(duration):            with patch('tools.config_validator.validate_all_configs', return_value=[]):    }))

            sleep_durations.append(duration)

        mock_sleep.side_effect = capture_sleep                loop = TradingLoop(config_dir="config")    

        

        loop.run(interval_seconds=60)                assert loop.loop_jitter_pct == 20.0  # Clamped to max    (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))

        

        assert len(sleep_durations) == 1        (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))

        sleep_duration = sleep_durations[0]

        assert 55.0 <= sleep_duration <= 61.0    def test_jitter_clamping_to_min(self):    

    

    @patch('runner.main_loop.time.sleep')        """Test jitter values are clamped to minimum 0%."""    loop = TradingLoop(config_dir=str(config_dir))

    @patch('runner.main_loop.time.monotonic')

    def test_zero_jitter_is_deterministic(self, mock_monotonic, mock_sleep):        with patch.object(TradingLoop, '_load_yaml') as mock_load:    assert loop.loop_jitter_pct == 5.0

        """Test 0% jitter results in no randomization."""

        mock_monotonic.side_effect = [0.0, 5.0]            def mock_yaml_loader(filename):

        

        with patch.object(TradingLoop, '_load_yaml') as mock_load:                if 'policy.yaml' in filename:

            def mock_yaml_loader(filename):

                if 'policy.yaml' in filename:                    return {def test_jitter_config_disabled(tmp_path):

                    return {

                        'loop': {'interval_seconds': 60, 'jitter_pct': 0.0},                        'loop': {'interval_seconds': 60, 'jitter_pct': -10.0},  # Negative    """Test jitter can be disabled with 0%."""

                        'risk': {'max_total_at_risk_pct': 15.0},

                        'execution': {'maker_fee_bps': 40, 'taker_fee_bps': 60},                        'risk': {'max_total_at_risk_pct': 15.0},    config_dir = tmp_path / "config"

                        'latency': {'total_seconds': 45.0}

                    }                        'execution': {'maker_fee_bps': 40, 'taker_fee_bps': 60},    config_dir.mkdir()

                return {'triggers': {}} if 'signals' in filename else {'tiers': {}}

                                    'latency': {'total_seconds': 45.0}    

            mock_load.side_effect = mock_yaml_loader

                                }    (config_dir / "app.yaml").write_text(yaml.dump({

            with patch('tools.config_validator.validate_all_configs', return_value=[]):

                loop = TradingLoop(config_dir="config")                return {'triggers': {}} if 'signals' in filename else {'tiers': {}}        "app": {"mode": "DRY_RUN"},

        

        loop._running = True                    "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},

        loop._acquire_lock = Mock(return_value=True)

        loop.run_cycle = Mock()            mock_load.side_effect = mock_yaml_loader        "logging": {"level": "INFO"}

        loop.state_store = Mock()

        loop.state_store.load = Mock(return_value={})                }))

        loop.state_store.save = Mock()

                    with patch('tools.config_validator.validate_all_configs', return_value=[]):    

        def stop_after_one():

            loop._running = False                loop = TradingLoop(config_dir="config")    (config_dir / "policy.yaml").write_text(yaml.dump({

        loop.run_cycle.side_effect = stop_after_one

                        assert loop.loop_jitter_pct == 0.0  # Clamped to min        "loop": {"interval_seconds": 60, "jitter_pct": 0.0},

        sleep_durations = []

        def capture_sleep(duration):        "risk": {"max_total_at_risk_pct": 15.0},

            sleep_durations.append(duration)

        mock_sleep.side_effect = capture_sleep        "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},

        

        loop.run(interval_seconds=60)class TestJitterApplication:        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}

        

        assert len(sleep_durations) == 1    """Test jitter is applied correctly during loop execution."""    }))

        assert sleep_durations[0] == 55.0

            

    @patch('runner.main_loop.time.sleep')

    @patch('runner.main_loop.time.monotonic')    @patch('runner.main_loop.time.sleep')    (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))

    def test_jitter_telemetry_saved_to_state(self, mock_monotonic, mock_sleep):

        """Test jitter stats are persisted to StateStore."""    @patch('runner.main_loop.time.monotonic')    (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))

        mock_monotonic.side_effect = [0.0, 5.0]

            def test_jitter_applied_in_valid_range(self, mock_monotonic, mock_sleep):    

        loop = TradingLoop(config_dir="config")

        loop._running = True        """Test jitter adds 0 to jitter_pct% to sleep duration."""    loop = TradingLoop(config_dir=str(config_dir))

        loop._acquire_lock = Mock(return_value=True)

        loop.run_cycle = Mock()        # Simulate cycle taking 5s    assert loop.loop_jitter_pct == 0.0

        

        saved_states = []        mock_monotonic.side_effect = [0.0, 5.0]

        loop.state_store = Mock()

        loop.state_store.load = Mock(return_value={})        

        def capture_save(state):

            saved_states.append(state.copy())        loop = TradingLoop(config_dir="config")def test_jitter_clamped_max(tmp_path):

        loop.state_store.save = Mock(side_effect=capture_save)

                loop._running = True    """Test jitter clamped to maximum 20%."""

        def stop_after_one():

            loop._running = False            config_dir = tmp_path / "config"

        loop.run_cycle.side_effect = stop_after_one

                # Mock dependencies    config_dir.mkdir()

        mock_sleep.return_value = None

                loop._acquire_lock = Mock(return_value=True)    

        loop.run(interval_seconds=60)

                loop.run_cycle = Mock()    (config_dir / "app.yaml").write_text(yaml.dump({

        assert len(saved_states) >= 1

        final_state = saved_states[-1]        loop.state_store = Mock()        "app": {"mode": "DRY_RUN"},

        assert 'jitter_stats' in final_state

                loop.state_store.load = Mock(return_value={})        "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},

        jitter_stats = final_state['jitter_stats']

        assert 'last_jitter_pct' in jitter_stats        loop.state_store.save = Mock()        "logging": {"level": "INFO"}

        assert 'last_sleep_seconds' in jitter_stats

        assert 'last_cycle_seconds' in jitter_stats            }))

        assert 'last_total_interval' in jitter_stats

        assert 0.0 <= jitter_stats['last_jitter_pct'] <= 10.0        def stop_after_one():    

        assert jitter_stats['last_cycle_seconds'] == 5.0

        assert 55.0 <= jitter_stats['last_sleep_seconds'] <= 61.0            loop._running = False    (config_dir / "policy.yaml").write_text(yaml.dump({

    

    @patch('runner.main_loop.time.sleep')        loop.run_cycle.side_effect = stop_after_one        "loop": {"interval_seconds": 60, "jitter_pct": 50.0},  # Try to set 50%

    @patch('runner.main_loop.time.monotonic')

    def test_jitter_minimum_sleep_enforced(self, mock_monotonic, mock_sleep):                "risk": {"max_total_at_risk_pct": 15.0},

        """Test minimum 1s sleep is enforced even with long cycles."""

        mock_monotonic.side_effect = [0.0, 58.0]        sleep_durations = []        "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},

        

        loop = TradingLoop(config_dir="config")        def capture_sleep(duration):        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}

        loop._running = True

        loop._acquire_lock = Mock(return_value=True)            sleep_durations.append(duration)    }))

        loop.run_cycle = Mock()

        loop.state_store = Mock()        mock_sleep.side_effect = capture_sleep    

        loop.state_store.load = Mock(return_value={})

        loop.state_store.save = Mock()            (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))

        

        def stop_after_one():        loop.run(interval_seconds=60)    (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))

            loop._running = False

        loop.run_cycle.side_effect = stop_after_one            

        

        sleep_durations = []        assert len(sleep_durations) == 1    loop = TradingLoop(config_dir=str(config_dir))

        def capture_sleep(duration):

            sleep_durations.append(duration)        sleep_duration = sleep_durations[0]    assert loop.loop_jitter_pct == 20.0  # Clamped to 20%

        mock_sleep.side_effect = capture_sleep

                

        loop.run(interval_seconds=60)

                # With 10% jitter and 60s interval:

        assert len(sleep_durations) == 1

        assert sleep_durations[0] >= 1.0        # Base sleep = 60 - 5 = 55sdef test_jitter_clamped_negative(tmp_path):



        # Jitter adds 0 to 6s (10% of 60)    """Test jitter clamped to minimum 0%."""

if __name__ == "__main__":

    pytest.main([__file__, "-v"])        # Total should be [55, 61]    config_dir = tmp_path / "config"


        assert 55.0 <= sleep_duration <= 61.0    config_dir.mkdir()

        

    @patch('runner.main_loop.time.sleep')    (config_dir / "app.yaml").write_text(yaml.dump({

    @patch('runner.main_loop.time.monotonic')        "app": {"mode": "DRY_RUN"},

    def test_zero_jitter_is_deterministic(self, mock_monotonic, mock_sleep):        "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},

        """Test 0% jitter results in no randomization."""        "logging": {"level": "INFO"}

        mock_monotonic.side_effect = [0.0, 5.0]    }))

            

        with patch.object(TradingLoop, '_load_yaml') as mock_load:    (config_dir / "policy.yaml").write_text(yaml.dump({

            def mock_yaml_loader(filename):        "loop": {"interval_seconds": 60, "jitter_pct": -5.0},  # Negative value

                if 'policy.yaml' in filename:        "risk": {"max_total_at_risk_pct": 15.0},

                    return {        "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},

                        'loop': {'interval_seconds': 60, 'jitter_pct': 0.0},        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}

                        'risk': {'max_total_at_risk_pct': 15.0},    }))

                        'execution': {'maker_fee_bps': 40, 'taker_fee_bps': 60},    

                        'latency': {'total_seconds': 45.0}    (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))

                    }    (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))

                return {'triggers': {}} if 'signals' in filename else {'tiers': {}}    

                loop = TradingLoop(config_dir=str(config_dir))

            mock_load.side_effect = mock_yaml_loader    assert loop.loop_jitter_pct == 0.0  # Clamped to 0%

            

            with patch('tools.config_validator.validate_all_configs', return_value=[]):

                loop = TradingLoop(config_dir="config")@patch('runner.main_loop.time.sleep')

        @patch('runner.main_loop.time.monotonic')

        loop._running = Truedef test_jitter_applied_in_range(mock_monotonic, mock_sleep, temp_config_dir):

        loop._acquire_lock = Mock(return_value=True)    """Test that jitter is applied within 0 to jitter_pct% range."""

        loop.run_cycle = Mock()    # Mock time progression

        loop.state_store = Mock()    mock_monotonic.side_effect = [0.0, 5.0, 5.0, 10.0, 10.0]  # Cycle takes 5s each

        loop.state_store.load = Mock(return_value={})    

        loop.state_store.save = Mock()    loop = TradingLoop(config_dir=temp_config_dir)

            loop._running = True

        def stop_after_one():    

            loop._running = False    # Mock all the heavy lifting

        loop.run_cycle.side_effect = stop_after_one    loop._acquire_lock = Mock(return_value=True)

            loop.run_cycle = Mock()

        sleep_durations = []    loop.state_store = Mock()

        def capture_sleep(duration):    loop.state_store.load = Mock(return_value={})

            sleep_durations.append(duration)    loop.state_store.save = Mock()

        mock_sleep.side_effect = capture_sleep    

            # Run one cycle then stop

        loop.run(interval_seconds=60)    def stop_after_first():

                loop._running = False

        assert len(sleep_durations) == 1    loop.run_cycle.side_effect = stop_after_first

        # With 0% jitter, sleep should be exactly base (55s)    

        assert sleep_durations[0] == 55.0    # Capture sleep durations

        sleep_durations = []

    @patch('runner.main_loop.time.sleep')    original_sleep = mock_sleep

    @patch('runner.main_loop.time.monotonic')    def capture_sleep(duration):

    def test_jitter_telemetry_saved_to_state(self, mock_monotonic, mock_sleep):        sleep_durations.append(duration)

        """Test jitter stats are persisted to StateStore."""    mock_sleep.side_effect = capture_sleep

        mock_monotonic.side_effect = [0.0, 5.0]    

            loop.run(interval_seconds=60)

        loop = TradingLoop(config_dir="config")    

        loop._running = True    # Verify sleep was called

            assert len(sleep_durations) == 1

        loop._acquire_lock = Mock(return_value=True)    

        loop.run_cycle = Mock()    # With 10% jitter, base sleep should be 60 - 5 = 55s

            # Jitter adds 0 to 6s (10% of 60s)

        saved_states = []    # So total sleep should be in range [55, 61]

        loop.state_store = Mock()    sleep_duration = sleep_durations[0]

        loop.state_store.load = Mock(return_value={})    assert 55.0 <= sleep_duration <= 61.0, f"Sleep {sleep_duration}s outside expected range"

        def capture_save(state):

            saved_states.append(state.copy())

        loop.state_store.save = Mock(side_effect=capture_save)@patch('runner.main_loop.time.sleep')

        @patch('runner.main_loop.time.monotonic')

        def stop_after_one():def test_jitter_disabled_no_randomization(mock_monotonic, mock_sleep, tmp_path):

            loop._running = False    """Test that 0% jitter results in deterministic sleep."""

        loop.run_cycle.side_effect = stop_after_one    config_dir = tmp_path / "config"

            config_dir.mkdir()

        mock_sleep.return_value = None    

            (config_dir / "app.yaml").write_text(yaml.dump({

        loop.run(interval_seconds=60)        "app": {"mode": "DRY_RUN"},

                "exchange": {"api_key": "test", "api_secret": "test", "read_only": True},

        # Verify jitter_stats were saved        "logging": {"level": "INFO"}

        assert len(saved_states) >= 1    }))

        final_state = saved_states[-1]    

        assert 'jitter_stats' in final_state    (config_dir / "policy.yaml").write_text(yaml.dump({

                "loop": {"interval_seconds": 60, "jitter_pct": 0.0},  # No jitter

        jitter_stats = final_state['jitter_stats']        "risk": {"max_total_at_risk_pct": 15.0},

        assert 'last_jitter_pct' in jitter_stats        "execution": {"maker_fee_bps": 40, "taker_fee_bps": 60},

        assert 'last_sleep_seconds' in jitter_stats        "latency": {"api_thresholds_ms": {}, "stage_budgets": {}, "total_seconds": 45.0}

        assert 'last_cycle_seconds' in jitter_stats    }))

        assert 'last_total_interval' in jitter_stats    

            (config_dir / "signals.yaml").write_text(yaml.dump({"triggers": {}}))

        # Validate values    (config_dir / "universe.yaml").write_text(yaml.dump({"tiers": {}}))

        assert 0.0 <= jitter_stats['last_jitter_pct'] <= 10.0    

        assert jitter_stats['last_cycle_seconds'] == 5.0    # Mock time: cycle takes exactly 5s

        assert 55.0 <= jitter_stats['last_sleep_seconds'] <= 61.0    mock_monotonic.side_effect = [0.0, 5.0]

        

    @patch('runner.main_loop.time.sleep')    loop = TradingLoop(config_dir=str(config_dir))

    @patch('runner.main_loop.time.monotonic')    loop._running = True

    def test_jitter_minimum_sleep_enforced(self, mock_monotonic, mock_sleep):    

        """Test minimum 1s sleep is enforced even with long cycles."""    loop._acquire_lock = Mock(return_value=True)

        # Simulate very long cycle (58s of 60s)    loop.run_cycle = Mock()

        mock_monotonic.side_effect = [0.0, 58.0]    loop.state_store = Mock()

            loop.state_store.load = Mock(return_value={})

        loop = TradingLoop(config_dir="config")    loop.state_store.save = Mock()

        loop._running = True    

            def stop_after_first():

        loop._acquire_lock = Mock(return_value=True)        loop._running = False

        loop.run_cycle = Mock()    loop.run_cycle.side_effect = stop_after_first

        loop.state_store = Mock()    

        loop.state_store.load = Mock(return_value={})    sleep_durations = []

        loop.state_store.save = Mock()    def capture_sleep(duration):

                sleep_durations.append(duration)

        def stop_after_one():    mock_sleep.side_effect = capture_sleep

            loop._running = False    

        loop.run_cycle.side_effect = stop_after_one    loop.run(interval_seconds=60)

            

        sleep_durations = []    # With 0% jitter and 5s cycle, sleep should be exactly 55s

        def capture_sleep(duration):    assert len(sleep_durations) == 1

            sleep_durations.append(duration)    assert sleep_durations[0] == 55.0

        mock_sleep.side_effect = capture_sleep

        

        loop.run(interval_seconds=60)@patch('runner.main_loop.time.sleep')

        @patch('runner.main_loop.time.monotonic')

        assert len(sleep_durations) == 1def test_jitter_telemetry_saved(mock_monotonic, mock_sleep, temp_config_dir):

        # Even with base 2s + jitter, minimum 1s enforced    """Test that jitter stats are saved to StateStore."""

        assert sleep_durations[0] >= 1.0    mock_monotonic.side_effect = [0.0, 5.0]  # 5s cycle

    

    loop = TradingLoop(config_dir=temp_config_dir)

if __name__ == "__main__":    loop._running = True

    pytest.main([__file__, "-v"])    

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
