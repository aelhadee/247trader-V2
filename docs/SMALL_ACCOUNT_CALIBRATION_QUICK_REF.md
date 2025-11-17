# Small Account Calibration – Quick Reference

## TL;DR

Fixed "too strict for small accounts" issue after first successful LIVE run. Bot correctly proposed HBAR trade but was blocked by impossible constraints ($10 minimum on $256 account with 0.8% sizing = $2.05 proposal).

## Changes Made

### 1. Lower Min Notional: $10 → $5
- **Why:** $10 minimum = 3.9% of $256 NAV (too aggressive for small accounts)
- **Impact:** Allows $5 trades (2% of $256), still 2-3x Coinbase minimums
- **Files:** 8 references in `config/policy.yaml`

### 2. Increase T2 Cap: 4.5% → 6%
- **Why:** More headroom for T2 assets (HBAR, ADA, LINK, etc.) on small accounts
- **Impact:** $15.36 per T2 asset (vs $11.52), allows 1-2 more trades
- **Files:** `config/policy.yaml` (profiles.day_trader.max_position_pct.T2)

### 3. NAV-Aware Proposal Filtering
- **Why:** Prevent pointless proposals that risk engine will always block
- **Impact:** Clear early filtering with actionable guidance
- **Files:** `runner/main_loop.py` (Step 10 filtering logic)

## Validation

```bash
$ python tools/config_validator.py config
✅ All configuration files are valid!

$ python -m py_compile runner/main_loop.py
✅ main_loop.py syntax valid
```

## Expected Behavior

### Before Changes
```
HBAR 0.8% ($2.05) → BLOCKED: want $2.05, cap allows $1.44, need $10
```

### After Changes (Scenario A - Still 0.8%)
```
HBAR 0.8% ($2.05) → SKIPPED: size=$2.05 < min_notional=$5.00 (clear message)
```

### After Changes (Scenario B - Larger trigger 2%+)
```
HBAR 2.0% ($5.12) → APPROVED (or degraded to fit $5-6 cap remaining) ✅
```

## Trade Frequency Expectations

On **$256 NAV** with new settings:
- **0-2 trades/day** if triggers appear with 2-3% strength
- **0 trades** if only 0.8-1% triggers (filtered by NAV-aware logic)

**Recommendation:** Fund to $500+ for smoother operation.

## Rollback (If Needed)

```yaml
# config/policy.yaml - Revert to original values
execution:
  min_notional_usd: 10.0  # Was 5.0

risk:
  min_trade_notional_usd: 10  # Was 5

profiles:
  day_trader:
    min_trade_notional: 15.0  # Was 5.0
    max_position_pct:
      T2: 0.045  # Was 0.06
```

## Monitoring

**Grafana Dashboard:**
- Panel: "Risk Rejections by Reason" (Row 6)
- Metric: `trader_risk_rejections_total{reason="below_min_after_caps"}`
- **Expected:** Should drop to near-zero

**Logs to Watch:**
```
Skipping proposal for HBAR-USD: size=$2.05 < min_notional=$5.00 (0.8% of $256.11 NAV).
Increase position size or NAV to meet minimum.
```

## Profile Recommendations by Account Size

| NAV Range | Profile | Min Notional | Expected Trades/Day |
|-----------|---------|--------------|---------------------|
| $100-$500 | day_trader | $5 | 0-2 (very selective) |
| $500-$2k | swing_trader | $5-9 | 2-5 |
| $2k-$10k | swing_trader | $10 | 3-8 |
| $10k+ | conservative | $10-15 | 5-15 |

**Sweet Spot:** $1k+ account for smooth operation with current sizing logic.

## Key Takeaways

1. ✅ Bot working correctly (rejected HBAR for valid reasons)
2. ✅ Configuration now calibrated for $250-$1k accounts
3. ⚠️ Still conservative - expect low trade frequency on $256 NAV
4. 💡 To see more trades: Fund to $500+ OR accept 0-2 trades/day
5. 🔒 All changes validated, no syntax errors, configs pass validation

---

**Full Details:** See `docs/SMALL_ACCOUNT_CALIBRATION_2025-11-16.md`
