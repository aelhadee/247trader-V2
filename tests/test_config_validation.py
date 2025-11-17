"""
Tests for configuration validation.

Validates that config_validator correctly identifies invalid configs
and accepts valid configs.
"""
import pytest
import tempfile
from pathlib import Path
import yaml

from tools.config_validator import (
    validate_policy,
    validate_all_configs,
    PolicySchema,
    UniverseSchema,
    SignalsSchema,
)


class TestPolicyValidation:
    """Test policy.yaml validation"""
    
    def test_valid_policy_config(self):
        """Valid policy config passes validation"""
        config = {
            "risk": {
                "max_total_at_risk_pct": 95.0,
                "max_position_size_pct": 7.0,
                "max_per_asset_pct": 7.0,
                "min_position_size_pct": 0.5,
                "max_open_positions": 12,
                "max_per_theme_pct": {"L2": 10.0, "MEME": 5.0},
                "daily_stop_pnl_pct": -3.0,
                "weekly_stop_pnl_pct": -7.0,
                "max_drawdown_pct": 10.0,
                "max_trades_per_day": 40,
                "max_trades_per_hour": 8,
                "max_new_trades_per_hour": 8,
                "min_seconds_between_trades": 120,
                "per_symbol_trade_spacing_seconds": 600,
                "cooldown_after_loss_trades": 3,
                "cooldown_minutes": 60,
                "per_symbol_cooldown_enabled": True,
                "per_symbol_cooldown_minutes": 30,
                "per_symbol_cooldown_after_stop": 60,
                "min_trade_notional_usd": 15.0,
                "dust_threshold_usd": 10.0,
                "allow_adds_when_over_cap": True,
                "count_open_orders_in_cap": True,
                "allow_pyramiding": True,
                "pyramid_cooldown_seconds": 300,
                "max_adds_per_asset_per_day": 2,
                "stop_loss_pct": 8.0,
                "take_profit_pct": 15.0,
            },
            "position_sizing": {
                "method": "risk_parity",
                "risk_per_trade_pct": 1.0,
                "fixed_size_usd": 100.0,
                "min_order_usd": 10.0,
                "max_order_usd": 10000.0,
                "allow_pyramiding": False,
                "max_pyramid_positions": 2,
            },
            "liquidity": {
                "min_24h_volume_usd": 8000000.0,
                "max_spread_bps": 80.0,
                "min_depth_20bps_usd": 10000.0,
            },
            "triggers": {
                "min_score": 0.2,
            },
            "strategy": {
                "base_position_pct": {"tier1": 2.0, "tier2": 1.0},
                "max_open_positions": 8,
                "min_conviction_to_propose": 0.45,
            },
            "microstructure": {
                "max_expected_slippage_bps": 50.0,
                "max_quote_age_seconds": 30,
            },
            "execution": {
                "default_order_type": "limit_post_only",
                "maker_fee_bps": 40.0,
                "taker_fee_bps": 60.0,
                "maker_first": True,
                "maker_max_reprices": 1,
                "maker_max_ttl_sec": 15,
                "maker_first_min_ttl_sec": 12,
                "maker_retry_min_ttl_sec": 8,
                "maker_reprice_decay": 0.7,
                "taker_fallback": True,
                "prefer_ioc": True,
                "taker_max_slippage_bps": {"default": 60},
                "purge_maker_ttl_sec": 25,
                "preferred_quote_currencies": ["USDC", "USD"],
                "auto_convert_preferred_quote": True,
                "clamp_small_trades": True,
                "small_order_market_threshold_usd": 6.0,
                "allow_min_bump_in_risk": True,
                "failed_order_cooldown_seconds": 0,
                "cancel_after_seconds": 60,
                "post_only_ttl_seconds": 15,
                "partial_fill_min_pct": 0.25,
                "max_order_age_seconds": 1800,
                "post_trade_reconcile_wait_seconds": 0.5,
                "min_notional_usd": 15.0,
                "max_slippage_bps": 40,
                "hard_max_spread_bps": 80,
                "slippage_budget_t1_bps": 60,
                "slippage_budget_t2_bps": 95,
                "slippage_budget_t3_bps": 120,
                "cancel_retry_backoff_ms": [250, 500, 1000],
                "promote_to_taker_if_budget_allows": False,
                "taker_promotion_requirements": {
                    "min_confidence": 0.7,
                    "max_slippage_bps": 50,
                },
            },
            "data": {
                "max_age_s": 60,
                "max_quote_staleness_seconds": 30,
            },
            "circuit_breaker": {
                "api_error_threshold": 5,
                "api_error_window_minutes": 5,
                "rate_limit_threshold": 3,
                "rate_limit_window_minutes": 10,
            },
        }
        
        # Should not raise
        schema = PolicySchema(**config)
        assert schema.risk.max_total_at_risk_pct == 95.0
    
    def test_invalid_risk_percentages(self):
        """Invalid risk percentages fail validation"""
        config = {
            "risk": {
                "max_total_at_risk_pct": 150.0,  # > 100 invalid
                "max_position_size_pct": 7.0,
                "max_per_asset_pct": 7.0,
                "min_position_size_pct": 0.5,
                "max_per_theme_pct": {},
                "daily_stop_pnl_pct": -3.0,
                "weekly_stop_pnl_pct": -7.0,
                "max_drawdown_pct": 10.0,
                "max_trades_per_day": 40,
                "max_trades_per_hour": 8,
                "max_new_trades_per_hour": 8,
                "max_open_positions": 12,
                "min_seconds_between_trades": 120,
                "per_symbol_trade_spacing_seconds": 600,
                "cooldown_after_loss_trades": 3,
                "cooldown_minutes": 60,
                "per_symbol_cooldown_enabled": True,
                "per_symbol_cooldown_minutes": 30,
                "per_symbol_cooldown_after_stop": 60,
                "min_trade_notional_usd": 15.0,
                "dust_threshold_usd": 10.0,
                "allow_adds_when_over_cap": True,
                "count_open_orders_in_cap": True,
                "allow_pyramiding": True,
                "pyramid_cooldown_seconds": 300,
                "max_adds_per_asset_per_day": 2,
                "stop_loss_pct": 8.0,
                "take_profit_pct": 15.0,
            },
        }
        
        # Partial config, will fail
        with pytest.raises(Exception):
            PolicySchema(**config)
    
    def test_invalid_order_type(self):
        """Invalid order type fails validation"""
        with pytest.raises(Exception):
            PolicySchema(execution={"default_order_type": "invalid_type"})


class TestUniverseValidation:
    """Test universe.yaml validation"""
    
    def test_valid_universe_config(self):
        """Valid universe config passes validation"""
        config = {
            "clusters": {
                "definitions": {
                    "DEFI": ["UNI-USD", "LINK-USD"],
                    "LAYER1": ["BTC-USD", "ETH-USD"],
                },
                "enabled": True,
            },
            "exclusions": {
                "never_trade": ["USDT-USD"],
                "red_flags": ["recent_exploit"],
                "temporary_ban_hours": 168,
            },
            "liquidity": {
                "max_spread_bps": 80.0,
                "min_24h_volume_usd": 8000000.0,
                "min_depth_20bps_usd": 10000.0,
            },
            "regime_modifiers": {
                "bear": {"tier_1_multiplier": 1.0},
                "bull": {"tier_1_multiplier": 1.2},
                "chop": {"tier_1_multiplier": 1.0},
                "crash": {"tier_1_multiplier": 0.0},
            },
            "tiers": {
                "tier_1_core": {
                    "constraints": {
                        "max_allocation_pct": 40.0,
                        "max_spread_bps": 30.0,
                        "min_24h_volume_usd": 50000000.0,
                    },
                    "refresh": "weekly",
                    "symbols": ["BTC-USD", "ETH-USD"],
                },
            },
            "universe": {
                "method": "dynamic_discovery",
                "max_universe_size": 30,
                "refresh_interval_hours": 24,
                "dynamic_config": {
                    "max_spread_bps": 80.0,
                    "min_price_usd": 0.01,
                    "tier1_max_symbols": 10,
                    "tier1_min_volume_usd": 80000000.0,
                    "tier2_max_symbols": 20,
                    "tier2_min_volume_usd": 8000000.0,
                    "tier3_max_symbols": 0,
                    "tier3_min_volume_usd": 5000000.0,
                },
            },
        }
        
        # Should not raise
        schema = UniverseSchema(**config)
        assert schema.clusters.enabled
    
    def test_empty_cluster_fails(self):
        """Empty cluster definitions fail validation"""
        config = {
            "clusters": {
                "definitions": {
                    "EMPTY_CLUSTER": [],  # No symbols
                },
                "enabled": True,
            },
        }
        
        with pytest.raises(Exception):
            UniverseSchema(**config)
    
    def test_invalid_refresh_frequency(self):
        """Invalid refresh frequency fails validation"""
        with pytest.raises(Exception):
            UniverseSchema(
                tiers={
                    "tier1": {
                        "constraints": {},
                        "refresh": "minutely",  # Invalid
                    }
                }
            )


class TestSignalsValidation:
    """Test signals.yaml validation"""
    
    def test_valid_signals_config(self):
        """Valid signals config passes validation"""
        config = {
            "triggers": {
                "volume_spike_min_ratio": 1.5,
                "volume_lookback_periods": 24,
                "breakout_lookback_bars": 24,
                "breakout_threshold_pct": 2.0,
                "min_trigger_score": 0.2,
                "min_trigger_confidence": 0.5,
                "max_triggers_per_cycle": 10,
                "regime_multipliers": {
                    "bull": 1.2,
                    "chop": 1.0,
                    "bear": 0.8,
                    "crash": 0.0,
                },
            }
        }
        
        # Should not raise
        schema = SignalsSchema(**config)
        assert schema.triggers.volume_spike_min_ratio == 1.5
    
    def test_invalid_trigger_score_range(self):
        """Trigger score outside 0-1 range fails"""
        config = {
            "triggers": {
                "volume_spike_min_ratio": 1.5,
                "volume_lookback_periods": 24,
                "breakout_lookback_bars": 24,
                "breakout_threshold_pct": 2.0,
                "min_trigger_score": 1.5,  # > 1 invalid
                "min_trigger_confidence": 0.5,
                "max_triggers_per_cycle": 10,
                "regime_multipliers": {
                    "bull": 1.2,
                    "chop": 1.0,
                    "bear": 0.8,
                    "crash": 0.0,
                },
            }
        }
        
        with pytest.raises(Exception):
            SignalsSchema(**config)


class TestFileValidation:
    """Test file-based validation"""
    
    def test_missing_file_returns_error(self):
        """Missing config file returns error"""
        with tempfile.TemporaryDirectory() as tmpdir:
            errors = validate_policy(Path(tmpdir))
            assert len(errors) == 1
            assert "not found" in errors[0].lower()
    
    def test_malformed_yaml_returns_error(self):
        """Malformed YAML returns error"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "policy.yaml"
            config_path.write_text("invalid: yaml: syntax [")
            
            errors = validate_policy(Path(tmpdir))
            assert len(errors) == 1
            assert "yaml" in errors[0].lower()
    
    def test_validate_all_configs_aggregates_errors(self):
        """validate_all_configs() aggregates all validation errors"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create minimal valid configs
            policy = {
                "risk": {
                    "max_total_at_risk_pct": 95.0,
                    "max_position_size_pct": 7.0,
                    "max_per_asset_pct": 7.0,
                    "min_position_size_pct": 0.5,
                    "max_per_theme_pct": {},
                    "daily_stop_pnl_pct": -3.0,
                    "weekly_stop_pnl_pct": -7.0,
                    "max_drawdown_pct": 10.0,
                    "max_trades_per_day": 40,
                    "max_trades_per_hour": 8,
                    "max_new_trades_per_hour": 8,
                    "max_open_positions": 12,
                    "min_seconds_between_trades": 120,
                    "per_symbol_trade_spacing_seconds": 600,
                    "cooldown_after_loss_trades": 3,
                    "cooldown_minutes": 60,
                    "per_symbol_cooldown_enabled": True,
                    "per_symbol_cooldown_minutes": 30,
                    "per_symbol_cooldown_after_stop": 60,
                    "min_trade_notional_usd": 15.0,
                    "dust_threshold_usd": 10.0,
                    "allow_adds_when_over_cap": True,
                    "count_open_orders_in_cap": True,
                    "allow_pyramiding": True,
                    "pyramid_cooldown_seconds": 300,
                    "max_adds_per_asset_per_day": 2,
                    "stop_loss_pct": 8.0,
                    "take_profit_pct": 15.0,
                },
                "position_sizing": {
                    "method": "risk_parity",
                    "risk_per_trade_pct": 1.0,
                    "fixed_size_usd": 100.0,
                    "min_order_usd": 10.0,
                    "max_order_usd": 10000.0,
                    "allow_pyramiding": False,
                    "max_pyramid_positions": 2,
                },
                "liquidity": {
                    "min_24h_volume_usd": 8000000.0,
                    "max_spread_bps": 80.0,
                    "min_depth_20bps_usd": 10000.0,
                },
                "triggers": {"min_score": 0.2},
                "strategy": {
                    "base_position_pct": {"tier1": 2.0},
                    "max_open_positions": 8,
                    "min_conviction_to_propose": 0.45,
                },
                "microstructure": {
                    "max_expected_slippage_bps": 50.0,
                    "max_quote_age_seconds": 30,
                },
                "execution": {
                    "default_order_type": "limit_post_only",
                    "maker_fee_bps": 40.0,
                    "taker_fee_bps": 60.0,
                    "maker_first": True,
                    "maker_max_reprices": 1,
                    "maker_max_ttl_sec": 15,
                    "maker_first_min_ttl_sec": 12,
                    "maker_retry_min_ttl_sec": 8,
                    "maker_reprice_decay": 0.7,
                    "taker_fallback": True,
                    "prefer_ioc": True,
                    "taker_max_slippage_bps": {"default": 60},
                    "purge_maker_ttl_sec": 25,
                    "preferred_quote_currencies": ["USDC"],
                    "auto_convert_preferred_quote": True,
                    "clamp_small_trades": True,
                    "small_order_market_threshold_usd": 6.0,
                    "allow_min_bump_in_risk": True,
                    "failed_order_cooldown_seconds": 0,
                    "cancel_after_seconds": 60,
                    "post_only_ttl_seconds": 15,
                    "partial_fill_min_pct": 0.25,
                    "max_order_age_seconds": 1800,
                    "post_trade_reconcile_wait_seconds": 0.5,
                    "min_notional_usd": 15.0,
                    "max_slippage_bps": 40,
                    "hard_max_spread_bps": 80,
                    "slippage_budget_t1_bps": 60,
                    "slippage_budget_t2_bps": 95,
                    "slippage_budget_t3_bps": 120,
                    "cancel_retry_backoff_ms": [250, 500, 1000],
                    "promote_to_taker_if_budget_allows": False,
                    "taker_promotion_requirements": {
                        "min_confidence": 0.7,
                        "max_slippage_bps": 50,
                    },
                },
                "data": {
                    "max_age_s": 60,
                    "max_quote_staleness_seconds": 30,
                },
                "circuit_breaker": {
                    "api_error_threshold": 5,
                    "api_error_window_minutes": 5,
                    "rate_limit_threshold": 3,
                    "rate_limit_window_minutes": 10,
                },
            }
            
            signals = {
                "triggers": {
                    "volume_spike_min_ratio": 1.5,
                    "volume_lookback_periods": 24,
                    "breakout_lookback_bars": 24,
                    "breakout_threshold_pct": 2.0,
                    "min_trigger_score": 0.2,
                    "min_trigger_confidence": 0.5,
                    "max_triggers_per_cycle": 10,
                    "regime_multipliers": {
                        "bull": 1.2,
                        "chop": 1.0,
                        "bear": 0.8,
                        "crash": 0.0,
                    },
                }
            }
            
            # Write configs
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "signals.yaml").write_text(yaml.dump(signals))
            # universe.yaml missing intentionally
            
            errors = validate_all_configs(tmpdir)
            assert len(errors) == 1  # Only universe.yaml missing
            assert "universe.yaml" in errors[0]


class TestSanityChecks:
    """Test configuration sanity checks for logical consistency"""
    
    def _create_minimal_valid_policy(self) -> dict:
        """Helper to create minimal valid policy config"""
        return {
            "risk": {
                "max_total_at_risk_pct": 25.0,
                "max_position_size_pct": 3.0,
                "max_per_asset_pct": 5.0,
                "min_position_size_pct": 0.5,
                "max_per_theme_pct": {"L2": 10.0, "DEFI": 10.0},
                "daily_stop_pnl_pct": -3.0,
                "weekly_stop_pnl_pct": -7.0,
                "max_drawdown_pct": 10.0,
                "max_trades_per_day": 120,  # Was 15 - increased to pass hourly check (5×24=120)
                "max_trades_per_hour": 5,
                "max_new_trades_per_hour": 3,
                "max_open_positions": 5,
                "min_seconds_between_trades": 180,
                "per_symbol_trade_spacing_seconds": 900,
                "cooldown_after_loss_trades": 3,
                "cooldown_minutes": 60,
                "per_symbol_cooldown_enabled": True,
                "per_symbol_cooldown_minutes": 30,
                "per_symbol_cooldown_after_stop": 120,
                "per_symbol_cooldown_win_minutes": 10,
                "per_symbol_cooldown_loss_minutes": 60,
                "min_trade_notional_usd": 10.0,
                "dust_threshold_usd": 10.0,
                "allow_adds_when_over_cap": False,
                "count_open_orders_in_cap": True,
                "allow_pyramiding": False,
                "pyramid_cooldown_seconds": 600,
                "max_adds_per_asset_per_day": 1,
                "stop_loss_pct": 10.0,
                "take_profit_pct": 12.0,
            },
            "position_sizing": {
                "method": "risk_parity",
                "risk_per_trade_pct": 1.0,
                "fixed_size_usd": 100.0,
                "min_order_usd": 10.0,
                "max_order_usd": 10000.0,
                "allow_pyramiding": False,
                "max_pyramid_positions": 0,
            },
            "liquidity": {
                "min_24h_volume_usd": 8000000.0,
                "max_spread_bps": 80.0,
                "min_depth_20bps_usd": 10000.0,
            },
            "triggers": {"min_score": 0.2},
            "strategy": {
                "base_position_pct": {"tier1": 2.0},
                "max_open_positions": 5,
                "min_conviction_to_propose": 0.3,
            },
            "microstructure": {
                "max_expected_slippage_bps": 50.0,
                "max_quote_age_seconds": 5,
            },
            "execution": {
                "default_order_type": "limit_post_only",
                "maker_fee_bps": 40.0,
                "taker_fee_bps": 60.0,
                "maker_first": True,
                "maker_max_reprices": 1,
                "maker_max_ttl_sec": 30,
                "maker_first_min_ttl_sec": 20,
                "maker_retry_min_ttl_sec": 15,
                "maker_reprice_decay": 0.7,
                "taker_fallback": True,
                "prefer_ioc": True,
                "taker_max_slippage_bps": {"default": 60},
                "purge_maker_ttl_sec": 45,
                "preferred_quote_currencies": ["USDC"],
                "auto_convert_preferred_quote": True,
                "clamp_small_trades": True,
                "small_order_market_threshold_usd": 6.0,
                "allow_min_bump_in_risk": True,
                "failed_order_cooldown_seconds": 300,
                "cancel_after_seconds": 60,
                "post_only_ttl_seconds": 15,
                "partial_fill_min_pct": 0.25,
                "max_order_age_seconds": 1800,
                "post_trade_reconcile_wait_seconds": 1.0,
                "min_notional_usd": 10.0,
                "max_slippage_bps": 40,
                "hard_max_spread_bps": 80,
                "slippage_budget_t1_bps": 60,
                "slippage_budget_t2_bps": 95,
                "slippage_budget_t3_bps": 120,
                "cancel_retry_backoff_ms": [250, 500, 1000],
                "promote_to_taker_if_budget_allows": False,
                "taker_promotion_requirements": {
                    "min_confidence": 0.7,
                    "max_slippage_bps": 50,
                },
            },
            "data": {
                "max_age_s": 60,
                "max_quote_staleness_seconds": 30,
            },
            "circuit_breaker": {
                "api_error_threshold": 5,
                "api_error_window_minutes": 5,
                "rate_limit_threshold": 3,
                "rate_limit_window_minutes": 10,
            },
        }
    
    def test_sanity_check_theme_caps_exceed_global_cap(self):
        """Sum of theme caps exceeding global cap is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            # Set theme caps that sum to > global cap
            policy["risk"]["max_total_at_risk_pct"] = 20.0
            policy["risk"]["max_per_theme_pct"] = {
                "L2": 10.0,
                "DEFI": 8.0,
                "MEME": 5.0,  # Sum = 23% > 20%
            }
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("Sum of theme caps" in e for e in errors), \
                f"Expected theme cap error, got: {errors}"
    
    def test_sanity_check_per_asset_exceeds_theme_cap(self):
        """Per-asset cap exceeding theme cap is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["risk"]["max_per_asset_pct"] = 12.0  # > theme caps
            policy["risk"]["max_per_theme_pct"] = {
                "L2": 10.0,
                "DEFI": 10.0,
            }
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("max_per_asset_pct" in e and "theme" in e for e in errors), \
                f"Expected per-asset vs theme error, got: {errors}"
    
    def test_sanity_check_stop_loss_exceeds_take_profit(self):
        """Stop loss >= take profit is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["risk"]["stop_loss_pct"] = 15.0
            policy["risk"]["take_profit_pct"] = 10.0  # Less than stop loss
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("stop_loss_pct" in e and "take_profit_pct" in e for e in errors), \
                f"Expected stop/target error, got: {errors}"
    
    def test_sanity_check_max_position_exceeds_global_cap(self):
        """Max position size exceeding global cap is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["risk"]["max_total_at_risk_pct"] = 20.0
            policy["risk"]["max_position_size_pct"] = 25.0  # > global cap
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("max_position_size_pct" in e and "max_total_at_risk_pct" in e for e in errors), \
                f"Expected position vs global cap error, got: {errors}"
    
    def test_sanity_check_theoretical_max_exceeds_cap(self):
        """Theoretical max (positions × size) exceeding cap is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["risk"]["max_total_at_risk_pct"] = 20.0
            policy["risk"]["max_open_positions"] = 10
            policy["risk"]["max_position_size_pct"] = 5.0  # 10 × 5% = 50% > 20%
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("max_open_positions" in e and "exceeds" in e for e in errors), \
                f"Expected theoretical max error, got: {errors}"
    
    def test_sanity_check_pyramiding_enabled_but_no_adds(self):
        """Pyramiding enabled but max_adds=0 is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["risk"]["allow_pyramiding"] = True
            policy["risk"]["max_adds_per_asset_per_day"] = 0
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("allow_pyramiding" in e and "max_adds" in e for e in errors), \
                f"Expected pyramiding contradiction, got: {errors}"
    
    def test_sanity_check_daily_stop_exceeds_weekly_stop(self):
        """Daily stop >= weekly stop is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["risk"]["daily_stop_pnl_pct"] = -10.0
            policy["risk"]["weekly_stop_pnl_pct"] = -5.0  # Less restrictive than daily
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("daily_stop" in e and "weekly_stop" in e for e in errors), \
                f"Expected daily/weekly stop error, got: {errors}"
    
    def test_sanity_check_maker_fee_exceeds_taker_fee(self):
        """Maker fee > taker fee is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["execution"]["maker_fee_bps"] = 80.0
            policy["execution"]["taker_fee_bps"] = 60.0  # Lower than maker
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("maker_fee_bps" in e and "taker_fee_bps" in e for e in errors), \
                f"Expected maker/taker fee error, got: {errors}"
    
    def test_sanity_check_maker_ttl_inversion(self):
        """Maker TTL inversions are detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["execution"]["maker_max_ttl_sec"] = 20
            policy["execution"]["maker_first_min_ttl_sec"] = 30  # > max
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("maker_first_min_ttl_sec" in e and "maker_max_ttl_sec" in e for e in errors), \
                f"Expected TTL inversion error, got: {errors}"
    
    def test_sanity_check_hourly_rate_far_exceeds_daily_cap(self):
        """Hourly rate × 24 far exceeding daily cap is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["risk"]["max_trades_per_hour"] = 10
            policy["risk"]["max_trades_per_day"] = 50  # 10×24=240 >> 50×2
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("max_trades_per_hour" in e and "max_trades_per_day" in e for e in errors), \
                f"Expected hourly/daily rate error, got: {errors}"
    
    def test_sanity_check_new_trades_exceed_total_trades(self):
        """New trades per hour > total trades per hour is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["risk"]["max_trades_per_hour"] = 5
            policy["risk"]["max_new_trades_per_hour"] = 8  # > total
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("max_new_trades_per_hour" in e and "max_trades_per_hour" in e for e in errors), \
                f"Expected new vs total trades error, got: {errors}"
    
    def test_sanity_check_dust_threshold_exceeds_min_trade(self):
        """Dust threshold > min trade notional is detected"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            policy["risk"]["dust_threshold_usd"] = 20.0
            policy["risk"]["min_trade_notional_usd"] = 10.0  # < dust
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text("dummy: value")
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            assert any("dust_threshold_usd" in e and "min_trade_notional_usd" in e for e in errors), \
                f"Expected dust threshold error, got: {errors}"
    
    def test_sanity_check_valid_config_passes(self):
        """Valid config passes all sanity checks"""
        from tools.config_validator import validate_sanity_checks
        
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = self._create_minimal_valid_policy()
            universe = {
                "tiers": {
                    "tier_1_core": ["BTC", "ETH"],
                    "tier_2_rotational": ["SOL"],
                    "tier_3_event_driven": []
                }
            }
            
            (Path(tmpdir) / "policy.yaml").write_text(yaml.dump(policy))
            (Path(tmpdir) / "universe.yaml").write_text(yaml.dump(universe))
            
            errors = validate_sanity_checks(Path(tmpdir))
            
            # Should pass all checks
            assert len(errors) == 0, f"Expected no errors, got: {errors}"


class TestConfigIntegration:
    """Test integration with actual config files"""
    
    def test_actual_config_files_are_valid(self):
        """Actual config files in config/ directory are valid"""
        errors = validate_all_configs("config")
        
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
        
        assert len(errors) == 0, f"Config validation failed: {errors}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
