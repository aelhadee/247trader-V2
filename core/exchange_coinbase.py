"""
247trader-v2 Core: Exchange Connector (Coinbase)

Real Coinbase Advanced Trade API integration.
Ported from v1 with HMAC authentication and full market data + execution support.
"""

import os
import time
import json
import hashlib
import hmac
import uuid
import secrets
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import requests

try:
    import jwt
    from cryptography.hazmat.primitives import serialization
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False

logger = logging.getLogger(__name__)

CB_BASE = "https://api.coinbase.com/api/v3/brokerage"


@dataclass
class Quote:
    """Real-time quote data"""
    symbol: str
    bid: float
    ask: float
    mid: float
    spread_bps: float
    last: float
    volume_24h: float
    timestamp: datetime
    
    @property
    def spread_pct(self) -> float:
        return self.spread_bps / 10000.0


@dataclass
class OrderbookSnapshot:
    """Orderbook depth snapshot"""
    symbol: str
    bid_depth_usd: float
    ask_depth_usd: float
    total_depth_usd: float
    bid_levels: int
    ask_levels: int
    timestamp: datetime


@dataclass
class OHLCV:
    """Candlestick data"""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class CoinbaseExchange:
    """
    Coinbase Advanced Trade API connector with HMAC authentication.
    
    Supports:
    - Market data (products, quotes, orderbooks, candles)
    - Account data (balances, positions)
    - Order execution (preview, place, cancel)
    """
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None,
                 read_only: bool = True):
        # Load credentials from JSON file if available
        secret_file = os.getenv("CB_API_SECRET_FILE")
        if secret_file and os.path.exists(secret_file) and not api_key:
            try:
                with open(secret_file, 'r') as f:
                    creds = json.load(f)
                    api_key = creds.get("name")
                    api_secret = creds.get("privateKey", "").replace("\\n", "\n")
                    logger.info(f"Loaded Coinbase credentials from {secret_file}")
            except Exception as e:
                logger.warning(f"Failed to load credentials from {secret_file}: {e}")
        
        self.api_key = api_key or os.getenv("COINBASE_API_KEY", "")
        secret_raw = (api_secret or os.getenv("COINBASE_API_SECRET", "")).replace("\\n", "\n").strip()
        self.api_secret = secret_raw
        self.read_only = read_only
        
        # Authentication mode
        self._mode = "hmac"
        self._pem = None
        
        # Detect PEM key (for org/cloud keys - not yet supported)
        if secret_raw.startswith("-----BEGIN"):
            self._pem = secret_raw
            self._mode = "pem"
            logger.warning("PEM key detected but not supported. Use HMAC keys for now.")
        
        # Rate limiting
        self._last_call = {}
        self._min_interval = 0.1  # 100ms between calls
        
        # Cache for products
        self._products_cache = None
        self._products_cache_time = None
        
        logger.info(f"Initialized CoinbaseExchange (read_only={read_only}, mode={self._mode})")
    
    def _rate_limit(self, endpoint: str):
        """Simple rate limiting"""
        last = self._last_call.get(endpoint, 0)
        elapsed = time.time() - last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call[endpoint] = time.time()
    
    def _build_jwt(self, method: str, path: str) -> str:
        """Build JWT token for Cloud API authentication (ES256)"""
        if not JWT_AVAILABLE:
            raise ImportError("PyJWT and cryptography required for Cloud API. Run: pip install PyJWT cryptography")
        
        if not self.api_key or not self._pem:
            raise ValueError("API key and private key required for JWT authentication")
        
        try:
            private_key_bytes = self._pem.encode("utf-8")
            private_key = serialization.load_pem_private_key(private_key_bytes, password=None)
        except Exception as e:
            raise ValueError(f"Failed to load private key: {e}")
        
        # Format URI for JWT: "METHOD api.coinbase.com/api/v3/brokerage/endpoint"
        uri = f"{method.upper()} api.coinbase.com{path}"
        
        jwt_data = {
            "sub": self.api_key,
            "iss": "cdp",  # Coinbase Developer Platform
            "nbf": int(time.time()),
            "exp": int(time.time()) + 120,  # 2 minute expiry
            "uri": uri,
        }
        
        jwt_token = jwt.encode(
            jwt_data,
            private_key,
            algorithm="ES256",
            headers={"kid": self.api_key, "nonce": secrets.token_hex()},
        )
        
        return jwt_token
    
    def _headers(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Generate signed headers for authenticated requests"""
        headers = {"Content-Type": "application/json"}
        
        if self._mode == "hmac":
            # Legacy HMAC authentication (retail keys)
            if not self.api_key or not self.api_secret:
                raise ValueError("COINBASE_API_KEY and COINBASE_API_SECRET required for authenticated requests")
            
            ts = str(int(time.time()))
            body_str = json.dumps(body) if body else ""
            prehash = ts + method.upper() + path + body_str
            
            sig = hmac.new(
                self.api_secret.encode(),
                prehash.encode(),
                hashlib.sha256
            ).hexdigest()
            
            headers.update({
                "CB-ACCESS-KEY": self.api_key,
                "CB-ACCESS-SIGN": sig,
                "CB-ACCESS-TIMESTAMP": ts,
            })
            return headers
        
        elif self._mode == "pem":
            # Cloud API JWT authentication (organization keys)
            jwt_token = self._build_jwt(method, path)
            headers.update({
                "Authorization": f"Bearer {jwt_token}",
            })
            return headers
        
        else:
            raise NotImplementedError(f"Unknown authentication mode: {self._mode}")
    
    def _req(self, method: str, endpoint: str, body: Optional[dict] = None, 
             authenticated: bool = True, max_retries: int = 3) -> dict:
        """
        Make HTTP request to Coinbase API with exponential backoff.
        
        Retries on:
        - 429 (rate limit)
        - 5xx (server errors)
        - Network errors (timeout, connection)
        
        Does NOT retry on:
        - 4xx (except 429) - client errors like 400, 401, 403
        """
        path = f"/api/v3/brokerage{endpoint}"
        url = CB_BASE + endpoint
        
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                if authenticated:
                    # For JWT, strip query params from path (they're not included in the signature)
                    path_for_auth = path.split('?')[0] if '?' in path else path
                    headers = self._headers(method, path_for_auth, body)
                else:
                    headers = {"Content-Type": "application/json"}
                
                response = requests.request(
                    method,
                    url,
                    headers=headers,
                    json=body,
                    timeout=20
                )
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code
                
                # Don't retry on client errors (except 429)
                if 400 <= status_code < 500 and status_code != 429:
                    logger.error(f"Coinbase API client error: {status_code} - {e.response.text}")
                    raise
                
                # Retry on 429 (rate limit) or 5xx (server errors)
                if status_code == 429:
                    logger.warning(f"Rate limited (429) on {endpoint}, attempt {attempt + 1}/{max_retries}")
                elif status_code >= 500:
                    logger.warning(f"Server error ({status_code}) on {endpoint}, attempt {attempt + 1}/{max_retries}")
                
                last_exception = e
                
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                logger.warning(f"Network error on {endpoint}: {e}, attempt {attempt + 1}/{max_retries}")
                last_exception = e
                
            except Exception as e:
                logger.error(f"Request failed: {e}")
                raise
            
            # Exponential backoff with jitter if not last attempt
            if attempt < max_retries - 1:
                import random
                backoff = (2 ** attempt) + random.uniform(0, 1)  # 1-2s, 2-3s, 4-5s
                logger.info(f"Retrying in {backoff:.1f}s...")
                time.sleep(backoff)
        
        # All retries exhausted
        logger.error(f"All {max_retries} retries exhausted for {endpoint}")
        if last_exception:
            raise last_exception
        else:
            raise Exception(f"Request to {endpoint} failed after {max_retries} attempts")
    
    def get_quote(self, symbol: str) -> Quote:
        """
        Get real-time quote for symbol using Coinbase public ticker endpoint.
        Falls back to product info if ticker data is incomplete.

        Returns a Quote with bid/ask/mid/spread and 24h volume.
        """
        self._rate_limit("quote")
        logger.debug(f"Fetching quote for {symbol}")

        best_bid = best_ask = last = 0.0
        volume_24h = 0.0

        # 1) Try public ticker for best bid/ask and last trade
        try:
            url = f"https://api.coinbase.com/api/v3/brokerage/market/products/{symbol}/ticker"
            r = requests.get(url, params={"limit": 1}, timeout=8)
            r.raise_for_status()
            data = r.json() or {}

            # Normalize possible key names
            bb = data.get("best_bid") or data.get("best_bid_price") or data.get("bid")
            ba = data.get("best_ask") or data.get("best_ask_price") or data.get("ask")
            lp = data.get("price") or data.get("last") or data.get("last_price")
            vol = data.get("volume_24h") or data.get("quote_volume_24h")

            if bb: best_bid = float(bb)
            if ba: best_ask = float(ba)
            if lp: last = float(lp)
            if vol: volume_24h = float(vol)
        except Exception as e:
            logger.warning(f"ticker fetch failed for {symbol}: {e}")

        # 2) If volume or prices missing, load product as fallback
        if not volume_24h or not (best_bid and best_ask):
            try:
                products = self.get_products([symbol])
                if products:
                    p = products[0]
                    if not last:
                        last = float(p.get("price") or 0)
                    if not volume_24h:
                        volume_24h = float(p.get("volume_24h") or 0)
            except Exception as e:
                logger.warning(f"product fallback failed for {symbol}: {e}")

        # 3) Derive missing bid/ask from last if necessary
        if (not best_bid or not best_ask) and last > 0:
            # Assume a tight spread if we lack explicit bid/ask
            assumed_spread_bps = 6.0
            best_bid = best_bid or last * (1 - assumed_spread_bps / 20000)
            best_ask = best_ask or last * (1 + assumed_spread_bps / 20000)

        if best_bid <= 0 or best_ask <= 0:
            raise ValueError(f"No liquidity for {symbol}: bid={best_bid}, ask={best_ask}, last={last}")

        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 10000.0 if mid > 0 else 0.0

        return Quote(
            symbol=symbol,
            bid=best_bid,
            ask=best_ask,
            mid=mid,
            spread_bps=spread_bps,
            last=last or mid,
            volume_24h=volume_24h,
            timestamp=datetime.now(timezone.utc)
        )
    
    def get_orderbook(self, symbol: str, depth_levels: int = 50) -> OrderbookSnapshot:
        """
        Get orderbook depth snapshot using Coinbase market book if available.
        Falls back to heuristic based on 24h volume when book is unavailable.
        
        Depth is computed within ±20bps of mid to match policy "min_depth_20bps_usd".
        
        Args:
            symbol: e.g. "BTC-USD"
            depth_levels: Number of levels to fetch (best-effort; 50-100 typical)
            
        Returns:
            OrderbookSnapshot with depth metrics
        """
        self._rate_limit("orderbook")
        logger.debug(f"Fetching orderbook for {symbol}")

        try:
            # Try Coinbase Advanced Trade market product book endpoint
            # API: /api/v3/brokerage/market/product_book?product_id=BTC-USD&limit=100
            url = "https://api.coinbase.com/api/v3/brokerage/market/product_book"
            params = {"product_id": symbol, "limit": max(1, min(depth_levels, 100))}
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json() or {}

            # Response may wrap levels under a 'pricebook' object
            pricebook = data.get("pricebook") or {}
            bids = pricebook.get("bids") if pricebook else data.get("bids") or []
            asks = pricebook.get("asks") if pricebook else data.get("asks") or []

            if not bids or not asks:
                raise ValueError("Empty book")

            # Prices and sizes may be strings; normalize
            def _norm(side):
                out = []
                for lvl in side:
                    p = float(lvl.get("price") or lvl.get("px") or 0)
                    s = float(lvl.get("size") or lvl.get("qty") or 0)
                    out.append((p, s))
                return out

            bids_n = _norm(bids)
            asks_n = _norm(asks)

            best_bid = bids_n[0][0]
            best_ask = asks_n[0][0]
            if best_bid <= 0 or best_ask <= 0:
                raise ValueError("Invalid top of book")

            mid = (best_bid + best_ask) / 2.0
            band = 0.002  # 20 bps
            max_buy_px = mid * (1 + band)
            min_sell_px = mid * (1 - band)

            # Sum USD depth within the price band (price * base_size)
            bid_depth_usd = 0.0
            for px, sz in bids_n:
                if px < min_sell_px:
                    break
                bid_depth_usd += px * sz

            ask_depth_usd = 0.0
            for px, sz in asks_n:
                if px > max_buy_px:
                    break
                ask_depth_usd += px * sz

            total_depth_usd = bid_depth_usd + ask_depth_usd

            return OrderbookSnapshot(
                symbol=symbol,
                bid_depth_usd=bid_depth_usd,
                ask_depth_usd=ask_depth_usd,
                total_depth_usd=total_depth_usd,
                bid_levels=len(bids_n),
                ask_levels=len(asks_n),
                timestamp=datetime.now(timezone.utc)
            )

        except Exception as e:
            logger.warning(f"product_book fetch failed for {symbol}: {e}; using heuristic depth")

            # Fallback heuristic from 24h volume
            quote = self.get_quote(symbol)
            estimated_depth_usd = quote.volume_24h * 0.0005  # 0.05% of 24h volume (less conservative)
            return OrderbookSnapshot(
                symbol=symbol,
                bid_depth_usd=estimated_depth_usd / 2,
                ask_depth_usd=estimated_depth_usd / 2,
                total_depth_usd=estimated_depth_usd,
                bid_levels=0,
                ask_levels=0,
                timestamp=datetime.now(timezone.utc)
            )
    
    def get_ohlcv(self, symbol: str, interval: str = "1h", 
                   limit: int = 100) -> List[OHLCV]:
        """
        Get historical OHLCV candlesticks from Coinbase.
        
        Args:
            symbol: e.g. "BTC-USD"
            interval: Granularity - "ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE", 
                     "THIRTY_MINUTE", "ONE_HOUR", "TWO_HOUR", "SIX_HOUR", "ONE_DAY"
                     Or shortcuts: "1m", "5m", "15m", "1h", "1d"
            limit: Number of candles (max 300)
            
        Returns:
            List of OHLCV candles (oldest to newest)
        """
        self._rate_limit("ohlcv")
        
        # Map shorthand intervals to Coinbase granularity
        interval_map = {
            "1m": "ONE_MINUTE",
            "5m": "FIVE_MINUTE",
            "15m": "FIFTEEN_MINUTE",
            "30m": "THIRTY_MINUTE",
            "1h": "ONE_HOUR",
            "2h": "TWO_HOUR",
            "6h": "SIX_HOUR",
            "1d": "ONE_DAY"
        }
        granularity = interval_map.get(interval, interval)
        
        # Calculate time range (Coinbase requires start/end)
        end = int(time.time())
        
        # Duration in seconds per candle
        duration_map = {
            "ONE_MINUTE": 60,
            "FIVE_MINUTE": 300,
            "FIFTEEN_MINUTE": 900,
            "THIRTY_MINUTE": 1800,
            "ONE_HOUR": 3600,
            "TWO_HOUR": 7200,
            "SIX_HOUR": 21600,
            "ONE_DAY": 86400
        }
        duration = duration_map.get(granularity, 3600)
        start = end - (duration * min(limit, 300))  # Coinbase max is 300
        
        logger.debug(f"Fetching OHLCV for {symbol} ({granularity}, limit={limit})")
        
        try:
            result = self._req(
                "GET",
                f"/products/{symbol}/candles?start={start}&end={end}&granularity={granularity}",
                authenticated=True  # Requires authentication
            )
            
            candles = []
            for candle in result.get("candles", []):
                # Coinbase returns: [timestamp, low, high, open, close, volume]
                candles.append(OHLCV(
                    symbol=symbol,
                    timestamp=datetime.fromtimestamp(int(candle["start"])),
                    open=float(candle["open"]),
                    high=float(candle["high"]),
                    low=float(candle["low"]),
                    close=float(candle["close"]),
                    volume=float(candle["volume"])
                ))
            
            # Sort oldest to newest
            candles.sort(key=lambda c: c.timestamp)
            return candles
            
        except Exception as e:
            logger.error(f"Failed to fetch OHLCV for {symbol}: {e}")
            return []
    
    def get_tickers(self, symbols: Optional[List[str]] = None) -> Dict[str, Quote]:
        """
        Get quotes for multiple symbols.
        
        Args:
            symbols: List of symbols (or None for all)
            
        Returns:
            Dict mapping symbol -> Quote
        """
        if symbols is None:
            # Get all tradeable symbols
            symbols = self.get_symbols()
        
        logger.debug(f"Fetching tickers for {len(symbols)} symbols")
        
        tickers = {}
        for symbol in symbols:
            try:
                tickers[symbol] = self.get_quote(symbol)
            except Exception as e:
                logger.warning(f"Failed to fetch ticker for {symbol}: {e}")
        
        return tickers
    
    def get_symbols(self) -> List[str]:
        """
        Get all tradeable USD symbols on exchange.
        
        Returns:
            List of symbol strings (e.g. ["BTC-USD", "ETH-USD", ...])
        """
        self._rate_limit("symbols")
        
        logger.debug("Fetching available symbols")
        
        products = self.list_public_products(limit=250)
        
        # Filter for USD pairs that are tradeable
        usd_symbols = []
        for p in products:
            product_id = p.get("product_id", "")
            status = p.get("status", "")
            
            if product_id.endswith("-USD") and status != "offline":
                usd_symbols.append(product_id)
        
        logger.debug(f"Found {len(usd_symbols)} USD trading pairs")
        return usd_symbols
    
    def check_connectivity(self) -> bool:
        """
        Test exchange connectivity.
        
        Returns:
            True if connected and healthy
        """
        try:
            self.get_symbols()
            logger.info("Exchange connectivity: OK")
            return True
        except Exception as e:
            logger.error(f"Exchange connectivity failed: {e}")
            return False
    
    # ========== V1 Methods (Authenticated) ==========
    
    def get_accounts(self) -> List[dict]:
        """
        Get account balances.
        
        Returns:
            List of accounts with balances for all currencies
        """
        self._rate_limit("accounts")
        logger.debug("Fetching account balances")
        response = self._req("GET", "/accounts", authenticated=True)
        return response.get("accounts", [])
    
    def get_products(self, product_ids: List[str]) -> List[dict]:
        """
        Get product details for specific products from public list.
        
        Args:
            product_ids: List of product IDs (e.g. ["BTC-USD", "ETH-USD"])
            
        Returns:
            List of product details
        """
        self._rate_limit("products")
        
        logger.debug(f"Fetching products: {product_ids}")
        
        # Use public products endpoint and filter
        all_products = self.list_public_products(limit=250)
        
        # Filter to requested products
        filtered = [p for p in all_products if p.get("product_id") in product_ids]
        return filtered
    
    def list_public_products(self, limit: int = 250) -> List[dict]:
        """
        List all public products (market data).
        
        Args:
            limit: Max products to return (1-250)
            
        Returns:
            List of product dicts with price, volume, status
        """
        self._rate_limit("products_list")
        
        url = "https://api.coinbase.com/api/v3/brokerage/market/products"
        try:
            r = requests.get(url, params={"limit": max(1, min(limit, 250))}, timeout=20)
            r.raise_for_status()
            data = r.json() or {}
            items = data.get("products", [])
            
            # Normalize format
            out = []
            for it in items:
                pid = it.get("product_id") or it.get("id")
                if not pid:
                    continue
                    
                out.append({
                    "product_id": pid,
                    "base_currency": it.get("base_currency_id") or it.get("base_currency"),
                    "quote_currency": it.get("quote_currency_id") or it.get("quote_currency"),
                    "status": it.get("status", ""),
                    "price": it.get("price"),
                    "volume_24h": it.get("volume_24h") or it.get("quote_volume_24h") or 0,
                })
            
            return out
        except Exception as e:
            logger.warning(f"list_public_products failed: {e}")
            return []
    
    def preview_order(self, product_id: str, side: str, quote_size_usd: float) -> dict:
        """
        Preview an order without placing it (dry-run).
        
        Args:
            product_id: e.g. "BTC-USD"
            side: "buy" or "sell"
            quote_size_usd: USD amount to trade
            
        Returns:
            Order preview with estimated fills, fees, slippage
        """
        if self.read_only:
            logger.info(f"READ_ONLY mode: Would preview {side} {quote_size_usd} USD of {product_id}")
            return {"success": False, "read_only": True}
        
        self._rate_limit("preview_order")
        
        side_up = side.upper()

        # Per Coinbase, market SELLs must be parameterized with base_size
        if side_up == "SELL":
            # Compute base size from quote_size using current price
            quote = self.get_quote(product_id)
            if quote.mid <= 0:
                raise ValueError(f"Invalid price for {product_id} in preview")
            base_size = quote_size_usd / quote.mid
            base_size_str = self._round_to_precision(base_size, product_id)
            body = {
                "order_configuration": {
                    "market_market_ioc": {
                        "base_size": base_size_str
                    }
                },
                "product_id": product_id,
                "side": side_up
            }
        else:
            # BUY: quote_size is accepted
            body = {
                "order_configuration": {
                    "market_market_ioc": {
                        "quote_size": f"{quote_size_usd:.2f}"
                    }
                },
                "product_id": product_id,
                "side": side_up
            }
        
        logger.info(f"Previewing {side} {quote_size_usd} USD of {product_id}")
        return self._req("POST", "/orders/preview", body, authenticated=True)
    
    def _round_to_precision(self, value: float, product_id: str) -> str:
        """
        Round value to appropriate precision for Coinbase.
        
        Most altcoins use 2 decimal places for quantity.
        BTC/ETH use up to 8 decimals.
        
        Args:
            value: Raw value to round
            product_id: Trading pair (e.g., "BTC-USD")
            
        Returns:
            Formatted string with appropriate precision
        """
        base_currency = product_id.split('-')[0]
        
        # High-value assets: 8 decimals
        if base_currency in ['BTC', 'ETH']:
            return f"{value:.8f}"
        # Most altcoins: 2 decimals (HBAR, XRP, DOGE, etc.)
        elif value < 10:
            return f"{value:.8f}"
        elif value < 100:
            return f"{value:.4f}"
        else:
            return f"{value:.2f}"
    
    def place_order(self, product_id: str, side: str, quote_size_usd: float, 
                   client_order_id: Optional[str] = None, 
                   order_type: str = "market") -> dict:
        """
        Place an order (market or limit).
        
        Args:
            product_id: e.g. "BTC-USD"
            side: "buy" or "sell"
            quote_size_usd: USD amount to trade
            client_order_id: Optional idempotency key
            order_type: "market" or "limit_post_only"
            
        Returns:
            Order result with fill details
        """
        if self.read_only:
            raise ValueError("Cannot place orders in READ_ONLY mode")
        
        self._rate_limit("place_order")
        
        if order_type == "limit_post_only":
            # Get current quote for limit order
            quote = self.get_quote(product_id)
            
            # Validate quote has liquidity
            if quote.bid <= 0 or quote.ask <= 0:
                raise ValueError(f"No liquidity for {product_id}: bid={quote.bid}, ask={quote.ask}")
            
            # For post-only orders (maker-only, no immediate execution):
            # - BUY: price must be < current ask (at or below bid side)
            # - SELL: price must be > current bid (at or above ask side)
            # 
            # Strategy: Join the top of book for quick fill while staying passive
            if side.upper() == "BUY":
                # Place at current bid (join buyers) - won't cross spread
                limit_price = quote.bid
                base_size = quote_size_usd / limit_price
            else:
                # Place at current ask (join sellers) - won't cross spread
                limit_price = quote.ask
                base_size = quote_size_usd / limit_price
            
            # Round base_size to appropriate precision
            base_size_str = self._round_to_precision(base_size, product_id)
            
            # Dynamic price precision: tiny-priced assets need more decimals.
            if limit_price >= 1:
                price_fmt = f"{limit_price:.2f}"
            elif limit_price >= 0.1:
                price_fmt = f"{limit_price:.4f}"
            elif limit_price >= 0.01:
                price_fmt = f"{limit_price:.5f}"
            else:
                price_fmt = f"{limit_price:.8f}"

            body = {
                "order_configuration": {
                    "limit_limit_gtc": {
                        "base_size": base_size_str,
                        # Use adaptive precision to avoid $0.00 rounding on micro-priced assets (e.g. PUMP)
                        "limit_price": price_fmt,
                        "post_only": True  # Critical: ensures maker-only
                    }
                },
                "product_id": product_id,
                "side": side.upper(),
                "client_order_id": client_order_id or str(uuid.uuid4()),
            }
            logger.warning(
                f"PLACING LIMIT POST-ONLY ORDER: {side} {base_size_str} of {product_id} @ ${price_fmt} "
                f"(bid={quote.bid:.8f}, ask={quote.ask:.8f})")
        else:
            # Market IOC order
            side_up = side.upper()
            if side_up == "SELL":
                # SELL must be in base_size
                quote = self.get_quote(product_id)
                if quote.mid <= 0:
                    raise ValueError(f"Invalid price for {product_id}")
                base_size = quote_size_usd / quote.mid
                base_size_str = self._round_to_precision(base_size, product_id)
                body = {
                    "order_configuration": {
                        "market_market_ioc": {
                            "base_size": base_size_str
                        }
                    },
                    "product_id": product_id,
                    "side": side_up,
                    "client_order_id": client_order_id or str(uuid.uuid4()),
                }
                logger.warning(f"PLACING MARKET ORDER: {side} {base_size_str} {product_id.split('-')[0]} of {product_id}")
            else:
                # BUY may use quote_size
                body = {
                    "order_configuration": {
                        "market_market_ioc": {
                            "quote_size": f"{quote_size_usd:.2f}"
                        }
                    },
                    "product_id": product_id,
                    "side": side_up,
                    "client_order_id": client_order_id or str(uuid.uuid4()),
                }
                logger.warning(f"PLACING MARKET ORDER: {side} {quote_size_usd} USD of {product_id}")
        
        return self._req("POST", "/orders", body, authenticated=True)
    
    # ========== Convert API (Crypto-to-Crypto) ==========
    
    def create_convert_quote(self, from_account: str, to_account: str, amount: str) -> dict:
        """
        Create a convert quote for crypto-to-crypto conversion.
        
        Args:
            from_account: Source account UUID
            to_account: Target account UUID
            amount: Amount in source currency
            
        Returns:
            Quote response with trade_id, exchange_rate, fees
        """
        if self.read_only:
            logger.warning("Read-only mode - would request convert quote")
            return {"trade": {"id": "dry-run", "status": "PREVIEW"}}
        
        self._rate_limit("convert_quote")
        
        body = {
            "from_account": from_account,
            "to_account": to_account,
            "amount": amount
        }
        
        logger.info(f"Creating convert quote: {amount} from {from_account[:8]}... to {to_account[:8]}...")
        return self._req("POST", "/convert/quote", body, authenticated=True)
    
    def get_convert_trade(self, trade_id: str, from_account: str, to_account: str) -> dict:
        """
        Get status of a convert trade.
        
        Args:
            trade_id: Trade ID from quote
            from_account: Source account UUID
            to_account: Target account UUID
            
        Returns:
            Trade status response
        """
        self._rate_limit("convert_status")
        
        params = {
            "from_account": from_account,
            "to_account": to_account
        }
        
        return self._req("GET", f"/convert/trade/{trade_id}", params=params, authenticated=True)
    
    def commit_convert_trade(self, trade_id: str, from_account: str, to_account: str) -> dict:
        """
        Execute a convert trade.
        
        Args:
            trade_id: Trade ID from quote
            from_account: Source account UUID
            to_account: Target account UUID
            
        Returns:
            Execution result
        """
        if self.read_only:
            logger.warning("Read-only mode - would commit convert trade")
            return {"trade": {"id": trade_id, "status": "DRY_RUN"}}
        
        self._rate_limit("convert_commit")
        
        body = {
            "from_account": from_account,
            "to_account": to_account
        }
        
        logger.warning(f"COMMITTING CONVERT TRADE: {trade_id}")
        return self._req("POST", f"/convert/trade/{trade_id}", body, authenticated=True)


# Singleton instance
_exchange = None


def get_exchange(read_only: bool = True) -> CoinbaseExchange:
    """
    Get singleton exchange instance.
    
    Args:
        read_only: If True, prevents order placement (safe default)
    """
    global _exchange
    if _exchange is None:
        _exchange = CoinbaseExchange(read_only=read_only)
    return _exchange
