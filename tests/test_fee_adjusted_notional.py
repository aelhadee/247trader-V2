"""
Tests for fee-adjusted minimum notional rounding.

Critical production safety feature that prevents orders from being rejected
after fees cause the net amount to fall below exchange minimums.
"""
import pytest
from unittest.mock import Mock
from core.execution import ExecutionEngine


class TestFeeAdjustedRounding:
    """Test suite for fee-adjusted minimum notional enforcement."""
    
    @pytest.fixture
    def mock_exchange(self):
        """Create mock exchange adapter."""
        exchange = Mock()
        exchange.name = "coinbase"
        return exchange
    
    @pytest.fixture
    def mock_state_store(self):
        """Create mock state store."""
        store = Mock()
        store.has_open_order = Mock(return_value=False)
        return store
    
    @pytest.fixture
    def policy(self):
        """Base policy configuration."""
        return {
            "exchange": {"read_only": False},
            "execution": {
                "default_order_type": "limit_post_only",
                "max_slippage_bps": 50,
                "spread_check_bps": 100,
                "min_depth_bps": 20,
                "clamp_small_trades": False,
            },
            "risk": {
                "min_trade_notional_usd": 10.0,  # Policy minimum
            },
        }
    
    def test_no_adjustment_when_net_exceeds_minimum(self, mock_exchange, mock_state_store, policy):
        """Test that no adjustment occurs when net amount already exceeds minimum."""
        # Product with $5 exchange minimum
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            "status": "ONLINE",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "min_market_funds": "5.0",
        }
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        # Size $20 at price $50000
        # With maker fee (40bps): net = $20 * (1 - 0.004) = $19.92
        # Net > max(policy_min=$10, exchange_min=$5), so no adjustment needed
        result = engine.enforce_product_constraints(
            symbol="BTC-USD",
            size_usd=20.0,
            price=50000.0,
            is_maker=True
        )
        
        assert result["success"]
        assert not result["fee_adjusted"], "Should not adjust when net exceeds minimum"
        assert result["adjusted_size_usd"] == pytest.approx(20.0, rel=1e-6)
    
    def test_adjustment_when_net_below_policy_minimum(self, mock_exchange, mock_state_store, policy):
        """Test adjustment when net amount falls below policy minimum after fees."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "ETH-USD",
            "status": "ONLINE",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "min_market_funds": "1.0",
        }
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        # Size $10.30 at price $3000
        # With maker fee (40bps): net = $10.30 * (1 - 0.004) = $10.26
        # Net > policy_min=$10, but after rounding down might fall below
        # Let's say rounding brings it to $10.00
        # Net after fee: $10.00 * 0.996 = $9.96 < $10 policy minimum
        # Should bump up to ensure net >= $10
        
        result = engine.enforce_product_constraints(
            symbol="ETH-USD",
            size_usd=10.05,  # Just above minimum
            price=3000.0,
            is_maker=True
        )
        
        assert result["success"]
        # After rounding and fee adjustment, net should exceed policy minimum
        fee_rate = engine.maker_fee_bps / 10000.0
        net_after_fees = result["adjusted_size_usd"] * (1.0 - fee_rate)
        assert net_after_fees >= policy["risk"]["min_trade_notional_usd"]
    
    def test_adjustment_when_net_below_exchange_minimum(self, mock_exchange, mock_state_store, policy):
        """Test adjustment when net amount falls below exchange minimum after fees."""
        # Exchange minimum is higher than policy minimum
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "SOL-USD",
            "status": "ONLINE",
            "base_increment": "0.01",
            "quote_increment": "0.01",
            "min_market_funds": "15.0",  # Higher than policy $10
        }
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        # Size $15.50 at price $100
        # With maker fee (40bps): net = $15.50 * (1 - 0.004) = $15.44
        # After rounding to quote increment ($0.01), might become $15.00
        # Net after fee: $15.00 * 0.996 = $14.94 < $15 exchange minimum
        # Should bump up to ensure net >= $15
        
        result = engine.enforce_product_constraints(
            symbol="SOL-USD",
            size_usd=15.10,
            price=100.0,
            is_maker=True
        )
        
        assert result["success"]
        # Net should exceed exchange minimum
        fee_rate = engine.maker_fee_bps / 10000.0
        net_after_fees = result["adjusted_size_usd"] * (1.0 - fee_rate)
        exchange_min = 15.0
        assert net_after_fees >= exchange_min
        
        # Size should have been bumped up
        assert result["fee_adjusted"], "Should be marked as fee-adjusted"
    
    def test_taker_fee_adjustment(self, mock_exchange, mock_state_store, policy):
        """Test adjustment works correctly with taker fees (higher rate)."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            "status": "ONLINE",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "min_market_funds": "10.0",
        }
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        # Size $10.50 at price $50000
        # With taker fee (60bps): net = $10.50 * (1 - 0.006) = $10.44
        # After rounding, if it becomes $10.00:
        # Net after fee: $10.00 * 0.994 = $9.94 < $10 minimum
        # Should bump up more than maker would
        
        result = engine.enforce_product_constraints(
            symbol="BTC-USD",
            size_usd=10.10,
            price=50000.0,
            is_maker=False  # Taker order
        )
        
        assert result["success"]
        # Net should exceed minimum even with higher taker fee
        fee_rate = engine.taker_fee_bps / 10000.0
        net_after_fees = result["adjusted_size_usd"] * (1.0 - fee_rate)
        assert net_after_fees >= 10.0
    
    def test_respects_base_increment_when_rounding_up(self, mock_exchange, mock_state_store, policy):
        """Test that fee adjustment respects base increment when rounding up."""
        # Small base increment for precise rounding
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            "status": "ONLINE",
            "base_increment": "0.0001",  # 0.0001 BTC precision
            "quote_increment": "0.01",
            "min_market_funds": "10.0",
        }
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        result = engine.enforce_product_constraints(
            symbol="BTC-USD",
            size_usd=10.05,
            price=50000.0,  # 0.000201 BTC
            is_maker=True
        )
        
        assert result["success"]
        # Base size should be multiple of 0.0001 (allow small floating-point error)
        base_increment = 0.0001
        remainder = result["adjusted_size_base"] % base_increment
        assert remainder < 1e-6, f"Base size {result['adjusted_size_base']} not aligned to increment {base_increment}"
    
    def test_respects_quote_increment_when_rounding_up(self, mock_exchange, mock_state_store, policy):
        """Test that fee adjustment respects quote increment when rounding up."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "ETH-USD",
            "status": "ONLINE",
            "base_increment": "0.00000001",
            "quote_increment": "0.25",  # $0.25 precision
            "min_market_funds": "10.0",
        }
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        result = engine.enforce_product_constraints(
            symbol="ETH-USD",
            size_usd=10.05,
            price=3000.0,
            is_maker=True
        )
        
        assert result["success"]
        # USD size should be multiple of $0.25
        quote_increment = 0.25
        remainder = result["adjusted_size_usd"] % quote_increment
        assert remainder < 1e-6, f"USD size {result['adjusted_size_usd']} not aligned to increment {quote_increment}"
    
    def test_handles_missing_metadata_gracefully(self, mock_exchange, mock_state_store, policy):
        """Test graceful handling when metadata is unavailable."""
        mock_exchange.get_product_metadata.return_value = None
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        result = engine.enforce_product_constraints(
            symbol="UNKNOWN-USD",
            size_usd=20.0,
            price=100.0,
            is_maker=True
        )
        
        # Should fail open (return original size)
        assert result["success"]
        assert not result["fee_adjusted"]
        assert result["adjusted_size_usd"] == 20.0
        assert "warning" in result
    
    def test_handles_metadata_fetch_error(self, mock_exchange, mock_state_store, policy):
        """Test graceful handling when metadata fetch raises exception."""
        mock_exchange.get_product_metadata.side_effect = Exception("API error")
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        result = engine.enforce_product_constraints(
            symbol="BTC-USD",
            size_usd=20.0,
            price=50000.0,
            is_maker=True
        )
        
        # Should fail open
        assert result["success"]
        assert not result["fee_adjusted"]
        assert "warning" in result
        assert "API error" in result["warning"]
    
    def test_rejects_when_size_below_minimum_initially(self, mock_exchange, mock_state_store, policy):
        """Test rejection when initial size is already below minimum."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            "status": "ONLINE",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "min_market_funds": "10.0",
        }
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        # Size $5 is below $10 minimum before any rounding
        result = engine.enforce_product_constraints(
            symbol="BTC-USD",
            size_usd=5.0,
            price=50000.0,
            is_maker=True
        )
        
        assert not result["success"]
        assert "error" in result
        assert "below exchange minimum" in result["error"]
    
    def test_large_order_no_adjustment_needed(self, mock_exchange, mock_state_store, policy):
        """Test that large orders well above minimums don't get adjusted."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            "status": "ONLINE",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "min_market_funds": "10.0",
        }
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        # Large $1000 order should sail through
        result = engine.enforce_product_constraints(
            symbol="BTC-USD",
            size_usd=1000.0,
            price=50000.0,
            is_maker=True
        )
        
        assert result["success"]
        assert not result["fee_adjusted"], "Large orders shouldn't need fee adjustment"
        assert result["adjusted_size_usd"] == pytest.approx(1000.0, rel=1e-4)
    
    def test_edge_case_net_exactly_at_minimum(self, mock_exchange, mock_state_store, policy):
        """Test edge case where net amount is exactly at minimum."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            "status": "ONLINE",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "min_market_funds": "10.0",
        }
        
        engine = ExecutionEngine(
            exchange=mock_exchange,
            policy=policy,
            mode="DRY_RUN",
            state_store=mock_state_store,
        )
        
        # Calculate size that results in exactly $10 net
        # gross = net / (1 - fee_rate)
        # gross = 10 / 0.996 = 10.0402...
        gross_size = 10.0 / (1.0 - engine.maker_fee_bps / 10000.0)
        
        result = engine.enforce_product_constraints(
            symbol="BTC-USD",
            size_usd=gross_size,
            price=50000.0,
            is_maker=True
        )
        
        assert result["success"]
        # Net should be at or above minimum
        fee_rate = engine.maker_fee_bps / 10000.0
        net_after_fees = result["adjusted_size_usd"] * (1.0 - fee_rate)
        assert net_after_fees >= 10.0
