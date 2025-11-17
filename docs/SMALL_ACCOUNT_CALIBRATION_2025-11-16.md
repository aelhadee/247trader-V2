# Small Account Calibration – 2025-11-16

## Summary

Calibrated bot configuration for **~$250 account** after first successful LIVE run revealed risk constraints were too tight. Bot correctly proposed HBAR trade but was blocked by `below_min_after_caps` rejection.

**Root Cause:** Configuration tuned for $10k+ accounts (min_notional=$10, tight per-asset caps) created impossible constraints on small accounts.

**Solution:** Reduced min_notional to $5, increased T2 cap to 6%, added NAV-aware proposal filtering.

---

## 1. Changes Made

### 1.1 Reduced Minimum Trade Notional ($10 → $5)

**Files Changed:** `config/policy.yaml`

**Rationale:** With NAV=$256, original $10 minimum required 3.9% position size. For day-trader profile proposing 0.8-2% trades, this was impossible. $5 minimum = 1.95% of $256 (still conservative, more achievable).

**Changes:**
```yaml
# profiles.day_trader
min_trade_notional: 5.0  # Was 15.0

# execution
min_notional_usd: 5.0  # Was 10.0

# risk
min_trade_notional_usd: 5  # Was 10
dust_threshold_usd: 5.0    # Was 10.0

# position_sizing
min_order_usd: 5.0  # Was 10.0

# portfolio_management
min_liquidation_value_usd: 5   # Was 10
trim_min_value_usd: 5.0        # Was 10.0
purge_execution:
  slice_usd: 10.0              # Was 15.0
  max_residual_usd: 8.0        # Was 12.0
```

**Impact:**
- Allows trades as small as $5 (vs $10 previously)
- On $250 account: 2% position = $5.00 (now tradeable)
- On $1k account: 0.5% position = $5.00 (reasonable granularity)
- **Tradeoff:** More tiny trades, slightly higher fee friction (~40-60 bps on $5 trades = $0.03-0.04)

**Risk Level:** ✅ Low
- Coinbase supports $1-2 minimums on many markets
- $5 is still ~2x exchange minimums (safety buffer)
- Dust threshold aligned with min_notional

---

### 1.2 Increased T2 Per-Asset Cap (4.5% → 6%)

**File Changed:** `config/policy.yaml`

**Rationale:** With 15 positions and small account, per-asset caps were bottleneck. Original 4.5% T2 cap = $11.52 on $256 account. With existing HBAR position eating most of that, no room for new orders.

**Change:**
```yaml
profiles:
  day_trader:
    max_position_pct:
      T2: 0.06  # Was 0.045 (4.5%)
```

**Impact:**
- T2 assets (HBAR, ADA, LINK, AVAX, DOGE, etc.) can now reach 6% of NAV
- On $256 account: 6% = $15.36 per T2 asset (up from $11.52)
- Extra $3.84 headroom per asset = can add ~1-2 more $2-5 trades
- **Tradeoff:** Higher per-coin concentration risk

**Risk Level:** ⚠️ Medium
- 33% increase in per-asset exposure (4.5% → 6%)
- Still well below T1 cap (7%) and global cap (25%)
- For small accounts, this is acceptable; for $10k+ accounts, may want to revert

**Recommendation:** Monitor T2 concentration. If account grows >$1k, consider reverting to 4.5% or using `swing_trader` profile.

---

### 1.3 NAV-Aware Proposal Filtering

**File Changed:** `runner/main_loop.py`

**Rationale:** Prevent strategy from generating proposals that risk engine will always block. Previous behavior: rules engine proposes 0.8% trade ($2.05 on $256), risk engine rejects it, logs confusing message. New behavior: filter catches it early with clear message.

**Implementation:**
```python
# In run_cycle() Step 10: Filtering proposals
for proposal in proposals:
    if proposal.side.upper() == "BUY":
        proposal_notional = (proposal.size_pct / 100.0) * nav
        
        if proposal_notional > 0 and proposal_notional < min_notional:
            logger.info(
                f"Skipping proposal for {proposal.symbol}: size=${proposal_notional:.2f} "
                f"< min_notional=${min_notional:.2f} (${proposal.size_pct:.1f}% of ${nav:.2f} NAV). "
                f"Increase position size or NAV to meet minimum."
            )
            skipped_capacity += 1
            continue
```

**Impact:**
- Clearer logging: user sees exactly why proposal was skipped
- Avoids pointless risk engine processing
- Surfaces actionable guidance: "increase position size or NAV"

**Example Log:**
```
Skipping proposal for HBAR-USD: size=$2.05 < min_notional=$5.00 (0.8% of $256.11 NAV).
Increase position size or NAV to meet minimum.
```

**Risk Level:** ✅ Low
- Pure observability improvement
- No trading logic changed
- Makes bot more transparent

---

## 2. Validation Results

### 2.1 Config Validation
```bash
$ python tools/config_validator.py config
✅ All configuration files are valid!
```

### 2.2 Min Notional Values
```
execution.min_notional_usd: 5.0        ✅
risk.min_trade_notional_usd: 5         ✅
risk.dust_threshold_usd: 5.0           ✅
position_sizing.min_order_usd: 5.0     ✅
day_trader.min_trade_notional: 5.0     ✅
```

### 2.3 HBAR Scenario Simulation
```
NAV: $256.11
Proposal: 0.8% = $2.05
min_notional: $5.00

Result: ❌ BLOCKED (expected - 0.8% still too small)
Shortfall: $2.95
```

**Analysis:** Even with $5 minimum, 0.8% of $256 = $2.05 is below threshold. This is **correct behavior** - strategy needs to propose larger sizes for small accounts, OR account needs to grow.

**Actionable:** Rules engine should adapt sizing for small accounts:
- NAV < $500: Use 2-3% base sizes (not 0.8%)
- NAV $500-$2k: Use 1-2% base sizes
- NAV > $2k: Current 0.8-1.5% sizing is fine

---

## 3. Monitoring & Metrics

### 3.1 Prometheus Metrics
All rejection reasons already tracked:
```promql
# Risk rejections by reason (including below_min_after_caps)
trader_risk_rejections_total{reason="below_min_after_caps"}

# Proposals filtered by NAV-aware logic
# Look for "skipped_capacity" in logs (not yet metricated)
```

**TODO (Future Enhancement):** Add Prometheus counter for NAV-aware filter:
```python
self.proposals_skipped_min_notional = Counter(
    'trader_proposals_skipped_min_notional_total',
    'Proposals skipped due to NAV-aware min_notional filter'
)
```

### 3.2 Grafana Dashboard
Risk rejection panel already exists:
- **Panel:** "Risk Rejections by Reason" (Row 6)
- **Query:** `rate(trader_risk_rejections_total[5m])`
- **Visualization:** Time series by reason label

**What to Watch:**
- `below_min_after_caps` should drop to near-zero
- If it persists: NAV too small OR strategy sizing too aggressive
- New log lines: "Skipping proposal... size=$X < min_notional=$Y"

---

## 4. Expected Behavior After Changes

### 4.1 On Next HBAR-like Trigger

**Old Behavior:**
```
HBAR-USD BUY 0.8% ($2.05)
→ Risk engine: BLOCKED below_min_after_caps (want $2.05, cap allows $1.44, need $10)
→ User sees: confusing rejection, unclear why
```

**New Behavior (Scenario A - Still 0.8% proposal):**
```
HBAR-USD BUY 0.8% ($2.05)
→ NAV-aware filter: SKIPPED (size=$2.05 < min_notional=$5.00)
→ User sees: clear message with actionable guidance
→ No risk engine processing (efficient)
```

**New Behavior (Scenario B - Larger proposal from better trigger):**
```
HBAR-USD BUY 2.0% ($5.12)
→ NAV-aware filter: PASSES ($5.12 >= $5.00)
→ Risk engine: Check caps...
  - T2 cap: 6% of $256 = $15.36 available
  - Existing HBAR: ~$9-10 (estimate)
  - Remaining: ~$5-6
→ Risk engine: APPROVED (degraded to $5.12 if needed)
→ Execution: Places $5.12 order ✅
```

### 4.2 Trade Frequency Expectations

**Conservative Estimate:**
- NAV=$256, min_notional=$5, T2 cap=6%
- **If** triggers appear with 2-3% strength: 1-2 trades per day
- **If** only 0.8-1% triggers appear: 0 trades (filtered early)

**Reality Check:**
This is still a **very conservative** bot on a small account. To see more trades:
1. **Easiest:** Increase NAV to $500-1000 (2-3% proposals = $10-30, well above min)
2. **Riskier:** Lower min_conviction further (0.28 → 0.25) to capture weaker signals
3. **Advanced:** Adaptive sizing logic (scale proposal % inversely with NAV)

---

## 5. Profile Recommendations by Account Size

Based on new calibration:

### $100-$500 Accounts
**Profile:** `day_trader` (after these changes)
**Min Notional:** $5
**Expected Trades:** 0-2 per day (depends on NAV and trigger strength)
**Caveat:** Bot will be VERY selective. Many proposals filtered by NAV-aware logic.

**Recommendation:** Fund account to at least $500 for smoother operation.

---

### $500-$2k Accounts
**Profile:** `swing_trader` or `day_trader`
**Min Notional:** $5-9
**Expected Trades:** 2-5 per day
**Sweet Spot:** $1k account allows 0.5-2% sizing ($5-20 per trade), good granularity.

---

### $2k-$10k Accounts
**Profile:** `swing_trader` recommended, `day_trader` if supervised
**Min Notional:** $10 (can raise back to $10 from $5)
**Expected Trades:** 3-8 per day
**Conservative:** Consider reverting T2 cap to 4.5% at this scale.

---

### $10k+ Accounts
**Profile:** `conservative` or `swing_trader`
**Min Notional:** $10-15 (original values)
**Expected Trades:** 5-15 per day (depending on conviction threshold)
**Revert Changes:** Can undo all small-account calibrations at this scale.

---

## 6. Rollback Plan

If changes cause issues:

### 6.1 Revert Min Notional ($5 → $10)
```yaml
# config/policy.yaml
profiles:
  day_trader:
    min_trade_notional: 15.0

execution:
  min_notional_usd: 10.0

risk:
  min_trade_notional_usd: 10
  dust_threshold_usd: 10.0

position_sizing:
  min_order_usd: 10.0

portfolio_management:
  min_liquidation_value_usd: 10
  trim_min_value_usd: 10.0
  purge_execution:
    slice_usd: 15.0
    max_residual_usd: 12.0
```

### 6.2 Revert T2 Cap (6% → 4.5%)
```yaml
profiles:
  day_trader:
    max_position_pct:
      T2: 0.045
```

### 6.3 Remove NAV-Aware Filter
```python
# runner/main_loop.py
# Comment out or remove lines ~1710-1725 (NAV-aware capacity check)
```

### 6.4 Validate and Restart
```bash
python tools/config_validator.py config
./app_run_live.sh --loop
```

---

## 7. Future Enhancements

### 7.1 Adaptive Position Sizing
Dynamically adjust proposal sizes based on NAV:
```python
def adaptive_size_pct(base_size_pct: float, nav: float, min_notional: float) -> float:
    """Scale position size to ensure min_notional is met."""
    base_notional = (base_size_pct / 100.0) * nav
    
    if base_notional >= min_notional:
        return base_size_pct  # No adjustment needed
    
    # Scale up to meet minimum
    required_pct = (min_notional / nav) * 100.0
    return min(required_pct, base_size_pct * 3.0)  # Cap at 3x original
```

### 7.2 Profile Auto-Selection
Automatically switch profiles based on NAV:
```python
if nav < 500:
    active_profile = "micro_trader"  # New profile: 3-5% sizing, very selective
elif nav < 2000:
    active_profile = "day_trader"
elif nav < 10000:
    active_profile = "swing_trader"
else:
    active_profile = "conservative"
```

### 7.3 Min Notional as % of NAV
Instead of fixed $5/$10, use dynamic floor:
```python
min_notional_usd = max(
    policy['execution']['min_notional_usd'],  # Absolute floor ($5)
    nav * 0.02  # 2% of NAV
)
```

---

## 8. Testing Checklist

Before next LIVE run:

- [x] Config validation passes
- [x] All min_notional values updated consistently
- [x] T2 cap increased to 6%
- [x] NAV-aware filter implemented
- [ ] **Observe first cycle:** Check for proposals and clear rejection messages
- [ ] **Monitor Grafana:** Watch `trader_risk_rejections_total{reason="below_min_after_caps"}`
- [ ] **Check logs:** Verify "Skipping proposal... size=$X < min_notional=$Y" appears if needed
- [ ] **First successful trade:** Confirm fills reconcile correctly at $5-10 size

---

## 9. Key Takeaways

1. **Bot is working correctly** - rejected HBAR for valid reasons (risk caps + min_notional)
2. **Configuration mismatch** - tuned for large accounts, too tight for $250 NAV
3. **$5 min_notional** - appropriate for $250-$1k accounts (was $10)
4. **T2 cap 6%** - gives small accounts breathing room (was 4.5%)
5. **NAV-aware filter** - prevents pointless proposals, clearer logging
6. **Strategy still selective** - even with changes, 0.8% proposals on $256 = $2.05 (blocked)
7. **Recommendation:** Fund to $500+ for smoother operation, OR accept low trade frequency

**Bottom Line:** Bot is safe, conservative, and now properly calibrated for small accounts. Expect 0-2 trades/day on $256 NAV, ramping up as account grows.

---

## Appendix: HBAR Rejection Analysis

### Original Rejection Message
```
RISK_REJECT HBAR-USD BUY reason=below_min_after_caps
(rules want $2.06, cap allows $1.44, but min_notional requires $10.00)
- insufficient capacity for minimum trade size; consider closing position to free $8.56
```

### Breakdown
- **Rules engine:** Proposed 0.8% of $256 = **$2.06**
- **Risk caps:** Only **$1.44** remaining under T2 HBAR cap (4.5% = $11.52, ~$10 already held)
- **Min notional:** Required **$10.00** minimum
- **Conflict:** No size exists where `$1.44 >= size >= $10.00` ❌

### Resolution (After Changes)
- **Min notional:** $10 → **$5**
- **T2 cap:** 4.5% → **6%** ($11.52 → $15.36 available)
- **NAV-aware filter:** Catches $2.06 < $5.00 early, skips with clear message

### Expected Next HBAR Trigger
- **If 0.8% again:** NAV filter skips early ("size=$2.05 < $5.00")
- **If 2%+ trigger:** $5.12+ passes filter, risk engine checks caps
  - Available: $15.36 - $10 held = ~$5-6 remaining
  - Proposal: $5.12
  - Result: ✅ APPROVED (or degraded slightly to fit cap)

---

**Document Version:** 1.0  
**Date:** 2025-11-16  
**Author:** 247trader-v2 Copilot  
**Status:** Production-Ready  
