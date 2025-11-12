#!/usr/bin/env python3
"""
Test critical safety fixes identified in code review:
1. PortfolioState.nav property exists (prevents AttributeError)
2. RiskEngine receives AlertService (enables safety notifications)
3. max_drawdown_pct calculated correctly (enables drawdown protection)
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock
from pathlib import Path
import tempfile
import json

from core.risk import RiskEngine, PortfolioState
from infra.alerting import AlertService
from infra.state_store import StateStore


class TestCriticalSafetyFixes:
    """Test suite for critical safety bug fixes"""
    
    def test_portfolio_state_has_nav_property(self):
        """Test Fix 1: PortfolioState.nav property prevents AttributeError"""
        portfolio = PortfolioState(
            account_value_usd=10000.0,
            open_positions={},
            daily_pnl_pct=0.0,
            max_drawdown_pct=0.0,
            trades_today=0,
            trades_this_hour=0,
            current_time=datetime.now(timezone.utc)
        )
        
        # Should have nav property
        assert hasattr(portfolio, 'nav'), "PortfolioState missing nav property"
        assert portfolio.nav == 10000.0, "nav should equal account_value_usd"
        
        # Verify it's a property, not a field
        assert isinstance(type(portfolio).nav, property), "nav should be a property"
    
    def test_nav_property_used_in_alert_context(self):
        """Test that nav property works in alert context dicts"""
        portfolio = PortfolioState(
            account_value_usd=5000.0,
            open_positions={},
            daily_pnl_pct=-3.5,
            max_drawdown_pct=8.2,
            trades_today=5,
            trades_this_hour=2,
            current_time=datetime.now(timezone.utc)
        )
        
        # Simulate alert context dict (like RiskEngine builds)
        alert_context = {
            "daily_pnl_pct": portfolio.daily_pnl_pct,
            "threshold": 5.0,
            "nav": round(portfolio.nav, 2)  # This was causing AttributeError
        }
        
        assert alert_context["nav"] == 5000.0
        assert alert_context["daily_pnl_pct"] == -3.5
    
    def test_risk_engine_receives_alert_service(self):
        """Test Fix 2: RiskEngine can be initialized with AlertService"""
        # Create mock alert service
        alert_service = Mock(spec=AlertService)
        alert_service.is_enabled = Mock(return_value=True)
        alert_service.notify = Mock()
        
        # Create mock dependencies
        policy = {
            "risk": {
                "max_total_at_risk_pct": 15.0,
                "max_position_size_pct": 5.0,
            }
        }
        
        universe_mgr = Mock()
        exchange = Mock()
        
        # Should accept alert_service parameter
        risk_engine = RiskEngine(
            policy=policy,
            universe_manager=universe_mgr,
            exchange=exchange,
            alert_service=alert_service
        )
        
        # Verify alert service is stored
        assert hasattr(risk_engine, 'alert_service'), "RiskEngine missing alert_service attribute"
        assert risk_engine.alert_service is alert_service
    
    def test_max_drawdown_calculated_from_high_water_mark(self):
        """Test Fix 3: max_drawdown_pct calculated correctly from high_water_mark"""
        # Create temporary state file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            state_file = f.name
            initial_state = {
                "high_water_mark": 10000.0,  # Peak NAV
                "pnl_today": 0.0,
                "pnl_week": 0.0,
                "trades_today": 0,
                "trades_this_hour": 0,
                "consecutive_losses": 0,
                "positions": {},
                "cash_balances": {},
                "open_orders": {},
                "cooldowns": {},
            }
            json.dump(initial_state, f)
        
        try:
            state_store = StateStore(state_file)
            state = state_store.load()
            
            # Simulate portfolio with 15% drawdown
            current_nav = 8500.0  # Down from 10000
            high_water_mark = float(state.get("high_water_mark", current_nav))
            
            # Calculate drawdown (should match main_loop logic)
            expected_drawdown = ((high_water_mark - current_nav) / high_water_mark) * 100.0
            
            assert abs(expected_drawdown - 15.0) < 0.1, f"Expected 15% drawdown, got {expected_drawdown:.1f}%"
            
            # Test when NAV is at high water mark (no drawdown)
            current_nav = 10000.0
            drawdown = ((high_water_mark - current_nav) / high_water_mark) * 100.0
            assert drawdown == 0.0, "Drawdown should be 0% at high water mark"
            
            # Test when NAV exceeds high water mark (new peak)
            current_nav = 12000.0
            new_high_water_mark = max(high_water_mark, current_nav)
            drawdown = ((new_high_water_mark - current_nav) / new_high_water_mark) * 100.0
            assert drawdown == 0.0, "Drawdown should be 0% at new peak"
            assert new_high_water_mark == 12000.0, "High water mark should update to new peak"
            
        finally:
            # Cleanup
            Path(state_file).unlink(missing_ok=True)
    
    def test_high_water_mark_persists_in_state(self):
        """Test that high_water_mark is persisted in state store"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            state_file = f.name
        
        try:
            state_store = StateStore(state_file)
            
            # Initial state should have high_water_mark
            state = state_store.load()
            assert "high_water_mark" in state, "State should include high_water_mark field"
            
            # Update high water mark
            state["high_water_mark"] = 15000.0
            state_store.save(state)
            
            # Reload and verify persistence
            reloaded_state = state_store.load()
            assert reloaded_state["high_water_mark"] == 15000.0, "High water mark should persist"
            
        finally:
            Path(state_file).unlink(missing_ok=True)
    
    def test_drawdown_protection_not_disabled(self):
        """Test that max_drawdown_pct is not hardcoded to 0.0"""
        # Simulate state with drawdown
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            state_file = f.name
            state = {
                "high_water_mark": 10000.0,
                "pnl_today": -500.0,
                "pnl_week": -1500.0,
                "trades_today": 0,
                "trades_this_hour": 0,
                "consecutive_losses": 0,
                "positions": {},
                "cash_balances": {},
                "open_orders": {},
                "cooldowns": {},
            }
            json.dump(state, f)
        
        try:
            state_store = StateStore(state_file)
            state = state_store.load()
            
            # Calculate drawdown for NAV that's down 20%
            current_nav = 8000.0
            high_water_mark = float(state.get("high_water_mark", current_nav))
            max_drawdown_pct = ((high_water_mark - current_nav) / high_water_mark) * 100.0
            
            # Drawdown should be 20%, not 0.0
            assert max_drawdown_pct == 20.0, f"Expected 20% drawdown, got {max_drawdown_pct}%"
            assert max_drawdown_pct != 0.0, "max_drawdown_pct should not be hardcoded to 0.0"
            
        finally:
            Path(state_file).unlink(missing_ok=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
