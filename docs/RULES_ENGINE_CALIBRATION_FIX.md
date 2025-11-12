# Rules Engine Calibration Fix

**Date:** 2025-11-11  
**Issue:** 15 triggers detected → 0 proposals generated (broken funnel)  
**Root Cause:** Price change thresholds in rules engine **WAY TOO HIGH** for 30s intervals

---

## Problem Analysis

### Symptoms
```
2025-11-11 22:39:40 INFO core.triggers: Found 15 triggers
2025-11-11 22:39:40 INFO strategy.rules_engine: Generated 0 trade proposals (filtered by min_conviction=0.38)
```

- **15 triggers fired** (volume spikes, breakouts, momentum) → Triggers are working ✓
- **0 proposals created** → Rules engine rejecting everything ✗

### Root Cause

Rules engine price thresholds were **calibrated for daily/hourly trading**, not 30-second intervals:

**Before (BROKEN):**
```python
# _rule_price_move
if price_change > 3.0:     # ← Too strict for 30s intervals!
    side = "BUY"
elif price_change < -5.0:  # ← Way too strict!
    side = "BUY"

# _rule_volume_spike  
if trigger.price_change_pct > 5.0:     # ← Too strict!
    side = "BUY"
elif trigger.price_change_pct < -5.0:  # ← Too strict!
    side = "BUY"
```

**Reality:**
- In crypto at 30s intervals, **1-2% moves are significant**
- Requiring 3-5% moves means you only trade on **extreme volatility**
- System was essentially in "crash-only" mode

---

## Solution Applied

### Changes Made

**File:** `strategy/rules_engine.py`

**1. Price Move Rule (lines 193-206)**
```python
# BEFORE: Too strict
if price_change > 3.0:           # Only catches big rallies
elif price_change < -5.0:        # Only catches crashes

# AFTER: Realistic for 30s intervals
if price_change > 1.5:           # ✓ Catches moderate upward momentum
elif price_change < -2.5:        # ✓ Catches moderate reversals
```

**2. Volume Spike Rule (lines 247-256)**
```python
# BEFORE: Too strict
if trigger.price_change_pct > 5.0:      # Only extreme moves
elif trigger.price_change_pct < -5.0:   # Only crashes

# AFTER: Balanced sensitivity
if trigger.price_change_pct > 2.0:      # ✓ Catches continuation setups
elif trigger.price_change_pct < -2.0:   # ✓ Catches reversal setups
```

**3. Added Conviction Logging (lines 161-173)**
```python
# Now logs at INFO level (was DEBUG):
logger.info(
    f"✓ Proposal: {proposal.side} {proposal.symbol} "
    f"size={proposal.size_pct:.1f}% conf={proposal.confidence:.2f} reason='{proposal.reason}'"
)

logger.info(
    f"✗ Rejected: {proposal.symbol} conf={proposal.confidence:.2f} "
    f"< min_conviction={self.min_conviction_to_propose} reason='{proposal.reason}'"
)
```

**4. Added Trigger Details Logging (`core/triggers.py`, line 160)**
```python
# Logs top 5 triggers with details:
logger.info(
    f"  Trigger #{i+1}: {sig.symbol} {sig.trigger_type} "
    f"strength={sig.strength:.2f} conf={sig.confidence:.2f} "
    f"price_chg={sig.price_change_pct:.2f}% vol_ratio={sig.volume_ratio:.2f}x"
)
```

---

## Expected Impact

### Before Fix
- **Triggers:** 14-15 per cycle ✓
- **Proposals:** 0 per cycle ✗ (100% rejection rate)
- **Executions:** 0 per cycle ✗

### After Fix (Expected)
- **Triggers:** 10-15 per cycle (unchanged)
- **Proposals:** 3-6 per cycle ✓ (20-40% conversion)
- **Executions:** 1-3 per cycle ✓ (after risk filters)

---

## Verification Steps

### 1. Restart Bot
```bash
# Stop current instance (Ctrl+C in run_live.sh terminal)
# Then restart:
./run_live.sh --once    # Test with single cycle first
```

### 2. Check Logs for New Output
```bash
tail -f logs/live_*.log | grep -E "(Trigger #|Proposal|Rejected)"
```

### Expected Log Output:
```
INFO core.triggers: Found 12 triggers
INFO core.triggers:   Trigger #1: BTC-USD momentum strength=0.73 conf=0.82 price_chg=2.1%
INFO core.triggers:   Trigger #2: ETH-USD volume_spike strength=0.61 conf=0.75 price_chg=1.8%
INFO strategy.rules_engine: ✓ Proposal: BUY BTC-USD size=1.8% conf=0.82 reason='Momentum up +2.1%'
INFO strategy.rules_engine: ✓ Proposal: BUY ETH-USD size=1.2% conf=0.75 reason='Volume spike 2.3x + price up 1.8%'
INFO strategy.rules_engine: Generated 2 trade proposals (filtered by min_conviction=0.38)
```

### 3. Validate Funnel Metrics
After 1 hour of continuous running, check:
```python
# Should see healthy conversion rates:
Triggers:  ~10-15 per cycle
Proposals: ~3-6 per cycle   (20-40% conversion from triggers)
Blocked:   ~1-3 per cycle   (by risk engine - expected)
Executed:  ~1-2 per cycle   (actual trades)
```

---

## Rollback Plan

If too many trades are generated (>10 per cycle):

**Option 1: Tighten thresholds slightly**
```python
# In strategy/rules_engine.py:
if price_change > 2.0:      # Was 1.5
if price_change < -3.0:     # Was -2.5

if trigger.price_change_pct > 2.5:    # Was 2.0
if trigger.price_change_pct < -2.5:   # Was -2.0
```

**Option 2: Increase min_conviction**
```yaml
# In config/policy.yaml:
strategy:
  min_conviction_to_propose: 0.42   # Was 0.38
```

**Option 3: Full rollback**
```bash
git checkout HEAD~1 strategy/rules_engine.py core/triggers.py
```

---

## Related Configuration

These settings work **together** to calibrate the funnel:

### Trigger Detection (`config/policy.yaml`)
```yaml
triggers:
  price_move:
    pct_15m: 3.0      # Trigger fires if 15m move ≥ 3%
    pct_60m: 5.0      # Trigger fires if 60m move ≥ 5%
  volume_spike:
    ratio_1h_vs_24h: 1.8   # Trigger fires if 1h vol ≥ 1.8× avg
  min_score: 0.2      # Minimum strength × confidence to qualify
```

### Rules Engine (`strategy/rules_engine.py`)
```python
# Price thresholds to CREATE proposals (this fix):
price_change > 1.5       # Momentum continuation
price_change < -2.5      # Reversal bounce
price_change > 2.0       # Volume spike continuation
price_change < -2.0      # Volume spike reversal
```

### Proposal Filtering (`config/policy.yaml`)
```yaml
strategy:
  min_conviction_to_propose: 0.38   # Minimum confidence to ACCEPT proposal
```

**Calibration Hierarchy:**
```
Trigger threshold (0.2) → Rule logic (1.5%/2.0%) → Conviction filter (0.38) → Risk checks
         ↓                        ↓                         ↓                    ↓
      15 triggers            6 proposals              4 accepted          1-2 executed
```

---

## Lessons Learned

1. **Threshold Mismatch:** Rules engine was calibrated for daily/hourly bars, but system runs on 30s intervals
2. **Silent Failures:** Triggers fired successfully, but proposals rejected silently (was DEBUG logging)
3. **Funnel Visibility:** Need INFO-level logging to diagnose conversion bottlenecks
4. **Parameter Interdependence:** Quote age, trigger thresholds, rule logic, and conviction must all align

---

## Status

- ✅ **Root cause identified:** Price thresholds too high (3-5%) for 30s intervals
- ✅ **Fix applied:** Lowered to 1.5-2.5% for realistic sensitivity
- ✅ **Logging enhanced:** Trigger details and proposal rejections at INFO level
- ⏳ **Testing required:** Restart bot and monitor for 1-2 hours
- ⏳ **Validation:** Should see 3-6 proposals/cycle, 1-3 executions/cycle

**Next Action:** Stop current instance (Ctrl+C) and restart with `./run_live.sh` to apply changes.
