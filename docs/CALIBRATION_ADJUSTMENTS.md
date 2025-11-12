# Calibration Adjustments - 2025-01-11

## Problem Statement

After implementing critical safety improvements (direction filter, ATR filter, slippage budgets, stricter depth/spread), the system became **over-restricted**:

```
Before fixes: 15 eligible → 13 triggers (mostly downside) → 0 proposals
After safety fixes: 10 eligible → 0 triggers → 0 proposals
```

**Root Cause:**
- T1 depth requirement ($100k) demoted quality alts (PEPE, VET) with current $490 portfolio
- Tight thresholds (15m=3%, 60m=5%, vol=1.8x) + ATR filter (1.2x) + only_upside filter
- Combined effect: no actionable signals in chop regime

## Solution Applied (Option A: Breakout/Trend-Follow)

### 1. Trigger Threshold Loosening (chop regime)

**config/policy.yaml - triggers section:**
```yaml
price_move:
  pct_15m: 2.5    # was 3.0% (-0.5%)
  pct_60m: 4.5    # was 5.0% (-0.5%)

volume_spike:
  ratio_1h_vs_24h: 1.9  # was 1.8x (+0.1x - demand real interest)
```

**config/policy.yaml - circuit_breakers:**
```yaml
atr_min_multiplier: 1.1  # was 1.2x (less restrictive)
```

**config/signals.yaml:**
```yaml
max_triggers_per_cycle: 5  # was 10 (focus on quality)
```

### 2. Rules Loosening

**config/policy.yaml - strategy:**
```yaml
min_conviction_to_propose: 0.34  # was 0.38 (-0.04)
```

### 3. Size-Aware Depth Rule

**config/policy.yaml - liquidity:**
```yaml
# Size-aware depth requirement (depth must be ≥ N× order notional)
require_depth_mult: 10  # Depth within ±0.5% must be ≥ 10× order notional

# Tier-specific depth floors (NOT hard requirements if depth_mult passes)
min_depth_floor_usd:
  T1: 50_000     # was $100K (-50%)
  T2: 25_000     # unchanged
  T3: 10_000     # unchanged
```

**Impact:**
- T1 floor lowered from $100k → $50k (realistic for small portfolio)
- Depth must still be ≥10× order notional (execution safety maintained)
- PEPE, VET may now qualify as T1 if depth supports trade size

### 4. Zero-Trade Sentinel (Auto-Recovery)

**infra/state_store.py:**
- Added `zero_proposal_cycles` counter to DEFAULT_STATE
- Added `auto_loosen_applied` flag to DEFAULT_STATE
- Added helper methods:
  - `increment_zero_proposal_cycles()` - track consecutive zero-proposal cycles
  - `reset_zero_proposal_cycles()` - reset when proposals resume
  - `mark_auto_loosen_applied()` - prevent repeated adjustments
  - `has_auto_loosen_applied()` - check if already applied

**runner/main_loop.py:**
- Added sentinel logic after `if not proposals` block
- Triggers after N=20 consecutive zero-proposal cycles (once only)
- Applies ONE of the following adjustments:
  - 15m threshold: -0.3%
  - 60m threshold: -0.5%
  - min_conviction: -0.02
- Modifies policy.yaml on disk (requires bot restart to apply)
- Sends alert if alerting is enabled

**Design Rationale:**
- Prevents indefinite no-trade periods in extreme chop
- Requires restart to apply (prevents runtime calibration drift)
- One-shot adjustment (no runaway loosening)
- Logs warning and sends alert for visibility

## Expected Behavior Change

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Eligible assets | 10 | 10-15 | - |
| Triggers/cycle | 0 | 2-5 | 2-5 |
| Proposals/cycle | 0 | 1-3 | 1-3 |
| Execution rate | - | ~60-80% | >50% |

## Guardrails Maintained

✅ **Slippage budgets:** T1=20bps, T2=35bps, T3=60bps  
✅ **Direction filter:** only_upside=true (long-only)  
✅ **Jitter:** ±10% on sleep intervals  
✅ **Auto-backoff:** +15s at 70% utilization  
✅ **Junk exclusions:** USELESS-USD, FARTCOIN-USD  
✅ **ATR filter:** Still active (1.1x median)  
✅ **Price outlier detection:** Still active  
✅ **Stale quote rejection:** Still active  

## Testing Plan

Run bot for 10-20 cycles and monitor:

1. **Trigger Quality**
   - Count: Expect 2-5 triggers/cycle (was 0)
   - Types: Should see price_move, volume_spike, momentum
   - Assets: Should include quality alts if they meet depth requirements

2. **Proposal Generation**
   - Count: Expect 1-3 proposals/cycle (was 0)
   - Conviction: Should be ≥0.34 (lowered threshold)
   - Symbols: Should NOT include junk tokens

3. **Execution Quality**
   - Slippage: Must be within budget (T1≤20bps, T2≤35bps, T3≤60bps)
   - Fills: Should get partial or full fills
   - No rejected orders due to slippage overruns

4. **System Health**
   - No junk tokens in trigger list
   - Utilization stays <70%
   - Jitter working (sleep times vary ±10%)
   - Zero-proposal counter resets when proposals resume

## Rollback Plan

If loosening invites garbage or excessive false positives:

1. **Immediate:** Set `exchange.read_only=true` and touch `data/KILL_SWITCH`
2. **Revert thresholds in policy.yaml:**
   ```yaml
   price_move:
     pct_15m: 3.0    # back to original
     pct_60m: 5.0    # back to original
   volume_spike:
     ratio_1h_vs_24h: 1.8  # back to original
   ```
3. **Revert conviction:**
   ```yaml
   min_conviction_to_propose: 0.38  # back to original
   ```
4. **Revert ATR:**
   ```yaml
   atr_min_multiplier: 1.2  # back to original
   ```
5. **Restart bot**

## Alternative Considered (Not Implemented)

**Option B: Bounce/Mean-Reversion**
- Would allow downside triggers with bounce confirmation
- Requires additional logic to detect V-shapes
- Higher complexity, more false positives
- Rejected in favor of simpler breakout approach

## Recommendation

**Go 70%** - Calibrated for chop regime while maintaining all critical safety guardrails.

**Uncertainty (30%):**
- Chop regime may persist longer than expected (trigger drought continues)
- ATR filter may still be too restrictive even at 1.1x
- Min conviction 0.34 may still be too high for weak signals

**Next Steps:**
1. Run bot for 10-20 cycles
2. Monitor metrics (see Testing Plan above)
3. If still 0 proposals after 20 cycles, zero-trade sentinel will auto-loosen further
4. If proposals resume but execution quality poor, tighten slippage budgets

## Files Modified

- `config/policy.yaml`: Loosened thresholds, added size-aware depth
- `config/signals.yaml`: Capped max_triggers to 5
- `infra/state_store.py`: Zero-proposal counter + sentinel helpers
- `runner/main_loop.py`: Sentinel logic + auto-loosen method

## Commit Reference

Commit: `448f717` (auto-committed)  
Branch: `main`  
Pushed: 2025-01-11 23:30 UTC

## Monitoring Checklist

- [ ] Verify trigger count increases (expect 2-5)
- [ ] Verify proposal count increases (expect 1-3)
- [ ] Confirm no junk tokens in triggers
- [ ] Validate slippage within budgets
- [ ] Check zero-proposal counter resets
- [ ] Monitor execution rate (fills/proposals)
- [ ] Assess signal quality (avoid false positives)
