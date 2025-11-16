"""
Live Smoke Test - Read-Only Validation

Tests real Coinbase connection without placing orders.
Run before enabling LIVE mode to validate:
- API authentication
- Data freshness
- Account access
- Quote quality
- Fill reconciliation

Run with: CB_API_SECRET_FILE=<path> pytest tests/test_live_smoke.py -v

These tests are skipped if credentials are not available.
"""

import pytest
import os
from datetime import datetime, timezone, timedelta
from core.exchange_coinbase import CoinbaseExchange
from core.execution import ExecutionEngine
from core.universe import UniverseManager
from infra.state_store import StateStore


# Check if credentials are available
def has_credentials():
    """Check if Coinbase credentials are available"""
    secret_file = os.environ.get("CB_API_SECRET_FILE")
    if secret_file and os.path.exists(secret_file):
        return True
    return bool(os.environ.get("COINBASE_API_KEY") and os.environ.get("COINBASE_API_SECRET"))


skip_without_creds = pytest.mark.skipif(
    not has_credentials(),
    reason="Coinbase credentials not available (set CB_API_SECRET_FILE or COINBASE_API_KEY/SECRET)"
)


@skip_without_creds
@skip_without_creds
def test_coinbase_connection():
    """Test basic Coinbase API connection"""
    exchange = CoinbaseExchange(read_only=True)
    
    # Should have API key configured
    assert exchange.api_key is not None, "API key not configured"
    assert exchange.api_secret is not None, "API secret not configured"
    
    print(f"✅ Coinbase connection established")


@skip_without_creds
@skip_without_creds
def test_account_access():
    """Test account balance fetching"""
    exchange = CoinbaseExchange(read_only=True)
    
    accounts = exchange.get_accounts()
    assert accounts is not None, "Failed to fetch accounts"
    assert len(accounts) > 0, "No accounts found"
    
    # Check for balances
    balances = {}
    for acc in accounts:
        currency = acc['currency']
        balance = float(acc.get('available_balance', {}).get('value', 0))
        if balance > 0:
            balances[currency] = balance
    
    assert len(balances) > 0, "No non-zero balances found"
    
    print(f"✅ Account access OK: {len(balances)} currencies with balance")
    for curr, bal in list(balances.items())[:5]:  # Show first 5
        print(f"   {curr}: {bal:.6f}")


@skip_without_creds
@skip_without_creds
def test_quote_freshness():
    """Test quote data quality and freshness"""
    exchange = CoinbaseExchange(read_only=True)
    
    test_pairs = ["BTC-USD", "ETH-USD", "SOL-USD"]
    
    for pair in test_pairs:
        try:
            quote = exchange.get_quote(pair)
            
            # Check quote has required fields
            assert quote.bid > 0, f"{pair}: Invalid bid price"
            assert quote.ask > 0, f"{pair}: Invalid ask price"
            assert quote.ask > quote.bid, f"{pair}: Ask <= Bid (crossed market)"
            
            # Check timestamp freshness
            now = datetime.now(timezone.utc)
            quote_ts = quote.timestamp
            if quote_ts.tzinfo is None:
                quote_ts = quote_ts.replace(tzinfo=timezone.utc)
            
            age_seconds = (now - quote_ts).total_seconds()
            assert age_seconds < 60, f"{pair}: Quote too stale ({age_seconds:.1f}s old)"
            assert age_seconds >= 0, f"{pair}: Quote timestamp in future (clock skew)"
            
            # Check spread is reasonable
            spread_bps = quote.spread_bps
            assert spread_bps < 200, f"{pair}: Spread too wide ({spread_bps:.1f}bps)"
            
            print(f"✅ {pair}: mid=${quote.mid:,.2f}, spread={spread_bps:.1f}bps, age={age_seconds:.1f}s")
            
        except Exception as e:
            pytest.fail(f"{pair}: Quote fetch failed: {e}")


@skip_without_creds
def test_ohlcv_data():
    """Test historical OHLCV data fetching"""
    exchange = CoinbaseExchange(read_only=True)
    
    # Fetch hourly candles for BTC
    candles = exchange.get_ohlcv("BTC-USD", interval="1h", limit=5)
    
    assert candles is not None, "Failed to fetch OHLCV data"
    assert len(candles) > 0, "No candles returned"
    assert len(candles) <= 5, "Too many candles returned"
    
    # Check candle structure
    for candle in candles:
        assert candle.open > 0, "Invalid open price"
        assert candle.high > 0, "Invalid high price"
        assert candle.low > 0, "Invalid low price"
        assert candle.close > 0, "Invalid close price"
        assert candle.volume >= 0, "Invalid volume"
        assert candle.high >= candle.low, "High < Low"
        assert candle.high >= candle.open, "High < Open"
        assert candle.high >= candle.close, "High < Close"
        assert candle.low <= candle.open, "Low > Open"
        assert candle.low <= candle.close, "Low > Close"
    
    # Check timestamps are in order
    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps), "Candles not in chronological order"
    
    # Check most recent candle is fresh
    latest = candles[-1]
    now = datetime.now(timezone.utc)
    if latest.timestamp.tzinfo is None:
        latest_ts = latest.timestamp.replace(tzinfo=timezone.utc)
    else:
        latest_ts = latest.timestamp
    
    age_seconds = (now - latest_ts).total_seconds()
    assert age_seconds < 7200, f"Latest candle too old ({age_seconds/3600:.1f}h)"
    
    print(f"✅ OHLCV data OK: {len(candles)} candles, latest @ {latest.timestamp}, age={age_seconds/60:.1f}min")


@skip_without_creds
def test_orderbook_depth():
    """Test orderbook depth fetching"""
    exchange = CoinbaseExchange(read_only=True)
    
    # Test major pairs
    test_pairs = ["BTC-USD", "ETH-USD"]
    
    for pair in test_pairs:
        try:
            orderbook = exchange.get_orderbook(pair)
            
            assert orderbook is not None, f"{pair}: No orderbook data"
            assert 'bids' in orderbook, f"{pair}: No bids"
            assert 'asks' in orderbook, f"{pair}: No asks"
            
            bids = orderbook['bids']
            asks = orderbook['asks']
            
            assert len(bids) > 0, f"{pair}: Empty bids"
            assert len(asks) > 0, f"{pair}: Empty asks"
            
            # Check bid/ask structure
            best_bid = float(bids[0]['price'])
            best_ask = float(asks[0]['price'])
            
            assert best_ask > best_bid, f"{pair}: Crossed market (ask={best_ask}, bid={best_bid})"
            
            # Calculate depth
            bid_depth_usd = sum(float(b['price']) * float(b['size']) for b in bids[:10])
            ask_depth_usd = sum(float(a['price']) * float(a['size']) for a in asks[:10])
            
            print(f"✅ {pair}: bid depth=${bid_depth_usd:,.0f}, ask depth=${ask_depth_usd:,.0f}")
            
        except Exception as e:
            pytest.fail(f"{pair}: Orderbook fetch failed: {e}")


@skip_without_creds
def test_universe_building():
    """Test universe manager with real data"""
    exchange = CoinbaseExchange(read_only=True)
    universe_config = {
        'tiers': {
            'tier1': {
                'min_24h_volume_usd': 50_000_000,
                'max_spread_bps': 50
            }
        },
        'static_list': ['BTC', 'ETH', 'SOL']
    }
    
    manager = UniverseManager(exchange=exchange, config=universe_config)
    
    # Build universe
    snapshot = manager.build_universe()
    
    assert snapshot is not None, "Universe build failed"
    assert len(snapshot.tier1) > 0, "No tier1 assets found"
    
    # Check that assets have metadata
    for asset in snapshot.tier1[:3]:
        assert asset.symbol is not None, "Asset missing symbol"
        assert asset.volume_24h_usd > 0, "Asset missing volume"
        assert asset.spread_bps >= 0, "Asset missing spread"
    
    print(f"✅ Universe built: {len(snapshot.tier1)} tier1 assets")


@skip_without_creds
def test_fill_reconciliation_empty():
    """Test fill reconciliation with no recent fills"""
    exchange = CoinbaseExchange(read_only=True)
    state_store = StateStore()
    executor = ExecutionEngine(mode="PAPER", exchange=exchange, state_store=state_store)
    
    # Reconcile last 5 minutes (should be empty for test account)
    summary = executor.reconcile_fills(lookback_minutes=5)
    
    assert summary is not None, "Reconciliation failed"
    assert 'fills_processed' in summary, "Missing fills_processed"
    assert 'orders_updated' in summary, "Missing orders_updated"
    assert 'total_fees' in summary, "Missing total_fees"
    
    print(f"✅ Fill reconciliation OK: {summary.get('fills_processed', 0)} fills processed")


@skip_without_creds
def test_execution_preview():
    """Test order preview without placing"""
    exchange = CoinbaseExchange(read_only=True)
    executor = ExecutionEngine(mode="DRY_RUN", exchange=exchange)
    
    # Preview BTC buy
    preview = executor.preview_order(
        symbol="BTC-USD",
        side="BUY",
        size_usd=100.0
    )
    
    assert preview is not None, "Preview failed"
    assert 'success' in preview, "Missing success field"
    
    if preview['success']:
        assert 'estimated_price' in preview, "Missing estimated_price"
        assert 'estimated_fees' in preview, "Missing estimated_fees"
        assert 'spread_bps' in preview, "Missing spread_bps"
        
        print(f"✅ Preview OK: ${preview['estimated_price']:,.2f}, fees=${preview['estimated_fees']:.2f}")
    else:
        print(f"⚠️  Preview rejected: {preview.get('error', 'unknown')}")


@skip_without_creds
def test_circuit_breaker_data():
    """Test circuit breaker can access required data"""
    exchange = CoinbaseExchange(read_only=True)
    
    # Test quote age check (simulated)
    quote = exchange.get_quote("BTC-USD")
    now = datetime.now(timezone.utc)
    
    if quote.timestamp.tzinfo is None:
        quote_ts = quote.timestamp.replace(tzinfo=timezone.utc)
    else:
        quote_ts = quote.timestamp
    
    age_seconds = (now - quote_ts).total_seconds()
    
    # Circuit breaker should reject quotes > 30s old (per policy)
    max_age = 30
    if age_seconds > max_age:
        print(f"⚠️  Quote stale ({age_seconds:.1f}s) - would trigger circuit breaker")
    else:
        print(f"✅ Quote fresh ({age_seconds:.1f}s) - circuit breaker OK")


@pytest.mark.skipif(
    not os.getenv("CB_API_SECRET_FILE"),
    reason="Requires CB_API_SECRET_FILE environment variable"
)
@skip_without_creds
def test_smoke_suite():
    """Run all smoke tests in sequence"""
    print("\n" + "="*80)
    print("LIVE SMOKE TEST SUITE - READ-ONLY VALIDATION")
    print("="*80 + "\n")
    
    test_coinbase_connection()
    test_account_access()
    test_quote_freshness()
    test_ohlcv_data()
    test_orderbook_depth()
    test_universe_building()
    test_fill_reconciliation_empty()
    test_execution_preview()
    test_circuit_breaker_data()
    
    print("\n" + "="*80)
    print("✅ ALL SMOKE TESTS PASSED - SYSTEM READY FOR PAPER/LIVE")
    print("="*80 + "\n")


if __name__ == "__main__":
    # Run smoke suite directly
    if not os.getenv("CB_API_SECRET_FILE"):
        print("ERROR: CB_API_SECRET_FILE environment variable required")
        print("Usage: CB_API_SECRET_FILE=<path> python tests/test_live_smoke.py")
        exit(1)
    
    test_smoke_suite()
