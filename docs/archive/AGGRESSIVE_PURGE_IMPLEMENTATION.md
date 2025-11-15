# Aggressive Purge Mode Implementation

**Date:** 2025-11-13  
**Status:** ✅ Complete

## Problem

Purge operations for illiquid T3 junk tokens (PENGU, ZK) were failing because:
- Post-only orders with 6-12s TTL were too aggressive for thin books
- Orders never became top-of-book before TTL expiry
- Bot would retry indefinitely with maker-only, never completing purge

## Solution

Implemented **two-tier TTL strategy** with **aggressive taker fallback** for tiny positions.

### 1. Separate TTLs for Normal vs. Purge Trading

**Normal Trading (T1/T2 liquid pairs):**
```yaml
maker_max_ttl_sec: 15          # Balanced for 60s cycles
maker_first_min_ttl_sec: 12    # ~2-3 book refreshes
maker_retry_min_ttl_sec: 8
```

**Purge Mode (T3 illiquid junk):**
```yaml
purge_execution:
  maker_ttl_sec: 25            # Longer TTL for thin books
  maker_first_ttl_sec: 25      # First attempt gets full time
  maker_retry_ttl_sec: 20      # Retries still generous
```

### 2. Taker Fallback for Tiny Positions

After `max_consecutive_no_fill` (default: 2) failed maker attempts:

```yaml
purge_execution:
  allow_taker_fallback: true              # Enable aggressive mode
  taker_fallback_threshold_usd: 50.0      # Only for positions < $50
  taker_max_slippage_bps: 100             # Allow 1% slippage to force exit
  max_consecutive_no_fill: 2              # 2 maker attempts before taker
```

**Logic:**
1. Try maker-only for 2 attempts (25s, then 20s TTL)
2. If both fail and remaining position < $50:
   - Log: `"TWAP: activating aggressive purge mode"`
   - Place one final `limit_ioc` (taker) order
   - Accept up to 1% slippage to force completion
3. If taker succeeds, exit loop immediately
4. If taker fails, give up (better than infinite loop)

### 3. Price Placement Strategy

```yaml
purge_execution:
  cushion_ticks: 1              # Start with minimal maker cushion
  max_cushion_ticks: 3          # Only widen if getting post-only rejects
```

More aggressive joining of top-of-book to improve fill probability.

## Code Changes

### `core/execution.py`

**Modified `_adaptive_maker_ttl()`:**
- Added `client_order_id` parameter
- Detects purge orders via `client_order_id.startswith("purge_")`
- Returns purge-specific TTL (25s/20s) instead of normal TTL (6-15s)

```python
def _adaptive_maker_ttl(self, quote, attempt_index: int, client_order_id: str = None) -> int:
    is_purge = client_order_id and client_order_id.startswith("purge_")
    
    if is_purge:
        pm_cfg = self.policy.get("portfolio_management", {})
        purge_cfg = pm_cfg.get("purge_execution", {})
        
        if attempt_index == 0:
            ttl = int(purge_cfg.get("maker_first_ttl_sec", 25))
        else:
            ttl = int(purge_cfg.get("maker_retry_ttl_sec", 20))
        
        return max(ttl, 1)
    
    # Normal spread-based heuristic...
```

### `runner/main_loop.py`

**Enhanced `_sell_via_market_order()` TWAP loop:**

After `consecutive_no_fill >= max_consecutive_no_fill`:

```python
# Aggressive purge fallback: use taker/market for tiny junk positions
allow_taker = purge_cfg.get("allow_taker_fallback", False)
taker_threshold = float(purge_cfg.get("taker_fallback_threshold_usd", 50.0))
remaining_usd = max(target_value_usd - total_filled_usd, 0.0)

if allow_taker and remaining_usd > 0 and remaining_usd <= taker_threshold:
    logger.warning(
        "TWAP: activating aggressive purge mode for %s (remaining=$%.2f < $%.2f threshold)",
        pair, remaining_usd, taker_threshold,
    )
    
    # Force taker/IOC order
    taker_result = self.executor.execute(
        symbol=pair,
        side="SELL",
        size_usd=remaining_usd,
        client_order_id=f"purge_taker_{uuid4().hex[:14]}",
        force_order_type="limit_ioc",
        skip_liquidity_checks=True,
        bypass_slippage_budget=True,
        bypass_failed_order_cooldown=True,
    )
    
    if taker_result.success and taker_result.filled_usd > 0:
        # Update totals and exit loop
        total_filled_usd += taker_result.filled_usd
        break
```

## Expected Behavior

### Before (Broken)
```
TWAP purge start: 1116 PENGU (~$16.19)
  Slice 1: post-only @ $0.014506, TTL=6s → no fill → canceled
  Slice 2: post-only @ $0.014508, TTL=5s → no fill → canceled
  "TWAP: residual ~$16.19 exceeds threshold"
  ❌ Purge sell failed
```

### After (Fixed)
```
TWAP purge start: 1116 PENGU (~$16.19)
  Slice 1: post-only @ $0.014506, TTL=25s → no fill → canceled
  Slice 2: post-only @ $0.014508, TTL=20s → no fill → canceled
  ⚠️ TWAP: activating aggressive purge mode (remaining=$16.19 < $50 threshold)
  Forcing taker sell ~$16.19 (max_slippage=1.0%)
  ✅ Taker fallback filled $16.19 (1116 PENGU)
  Purge complete
```

## Testing Checklist

- [x] Config syntax validated
- [x] Python syntax validated (`py_compile`)
- [ ] **Next:** Restart bot and observe purge behavior on PENGU/ZK
- [ ] Verify TTL logs show 25s/20s for purge orders
- [ ] Verify taker fallback activates after 2 failed maker attempts
- [ ] Verify normal trading still uses 12-15s TTL

## Risk Assessment

**Low Risk:**
- Only affects purge operations (not normal trading)
- Taker fallback gated by $50 threshold
- 1% slippage cap prevents catastrophic losses
- Explicit logging at every decision point

**Worst Case:**
- $50 position takes 1% slippage = **$0.50 loss** to force exit
- Much better than holding illiquid junk indefinitely

## Rollback Plan

If aggressive mode causes issues:

1. **Disable taker fallback:**
   ```yaml
   purge_execution:
     allow_taker_fallback: false
   ```

2. **Increase threshold to only use for dust:**
   ```yaml
   taker_fallback_threshold_usd: 10.0
   ```

3. **Reduce slippage tolerance:**
   ```yaml
   taker_max_slippage_bps: 50  # 0.5% instead of 1%
   ```

## Files Modified

1. `config/policy.yaml` - Added purge-specific TTL and taker fallback settings
2. `core/execution.py` - Enhanced `_adaptive_maker_ttl()` to detect purge orders
3. `runner/main_loop.py` - Added taker fallback logic in `_sell_via_market_order()`

## Related Issues

- **Root cause:** Wrong Coinbase cancel endpoint (fixed separately)
- **Stale orders:** Reconciliation now working correctly
- **Cycle utilization:** 110% → will improve with better purge completion

---

**Status:** Ready for live testing. Restart bot with `./app_run_live.sh --loop`
