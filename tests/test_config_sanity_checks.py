"""
Tests for config_validator.py sanity checks.

Tests the enhanced validation rules added in Task 8:
- TradeLimits daily/hourly consistency
- Cooldown completeness
- Profile validation
- Exit configuration
- Cluster/theme alignment
- Exposure hierarchy coherence
"""

import pytest
from pathlib import Path
import tempfile
import yaml
from tools.config_validator import validate_sanity_checks


def create_test_config(config_dir: Path, policy: dict, universe: dict = None):
    """Helper to create temporary config files for testing."""
    config_dir.mkdir(parents=True, exist_ok=True)
    
    policy_path = config_dir / "policy.yaml"
    with open(policy_path, 'w') as f:
        yaml.dump(policy, f)
    
    if universe:
        universe_path = config_dir / "universe.yaml"
        with open(universe_path, 'w') as f:
            yaml.dump(universe, f)
    
    # Create minimal signals.yaml to satisfy schema validation
    signals_path = config_dir / "signals.yaml"
    with open(signals_path, 'w') as f:
        yaml.dump({"triggers": {}}, f)


def test_trade_limits_daily_hourly_consistency():
    """Test that daily limit must accommodate hourly rate × 24."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Invalid: daily < hourly × 24
        policy = {
            "profile": "test",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.02},
                    "max_position_pct": {"T1": 0.04},
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "max_trades_per_hour": 5,
                "max_trades_per_day": 100,  # Should be >= 120 (5 × 24)
                "max_total_at_risk_pct": 50.0,
                "stop_loss_pct": 10.0,
                "take_profit_pct": 15.0
            },
            "exits": {"enabled": True},
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        create_test_config(config_dir, policy)
        errors = validate_sanity_checks(config_dir)
        
        # Should catch daily < hourly × 24
        assert any("max_trades_per_day" in err and "120" in err for err in errors)


def test_cooldown_completeness():
    """Test that enabled cooldowns require all outcome configurations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Invalid: cooldown enabled but missing configurations
        policy = {
            "profile": "test",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.02},
                    "max_position_pct": {"T1": 0.04},
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "per_symbol_cooldown_enabled": True,
                "per_symbol_cooldown_win_minutes": 30,
                # Missing: per_symbol_cooldown_loss_minutes, per_symbol_cooldown_after_stop
                "max_total_at_risk_pct": 50.0,
                "stop_loss_pct": 10.0,
                "take_profit_pct": 15.0
            },
            "exits": {"enabled": True},
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        create_test_config(config_dir, policy)
        errors = validate_sanity_checks(config_dir)
        
        # Should catch missing cooldowns
        assert any("per_symbol_cooldown_enabled=true" in err and "missing outcome cooldowns" in err 
                   for err in errors)


def test_profile_tier_sizing_exceeds_max():
    """Test that tier_sizing cannot exceed max_position_pct."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Invalid: tier_sizing > max_position_pct
        policy = {
            "profile": "test",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.05},  # 5%
                    "max_position_pct": {"T1": 0.03},  # 3% - UNSAFE!
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "max_total_at_risk_pct": 50.0,
                "stop_loss_pct": 10.0,
                "take_profit_pct": 15.0
            },
            "exits": {"enabled": True},
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        create_test_config(config_dir, policy)
        errors = validate_sanity_checks(config_dir)
        
        # Should catch tier_sizing > max_position_pct
        assert any("tier_sizing.T1" in err and "exceeds tier maximum" in err for err in errors)


def test_active_profile_not_defined():
    """Test that active profile must exist in profiles section."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Invalid: profile 'nonexistent' not defined
        policy = {
            "profile": "nonexistent",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.02},
                    "max_position_pct": {"T1": 0.04},
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "max_total_at_risk_pct": 50.0,
                "stop_loss_pct": 10.0,
                "take_profit_pct": 15.0
            },
            "exits": {"enabled": True},
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        create_test_config(config_dir, policy)
        errors = validate_sanity_checks(config_dir)
        
        # Should catch missing profile
        assert any("'nonexistent'" in err and "not found" in err for err in errors)


def test_stop_loss_required():
    """Test that stop_loss_pct must be configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Invalid: no stop_loss_pct
        policy = {
            "profile": "test",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.02},
                    "max_position_pct": {"T1": 0.04},
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "max_total_at_risk_pct": 50.0,
                # Missing: stop_loss_pct
                "take_profit_pct": 15.0
            },
            "exits": {"enabled": True},
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        create_test_config(config_dir, policy)
        errors = validate_sanity_checks(config_dir)
        
        # Should catch missing stop loss
        assert any("stop_loss_pct" in err and "unlimited downside" in err for err in errors)


def test_take_profit_less_than_stop_loss():
    """Test that take_profit should exceed stop_loss (risk/reward ratio)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Invalid: take_profit <= stop_loss
        policy = {
            "profile": "test",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.02},
                    "max_position_pct": {"T1": 0.04},
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "max_total_at_risk_pct": 50.0,
                "stop_loss_pct": 10.0,
                "take_profit_pct": 8.0  # Less than stop loss!
            },
            "exits": {"enabled": True},
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        create_test_config(config_dir, policy)
        errors = validate_sanity_checks(config_dir)
        
        # Should catch bad risk/reward ratio
        assert any("take_profit_pct" in err and "Risk/reward ratio" in err for err in errors)


def test_trailing_stop_enabled_without_pct():
    """Test that trailing stop requires trailing_stop_pct > 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Invalid: trailing enabled but pct=0
        policy = {
            "profile": "test",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.02},
                    "max_position_pct": {"T1": 0.04},
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "max_total_at_risk_pct": 50.0,
                "stop_loss_pct": 10.0,
                "take_profit_pct": 15.0
            },
            "exits": {
                "enabled": True,
                "use_trailing_stop": True,
                "trailing_stop_pct": 0  # Invalid!
            },
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        create_test_config(config_dir, policy)
        errors = validate_sanity_checks(config_dir)
        
        # Should catch missing trailing_stop_pct
        assert any("trailing_stop_pct=0" in err for err in errors)


def test_cluster_theme_name_mismatch():
    """Test detection of cluster/theme naming inconsistencies."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Mismatch: universe has LAYER2, policy has L2
        policy = {
            "profile": "test",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.02},
                    "max_position_pct": {"T1": 0.04},
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "max_total_at_risk_pct": 50.0,
                "max_per_theme_pct": {"L2": 10.0},  # Abbreviated name
                "stop_loss_pct": 10.0,
                "take_profit_pct": 15.0
            },
            "exits": {"enabled": True},
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        universe = {
            "clusters": {
                "definitions": {
                    "LAYER2": ["OP-USD", "ARB-USD"]  # Full name
                }
            }
        }
        
        create_test_config(config_dir, policy, universe)
        errors = validate_sanity_checks(config_dir)
        
        # Should catch naming mismatch
        assert any("LAYER2" in err and "L2" in err and "different naming" in err for err in errors)


def test_asset_cap_exceeds_theme_cap():
    """Test that per-asset cap cannot exceed its theme cap."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Invalid: asset cap > theme cap
        policy = {
            "profile": "test",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.02},
                    "max_position_pct": {"T1": 0.04},
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "max_total_at_risk_pct": 50.0,
                "max_per_asset_pct": {"BTC-USD": 15.0},  # 15%
                "max_per_theme_pct": {"LAYER1": 10.0},  # 10% - UNSAFE!
                "stop_loss_pct": 10.0,
                "take_profit_pct": 15.0
            },
            "exits": {"enabled": True},
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        universe = {
            "clusters": {
                "definitions": {
                    "LAYER1": ["BTC-USD", "ETH-USD"]
                }
            }
        }
        
        create_test_config(config_dir, policy, universe)
        errors = validate_sanity_checks(config_dir)
        
        # Should catch asset > theme hierarchy violation
        assert any("BTC-USD" in err and "exceeds its theme cap" in err for err in errors), \
            f"Expected asset cap > theme cap error. Got: {errors}"


def test_valid_config_passes():
    """Test that a properly configured policy passes all checks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        
        # Valid configuration
        policy = {
            "profile": "test",
            "profiles": {
                "test": {
                    "min_conviction": 0.3,
                    "tier_sizing": {"T1": 0.02, "T2": 0.01},
                    "max_position_pct": {"T1": 0.04, "T2": 0.02},
                    "min_trade_notional": 10.0
                }
            },
            "risk": {
                "max_trades_per_hour": 5,
                "max_trades_per_day": 120,  # Valid: 5 × 24
                "per_symbol_cooldown_enabled": True,
                "per_symbol_cooldown_win_minutes": 30,
                "per_symbol_cooldown_loss_minutes": 60,
                "per_symbol_cooldown_after_stop": 120,
                "max_total_at_risk_pct": 50.0,
                "max_per_asset_pct": {"BTC-USD": 8.0},
                "max_per_theme_pct": {"LAYER1": 15.0},
                "stop_loss_pct": 10.0,
                "take_profit_pct": 15.0
            },
            "exits": {
                "enabled": True,
                "use_trailing_stop": True,
                "trailing_stop_pct": 5.0
            },
            "position_sizing": {"method": "risk_parity"},
            "liquidity": {}
        }
        
        universe = {
            "clusters": {
                "definitions": {
                    "LAYER1": ["BTC-USD", "ETH-USD"]
                }
            }
        }
        
        create_test_config(config_dir, policy, universe)
        errors = validate_sanity_checks(config_dir)
        
        # Should pass all checks (or only have non-critical warnings)
        critical_errors = [e for e in errors if "UNSAFE" in e or "INVALID" in e or "INCOMPLETE" in e]
        assert len(critical_errors) == 0, f"Unexpected errors: {critical_errors}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
