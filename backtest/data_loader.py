"""
247trader-v2 Backtest: Data Loader

Fetch historical OHLCV data for backtesting from multiple sources:
- Coinbase public API (live fetch, no auth)
- CSV files (local cache)
- Parquet files (fast columnar storage)

Compatible with MockExchange for realistic backtesting.
"""

import time
import requests
import json
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class Candle:
    """OHLCV candle"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class HistoricalDataLoader:
    """
    Load historical OHLCV data from Coinbase public API.
    
    API endpoint: GET /products/{product_id}/candles
    Docs: https://docs.cloud.coinbase.com/exchange/reference/exchangerestapi_getproductcandles
    """
    
    def __init__(self, base_url: str = "https://api.exchange.coinbase.com"):
        self.base_url = base_url
        self._cache: Dict[str, List[Candle]] = {}
        
    def load(self, 
             symbols: List[str],
             start: datetime,
             end: datetime,
             granularity: int = 900) -> Dict[str, List[Candle]]:
        """
        Load historical candles for multiple symbols.
        
        Args:
            symbols: List of symbols (e.g. ["BTC-USD", "ETH-USD"])
            start: Start datetime
            end: End datetime
            granularity: Candle size in seconds (60, 300, 900, 3600, 21600, 86400)
            
        Returns:
            Dict mapping symbol -> list of candles
        """
        logger.info(
            f"Loading historical data for {len(symbols)} symbols: "
            f"{start.isoformat()} to {end.isoformat()} (granularity={granularity}s)"
        )
        
        result = {}
        
        for symbol in symbols:
            try:
                candles = self._load_symbol(symbol, start, end, granularity)
                result[symbol] = candles
                logger.info(f"Loaded {len(candles)} candles for {symbol}")
                
                # Rate limiting
                time.sleep(0.2)  # 5 req/sec limit
                
            except Exception as e:
                logger.error(f"Failed to load {symbol}: {e}")
                result[symbol] = []
        
        return result
    
    def _load_symbol(self,
                     symbol: str,
                     start: datetime,
                     end: datetime,
                     granularity: int) -> List[Candle]:
        """Load candles for one symbol"""
        
        # Check cache
        cache_key = f"{symbol}_{start.isoformat()}_{end.isoformat()}_{granularity}"
        if cache_key in self._cache:
            logger.debug(f"Using cached data for {symbol}")
            return self._cache[cache_key]
        
        # Coinbase API has a max of 300 candles per request
        # Need to paginate for longer date ranges
        all_candles = []
        current_start = start
        
        while current_start < end:
            # Calculate chunk end (max 300 candles)
            max_duration = timedelta(seconds=granularity * 300)
            chunk_end = min(current_start + max_duration, end)
            
            # Fetch chunk
            candles = self._fetch_candles(symbol, current_start, chunk_end, granularity)
            all_candles.extend(candles)
            
            # Move to next chunk
            current_start = chunk_end
            
            # Rate limiting between chunks
            if current_start < end:
                time.sleep(0.2)
        
        # Sort by timestamp (ascending)
        all_candles.sort(key=lambda c: c.timestamp)
        
        # Cache result
        self._cache[cache_key] = all_candles
        
        return all_candles
    
    def _fetch_candles(self,
                      symbol: str,
                      start: datetime,
                      end: datetime,
                      granularity: int) -> List[Candle]:
        """Fetch one chunk of candles from API"""
        
        url = f"{self.base_url}/products/{symbol}/candles"
        
        params = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "granularity": granularity
        }
        
        logger.debug(f"Fetching {symbol} candles: {start} to {end}")
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # Parse response
        # Format: [[timestamp, low, high, open, close, volume], ...]
        candles = []
        for row in data:
            if len(row) != 6:
                logger.warning(f"Invalid candle data: {row}")
                continue
            
            timestamp, low, high, open_price, close, volume = row
            
            candles.append(Candle(
                timestamp=datetime.utcfromtimestamp(timestamp),
                open=float(open_price),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume)
            ))
        
        return candles
    
    def get_price_at(self, symbol: str, timestamp: datetime, 
                     candles: Optional[List[Candle]] = None) -> Optional[float]:
        """
        Get price at specific timestamp.
        
        Args:
            symbol: Symbol
            timestamp: Timestamp
            candles: Pre-loaded candles (or None to use cache)
            
        Returns:
            Close price at timestamp (or None if not found)
        """
        if candles is None:
            # Try to find in cache
            for key, cached_candles in self._cache.items():
                if key.startswith(symbol):
                    candles = cached_candles
                    break
        
        if not candles:
            return None
        
        # Find closest candle
        closest = None
        min_diff = float('inf')
        
        for candle in candles:
            diff = abs((candle.timestamp - timestamp).total_seconds())
            if diff < min_diff:
                min_diff = diff
                closest = candle
        
        return closest.close if closest else None


def test_loader():
    """Test the data loader"""
    loader = HistoricalDataLoader()
    
    # Load 1 day of BTC data at 15-minute intervals
    start = datetime(2024, 11, 1)
    end = datetime(2024, 11, 2)
    
    data = loader.load(
        symbols=["BTC-USD", "ETH-USD"],
        start=start,
        end=end,
        granularity=900  # 15 minutes
    )
    
    for symbol, candles in data.items():
        if candles:
            print(f"\n{symbol}: {len(candles)} candles")
            print(f"  First: {candles[0].timestamp} | O:{candles[0].open:.2f} C:{candles[0].close:.2f}")
            print(f"  Last:  {candles[-1].timestamp} | O:{candles[-1].open:.2f} C:{candles[-1].close:.2f}")
        else:
            print(f"\n{symbol}: No data")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_loader()
