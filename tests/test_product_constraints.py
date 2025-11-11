"""
Test Coinbase product constraint enforcement.
"""
import pytest
from core.execution import ExecutionEngine
from core.exchange_coinbase import CoinbaseExchange
from unittest.mock import Mock, patch


class MockExchange:
    """Mock exchange for testing product constraints."""
    
    def __init__(self, metadata=None):
        self.metadata = metadata or {}
        self.read_only = True
    
    def get_product_metadata(self, product_id):
        return self.metadata.get(product_id, {})


def test_round_to_increment():
    """Test rounding to specific increments."""
    
    policy = {
        "execution": {},
        "risk": {"min_trade_notional_usd": 10.0}
    }
    
    engine = ExecutionEngine(mode="DRY_RUN", policy=policy)
    
    # Test BTC-style increment (0.00000001)
    btc_increment = 0.00000001
    assert engine.round_to_increment(0.123456789, btc_increment) == 0.12345678
    assert engine.round_to_increment(1.0, btc_increment) == 1.0
    assert engine.round_to_increment(0.00000001, btc_increment) == 0.00000001
    assert engine.round_to_increment(0.000000009, btc_increment) == 0.0  # Rounds down
    
    # Test USD-style increment (0.01)
    usd_increment = 0.01
    assert abs(engine.round_to_increment(10.456, usd_increment) - 10.45) < 0.001
    assert abs(engine.round_to_increment(10.454, usd_increment) - 10.45) < 0.001
    assert abs(engine.round_to_increment(10.001, usd_increment) - 10.00) < 0.001
    
    # Test larger increment (0.1)
    assert engine.round_to_increment(10.56, 0.1) == 10.5
    assert engine.round_to_increment(10.99, 0.1) == 10.9
    
    print("✅ Round to increment test passed")


def test_enforce_product_constraints_btc():
    """Test product constraints for BTC-USD (small base increment, large price)."""
    
    policy = {
        "execution": {},
        "risk": {"min_trade_notional_usd": 10.0}
    }
    
    # Mock BTC-USD metadata
    btc_metadata = {
        "BTC-USD": {
            "product_id": "BTC-USD",
            "base_increment": "0.00000001",  # 1 satoshi
            "quote_increment": "0.01",  # 1 cent
            "min_market_funds": "1.00"
        }
    }
    
    exchange = MockExchange(metadata=btc_metadata)
    engine = ExecutionEngine(mode="DRY_RUN", exchange=exchange, policy=policy)
    
    # Test: $1000 order at $50,000/BTC = 0.02 BTC
    result = engine.enforce_product_constraints("BTC-USD", 1000.0, 50000.0)
    
    assert result["success"] is True
    assert result["adjusted_size_base"] == 0.02  # Exact 0.02 BTC
    assert abs(result["adjusted_size_usd"] - 1000.0) < 0.01  # ~$1000
    
    # Test: Small order that rounds to zero base
    result = engine.enforce_product_constraints("BTC-USD", 0.0001, 50000.0)
    assert result["success"] is False
    assert "minimum" in result.get("error", "").lower() or "too small" in result.get("error", "").lower()
    
    print("✅ BTC product constraints test passed")


def test_enforce_product_constraints_shib():
    """Test product constraints for SHIB-USD (large base increment, small price)."""
    
    policy = {
        "execution": {},
        "risk": {"min_trade_notional_usd": 10.0}
    }
    
    # Mock SHIB-USD metadata (example: price $0.00001, increment 1 SHIB)
    shib_metadata = {
        "SHIB-USD": {
            "product_id": "SHIB-USD",
            "base_increment": "1",  # 1 SHIB minimum
            "quote_increment": "0.01",  # 1 cent
            "min_market_funds": "1.00"
        }
    }
    
    exchange = MockExchange(metadata=shib_metadata)
    engine = ExecutionEngine(mode="DRY_RUN", exchange=exchange, policy=policy)
    
    # Test: $100 order at $0.00001/SHIB = 10,000,000 SHIB
    result = engine.enforce_product_constraints("SHIB-USD", 100.0, 0.00001)
    
    assert result["success"] is True
    assert result["adjusted_size_base"] == 10000000.0  # 10M SHIB
    assert abs(result["adjusted_size_usd"] - 100.0) < 0.01
    
    # Test: Order that requires rounding
    # $99.999 at $0.00001 = 9,999,900 SHIB → rounds to 9,999,899 or 9,999,900 SHIB (fp precision)
    result = engine.enforce_product_constraints("SHIB-USD", 99.999, 0.00001)
    assert result["success"] is True
    assert 9999890.0 <= result["adjusted_size_base"] <= 9999910.0  # Allow fp precision variance
    
    print("✅ SHIB product constraints test passed")


def test_minimum_market_funds():
    """Test minimum market funds constraint."""
    
    policy = {
        "execution": {},
        "risk": {"min_trade_notional_usd": 5.0}
    }
    
    # Mock product with $10 minimum
    metadata = {
        "ETH-USD": {
            "product_id": "ETH-USD",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "min_market_funds": "10.00"
        }
    }
    
    exchange = MockExchange(metadata=metadata)
    engine = ExecutionEngine(mode="DRY_RUN", exchange=exchange, policy=policy)
    
    # Test: Order below exchange minimum (even though above our policy min)
    result = engine.enforce_product_constraints("ETH-USD", 8.0, 2000.0)
    assert result["success"] is False
    assert "below exchange minimum" in result.get("error", "").lower()
    
    # Test: Order above exchange minimum
    result = engine.enforce_product_constraints("ETH-USD", 12.0, 2000.0)
    assert result["success"] is True
    assert result["adjusted_size_usd"] >= 10.0
    
    print("✅ Minimum market funds test passed")


def test_missing_metadata():
    """Test graceful handling when product metadata is unavailable."""
    
    policy = {
        "execution": {},
        "risk": {"min_trade_notional_usd": 10.0}
    }
    
    # Mock exchange with no metadata
    exchange = MockExchange(metadata={})
    engine = ExecutionEngine(mode="DRY_RUN", exchange=exchange, policy=policy)
    
    # Should fail open (allow order) with warning
    result = engine.enforce_product_constraints("UNKNOWN-USD", 100.0, 1.0)
    assert result["success"] is True  # Fail open
    assert "warning" in result or "metadata" in str(result)
    
    print("✅ Missing metadata test passed")


def test_quote_increment_rounding():
    """Test that quote (USD) side is rounded to increment."""
    
    policy = {
        "execution": {},
        "risk": {"min_trade_notional_usd": 10.0}
    }
    
    # Metadata with 0.01 quote increment (1 cent)
    metadata = {
        "ETH-USD": {
            "product_id": "ETH-USD",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "min_market_funds": "1.00"
        }
    }
    
    exchange = MockExchange(metadata=metadata)
    engine = ExecutionEngine(mode="DRY_RUN", exchange=exchange, policy=policy)
    
    # $100.456 → should round to $100.45
    result = engine.enforce_product_constraints("ETH-USD", 100.456, 2000.0)
    assert result["success"] is True
    # After rounding base, then recalculating USD, then rounding quote
    # Should be close to $100.45 or similar
    assert result["adjusted_size_usd"] < 100.46
    
    print("✅ Quote increment rounding test passed")


def test_very_small_price():
    """Test handling of very small prices that might cause precision issues."""
    
    policy = {
        "execution": {},
        "risk": {"min_trade_notional_usd": 10.0}
    }
    
    metadata = {
        "MICRO-USD": {
            "product_id": "MICRO-USD",
            "base_increment": "1",
            "quote_increment": "0.01",
            "min_market_funds": "1.00"
        }
    }
    
    exchange = MockExchange(metadata=metadata)
    engine = ExecutionEngine(mode="DRY_RUN", exchange=exchange, policy=policy)
    
    # $10 at $0.0000001 price = 100,000,000 tokens
    result = engine.enforce_product_constraints("MICRO-USD", 10.0, 0.0000001)
    assert result["success"] is True
    assert result["adjusted_size_base"] > 0
    
    print("✅ Very small price test passed")


if __name__ == "__main__":
    test_round_to_increment()
    test_enforce_product_constraints_btc()
    test_enforce_product_constraints_shib()
    test_minimum_market_funds()
    test_missing_metadata()
    test_quote_increment_rounding()
    test_very_small_price()
    print("\n✅ All product constraint tests passed!")
