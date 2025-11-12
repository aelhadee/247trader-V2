# Regime-Aware Calibration - 2025-01-11

## Problem Statement

After implementing safety improvements (direction filter, ATR filter, slippage budgets), the system became **over-tightened** with blanket thresholds:

```
Status: 10 eligible assets → 0 triggers → 0 proposals
Issue: System starved - no actionable signals in chop regime
```

**Root Causes:**
1. **Blanket thresholds** don't adapt to market regime (chop needs looser bars than bull)
2. **T1 depth=$100k floor** too high for $490 portfolio (unrealistic size requirement)
3. **No recovery mechanism** for prolonged zero-trigger periods

## Solution: Regime-Aware + Size-Aware + Bounded Auto-Tune

### 1. Regime-Aware Trigger Thresholds

**config/signals.yaml** - Adaptive thresholds per regime:

```yaml
regime_thresholds:
  chop:
    pct_change_15m: 2.0        # Realistic for BTC/ETH/SOL (was 2.5% blanket)
    pct_change_60m: 4.0        # Sustained move (was 4.5% blanket)
    volume_ratio_1h: 1.9       # Demand real interest
    atr_filter_min_mult: 1.1   # Less restrictive volatility filter
  bull:
    pct_change_15m: 3.5        # Higher bar for momentum expected
    pct_change_60m: 7.0        # Strong sustained moves
    volume_ratio_1h: 2.0       # Strong volume confirmation
    atr_filter_min_mult: 1.2   # Normal volatility requirement
  bear:
    pct_change_15m: 3.0        # Moderate threshold
    pct_change_60m: 7.0        # Strong moves only
    volume_ratio_1h: 2.0       # High volume confirmation
    atr_filter_min_mult: 1.2   # Normal volatility requirement
```

**Implementation (core/triggers.py):**
- TriggerEngine reads `regime_thresholds` from signals.yaml
- `_check_price_move()`, `_check_volume_spike()`, `_check_atr_filter()` accept `regime` parameter
- Applies appropriate thresholds based on current market regime
- Logs regime in trigger reason strings for visibility

**Benefits:**
- **Chop regime:** Lower bars (2%/4%) allow signals without inviting junk
- **Bull regime:** Higher bars (3.5%/7%) demand stronger momentum
- **Bear regime:** Moderate bars (3%/7%) with high volume confirmation
- **Adaptive:** Same safety framework, different sensitivity per context

### 2. Size-Aware Liquidity Rules

**config/policy.yaml** - Scales with account size:

```yaml
liquidity:
  # Size-aware depth requirement (scales with order size)
  require_depth_mult: 10      # Depth ≥ 10× order notional
  
  # Tier-specific constraints
  spreads_bps:
    T1: 20                    # Max 20bps spread
    T2: 35                    # Max 35bps spread
    T3: 60                    # Max 60bps spread
  
  min_depth_floor_usd:
    T1: 50_000                # $50K floor (was $100K - realistic for small accounts)
    T2: 25_000                # $25K floor
    T3: 10_000                # $10K floor
  
  slippage_budget_bps:
    T1: 20                    # Max 20bps total cost (slippage + fees)
    T2: 35                    # Max 35bps total cost
    T3: 60                    # Max 60bps total cost
  
  # Eligibility persistence (avoid thrashing)
  eligibility_persistence:
    ineligible_grace_cycles: 5   # Don't purge until 5 consecutive ineligible
    eligible_grace_cycles: 2     # Must be eligible 2× before adding
```

**Benefits:**
- **10× notional rule:** Ensures adequate depth for any account size ($50 order needs $500 depth)
- **T1 floor lowered:** $50k realistic for $490 portfolio (was $100k)
- **Grace cycles:** Prevents thrashing when assets flip near eligibility thresholds
- **Slippage budgets:** Hard caps on total execution cost per tier

### 3. Bounded Auto-Tune

**config/app.yaml** - Self-recovery with hard floors:

```yaml
auto_tune:
  # Bounded auto-loosening for prolonged zero-trigger periods
  zero_trigger_cycles: 12     # Trigger after 12 consecutive 0-trigger cycles
  loosen:
    pct_change_15m_delta: -0.2    # Reduce chop 15m threshold by 0.2%
    pct_change_60m_delta: -0.3    # Reduce chop 60m threshold by 0.3%
    min_conviction_delta: -0.02   # Reduce conviction by 0.02
  floors:
    pct_change_15m: 1.2       # DO NOT go below 1.2% for 15m
    pct_change_60m: 2.5       # DO NOT go below 2.5% for 60m
    min_conviction: 0.30      # DO NOT go below 0.30 conviction
```

**Implementation (runner/main_loop.py):**
- Track `zero_trigger_cycles` in state (incremented when triggers=0)
- After 12 consecutive cycles with 0 triggers, call `_apply_bounded_auto_loosen()`
- Reads auto_tune config, applies deltas to chop regime thresholds
- **Respects hard floors** to prevent runaway loosening
- One-shot adjustment (flag prevents repeated loosening)
- Modifies signals.yaml and policy.yaml on disk (requires restart to apply)
- Logs warning and sends alert if enabled

**Benefits:**
- **Early detection:** Zero-trigger cycles detected earlier than zero-proposals
- **Faster response:** 12 cycles (6 minutes) vs previous 20 cycles
- **Bounded:** Hard floors prevent excessive loosening
- **One-shot:** Flag prevents repeated adjustments
- **Safe:** Requires restart to apply (no runtime drift)

### 4. Jitter + Auto-Backoff

**config/app.yaml** - Load distribution and overload protection:

```yaml
loop:
  interval:
    seconds: 30              # Base interval
    jitter_pct: 10          # ±10% randomization (27-33s range)
  utilization_autobackoff:
    target_util_pct: 70     # Trigger backoff at 70% utilization
    backoff_seconds: 15     # Add 15s when over target
```

**Benefits:**
- **Jitter:** Prevents synchronized API bursts across multiple bot instances
- **Auto-backoff:** Prevents missed cycles and SLO violations under load
- **Self-regulating:** System adapts to actual execution time

## Expected Behavior Change

| Metric | Before (Starved) | After (Regime-Aware) |
|--------|------------------|----------------------|
| Eligible assets | 10 | **12-18** (more realistic depth floors) |
| Triggers/cycle (chop) | 0 | **1-5** (2%/4% thresholds) |
| Proposals/cycle | 0 | **1-3** (min_conviction=0.34) |
| Auto-recovery | None | **Yes** (bounded loosen after 12 cycles) |

## Guardrails Maintained

✅ **Slippage budgets:** T1=20bps, T2=35bps, T3=60bps (execution cost caps)  
✅ **Direction filter:** only_upside=true (long-only strategy)  
✅ **ATR filter:** Regime-aware (chop=1.1x, bull/bear=1.2x median)  
✅ **Price outlier detection:** Max 10% deviation without volume confirmation  
✅ **Stale quote rejection:** Max 60s age for OHLCV data  
✅ **Bounded auto-tune:** Hard floors prevent runaway loosening  
✅ **Jitter:** ±10% prevents synchronized API bursts  

## Files Modified

### Configuration
- **config/policy.yaml** - Size-aware liquidity, eligibility_persistence, min_conviction=0.34
- **config/signals.yaml** - regime_thresholds (chop/bull/bear), only_upside flag
- **config/app.yaml** - auto_tune config, jitter, utilization_autobackoff

### Core Logic
- **core/triggers.py** - Regime-aware threshold selection in:
  - `__init__`: Load regime_thresholds from signals.yaml
  - `_check_price_move()`: Apply regime-specific pct_change_15m/60m
  - `_check_volume_spike()`: Apply regime-specific volume_ratio_1h
  - `_check_atr_filter()`: Apply regime-specific atr_filter_min_mult

### Runner
- **runner/main_loop.py**:
  - Zero-trigger sentinel: Track and detect 0-trigger cycles
  - `_apply_bounded_auto_loosen()`: Apply bounded adjustments with floors
  - Reset counter when triggers resume

### Infrastructure
- **infra/state_store.py**:
  - Added `zero_trigger_cycles` counter to DEFAULT_STATE
  - Added `auto_tune_applied` flag to DEFAULT_STATE
  - Added `get()` method for generic state access

## Testing Plan

### 1. Universe Size Validation
- [ ] Verify **12-18 eligible assets** (was 10)
- [ ] Confirm PEPE, VET qualify as T1 with $50k floor
- [ ] Check depth_mult=10 rule working (depth ≥ 10× notional)

### 2. Trigger Quality (Chop Regime)
- [ ] Verify **1-5 triggers per cycle** (was 0)
- [ ] Confirm 2%/4% thresholds firing on BTC/ETH/SOL
- [ ] Validate ATR filter at 1.1x (less restrictive)
- [ ] No junk tokens in trigger list

### 3. Proposal Generation
- [ ] Verify **1-3 proposals per cycle** (was 0)
- [ ] Confirm min_conviction=0.34 working
- [ ] Check conviction scores in proposal logs

### 4. Execution Quality
- [ ] Slippage **within budgets** (T1≤20bps, T2≤35bps, T3≤60bps)
- [ ] Depth check passing (depth ≥ 10× notional)
- [ ] Spread check passing (T1≤20bps, T2≤35bps, T3≤60bps)

### 5. Auto-Tune Validation
- [ ] If 0 triggers for 12 cycles, bounded loosen applied
- [ ] Verify floors enforced (15m≥1.2%, 60m≥2.5%, conviction≥0.30)
- [ ] Confirm one-shot (auto_tune_applied flag prevents repeat)
- [ ] Counter resets when triggers resume

### 6. System Health
- [ ] Jitter working (sleep times vary ±10%)
- [ ] Utilization stays <70%
- [ ] Auto-backoff triggers if needed (+15s at >70%)

## Regime Transition Behavior

### Chop → Bull
- Thresholds tighten: 2%/4% → 3.5%/7%
- Fewer triggers expected (stronger signals required)
- No action needed - automatic based on regime detection

### Bull → Chop
- Thresholds loosen: 3.5%/7% → 2%/4%
- More triggers expected (lower bar acceptable)
- Auto-recovery available if still 0 triggers

### Chop → Bear
- Thresholds moderate: 2%/4% → 3%/7%
- Long-only filter still active (only_upside=true)
- May see 0 triggers in bear (expected for long-only)

## Rollback Plan

If regime-aware calibration causes issues:

1. **Immediate:** Set `exchange.read_only=true` and touch `data/KILL_SWITCH`

2. **Revert to blanket thresholds** - Edit signals.yaml:
   ```yaml
   # Comment out regime_thresholds section
   # Use policy.yaml fallback values
   ```

3. **Revert depth floor** - Edit policy.yaml:
   ```yaml
   min_depth_floor_usd:
     T1: 100_000  # back to original
   ```

4. **Disable auto-tune** - Edit app.yaml:
   ```yaml
   auto_tune:
     zero_trigger_cycles: 9999  # effectively disable
   ```

5. **Restart bot** to apply changes

## Alternative Approaches (Not Implemented)

### Option B: Machine Learning Threshold Optimization
- **Pros:** Could discover optimal thresholds per asset/regime
- **Cons:** Black box, training data needed, overfitting risk
- **Decision:** Rules-first approach preferred for transparency

### Option C: Percentage-Based Depth Floors
- **Pros:** Automatically scales with NLV
- **Cons:** May allow too-small depth on large accounts
- **Decision:** Hybrid approach chosen (10× notional + floor)

## Monitoring Checklist

Daily (First Week):
- [ ] Track eligible asset count (target: 12-18)
- [ ] Track triggers per cycle (chop target: 1-5)
- [ ] Track proposals per cycle (target: 1-3)
- [ ] Monitor auto-tune activations (should be rare)
- [ ] Check execution slippage (within budgets)
- [ ] Validate no junk tokens trading

Weekly (Ongoing):
- [ ] Review regime transitions (smooth threshold changes)
- [ ] Analyze trigger quality (false positive rate)
- [ ] Assess fill rates (proposals → executions)
- [ ] Check for threshold floor hits (may need adjustment)

## Recommendation

**Go 75%** - Regime-aware thresholds are realistic for chop without inviting junk. Size-aware liquidity scales with account size. Bounded auto-tune provides self-recovery with hard safety floors.

**Uncertainty (25%):**
- Chop regime may persist longer than expected (auto-tune will handle)
- 2%/4% thresholds may still be too tight for some alts (monitor trigger diversity)
- Auto-tune floors may need adjustment after observing behavior

**Next Steps:**
1. Run bot for 10-20 cycles with regime=chop
2. Validate metrics match expected behavior
3. If auto-tune triggers, review floor settings
4. If proposals still 0, consider temporary manual adjustment: 1.8%/3.5%

## Success Criteria

✅ System considered **properly calibrated** when:
- 12-18 eligible assets consistently
- 1-5 triggers per cycle in chop
- 1-3 proposals per cycle
- 60-80% of proposals execute
- Slippage within budgets (no rejections)
- Auto-tune rarely/never triggers (threshold sweet spot found)

## Commit Reference

Commit: `27800ed`  
Branch: `main`  
Pushed: 2025-01-11 23:45 UTC

---

**TL;DR:** Replaced blanket thresholds with regime-aware calibration (chop: 2%/4%, bull: 3.5%/7%, bear: 3%/7%). Added size-aware liquidity (10× notional + $50k T1 floor). Implemented bounded auto-tune with hard floors for self-recovery. Expected: 0 triggers → 1-5 triggers → 1-3 proposals in chop regime. Go 75%.
