"""
Test helpers for execution engine tests.

Provides realistic Quote and OHLCV dataclass stubs that mirror production data structures.
Use these instead of raw dicts to ensure type safety and catch API contract changes.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional


@dataclass
class Quote:
    """Realistic quote stub matching CoinbaseExchange.get_quote() response"""
    bid: float
    ask: float
    mid: float
    timestamp: datetime
    spread_bps: float
    last_trade_price: Optional[float] = None
    volume_24h: Optional[float] = None
    
    @classmethod
    def create(
        cls,
        mid: float = 50000.0,
        spread_bps: float = 20.0,
        age_seconds: int = 0,
        volume_24h: Optional[float] = None
    ) -> "Quote":
        """
        Factory for creating realistic quotes.
        
        Args:
            mid: Mid price
            spread_bps: Spread in basis points (20 bps = 0.2%)
            age_seconds: How old the quote is (for staleness tests)
            volume_24h: Optional 24h volume
            
        Returns:
            Quote instance
            
        Example:
            >>> fresh_quote = Quote.create(mid=50000, spread_bps=20)
            >>> stale_quote = Quote.create(mid=50000, age_seconds=120)
            >>> wide_spread = Quote.create(mid=50000, spread_bps=150)
        """
        spread_fraction = spread_bps / 10000.0
        half_spread = mid * spread_fraction / 2
        
        return cls(
            bid=mid - half_spread,
            ask=mid + half_spread,
            mid=mid,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
            spread_bps=spread_bps,
            last_trade_price=mid,
            volume_24h=volume_24h or mid * 1000  # Reasonable default volume
        )


@dataclass
class OHLCV:
    """Realistic OHLCV candle stub matching CoinbaseExchange.get_ohlcv() response"""
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime
    vwap: Optional[float] = None
    
    @classmethod
    def create(
        cls,
        close: float = 50000.0,
        volatility_pct: float = 2.0,
        volume_multiple: float = 1.0,
        age_seconds: int = 0
    ) -> "OHLCV":
        """
        Factory for creating realistic OHLCV candles.
        
        Args:
            close: Closing price
            volatility_pct: Intrabar volatility as % of close
            volume_multiple: Multiplier for base volume
            age_seconds: How old the candle is
            
        Returns:
            OHLCV instance
            
        Example:
            >>> normal_candle = OHLCV.create(close=50000, volatility_pct=2.0)
            >>> volatile_candle = OHLCV.create(close=50000, volatility_pct=10.0)
            >>> low_volume = OHLCV.create(close=50000, volume_multiple=0.1)
        """
        range_size = close * (volatility_pct / 100.0)
        half_range = range_size / 2
        
        high = close + half_range
        low = close - half_range
        open_price = close * (1 + (volatility_pct / 200.0))  # Slight upward bias
        
        base_volume = close * 100 * volume_multiple
        vwap = (high + low + close * 2) / 4  # Weighted average approximation
        
        return cls(
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=base_volume,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
            vwap=vwap
        )
    
    @classmethod
    def create_series(
        cls,
        count: int = 20,
        close: float = 50000.0,
        trend_pct: float = 0.0,
        volatility_pct: float = 2.0
    ) -> List["OHLCV"]:
        """
        Create a series of OHLCV candles with optional trend.
        
        Args:
            count: Number of candles
            close: Starting close price
            trend_pct: Price change per candle as % (positive = uptrend)
            volatility_pct: Intrabar volatility
            
        Returns:
            List of OHLCV instances in chronological order (oldest first)
            
        Example:
            >>> uptrend = OHLCV.create_series(count=20, close=50000, trend_pct=0.5)
            >>> downtrend = OHLCV.create_series(count=20, close=50000, trend_pct=-0.5)
            >>> sideways = OHLCV.create_series(count=20, close=50000, trend_pct=0.0)
        """
        candles = []
        current_price = close
        
        for i in range(count):
            # Age in reverse (oldest first)
            age_seconds = (count - i) * 60  # 1-minute candles
            
            candle = cls.create(
                close=current_price,
                volatility_pct=volatility_pct,
                age_seconds=age_seconds
            )
            candles.append(candle)
            
            # Apply trend for next candle
            current_price *= (1 + trend_pct / 100.0)
        
        return candles


@dataclass
class Balance:
    """Account balance stub"""
    currency: str
    available: float
    hold: float = 0.0
    
    @property
    def total(self) -> float:
        return self.available + self.hold


@dataclass
class ProductMetadata:
    """Product metadata stub matching Coinbase product spec"""
    product_id: str
    base_currency: str
    quote_currency: str
    base_increment: str  # e.g., "0.00000001"
    quote_increment: str  # e.g., "0.01"
    base_min_size: str
    base_max_size: str
    min_market_funds: str
    max_market_funds: str
    status: str = "online"
    trading_disabled: bool = False
    post_only: bool = False
    limit_only: bool = False
    cancel_only: bool = False
    
    @classmethod
    def create_standard(
        cls,
        base: str = "BTC",
        quote: str = "USDC",
        status: str = "online"
    ) -> "ProductMetadata":
        """Create standard product metadata for common pairs"""
        return cls(
            product_id=f"{base}-{quote}",
            base_currency=base,
            quote_currency=quote,
            base_increment="0.00000001",
            quote_increment="0.01",
            base_min_size="0.0001",
            base_max_size="10000.0",
            min_market_funds="10.00",
            max_market_funds="1000000.00",
            status=status
        )


@dataclass
class OrderResponse:
    """Order placement response stub"""
    order_id: str
    client_order_id: str
    product_id: str
    side: str  # BUY or SELL
    order_type: str  # limit, market
    size: str
    price: Optional[str] = None
    status: str = "OPEN"
    filled_size: str = "0"
    filled_value: str = "0.00"
    average_filled_price: str = "0.00"
    created_at: Optional[datetime] = None
    
    @classmethod
    def create_filled(
        cls,
        order_id: str = "test_order_123",
        product_id: str = "BTC-USDC",
        side: str = "BUY",
        size: float = 0.001,
        price: float = 50000.0,
        client_order_id: Optional[str] = None
    ) -> "OrderResponse":
        """Create a fully filled order response"""
        filled_value = size * price
        
        return cls(
            order_id=order_id,
            client_order_id=client_order_id or f"client_{order_id}",
            product_id=product_id,
            side=side,
            order_type="limit",
            size=str(size),
            price=str(price),
            status="FILLED",
            filled_size=str(size),
            filled_value=f"{filled_value:.2f}",
            average_filled_price=str(price),
            created_at=datetime.now(timezone.utc)
        )
    
    @classmethod
    def create_partial(
        cls,
        order_id: str = "test_order_123",
        product_id: str = "BTC-USDC",
        size: float = 0.001,
        filled_pct: float = 50.0,
        price: float = 50000.0
    ) -> "OrderResponse":
        """Create a partially filled order response"""
        filled_size = size * (filled_pct / 100.0)
        filled_value = filled_size * price
        
        return cls(
            order_id=order_id,
            client_order_id=f"client_{order_id}",
            product_id=product_id,
            side="BUY",
            order_type="limit",
            size=str(size),
            price=str(price),
            status="OPEN",
            filled_size=str(filled_size),
            filled_value=f"{filled_value:.2f}",
            average_filled_price=str(price),
            created_at=datetime.now(timezone.utc)
        )


class MockExchangeBuilder:
    """Builder for creating realistic mock exchanges with proper state"""
    
    def __init__(self):
        self.balances: Dict[str, float] = {
            "USDC": 10000.0,
            "USD": 5000.0,
            "BTC": 0.0,
            "ETH": 0.0
        }
        self.quotes: Dict[str, Quote] = {}
        self.products: List[ProductMetadata] = []
        self.read_only = False
        
    def with_balance(self, currency: str, amount: float) -> "MockExchangeBuilder":
        """Set balance for a currency"""
        self.balances[currency] = amount
        return self
    
    def with_quote(self, symbol: str, quote: Quote) -> "MockExchangeBuilder":
        """Add a quote for a symbol"""
        self.quotes[symbol] = quote
        return self
    
    def with_standard_products(self) -> "MockExchangeBuilder":
        """Add standard BTC/ETH products with USDC and USD quotes"""
        self.products = [
            ProductMetadata.create_standard("BTC", "USDC"),
            ProductMetadata.create_standard("BTC", "USD"),
            ProductMetadata.create_standard("ETH", "USDC"),
            ProductMetadata.create_standard("ETH", "USD"),
        ]
        return self
    
    def with_product(self, metadata: ProductMetadata) -> "MockExchangeBuilder":
        """Add a custom product"""
        self.products.append(metadata)
        return self
    
    def build(self):
        """Build mock exchange with configured state"""
        from unittest.mock import MagicMock
        
        exchange = MagicMock()
        exchange.read_only = self.read_only
        exchange.min_notional_usd = 1.0
        
        # Set up balance responses
        exchange.get_balances.return_value = self.balances.copy()
        
        # Set up quote responses
        def get_quote_side_effect(symbol):
            if symbol in self.quotes:
                quote = self.quotes[symbol]
                return {
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "mid": quote.mid,
                    "timestamp": quote.timestamp,
                    "spread_bps": quote.spread_bps
                }
            # Default quote
            return Quote.create().__dict__
        
        exchange.get_quote.side_effect = get_quote_side_effect
        
        # Set up product responses
        exchange.get_products.return_value = [
            {
                "id": p.product_id,
                "base_currency": p.base_currency,
                "quote_currency": p.quote_currency,
                "status": p.status
            }
            for p in self.products
        ]
        
        # Set up product metadata responses
        def get_product_metadata_side_effect(product_id):
            for p in self.products:
                if p.product_id == product_id:
                    return {
                        "product_id": p.product_id,
                        "base_currency": p.base_currency,
                        "quote_currency": p.quote_currency,
                        "base_increment": p.base_increment,
                        "quote_increment": p.quote_increment,
                        "base_min_size": p.base_min_size,
                        "base_max_size": p.base_max_size,
                        "min_market_funds": p.min_market_funds,
                        "max_market_funds": p.max_market_funds,
                        "status": p.status,
                        "trading_disabled": p.trading_disabled,
                        "post_only": p.post_only,
                        "limit_only": p.limit_only,
                        "cancel_only": p.cancel_only
                    }
            return None
        
        exchange.get_product_metadata.side_effect = get_product_metadata_side_effect
        
        # Default order placement (can be overridden in tests)
        exchange.place_limit_order.return_value = OrderResponse.create_filled().__dict__
        exchange.place_market_order.return_value = OrderResponse.create_filled().__dict__
        
        return exchange


# Convenience functions for common test scenarios

def create_tight_market(mid: float = 50000.0) -> Quote:
    """Create a quote with tight spread (good for execution)"""
    return Quote.create(mid=mid, spread_bps=10.0)


def create_wide_market(mid: float = 50000.0) -> Quote:
    """Create a quote with wide spread (should trigger slippage check)"""
    return Quote.create(mid=mid, spread_bps=150.0)


def create_stale_quote(mid: float = 50000.0, age_seconds: int = 120) -> Quote:
    """Create a stale quote (should be rejected)"""
    return Quote.create(mid=mid, age_seconds=age_seconds)


def create_volatile_candles(count: int = 20) -> List[OHLCV]:
    """Create highly volatile OHLCV series"""
    return OHLCV.create_series(count=count, volatility_pct=10.0)


def create_trending_candles(count: int = 20, trend: str = "up") -> List[OHLCV]:
    """Create trending OHLCV series"""
    trend_pct = 2.0 if trend == "up" else -2.0
    return OHLCV.create_series(count=count, trend_pct=trend_pct)
