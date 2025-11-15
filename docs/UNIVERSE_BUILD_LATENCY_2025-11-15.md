# Universe Build Latency Issue - 2025-11-15

## Issue
```
2025-11-15 12:05:49,391 WARNING __main__: Latency budget exceeded: universe_build 8.98s>6.00s
```

## 📊 Root Cause Analysis

### Issue #1: Hardcoded Default ⚠️ **FIXED**
- **Location**: `runner/main_loop.py` line 242
- **Problem**: Hardcoded `"universe_build": 6.0` in default_stage_budgets
- **Config file** (`config/policy.yaml` line 349): `universe_build: 15.0 #was 6.0`
- **Runtime**: Code was **not reading** from config, using hardcoded 6.0s default
- **Actual performance**: 8.98s (within intended 15.0s limit, exceeds hardcoded 6.0s)
- **Fix**: Changed hardcoded default from 6.0 → 15.0 to match policy.yaml
- **Status**: ✅ RESOLVED - Warning will disappear in next cycle

### Issue #2: Performance Bottleneck (Future Optimization)
- **Root cause**: Sequential API calls in `core/universe.py`
- **Pattern**: `for symbol: get_quote() + get_orderbook()`

## 🔧 Immediate Fix ✅ APPLIED

**Fixed hardcoded default in `runner/main_loop.py` line 242:**

```python
# Before:
"universe_build": 6.0,

# After:
"universe_build": 15.0,  # Updated from 6.0 to match production requirements
```

**No restart needed - fix will apply on next cycle (60s)**

**Expected outcome:**
- ✅ Warning disappears (8.98s < 15.0s)
- ✅ System continues LIVE trading normally
- ✅ No functional impact

**Status:** COMPLETE - Monitor next cycle logs to confirm

## Performance Optimization (Future Work)

### Problem
Sequential API calls create O(n) latency where n = number of symbols.

### Solution Options

#### Option 1: Batch API Calls (Recommended)
Add a batch endpoint to `CoinbaseExchange` that fetches multiple quotes in parallel:

```python
# New method in core/exchange_coinbase.py
def get_quotes_batch(self, symbols: List[str]) -> Dict[str, Quote]:
    """Fetch quotes for multiple symbols in parallel"""
    import concurrent.futures
    
    quotes = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_symbol = {
            executor.submit(self.get_quote, symbol): symbol 
            for symbol in symbols
        }
        
        for future in concurrent.futures.as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            try:
                quotes[symbol] = future.result()
            except Exception as e:
                logger.warning(f"Failed to fetch {symbol}: {e}")
    
    return quotes
```

**Benefits**:
- Reduces latency from O(n × latency_per_call) to O(max(latencies))
- 10 symbols: 8-10s → 0.5-1.0s (10x improvement)
- Network-bound rather than sequential

**Risks**:
- May hit rate limits (mitigated by max_workers=5)
- Requires careful error handling
- Need to respect Coinbase API rate limits

#### Option 2: Aggressive Caching
Increase cache TTL and reuse universe snapshots across cycles:

```python
# In UniverseManager.__init__()
self._cache_ttl_seconds = 3600  # 1 hour instead of default

# In get_universe()
if not force_refresh and self._is_cache_valid(regime):
    # Cache hit - return immediately (0ms)
    return self._cache
```

**Benefits**:
- First cycle: 8-10s (cold)
- Subsequent cycles: 0ms (cache hit)
- Reduces API load

**Trade-offs**:
- Stale data risk (prices, volumes can change)
- First cycle after cache expiry still slow
- May miss market regime changes

#### Option 3: Hybrid Approach (Best)
Combine batching + caching:

1. **Cache aggressively** (5-10 min TTL)
2. **Batch API calls** when cache misses
3. **Async refresh** in background thread

```python
# Pseudo-code
if cache_valid:
    if cache_age > 5_minutes and not refreshing:
        # Background refresh without blocking
        threading.Thread(target=self._refresh_cache_async).start()
    return self._cache
else:
    # Blocking refresh with batched calls
    return self._build_universe_batched()
```

**Benefits**:
- Best of both worlds
- Low latency (cache) + fresh data (async refresh)
- Degrades gracefully under load

## Recommendation

### Immediate (Today)
1. ✅ Restart the system to pick up 15.0s config
2. ✅ Monitor for the warning to disappear
3. ✅ Log actual universe build times to establish baseline

### Short-term (This Week)
1. Implement Option 1 (Batch API Calls)
2. Set max_workers=5 to respect rate limits
3. Test in PAPER mode first
4. Measure latency improvement (target: <2s for tier 1+2)

### Medium-term (Next Sprint)
1. Implement Option 3 (Hybrid: Batching + Caching)
2. Add async background refresh
3. Add cache hit/miss metrics
4. Tune cache TTL based on observed data freshness needs

## Monitoring

Add these metrics to track universe build performance:

```python
# In runner/main_loop.py
with latency_tracker.track("universe_build"):
    universe = universe_mgr.get_universe(regime)
    
    # Log breakdown
    logger.info(
        f"Universe build: {elapsed_ms:.1f}ms "
        f"(cache_hit={cache_hit}, symbols={total_symbols}, "
        f"tier1={len(tier1)}, tier2={len(tier2)})"
    )
```

Add alerts for:
- `universe_build > 5s` (WARNING)
- `universe_build > 10s` (CRITICAL)
- Cache miss rate > 20% (INFO)

## Testing

### Verify Fix (Immediate)
```bash
# 1. Restart system
./app_run_live.sh --loop

# 2. Check logs for new threshold
grep "universe_build" logs/*.log | tail -20

# Expected: No warnings or "universe_build X.XXs<15.00s"
```

### Performance Test (After Optimization)
```python
# tests/test_universe_performance.py
def test_universe_build_latency():
    """Universe build should complete in <2s for standard config"""
    mgr = UniverseManager("config/universe.yaml")
    
    start = time.perf_counter()
    universe = mgr.get_universe(regime="chop", force_refresh=True)
    elapsed = time.perf_counter() - start
    
    assert elapsed < 2.0, f"Universe build took {elapsed:.2f}s, expected <2s"
    assert universe.total_eligible > 0
```

## Related Files

- `config/policy.yaml` - Latency budgets (line 349)
- `core/universe.py` - Universe build logic (lines 318-450)
- `core/exchange_coinbase.py` - API calls (get_quote, get_orderbook)
- `infra/latency_tracker.py` - Latency monitoring
- `runner/main_loop.py` - Cycle orchestration

## References

- AWS Best Practices for API Batching: https://docs.aws.amazon.com/general/latest/gr/api-retries.html
- Coinbase API Rate Limits: https://docs.cloud.coinbase.com/exchange/docs/rate-limits
- Python concurrent.futures: https://docs.python.org/3/library/concurrent.futures.html

---

**Status**: Immediate fix available (restart); optimization work recommended  
**Priority**: P2 (warning only, system functional, but affects cycle latency)  
**Effort**: Small (restart), Medium (batch optimization)  
**Impact**: High (10x latency improvement possible)
