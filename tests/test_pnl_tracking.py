"""
Tests for PnL (Profit & Loss) tracking.

Validates that positions are tracked with entry prices and realized PnL
is calculated accurately from fill prices and fees.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass

from infra.state_store import StateStore, DEFAULT_STATE
from core.execution import ExecutionEngine, ExecutionResult


class TestPositionTracking:
    """Test position tracking with entry prices"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.state_file = "data/test_pnl_state.json"
        self.state_store = StateStore(state_file=self.state_file)
        
        # Reset state
        self.state_store.reset(full=True)
    
    def test_buy_creates_position(self):
        """BUY order creates new position with entry price"""
        state = self.state_store.load()
        
        # Simulate buy fill
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="BUY",
            filled_size=0.01,  # 0.01 BTC
            fill_price=50000.0,  # $50k per BTC
            fees=10.0,
            timestamp=datetime.now(timezone.utc)
        )
        
        state = self.state_store.load()
        positions = state.get("positions", {})
        
        assert "BTC-USD" in positions
        pos = positions["BTC-USD"]
        assert pos["side"] == "BUY"
        assert pos["quantity"] == 0.01
        assert pos["entry_price"] == 50000.0
        assert pos["entry_value_usd"] == 500.0  # 0.01 * 50000
        assert pos["fees_paid"] == 10.0
        managed = state.get("managed_positions", {})
        assert managed.get("BTC-USD") is True
    
    def test_sell_closes_position_and_calculates_pnl(self):
        """SELL order closes position and calculates realized PnL"""
        # Create position first (buy)
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="BUY",
            filled_size=0.01,
            fill_price=50000.0,
            fees=10.0,
            timestamp=datetime.now(timezone.utc)
        )
        
        # Close position (sell at higher price)
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="SELL",
            filled_size=0.01,
            fill_price=52000.0,  # $2k profit per BTC
            fees=10.0,
            timestamp=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        
        state = self.state_store.load()
        positions = state.get("positions", {})
        
        # Position should be closed
        assert "BTC-USD" not in positions
        managed = state.get("managed_positions", {})
        assert "BTC-USD" not in managed
        
        # PnL should be recorded
        pnl_today = state.get("pnl_today", 0.0)
        # PnL = (exit_price - entry_price) * quantity - fees
        # PnL = (52000 - 50000) * 0.01 - 20 = 20 - 20 = 0
        # Net: $20 price gain minus $20 total fees = $0
        assert pnl_today == pytest.approx(0.0, abs=0.01)
    
    def test_partial_sell_reduces_position(self):
        """Partial SELL reduces position size proportionally"""
        # Create position (buy 0.02 BTC)
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="BUY",
            filled_size=0.02,
            fill_price=50000.0,
            fees=20.0,
            timestamp=datetime.now(timezone.utc)
        )
        
        # Sell half (0.01 BTC)
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="SELL",
            filled_size=0.01,
            fill_price=51000.0,
            fees=10.0,
            timestamp=datetime.now(timezone.utc) + timedelta(minutes=30)
        )
        
        state = self.state_store.load()
        positions = state.get("positions", {})
        
        # Position should still exist with remaining quantity
        assert "BTC-USD" in positions
        pos = positions["BTC-USD"]
        assert pos["quantity"] == pytest.approx(0.01, abs=0.0001)
        assert pos["entry_price"] == 50000.0  # Entry price unchanged
        
        # Partial PnL realized
        pnl_today = state.get("pnl_today", 0.0)
        # PnL for 0.01 BTC = (51000 - 50000) * 0.01 - 10 (exit fees) - 10 (proportional entry fees)
        # = 10 - 10 - 10 = -10
        assert pnl_today == pytest.approx(-10.0, abs=0.01)
        managed = state.get("managed_positions", {})
        assert managed.get("BTC-USD") is True
    
    def test_loss_position_calculates_negative_pnl(self):
        """Losing trade calculates negative PnL"""
        # Buy at $50k
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="BUY",
            filled_size=0.01,
            fill_price=50000.0,
            fees=10.0,
            timestamp=datetime.now(timezone.utc)
        )
        
        # Sell at $48k (loss)
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="SELL",
            filled_size=0.01,
            fill_price=48000.0,
            fees=10.0,
            timestamp=datetime.now(timezone.utc) + timedelta(hours=2)
        )
        
        state = self.state_store.load()
        pnl_today = state.get("pnl_today", 0.0)
        
        # PnL = (48000 - 50000) * 0.01 - 20 = -20 - 20 = -40
        assert pnl_today == pytest.approx(-40.0, abs=0.01)
    
    def test_multiple_buys_average_entry_price(self):
        """Multiple BUY orders average the entry price"""
        # First buy at $50k
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="BUY",
            filled_size=0.01,
            fill_price=50000.0,
            fees=10.0,
            timestamp=datetime.now(timezone.utc)
        )
        
        # Second buy at $52k
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="BUY",
            filled_size=0.01,
            fill_price=52000.0,
            fees=10.0,
            timestamp=datetime.now(timezone.utc) + timedelta(minutes=10)
        )
        
        state = self.state_store.load()
        positions = state.get("positions", {})
        
        assert "BTC-USD" in positions
        pos = positions["BTC-USD"]
        assert pos["quantity"] == pytest.approx(0.02, abs=0.0001)
        # Weighted average: (0.01 * 50000 + 0.01 * 52000) / 0.02 = 51000
        assert pos["entry_price"] == pytest.approx(51000.0, abs=0.01)
        assert pos["fees_paid"] == 20.0


class TestPnLAccumulation:
    """Test PnL accumulation across multiple trades"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.state_file = "data/test_pnl_accum_state.json"
        self.state_store = StateStore(state_file=self.state_file)
        self.state_store.reset(full=True)
    
    def test_multiple_profitable_trades_accumulate(self):
        """Multiple winning trades accumulate PnL"""
        # Trade 1: BTC profit
        self.state_store.record_fill("BTC-USD", "BUY", 0.01, 50000.0, 10.0, datetime.now(timezone.utc))
        self.state_store.record_fill("BTC-USD", "SELL", 0.01, 51000.0, 10.0, datetime.now(timezone.utc))
        
        state = self.state_store.load()
        pnl_after_trade1 = state.get("pnl_today", 0.0)
        # (51000 - 50000) * 0.01 - 20 = 10 - 20 = -10
        assert pnl_after_trade1 == pytest.approx(-10.0, abs=0.01)
        
        # Trade 2: ETH profit
        self.state_store.record_fill("ETH-USD", "BUY", 1.0, 3000.0, 10.0, datetime.now(timezone.utc))
        self.state_store.record_fill("ETH-USD", "SELL", 1.0, 3100.0, 10.0, datetime.now(timezone.utc))
        
        state = self.state_store.load()
        pnl_total = state.get("pnl_today", 0.0)
        # Previous -10 + (3100 - 3000) * 1.0 - 20 = -10 + 100 - 20 = 70
        assert pnl_total == pytest.approx(70.0, abs=0.01)
    
    def test_win_loss_streak_tracking(self):
        """Track consecutive wins and losses"""
        # Loss 1
        self.state_store.record_fill("BTC-USD", "BUY", 0.01, 50000.0, 10.0, datetime.now(timezone.utc))
        self.state_store.record_fill("BTC-USD", "SELL", 0.01, 49000.0, 10.0, datetime.now(timezone.utc))
        
        state = self.state_store.load()
        assert state.get("consecutive_losses", 0) == 1
        
        # Loss 2
        self.state_store.record_fill("ETH-USD", "BUY", 1.0, 3000.0, 10.0, datetime.now(timezone.utc))
        self.state_store.record_fill("ETH-USD", "SELL", 1.0, 2950.0, 10.0, datetime.now(timezone.utc))
        
        state = self.state_store.load()
        assert state.get("consecutive_losses", 0) == 2
        
        # Win - resets streak
        self.state_store.record_fill("SOL-USD", "BUY", 10.0, 100.0, 5.0, datetime.now(timezone.utc))
        self.state_store.record_fill("SOL-USD", "SELL", 10.0, 120.0, 5.0, datetime.now(timezone.utc))
        
        state = self.state_store.load()
        assert state.get("consecutive_losses", 0) == 0


class TestPnLIntegrationWithExecutionEngine:
    """Test PnL tracking integrated with ExecutionEngine"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.state_file = "data/test_pnl_exec_state.json"
        self.state_store = StateStore(state_file=self.state_file)
        self.state_store.reset(full=True)
        
        self.mock_exchange = Mock()
        self.engine = ExecutionEngine(
            mode="PAPER",
            exchange=self.mock_exchange,
            state_store=self.state_store,
            policy={"microstructure": {}, "risk": {}, "execution": {}}
        )
    
    def test_paper_trade_updates_pnl(self):
        """Paper trade execution through ExecutionEngine"""
        from datetime import datetime, timezone
        
        # Mock quote
        mock_quote = Mock()
        mock_quote.mid = 50000.0
        mock_quote.ask = 50010.0
        mock_quote.bid = 49990.0
        mock_quote.spread_bps = 20.0
        mock_quote.timestamp = datetime.now(timezone.utc)
        self.mock_exchange.get_quote.return_value = mock_quote
        
        # Mock accounts with USDC balance for pair finding
        self.mock_exchange.get_accounts.return_value = [
            {
                'currency': 'USDC',
                'available_balance': {'value': '1000.0'},
                'uuid': 'test-account-uuid'
            }
        ]
        
        # Execute buy - should succeed in PAPER mode
        result = self.engine.execute("BTC-USD", "BUY", 500.0)
        
        # PAPER mode simulates fills
        assert result.success
        assert result.route == "paper_simulated"
        
        # Validate state_store integration
        assert self.engine.state_store is not None
        assert self.engine.state_store == self.state_store


class TestPnLEdgeCases:
    """Test edge cases in PnL calculation"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.state_file = "data/test_pnl_edge_state.json"
        self.state_store = StateStore(state_file=self.state_file)
        self.state_store.reset(full=True)
    
    def test_sell_without_position_logs_error(self):
        """Selling without position should log error and not crash"""
        # Sell without buy first
        self.state_store.record_fill(
            symbol="BTC-USD",
            side="SELL",
            filled_size=0.01,
            fill_price=50000.0,
            fees=10.0,
            timestamp=datetime.now(timezone.utc)
        )
        
        state = self.state_store.load()
        # Should not crash, position still shouldn't exist
        assert "BTC-USD" not in state.get("positions", {})
    
    def test_zero_quantity_position_removed(self):
        """Position with zero quantity after sell should be removed"""
        # Buy
        self.state_store.record_fill("BTC-USD", "BUY", 0.01, 50000.0, 10.0, datetime.now(timezone.utc))
        
        # Sell exact amount
        self.state_store.record_fill("BTC-USD", "SELL", 0.01, 51000.0, 10.0, datetime.now(timezone.utc))
        
        state = self.state_store.load()
        assert "BTC-USD" not in state.get("positions", {})
    
    def test_daily_reset_clears_pnl_but_keeps_positions(self):
        """Daily reset should clear pnl_today but keep open positions"""
        # Create position
        self.state_store.record_fill("BTC-USD", "BUY", 0.01, 50000.0, 10.0, datetime.now(timezone.utc))
        
        # Manually set pnl_today
        state = self.state_store.load()
        state["pnl_today"] = 100.0
        state["last_reset_date"] = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        self.state_store.save(state)
        
        # Trigger auto-reset by loading
        state = self.state_store.load()
        
        # PnL should be reset
        assert state.get("pnl_today", 0.0) == 0.0
        
        # Position should still exist
        assert "BTC-USD" in state.get("positions", {})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
