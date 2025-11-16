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


class DataLoader:
    """
    Enhanced data loader for backtesting with MockExchange integration.
    
    Supports multiple data sources:
    - API: Live fetch from Coinbase
    - CSV: Local files with OHLCV data
    - Parquet: Fast columnar format
    
    MockExchange-compatible interface:
    - get_latest_candle(symbol, time) -> Candle
    - get_candles(symbol, start, end, granularity) -> List[Candle]
    """
    
    def __init__(
        self,
        source: str = "api",
        data_dir: Optional[Path] = None,
        api_base_url: str = "https://api.exchange.coinbase.com"
    ):
        """
        Initialize data loader.
        
        Args:
            source: "api", "csv", or "parquet"
            data_dir: Directory for CSV/Parquet files
            api_base_url: Coinbase API endpoint
        """
        self.source = source
        self.data_dir = Path(data_dir) if data_dir else Path("data/backtest")
        self.api_base_url = api_base_url
        
        # In-memory cache for fast lookups
        self._cache: Dict[str, List[Candle]] = {}
        
        # API loader for live fetching
        if source == "api":
            self._api_loader = HistoricalDataLoader(api_base_url)
        else:
            self._api_loader = None
        
        logger.info(f"DataLoader initialized: source={source}, data_dir={self.data_dir}")
    
    def load_range(
        self,
        symbols: List[str],
        start: datetime,
        end: datetime,
        granularity: int = 900
    ) -> Dict[str, List[Candle]]:
        """
        Load data for symbols over date range.
        
        Args:
            symbols: List of symbols
            start: Start datetime
            end: End datetime
            granularity: Candle size in seconds
            
        Returns:
            Dict mapping symbol -> candles
        """
        if self.source == "api":
            return self._load_from_api(symbols, start, end, granularity)
        elif self.source == "csv":
            return self._load_from_csv(symbols, start, end)
        elif self.source == "parquet":
            return self._load_from_parquet(symbols, start, end)
        else:
            raise ValueError(f"Unknown source: {self.source}")
    
    def get_latest_candle(self, symbol: str, time: datetime) -> Optional[Candle]:
        """
        Get candle at or before specified time (MockExchange-compatible).
        
        Args:
            symbol: Symbol (e.g. "BTC-USD")
            time: Timestamp
            
        Returns:
            Candle at/before time, or None
        """
        # Check cache first
        if symbol not in self._cache:
            # Try to load from source
            logger.debug(f"Cache miss for {symbol}, attempting to load")
            # Load a small window around the requested time
            window_start = time - timedelta(days=1)
            window_end = time + timedelta(hours=1)
            self.load_range([symbol], window_start, window_end)
        
        candles = self._cache.get(symbol, [])
        if not candles:
            return None
        
        # Find latest candle at or before time
        # Ensure time is timezone-aware
        if time.tzinfo is None:
            time = time.replace(tzinfo=timezone.utc)
        
        latest = None
        for candle in candles:
            candle_time = candle.timestamp
            if candle_time.tzinfo is None:
                candle_time = candle_time.replace(tzinfo=timezone.utc)
            
            if candle_time <= time:
                if latest is None or candle_time > latest.timestamp:
                    latest = candle
        
        return latest
    
    def get_candles(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        granularity: str = "ONE_MINUTE"
    ) -> List[Candle]:
        """
        Get candles for symbol in range (MockExchange-compatible).
        
        Args:
            symbol: Symbol
            start: Start time
            end: End time
            granularity: "ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE", etc.
            
        Returns:
            List of candles
        """
        # Convert granularity string to seconds
        granularity_map = {
            "ONE_MINUTE": 60,
            "FIVE_MINUTE": 300,
            "FIFTEEN_MINUTE": 900,
            "ONE_HOUR": 3600,
            "SIX_HOUR": 21600,
            "ONE_DAY": 86400,
        }
        granularity_seconds = granularity_map.get(granularity, 900)
        
        # Load if not cached
        if symbol not in self._cache:
            self.load_range([symbol], start, end, granularity_seconds)
        
        candles = self._cache.get(symbol, [])
        
        # Filter to range
        # Ensure times are timezone-aware
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        
        filtered = []
        for candle in candles:
            candle_time = candle.timestamp
            if candle_time.tzinfo is None:
                candle_time = candle_time.replace(tzinfo=timezone.utc)
            
            if start <= candle_time <= end:
                filtered.append(candle)
        
        return filtered
    
    # Internal loaders
    
    def _load_from_api(
        self,
        symbols: List[str],
        start: datetime,
        end: datetime,
        granularity: int
    ) -> Dict[str, List[Candle]]:
        """Load from Coinbase API"""
        if not self._api_loader:
            raise ValueError("API loader not initialized")
        
        result = self._api_loader.load(symbols, start, end, granularity)
        
        # Update cache
        for symbol, candles in result.items():
            if symbol in self._cache:
                # Merge with existing cache
                existing = self._cache[symbol]
                all_candles = existing + candles
                # Deduplicate by timestamp
                seen = set()
                unique = []
                for c in all_candles:
                    ts = c.timestamp.isoformat()
                    if ts not in seen:
                        seen.add(ts)
                        unique.append(c)
                unique.sort(key=lambda c: c.timestamp)
                self._cache[symbol] = unique
            else:
                self._cache[symbol] = candles
        
        return result
    
    def _load_from_csv(
        self,
        symbols: List[str],
        start: datetime,
        end: datetime
    ) -> Dict[str, List[Candle]]:
        """Load from CSV files"""
        result = {}
        
        for symbol in symbols:
            csv_path = self.data_dir / f"{symbol}.csv"
            
            if not csv_path.exists():
                logger.warning(f"CSV file not found: {csv_path}")
                result[symbol] = []
                continue
            
            try:
                import csv
                candles = []
                
                with open(csv_path, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        timestamp = datetime.fromisoformat(row['timestamp'])
                        
                        # Filter by date range
                        if start <= timestamp <= end:
                            candles.append(Candle(
                                timestamp=timestamp,
                                open=float(row['open']),
                                high=float(row['high']),
                                low=float(row['low']),
                                close=float(row['close']),
                                volume=float(row['volume'])
                            ))
                
                result[symbol] = candles
                self._cache[symbol] = candles
                logger.info(f"Loaded {len(candles)} candles from {csv_path}")
                
            except Exception as e:
                logger.error(f"Error loading CSV for {symbol}: {e}")
                result[symbol] = []
        
        return result
    
    def _load_from_parquet(
        self,
        symbols: List[str],
        start: datetime,
        end: datetime
    ) -> Dict[str, List[Candle]]:
        """Load from Parquet files"""
        result = {}
        
        try:
            import pandas as pd
        except ImportError:
            logger.error("pandas not installed, cannot load Parquet files")
            return {symbol: [] for symbol in symbols}
        
        for symbol in symbols:
            parquet_path = self.data_dir / f"{symbol}.parquet"
            
            if not parquet_path.exists():
                logger.warning(f"Parquet file not found: {parquet_path}")
                result[symbol] = []
                continue
            
            try:
                df = pd.read_parquet(parquet_path)
                
                # Filter by date range
                df = df[(df['timestamp'] >= start) & (df['timestamp'] <= end)]
                
                # Convert to Candle objects
                candles = [
                    Candle(
                        timestamp=row['timestamp'].to_pydatetime(),
                        open=float(row['open']),
                        high=float(row['high']),
                        low=float(row['low']),
                        close=float(row['close']),
                        volume=float(row['volume'])
                    )
                    for _, row in df.iterrows()
                ]
                
                result[symbol] = candles
                self._cache[symbol] = candles
                logger.info(f"Loaded {len(candles)} candles from {parquet_path}")
                
            except Exception as e:
                logger.error(f"Error loading Parquet for {symbol}: {e}")
                result[symbol] = []
        
        return result
    
    def save_to_csv(self, symbol: str, candles: List[Candle]):
        """Save candles to CSV for caching"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        csv_path = self.data_dir / f"{symbol}.csv"
        
        import csv
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(
                f,
                fieldnames=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            writer.writeheader()
            for candle in candles:
                writer.writerow({
                    'timestamp': candle.timestamp.isoformat(),
                    'open': candle.open,
                    'high': candle.high,
                    'low': candle.low,
                    'close': candle.close,
                    'volume': candle.volume
                })
        
        logger.info(f"Saved {len(candles)} candles to {csv_path}")
    
    def handle_missing_data(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        granularity: int = 900
    ) -> List[Candle]:
        """
        Handle missing data with forward-fill strategy.
        
        Args:
            symbol: Symbol
            start: Start time
            end: End time
            granularity: Expected interval in seconds
            
        Returns:
            Candles with gaps filled
        """
        candles = self._cache.get(symbol, [])
        if not candles:
            return []
        
        # Sort by timestamp
        candles.sort(key=lambda c: c.timestamp)
        
        # Find gaps and forward-fill
        filled = []
        expected_time = start
        candle_idx = 0
        
        while expected_time <= end:
            # Find candle for this time
            found = None
            while candle_idx < len(candles):
                if candles[candle_idx].timestamp >= expected_time:
                    if candles[candle_idx].timestamp == expected_time:
                        found = candles[candle_idx]
                    break
                candle_idx += 1
            
            if found:
                filled.append(found)
            elif filled:
                # Forward-fill from last known candle
                last = filled[-1]
                filled.append(Candle(
                    timestamp=expected_time,
                    open=last.close,
                    high=last.close,
                    low=last.close,
                    close=last.close,
                    volume=0.0  # No volume for filled candles
                ))
            
            expected_time += timedelta(seconds=granularity)
        
        return filled


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
