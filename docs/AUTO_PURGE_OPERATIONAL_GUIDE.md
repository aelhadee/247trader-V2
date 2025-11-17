# Auto-Purge Operational Guide – 2025-11-16

## Overview

Auto-purge automatically liquidates **ineligible** and **excluded** holdings to maintain a clean portfolio aligned with universe tiers and risk policy. This doc explains expected behavior, failure patterns, and monitoring.

---

## 1. What Gets Purged?

### Eligible for Auto-Purge
- **Ineligible assets**: Fail universe criteria (volume, spread, depth, red flags)
- **Excluded assets**: Blacklisted in `universe.yaml` or banned by state
- **Minimum value**: Must be ≥ `min_liquidation_value_usd` (default: $5)

### Never Purged
- **Quote currencies**: USD, USDC, USDT, BTC, ETH (always kept)
- **Dust positions**: Below `min_liquidation_value_usd` (not worth gas/fees)
- **Managed positions**: Explicitly tagged positions (if configured)

---

## 2. Normal Purge Behavior (Success Cases)

### Example: SEI-USD ($5.69 position)
```
Purge: selling 36.400000 SEI (ineligible), ~$5.69
TWAP purge start ... slice=$5.00
LIVE: Executing SELL $5.69 of SEI-USD
...
partial_fill ... 36.200000 SEI (~$5.66)
TWAP: residual $0.03 below minimum notional $5.00, stopping
✅ TWAP liquidation complete
```

**What happened:**
1. SEI failed universe eligibility (volume/spread/depth)
2. Bot queued it for purge (~$5.69 value)
3. TWAP executor:
   - **Slice size**: $5 (configurable via `purge_execution.slice_usd`)
   - **First attempt**: Limit post-only order (maker)
   - **Timeout**: 25s TTL, no fill
   - **Retry**: Taker fallback, filled 36.2 SEI
4. **Residual**: $0.03 remaining (below $5 min_notional) → STOP
5. **Fees**: ~$0.03-0.05 (40-60 bps)

**Key Points:**
- ✅ Position effectively closed (99.5% filled)
- ✅ Dust ($0.03) correctly ignored (not worth another order)
- ✅ Maker-first, taker fallback (fee optimization)

---

### Example: WLFI-USD ($8.23 position)
```
Purge: selling 57.500000 WLFI (ineligible), ~$8.23
LIVE: Executing SELL $8.22 of WLFI-USD
...
EXEC_FILL WLFI-USD SELL ... filled=57.300000 avg_price=0.143170
TWAP: residual $0.02 below minimum notional $5.00, stopping
✅ TWAP liquidation complete
```

**What happened:**
1. WLFI ineligible (likely low volume or red flag)
2. Single slice executed: 57.3/57.5 units (~99.7%)
3. Residual $0.02 → STOP
4. Fees: ~$0.05

**Expected Outcome:**
- ✅ 99%+ of position liquidated
- ✅ Dust ignored
- ✅ Portfolio cleaner

---

## 3. Failure Patterns

### 3.1 BONK-USD Purge Failure (Typical)
```
Purge: selling 889587.000000 BONK (ineligible), ~$9.48
TWAP purge start ... slice=$5.00
LIVE: Executing SELL $9.47 of BONK-USD
...
EXEC_RESULT BONK-USD SELL success=True ... filled=0.000000
TWAP: slice 1 ... produced no fill

[Retry slice 2]
Order ... transitioned: new → rejected
ORDER_REJECT BONK-USD SELL client_id=... | Order placement failed: ... | error=INVALID_ORDER_CONFIGURATION | message=... | raw_response={...}
Purge: residual ~$9.47 > threshold $8.00
⚠️ Purge sell failed for BONK-USD
📝 Tracked purge failure: count=1, balance=889587.0, value=$9.48
```

**What happened:**
1. **Slice 1**: Order placed, no fill (timeout)
2. **Slice 2**: Coinbase rejected order outright
   - Error: `INVALID_ORDER_CONFIGURATION` or similar
   - Reason: Product status, precision constraints, or transient API issue
3. **Residual**: $9.47 > $8 threshold → FAIL logged
4. **State tracking**: Marked as `purge_failures[BONK-USD]` with failure count

**Why Coinbase Rejects:**
- **Product suspended**: Temporarily disabled trading
- **Precision violation**: Size/price doesn't meet market requirements
- **Stale quote**: Price moved too far from last quote
- **API transient**: 500/503 errors, rate limits

**Bot Response (Correct):**
- ✅ Stops trying this cycle (no infinite loop)
- ✅ Logs full error details for debugging
- ✅ Tracks failure count in state for backoff

---

### 3.2 Purge Failure Backoff Logic

After 3+ failures, bot applies **exponential backoff**:

| Failure Count | Backoff Duration | Behavior |
|---------------|------------------|----------|
| 1-2           | None             | Retry every cycle |
| 3             | 1 hour           | Skip for 1h after last failure |
| 4             | 2 hours          | Skip for 2h |
| 5+            | 4 hours (max)    | Skip for 4h |

**Example Log:**
```
⏸️  Skipping purge for BONK-USD: 3 recent failures, backoff 1h, 42min remaining
```

**Retry Log:**
```
🔄 Retrying purge for BONK-USD: backoff expired (3 failures, last 2h ago)
```

**Success Clears Tracking:**
```
✅ Purge success for BONK-USD, cleared failure tracking
```

---

## 4. Configuration

### policy.yaml Settings
```yaml
portfolio_management:
  auto_liquidate_ineligible: true  # Enable auto-purge
  min_liquidation_value_usd: 5     # Ignore positions < $5
  max_liquidations_per_cycle: 2    # Max 2 purges per 60s cycle
  
  purge_execution:
    slice_usd: 5.0                 # TWAP slice size ($5 for small accounts)
    maker_ttl_sec: 25              # Post-only TTL before retry
    maker_first_ttl_sec: 25
    maker_retry_ttl_sec: 20
    replace_seconds: 45            # Total budget per slice
    max_duration_seconds: 240      # Max 4min per purge (4-8 slices)
    poll_interval_seconds: 2.0
    max_slices: 24                 # Max attempts
    max_residual_usd: 8.0          # Fail if residual > $8 after slices
    max_consecutive_no_fill: 2     # Fail after 2 no-fill slices
    allow_taker_fallback: true     # Use market orders after maker timeout
    taker_fallback_threshold_usd: 25.0  # Use taker for >$25 positions
    taker_max_slippage_bps: 100    # Max 1% slippage for taker
```

### Tuning for Small Accounts (~$250)
- **slice_usd**: $5 (matches min_notional)
- **max_residual_usd**: $8 (allows 1-2 slices to fail before giving up)
- **max_liquidations_per_cycle**: 2 (avoid overloading 60s cycles)

### Tuning for Large Accounts ($10k+)
- **slice_usd**: $10-15 (faster liquidation)
- **max_residual_usd**: $12-15
- **max_liquidations_per_cycle**: 3-5 (more capacity)

---

## 5. Monitoring & Metrics

### Grafana Dashboard Panels
1. **Portfolio Stats** (Row 1):
   - Open Positions (drops as purges complete)
   - Account Value (should stay stable or increase)

2. **Operational** (Row 5):
   - Cycle Duration (watch for purge-heavy cycles >45s)

3. **Health** (Row 6):
   - Risk Rejections by Reason (purge failures show here)

### Prometheus Metrics
```promql
# Purge failures
rate(trader_risk_rejections_total{reason="purge_failed"}[5m])

# Order rejections during purge
rate(trader_orders_rejected_total[5m])

# Cycle duration (purge overhead)
trader_cycle_duration_seconds
```

### Log Patterns to Watch

**Success:**
```
✅ TWAP liquidation complete
```

**Failure:**
```
⚠️ Purge sell failed for BONK-USD
📝 Tracked purge failure: count=1, balance=..., value=$...
```

**Backoff:**
```
⏸️  Skipping purge for BONK-USD: 3 recent failures, backoff 1h, 42min remaining
```

**Retry:**
```
🔄 Retrying purge for BONK-USD: backoff expired (3 failures, last 2h ago)
```

---

## 6. Residual Dust Handling

### Why Residual is Left Behind
After TWAP liquidation, small residual (<$5) is intentionally **not** purged:
- **Reason 1**: Below `min_notional_usd` → exchange may reject
- **Reason 2**: Fees (40-60 bps) would eat 10-20% of value
- **Reason 3**: Not worth API quota / latency

**Example:**
```
SEI: 36.4 units → 36.2 filled → 0.2 units ($0.03) left
```

**Impact:**
- Portfolio shows $0.03 "dust" position
- Ignored in exposure calculations (below dust_threshold)
- Will be purged if price rises and dust > $5

### Manual Cleanup (If Needed)
If many dust positions accumulate (>10), manually liquidate:
```bash
# Use liquidate_holdings.py script
python liquidate_holdings.py --symbol SEI-USD --all
```

Or wait for dust to appreciate > $5 and auto-purge will retry.

---

## 7. Troubleshooting

### Problem: "Purge sell failed" Every Cycle
**Symptoms:**
```
⚠️ Purge sell failed for BONK-USD
(repeats every 60s)
```

**Root Causes:**
1. **Product suspended**: Check Coinbase status page
2. **Precision issue**: Symbol requires unusual lot size
3. **API transient**: 429/500 errors

**Solutions:**
1. **Wait for backoff** (after 3 failures, bot will pause for 1h)
2. **Check logs**: Look for `ORDER_REJECT` with detailed error
3. **Manual liquidation**: Use `liquidate_holdings.py` with force flag
4. **Exclude symbol**: Add to `universe.yaml` excluded list (won't retry)

---

### Problem: Cycle Duration Exceeds 45s
**Symptoms:**
```
Latency budget exceeded: total 67.38s>45.00s
purge_ineligible=51.276s
```

**Root Causes:**
- Multiple purges in one cycle (2-3 assets)
- TWAP slices timing out (maker → taker fallback)
- High API latency during purges

**Solutions:**
1. **Reduce max_liquidations_per_cycle**: 2 → 1 (slower but safer)
2. **Increase slice_usd**: $5 → $10 (fewer slices, faster completion)
3. **Accept occasional overruns**: Bot adds 15s backoff automatically

---

### Problem: Purge Failures Not Clearing
**Symptoms:**
```
🔄 Retrying purge for BONK-USD: backoff expired (5 failures, last 4h ago)
⚠️ Purge sell failed for BONK-USD
(failure count keeps increasing)
```

**Root Cause:** Persistent Coinbase issue with that product

**Solutions:**
1. **Manual intervention**: Add to `red_flag_bans` in state:
   ```python
   # In Python REPL or script
   from infra.state_store import StateStore
   store = StateStore()
   state = store.load()
   
   state['red_flag_bans']['BONK-USD'] = {
       'reason': 'persistent_purge_failure',
       'banned_at_iso': datetime.now(timezone.utc).isoformat(),
       'expires_at_iso': (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
   }
   
   # Clear purge failure tracking
   if 'BONK-USD' in state.get('purge_failures', {}):
       del state['purge_failures']['BONK-USD']
   
   store.save(state)
   ```

2. **Exclude in config**: Add to `universe.yaml`:
   ```yaml
   excluded_symbols:
     - BONK-USD  # Persistent purge issues
   ```

3. **Wait for product reactivation**: Check Coinbase status

---

## 8. State Store Schema

### purge_failures (New)
```json
{
  "purge_failures": {
    "BONK-USD": {
      "failure_count": 3,
      "last_failed_at_iso": "2025-11-16T12:34:56Z",
      "last_error": "Purge failed after 1 other liquidations",
      "balance": 889587.0,
      "value_usd": 9.48
    }
  }
}
```

**Fields:**
- `failure_count`: Number of consecutive failures (triggers backoff)
- `last_failed_at_iso`: Timestamp of last failure (for backoff calculation)
- `last_error`: Human-readable error context
- `balance`: Asset balance at failure (for debugging)
- `value_usd`: USD value at failure (for threshold checks)

**Lifecycle:**
- **Created**: On first purge failure
- **Updated**: Increments `failure_count` on each subsequent failure
- **Deleted**: On successful purge (cleared automatically)

---

## 9. Expected Behavior Summary

### Normal Operation
- ✅ SEI/WLFI purge: 99%+ filled, dust ignored
- ✅ Cycle duration: 10-30s (no purges), 40-60s (1-2 purges)
- ✅ Rate limits: <50% utilization (product cache helps)

### Failure Handling
- ✅ BONK purge fails: Logged, tracked, backoff applied
- ✅ No infinite loops: Max 24 slices, then fail gracefully
- ✅ Backoff escalation: 1h → 2h → 4h after repeated failures
- ✅ Auto-retry: After backoff expires, tries again

### Portfolio Impact
- ✅ Ineligible assets removed within 1-3 cycles
- ✅ Dust positions (<$5) remain (expected, harmless)
- ✅ Failed purges retry after backoff (patient approach)

---

## 10. Operational Checklist

**Daily:**
- [ ] Check Grafana: Open Positions count dropping?
- [ ] Review logs: Any persistent purge failures?
- [ ] Monitor cycle duration: Staying under 60s average?

**Weekly:**
- [ ] Review `purge_failures` in state: Any >5 failures?
- [ ] Clean up dust positions: Run `liquidate_holdings.py` if >10 dust
- [ ] Adjust `max_liquidations_per_cycle` based on portfolio size

**On Purge Failure Alert:**
1. Check logs for `ORDER_REJECT` details
2. Verify Coinbase product status
3. If persistent (>5 failures), add to `red_flag_bans` or `excluded_symbols`
4. Consider manual liquidation with force flag

---

## 11. Advanced: Error Code Reference

### Common Coinbase Rejection Errors

| Error Code | Meaning | Solution |
|------------|---------|----------|
| `INVALID_ORDER_CONFIGURATION` | Order size/price violates constraints | Check lot size, min notional |
| `INVALID_LIMIT_PRICE_POST_ONLY` | Post-only price crosses book | Use taker fallback |
| `INSUFFICIENT_BALANCE` | Not enough balance (rare) | Reconcile state |
| `PRODUCT_NOT_TRADEABLE` | Product suspended | Wait for reactivation |
| `INVALID_PRODUCT_ID` | Symbol not found | Check product listing |

### Bot Response by Error
- `INVALID_LIMIT_PRICE_POST_ONLY`: Auto-retry with taker (handled)
- `INVALID_ORDER_CONFIGURATION`: Log and track failure, backoff
- `PRODUCT_NOT_TRADEABLE`: Track failure, backoff, retry after 4h
- All others: Log full details, track failure, backoff

---

**Document Version:** 1.0  
**Date:** 2025-11-16  
**Author:** 247trader-v2 Copilot  
**Status:** Production-Ready  
