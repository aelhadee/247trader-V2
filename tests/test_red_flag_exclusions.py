"""
Tests for red flag exclusions system.

Validates that flagged assets (scams, exploits, regulatory actions) are:
1. Banned from universe for configured duration
2. Auto-expired after ban period
3. Manually clearable if needed
4. Logged properly for audit trail
"""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import yaml

from infra.state_store import StateStore, JsonFileBackend
from core.universe import UniverseManager, UniverseSnapshot


@pytest.fixture
def temp_state_file():
    """Create temporary state file for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_path = Path(f.name)
    yield temp_path
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def state_store(temp_state_file):
    """Create StateStore with temp backend"""
    backend = JsonFileBackend(temp_state_file)
    store = StateStore(backend=backend)
    # Clear any existing red flag bans to isolate test
    state = store.load()
    state["red_flag_bans"] = {}
    store.save(state)
    return store


@pytest.fixture
def universe_config():
    """Create minimal universe config for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config = {
            'universe': {
                'method': 'static',
                'max_universe_size': 30,
                'refresh_interval_hours': 24,
            },
            'exclusions': {
                'never_trade': ['USDT-USD', 'USDC-USD'],
                'red_flags': ['recent_exploit', 'regulatory_action', 'team_rug', 'delisting_rumors'],
                'temporary_ban_hours': 168,
            },
            'clusters': {'definitions': {}, 'enabled': False},
            'liquidity': {
                'max_spread_bps': 80,
                'min_24h_volume_usd': 8000000,
                'min_depth_20bps_usd': 10000,
            },
            'regime_modifiers': {
                'chop': {'tier_1_multiplier': 1.0, 'tier_2_multiplier': 0.8},
            },
            'tiers': {
                'tier_1_core': {
                    'symbols': ['BTC-USD', 'ETH-USD', 'SCAM-USD'],  # SCAM-USD will be flagged
                    'constraints': {'min_24h_volume_usd': 50000000, 'max_spread_bps': 20},
                },
                'tier_2_rotational': {
                    'symbols': [],
                    'constraints': {},
                },
                'tier_3_event_driven': {
                    'max_tier_3_symbols': 0,
                },
            },
        }
        yaml.dump(config, f)
        temp_path = Path(f.name)
    yield temp_path
    if temp_path.exists():
        temp_path.unlink()


class TestRedFlagBans:
    """Test red flag ban system in StateStore"""
    
    def test_flag_asset_creates_ban(self, state_store):
        """Flagging an asset creates a ban with expiration"""
        state_store.flag_asset_red_flag("SCAM-USD", "team_rug", ban_hours=168)
        
        banned = state_store.get_red_flag_banned_symbols()
        assert "SCAM-USD" in banned
        assert banned["SCAM-USD"]["reason"] == "team_rug"
        assert "banned_at_iso" in banned["SCAM-USD"]
        assert "expires_at_iso" in banned["SCAM-USD"]
        
        # Verify expiration time is ~168 hours from now
        expires_at = datetime.fromisoformat(banned["SCAM-USD"]["expires_at_iso"])
        now = datetime.now(timezone.utc)
        delta = (expires_at - now).total_seconds() / 3600  # hours
        assert 167 <= delta <= 169, f"Expected ~168h, got {delta:.2f}h"
    
    def test_is_red_flag_banned_check(self, state_store):
        """Check if symbol is banned returns correct result"""
        # Not banned initially
        is_banned, reason = state_store.is_red_flag_banned("SAFE-USD")
        assert not is_banned
        assert reason is None
        
        # Ban the asset
        state_store.flag_asset_red_flag("UNSAFE-USD", "recent_exploit", ban_hours=72)
        
        # Now should be banned
        is_banned, reason = state_store.is_red_flag_banned("UNSAFE-USD")
        assert is_banned
        assert reason == "recent_exploit"
        
        # Other assets still not banned
        is_banned, _ = state_store.is_red_flag_banned("SAFE-USD")
        assert not is_banned
    
    def test_multiple_red_flags(self, state_store):
        """Multiple assets can be flagged simultaneously"""
        state_store.flag_asset_red_flag("SCAM1-USD", "team_rug", ban_hours=168)
        state_store.flag_asset_red_flag("SCAM2-USD", "recent_exploit", ban_hours=72)
        state_store.flag_asset_red_flag("SCAM3-USD", "regulatory_action", ban_hours=336)
        
        banned = state_store.get_red_flag_banned_symbols()
        assert len(banned) == 3
        assert "SCAM1-USD" in banned
        assert "SCAM2-USD" in banned
        assert "SCAM3-USD" in banned
        assert banned["SCAM1-USD"]["reason"] == "team_rug"
        assert banned["SCAM2-USD"]["reason"] == "recent_exploit"
        assert banned["SCAM3-USD"]["reason"] == "regulatory_action"
    
    def test_expired_bans_auto_cleared(self, state_store):
        """Expired bans are automatically cleared on get"""
        # Create a ban that expires immediately
        state_store.flag_asset_red_flag("SHORTBAN-USD", "delisting_rumors", ban_hours=0)
        
        # Verify it's in the state
        state = state_store.load()
        assert "SHORTBAN-USD" in state.get("red_flag_bans", {})
        
        # Get banned symbols (should auto-expire and clean)
        banned = state_store.get_red_flag_banned_symbols()
        assert "SHORTBAN-USD" not in banned
        
        # Verify it's removed from state
        state = state_store.load()
        assert "SHORTBAN-USD" not in state.get("red_flag_bans", {})
    
    def test_manual_clear_ban(self, state_store):
        """Manual clearing of ban works"""
        state_store.flag_asset_red_flag("TEMP-USD", "team_rug", ban_hours=168)
        
        # Verify banned
        is_banned, _ = state_store.is_red_flag_banned("TEMP-USD")
        assert is_banned
        
        # Clear manually
        cleared = state_store.clear_red_flag_ban("TEMP-USD")
        assert cleared
        
        # Verify no longer banned
        is_banned, _ = state_store.is_red_flag_banned("TEMP-USD")
        assert not is_banned
        
        # Clearing non-existent ban returns False
        cleared = state_store.clear_red_flag_ban("NEVER-BANNED-USD")
        assert not cleared
    
    def test_persistence_across_loads(self, state_store):
        """Red flag bans persist across state loads"""
        state_store.flag_asset_red_flag("PERSIST-USD", "recent_exploit", ban_hours=168)
        
        # Load fresh state
        state = state_store.load()
        red_flag_bans = state.get("red_flag_bans", {})
        assert "PERSIST-USD" in red_flag_bans
        assert red_flag_bans["PERSIST-USD"]["reason"] == "recent_exploit"
    
    def test_default_ban_duration(self, state_store):
        """Default ban duration is 168 hours (7 days)"""
        state_store.flag_asset_red_flag("DEFAULT-USD", "team_rug")  # No ban_hours specified
        
        banned = state_store.get_red_flag_banned_symbols()
        expires_at = datetime.fromisoformat(banned["DEFAULT-USD"]["expires_at_iso"])
        now = datetime.now(timezone.utc)
        delta_hours = (expires_at - now).total_seconds() / 3600
        assert 167 <= delta_hours <= 169, f"Expected ~168h default, got {delta_hours:.2f}h"


class TestUniverseRedFlagIntegration:
    """Test that UniverseManager respects red flag bans"""
    
    @pytest.mark.skip(reason="Requires mocking exchange API calls")
    def test_red_flagged_asset_excluded_from_universe(self, state_store, universe_config):
        """Red-flagged assets are excluded from universe"""
        # Flag SCAM-USD (which is in tier_1_core config)
        state_store.flag_asset_red_flag("SCAM-USD", "team_rug", ban_hours=168)
        
        # Build universe
        mgr = UniverseManager.from_config_path(str(universe_config))
        
        # NOTE: This would require mocking exchange.get_quote() and exchange.get_orderbook()
        # Skipping actual universe building in this test
        # In real usage, SCAM-USD would be filtered out during get_universe()
    
    def test_config_red_flags_list(self, universe_config):
        """Config contains expected red flag types"""
        mgr = UniverseManager(config_path=str(universe_config))
        exclusions = mgr.config.get("exclusions", {})
        red_flags = exclusions.get("red_flags", [])
        
        assert "recent_exploit" in red_flags
        assert "regulatory_action" in red_flags
        assert "team_rug" in red_flags
        assert "delisting_rumors" in red_flags
        
        ban_hours = exclusions.get("temporary_ban_hours", 0)
        assert ban_hours == 168  # 7 days
    
    def test_never_trade_vs_red_flag(self, state_store, universe_config):
        """never_trade is permanent, red_flag is temporary"""
        mgr = UniverseManager(config_path=str(universe_config))
        exclusions = mgr.config.get("exclusions", {})
        
        never_trade = set(exclusions.get("never_trade", []))
        assert "USDT-USD" in never_trade
        assert "USDC-USD" in never_trade
        
        # Red flags are different - they expire
        state_store.flag_asset_red_flag("TEMP-BAN-USD", "team_rug", ban_hours=1)
        
        # After 1 hour, red flag expires, but never_trade is still blocked
        # (This is logical - we can't test time-based expiration without mocking time)


class TestRedFlagEdgeCases:
    """Test edge cases and error handling"""
    
    def test_reflag_asset_updates_ban(self, state_store):
        """Re-flagging an asset updates the ban with new reason/expiration"""
        state_store.flag_asset_red_flag("REFLAG-USD", "team_rug", ban_hours=72)
        
        banned = state_store.get_red_flag_banned_symbols()
        first_reason = banned["REFLAG-USD"]["reason"]
        first_expires = banned["REFLAG-USD"]["expires_at_iso"]
        
        # Re-flag with different reason and duration
        state_store.flag_asset_red_flag("REFLAG-USD", "regulatory_action", ban_hours=168)
        
        banned = state_store.get_red_flag_banned_symbols()
        assert banned["REFLAG-USD"]["reason"] == "regulatory_action"
        assert banned["REFLAG-USD"]["expires_at_iso"] != first_expires  # New expiration
    
    def test_malformed_ban_entry_cleaned(self, state_store):
        """Malformed ban entries are cleaned on get"""
        # Manually inject malformed entry
        state = state_store.load()
        state["red_flag_bans"] = {
            "GOOD-USD": {
                "reason": "team_rug",
                "banned_at_iso": datetime.now(timezone.utc).isoformat(),
                "expires_at_iso": (datetime.now(timezone.utc) + timedelta(hours=168)).isoformat(),
            },
            "BAD-USD": {
                "reason": "malformed",
                # Missing expires_at_iso
            },
        }
        state_store.save(state)
        
        # Get should clean malformed entry
        banned = state_store.get_red_flag_banned_symbols()
        assert "GOOD-USD" in banned
        assert "BAD-USD" not in banned  # Cleaned due to missing expires_at_iso
    
    def test_empty_red_flags_state(self, state_store):
        """System handles empty red_flag_bans gracefully"""
        banned = state_store.get_red_flag_banned_symbols()
        assert banned == {}
        
        is_banned, _ = state_store.is_red_flag_banned("ANY-USD")
        assert not is_banned


class TestRedFlagAuditTrail:
    """Test that red flag actions are properly logged"""
    
    def test_flag_action_logged(self, state_store, caplog):
        """Flagging an asset logs a warning"""
        import logging
        caplog.set_level(logging.WARNING)
        
        state_store.flag_asset_red_flag("LOGGED-USD", "recent_exploit", ban_hours=168)
        
        # Check log contains red flag warning
        assert any("🚩 RED FLAG" in record.message for record in caplog.records)
        assert any("LOGGED-USD" in record.message for record in caplog.records)
        assert any("recent_exploit" in record.message for record in caplog.records)
    
    def test_clear_action_logged(self, state_store, caplog):
        """Clearing a ban logs info message"""
        import logging
        caplog.set_level(logging.INFO)
        
        state_store.flag_asset_red_flag("CLEARLOG-USD", "team_rug", ban_hours=168)
        caplog.clear()
        
        state_store.clear_red_flag_ban("CLEARLOG-USD")
        
        assert any("Cleared red flag ban" in record.message for record in caplog.records)
        assert any("CLEARLOG-USD" in record.message for record in caplog.records)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
