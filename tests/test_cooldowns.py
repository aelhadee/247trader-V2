"""
Test per-symbol cooldown enforcement.
"""
import pytest
from datetime import datetime, timedelta, timezone
from core.risk import RiskEngine
from strategy.rules_engine import TradeProposal, TriggerSignal
from core.universe import UniverseAsset
from infra.state_store import StateStore
import tempfile
import os


def test_cooldown_application_and_filtering():
    """Test that cooldowns are applied after trades and proposals are filtered."""
    
    # Create temp state file with valid initial state
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        import json
        json.dump({}, f)
        state_file = f.name
    
    try:
        # Setup
        policy = {
            "risk": {
                "per_symbol_cooldown_enabled": True,
                "per_symbol_cooldown_minutes": 30,
                "per_symbol_cooldown_after_stop": 60,
                "max_position_size_pct": 10.0,
                "max_total_exposure_pct": 50.0,
                "cluster_limit": 3,
            },
            "circuit_breakers": {
                "enabled": False,  # Disable for this test
            }
        }
        
        state_store = StateStore(state_file)
        risk_engine = RiskEngine(policy=policy, state_store=state_store)
        
        # Create simple proposal (minimal fields)
        proposal = TradeProposal(
            symbol="BTC-USD",
            side="BUY",
            size_pct=5.0,
            reason="test",
            confidence=0.9
        )
        
        # Before cooldown: proposal should pass
        filtered = risk_engine._filter_cooled_symbols([proposal])
        assert len(filtered) == 1, "Proposal should pass before cooldown"
        
        # Apply cooldown (regular loss, not stop-loss)
        risk_engine.apply_symbol_cooldown("BTC-USD", is_stop_loss=False)
        
        # After cooldown: proposal should be filtered
        filtered = risk_engine._filter_cooled_symbols([proposal])
        assert len(filtered) == 0, "Proposal should be filtered during cooldown"
        
        # Verify cooldown is active
        assert state_store.is_cooldown_active("BTC-USD"), "Cooldown should be active"
        
        # Test stop-loss cooldown (longer duration)
        proposal2 = TradeProposal(
            symbol="ETH-USD",
            side="BUY",
            size_pct=5.0,
            reason="test",
            confidence=0.9
        )
        
        # Apply stop-loss cooldown
        risk_engine.apply_symbol_cooldown("ETH-USD", is_stop_loss=True)
        
        # Should be filtered
        filtered = risk_engine._filter_cooled_symbols([proposal2])
        assert len(filtered) == 0, "Proposal should be filtered during stop-loss cooldown"
        
        # Verify cooldown is active
        assert state_store.is_cooldown_active("ETH-USD"), "Stop-loss cooldown should be active"
        
        # Simulate cooldown expiry by manually adjusting timestamp
        state = state_store.load()
        past_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        state["cooldowns"]["BTC-USD"] = past_time
        state_store.save(state)
        
        # After expiry: proposal should pass again
        filtered = risk_engine._filter_cooled_symbols([proposal])
        assert len(filtered) == 1, "Proposal should pass after cooldown expires"
        assert not state_store.is_cooldown_active("BTC-USD"), "Cooldown should have expired"
        
        print("✅ Cooldown application and filtering test passed")
        
    finally:
        # Cleanup
        if os.path.exists(state_file):
            os.unlink(state_file)


def test_cooldown_disabled():
    """Test that cooldowns can be disabled via config."""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        import json
        json.dump({}, f)
        state_file = f.name
    
    try:
        policy = {
            "risk": {
                "per_symbol_cooldown_enabled": False,  # Disabled
                "max_position_size_pct": 10.0,
                "max_total_exposure_pct": 50.0,
                "cluster_limit": 3,
            },
            "circuit_breakers": {
                "enabled": False,
            }
        }
        
        state_store = StateStore(state_file)
        risk_engine = RiskEngine(policy=policy, state_store=state_store)
        
        proposal = TradeProposal(
            symbol="BTC-USD",
            side="BUY",
            size_pct=5.0,
            reason="test",
            confidence=0.9
        )
        
        # Apply cooldown
        risk_engine.apply_symbol_cooldown("BTC-USD", is_stop_loss=False)
        
        # Even with cooldown applied, proposals should not be filtered when disabled
        filtered = risk_engine._filter_cooled_symbols([proposal])
        assert len(filtered) == 1, "Proposal should pass when cooldowns are disabled"
        
        print("✅ Cooldown disabled test passed")
        
    finally:
        if os.path.exists(state_file):
            os.unlink(state_file)


if __name__ == "__main__":
    test_cooldown_application_and_filtering()
    test_cooldown_disabled()
    print("\n✅ All cooldown tests passed!")
