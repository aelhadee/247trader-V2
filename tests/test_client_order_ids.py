"""
Tests for deterministic client order ID generation.

Ensures idempotent order submission by verifying:
1. Same inputs always produce same ID
2. Different inputs produce different IDs
3. No collisions across reasonable scenarios
4. StateStore deduplication works correctly
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from core.execution import ExecutionEngine
from infra.state_store import StateStore


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset Prometheus metrics between tests to avoid registry conflicts"""
    from infra.metrics import MetricsRecorder
    # Clean up BEFORE test (in case previous test didn't have fixture)
    MetricsRecorder._reset_for_testing()
    yield
    # Clean up AFTER test
    MetricsRecorder._reset_for_testing()


class TestDeterministicClientOrderIds:
    """Test suite for deterministic client order ID generation"""
    
    def test_same_inputs_same_id(self):
        """Same trade parameters should always produce same ID"""
        engine = ExecutionEngine(mode="DRY_RUN")
        
        # Create fixed timestamp
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        
        # Generate ID multiple times with same inputs
        id1 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts)
        id2 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts)
        id3 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts)
        
        assert id1 == id2 == id3, "Same inputs must produce same ID"
        assert id1.startswith("247trader_coid_"), "ID should include managed prefix"
    
    def test_different_symbols_different_ids(self):
        """Different symbols should produce different IDs"""
        engine = ExecutionEngine(mode="DRY_RUN")
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        
        id_btc = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts)
        id_eth = engine.generate_client_order_id("ETH-USD", "BUY", 100.0, ts)
        
        assert id_btc != id_eth, "Different symbols must produce different IDs"
    
    def test_different_sides_different_ids(self):
        """Different sides (BUY/SELL) should produce different IDs"""
        engine = ExecutionEngine(mode="DRY_RUN")
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        
        id_buy = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts)
        id_sell = engine.generate_client_order_id("BTC-USD", "SELL", 100.0, ts)
        
        assert id_buy != id_sell, "BUY and SELL must produce different IDs"
    
    def test_different_sizes_different_ids(self):
        """Different sizes should produce different IDs"""
        engine = ExecutionEngine(mode="DRY_RUN")
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        
        id_100 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts)
        id_200 = engine.generate_client_order_id("BTC-USD", "BUY", 200.0, ts)
        
        assert id_100 != id_200, "Different sizes must produce different IDs"
    
    def test_minute_granularity(self):
        """IDs should be same within same minute, different across minutes"""
        engine = ExecutionEngine(mode="DRY_RUN")
        
        # Same minute, different seconds
        ts1 = datetime(2024, 1, 15, 10, 30, 15, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        
        id1 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts1)
        id2 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts2)
        
        assert id1 == id2, "Same minute should produce same ID"
        
        # Different minute
        ts3 = datetime(2024, 1, 15, 10, 31, 15, tzinfo=timezone.utc)
        id3 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts3)
        
        assert id1 != id3, "Different minute should produce different ID"
    
    def test_floating_point_rounding(self):
        """Close float values should produce same ID (avoids precision issues)"""
        engine = ExecutionEngine(mode="DRY_RUN")
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        
        # Test values that differ by less than $0.01
        id1 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0000, ts)
        id2 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0001, ts)
        
        assert id1 == id2, "Values differing by < $0.01 should produce same ID"
        
        # Test values that differ by >= $0.01
        id3 = engine.generate_client_order_id("BTC-USD", "BUY", 100.01, ts)
        assert id1 != id3, "Values differing by >= $0.01 should produce different IDs"
    
    def test_case_insensitive_side(self):
        """Side parameter should be case-insensitive"""
        engine = ExecutionEngine(mode="DRY_RUN")
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        
        id_upper = engine.generate_client_order_id("BTC-USD", "BUY", 100.0, ts)
        id_lower = engine.generate_client_order_id("BTC-USD", "buy", 100.0, ts)
        id_mixed = engine.generate_client_order_id("BTC-USD", "BuY", 100.0, ts)
        
        assert id_upper == id_lower == id_mixed, "Side should be case-insensitive"
    
    def test_no_collisions_stress(self):
        """Generate many IDs to check for collisions"""
        engine = ExecutionEngine(mode="DRY_RUN")
        
        symbols = ["BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD"]
        sides = ["BUY", "SELL"]
        sizes = [100.0, 200.0, 500.0, 1000.0]
        timestamps = [
            datetime(2024, 1, 15, h, m, 0, tzinfo=timezone.utc)
            for h in range(10, 12)
            for m in range(0, 60, 15)
        ]
        
        ids = set()
        for symbol in symbols:
            for side in sides:
                for size in sizes:
                    for ts in timestamps:
                        order_id = engine.generate_client_order_id(symbol, side, size, ts)
                        ids.add(order_id)
        
        # Calculate expected unique combinations
        expected = len(symbols) * len(sides) * len(sizes) * len(timestamps)
        assert len(ids) == expected, f"Expected {expected} unique IDs, got {len(ids)} (collision detected)"
    
    def test_default_timestamp(self):
        """When timestamp is None, should use current time"""
        engine = ExecutionEngine(mode="DRY_RUN")
        
        # Generate ID without timestamp (uses now)
        id1 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0)
        
        # Should produce valid ID
        assert id1.startswith("247trader_coid_")
        
        # Generate again immediately - should be same (within same minute)
        id2 = engine.generate_client_order_id("BTC-USD", "BUY", 100.0)
        assert id1 == id2, "IDs generated immediately should be same"


class TestClientOrderIdIntegration:
    """Test client order ID integration with execution flow"""
    
    @patch('core.execution.get_exchange')
    def test_dry_run_uses_deterministic_id(self, mock_get_exchange):
        """DRY_RUN mode should use deterministic IDs"""
        mock_exchange = Mock()
        mock_get_exchange.return_value = mock_exchange
        
        engine = ExecutionEngine(mode="DRY_RUN")
        
        # Execute without providing client_order_id
        result = engine.execute("BTC-USD", "BUY", 100.0)

        assert result.success is True
        assert result.order_id.startswith("shadow_247trader_coid_")  # DRY_RUN now uses shadow execution
        
        # Execute again with same params - should get same underlying ID
        result2 = engine.execute("BTC-USD", "BUY", 100.0)
        assert result.order_id == result2.order_id, "Same params should produce same order_id"
    
    @patch('core.execution.get_exchange')
    def test_paper_uses_deterministic_id(self, mock_get_exchange):
        """PAPER mode should use deterministic IDs"""
        mock_exchange = Mock()
        mock_exchange.get_quote.return_value = Mock(ask=50000, bid=49990, spread_bps=20)
        mock_exchange.list_accounts.return_value = []
        mock_get_exchange.return_value = mock_exchange
        
        engine = ExecutionEngine(mode="PAPER", exchange=mock_exchange)
        
        # Execute with full trading pair to avoid _find_best_trading_pair
        result = engine.execute("BTC-USD", "SELL", 100.0)  # Use SELL to avoid balance lookup

        assert result.success is True
        assert result.order_id.startswith("paper_247trader_coid_")
        
        # Execute again with same params - should get same underlying ID
        result2 = engine.execute("BTC-USD", "SELL", 100.0)
        assert result.order_id == result2.order_id, "Same params should produce same order_id"
    
    @patch('core.execution.get_exchange')
    def test_live_uses_deterministic_id(self, mock_get_exchange):
        """LIVE mode should use deterministic IDs"""
        from datetime import datetime, timezone
        
        mock_exchange = Mock()
        mock_exchange.read_only = False
        mock_quote = Mock(
            mid=50000,
            ask=50010,
            bid=49990,
            spread_bps=20,
            timestamp=datetime.now(timezone.utc)
        )
        mock_exchange.get_quote.return_value = mock_quote
        mock_exchange.list_accounts.return_value = []
        mock_exchange.get_product_metadata.return_value = {
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "base_min_size": "0.0001",
            "base_max_size": "1000",
            "min_market_funds": "1"
        }
        mock_exchange.place_order.return_value = {
            "success": True,
            "order_id": "real-order-123",
            "status": "open",
            "fills": []
        }
        mock_get_exchange.return_value = mock_exchange
        
        state_store = StateStore(state_file="data/test_state.json")
        engine = ExecutionEngine(mode="LIVE", exchange=mock_exchange, state_store=state_store)
        
        # Execute with SELL to avoid balance lookup in _find_best_trading_pair
        result = engine.execute("BTC-USD", "SELL", 100.0, skip_liquidity_checks=True)
        
        assert result.success is True
        
        # Verify deterministic client_order_id was passed to place_order
        call_args = mock_exchange.place_order.call_args
        client_order_id = call_args.kwargs.get("client_order_id")
        assert client_order_id is not None
        assert client_order_id.startswith("247trader_coid_")
    
    @patch('core.execution.get_exchange')
    def test_explicit_client_order_id_preserved(self, mock_get_exchange):
        """Explicitly provided client_order_id should be preserved"""
        mock_exchange = Mock()
        mock_get_exchange.return_value = mock_exchange
        
        engine = ExecutionEngine(mode="DRY_RUN")
        
        # Execute with explicit client_order_id
        custom_id = "custom_order_123"
        result = engine.execute("BTC-USD", "BUY", 100.0, client_order_id=custom_id)
        
        assert result.success is True
        assert custom_id in result.order_id, "Custom client_order_id should be preserved"
    
    @patch('core.execution.get_exchange')
    def test_deduplication_with_state_store(self, mock_get_exchange):
        """StateStore should detect duplicate client_order_id submissions"""
        mock_exchange = Mock()
        mock_exchange.read_only = False
        mock_exchange.get_quote.return_value = Mock(mid=50000, ask=50010, bid=49990, spread_bps=20)
        mock_exchange.list_accounts.return_value = []
        mock_exchange.get_product_metadata.return_value = {
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "base_min_size": "0.0001",
            "min_market_funds": "1"
        }
        mock_get_exchange.return_value = mock_exchange
        
        # Create state store and add an open order
        state_store = StateStore(state_file="data/test_state.json")
        custom_id = "test_duplicate_123"
        state_store.record_open_order(custom_id, {
            "symbol": "BTC-USD",
            "side": "SELL",
            "size_usd": 100.0,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        engine = ExecutionEngine(mode="LIVE", exchange=mock_exchange, state_store=state_store)
        
        # Try to execute with same client_order_id - should be rejected
        result = engine.execute("BTC-USD", "SELL", 100.0, client_order_id=custom_id, skip_liquidity_checks=True)
        
        assert result.success is False
        assert "duplicate" in result.error.lower()
        assert result.route == "skipped_duplicate"
        
        # place_order should NOT have been called
        mock_exchange.place_order.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
