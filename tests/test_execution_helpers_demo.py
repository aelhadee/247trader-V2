"""
Demo: Using new execution test helpers

Shows how to use Quote, OHLCV, and MockExchangeBuilder for cleaner tests.
Run with: pytest tests/test_execution_helpers_demo.py -v
"""

import pytest
from tests.helpers import (
    Quote,
    OHLCV,
    MockExchangeBuilder,
    create_tight_market,
    create_wide_market,
    create_stale_quote,
)


def test_quote_factory_creates_realistic_data():
    """Quote factory creates realistic bid/ask/mid"""
    quote = Quote.create(mid=50000.0, spread_bps=20.0)
    
    assert quote.mid == 50000.0
    assert quote.spread_bps == 20.0
    # 20 bps = 0.2%, half-spread = 0.1% = $50
    assert abs(quote.ask - quote.bid - 100.0) < 1.0
    assert abs(quote.mid - (quote.bid + quote.ask) / 2) < 0.01


def test_convenience_functions():
    """Convenience functions create expected scenarios"""
    tight = create_tight_market(50000.0)
    assert tight.spread_bps == 10.0
    
    wide = create_wide_market(50000.0)
    assert wide.spread_bps == 150.0
    
    stale = create_stale_quote(50000.0, age_seconds=120)
    # Quote is 2 minutes old
    from datetime import datetime, timezone, timedelta
    age = (datetime.now(timezone.utc) - stale.timestamp).total_seconds()
    assert age >= 119  # Allow 1s tolerance


def test_ohlcv_series_with_trend():
    """OHLCV.create_series() generates trending data"""
    uptrend = OHLCV.create_series(count=10, close=50000, trend_pct=1.0)
    
    # Prices should increase over time
    assert len(uptrend) == 10
    assert uptrend[0].close < uptrend[-1].close
    
    # Approximate 1% per candle = ~9% total
    total_change = (uptrend[-1].close - uptrend[0].close) / uptrend[0].close
    assert 0.08 < total_change < 0.10


def test_mock_exchange_builder_with_balances():
    """MockExchangeBuilder creates properly configured mock"""
    exchange = (
        MockExchangeBuilder()
        .with_balance("USDC", 10000.0)
        .with_balance("BTC", 0.1)
        .with_standard_products()
        .build()
    )
    
    # Check balances
    balances = exchange.get_balances()
    assert balances["USDC"] == 10000.0
    assert balances["BTC"] == 0.1
    
    # Check products
    products = exchange.get_products()
    assert len(products) == 4  # BTC/ETH × USDC/USD
    assert any(p["id"] == "BTC-USDC" for p in products)


def test_mock_exchange_with_custom_quote():
    """MockExchangeBuilder accepts custom quotes"""
    wide_spread_quote = create_wide_market(50000.0)
    
    exchange = (
        MockExchangeBuilder()
        .with_quote("BTC-USDC", wide_spread_quote)
        .with_standard_products()
        .build()
    )
    
    # Quote should return wide spread
    quote = exchange.get_quote("BTC-USDC")
    assert quote["spread_bps"] == 150.0


def test_mock_exchange_product_metadata():
    """MockExchangeBuilder provides product metadata"""
    exchange = (
        MockExchangeBuilder()
        .with_standard_products()
        .build()
    )
    
    metadata = exchange.get_product_metadata("BTC-USDC")
    assert metadata is not None
    assert metadata["product_id"] == "BTC-USDC"
    assert metadata["base_currency"] == "BTC"
    assert metadata["quote_currency"] == "USDC"
    assert metadata["status"] == "online"
