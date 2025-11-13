# Day Trader Profile Activation

**Date:** 2025-11-13  
**Account Size:** ~$500  
**Status:** ✅ Activated

## Problem

Bot was configured for **swing trading** (larger accounts, higher conviction bar):
- Min conviction: **0.34** (33% of signals blocked)
- Tier sizing: **2.0% / 1.2% / 0.5%** → too small for $500 account
- Position caps: **4.0% / 3.0% / 2.0%** → too tight for adds
- Min trade: **$9.00** → conviction-scaled trades ($7-8) rejected

**Result:** Triggers fired, rules proposed, **risk rejected everything** due to:
- `below_min_after_caps` (wants $7.19, needs $9.00)
- `position_size_with_pending` (4.54% > 4.0% cap)

Bot behaved like a **paranoid risk officer**, not a day trader.

## Solution: Profile-Based Configuration

Added **profile system** to `config/policy.yaml`:

```yaml
profile: day_trader  # Active profile

profiles:
  swing_trader:
    # Conservative for larger accounts ($2K+)
    min_conviction: 0.34
    tier_sizing: { T1: 2.0%, T2: 1.2%, T3: 0.5% }
    max_position_pct: { T1: 4.0%, T2: 3.0%, T3: 2.0% }
    min_trade_notional: $9.00
    
  day_trader:
    # Active for smaller accounts ($300-$1K)
    min_conviction: 0.30       # Lower bar → more setups
    tier_sizing: { T1: 3.0%, T2: 1.8%, T3: 0.7% }
    max_position_pct: { T1: 7.0%, T2: 4.5%, T3: 2.5% }
    min_trade_notional: $5.00  # Allows smaller trades
```

## Changes Applied

### 1. Lower Conviction Bar
**Before:** `min_conviction: 0.34`  
**After:** `min_conviction: 0.30`

**Effect:** More price_move/volume triggers convert to proposals

### 2. Larger Base Sizing
**Before:** T1=2.0%, T2=1.2%, T3=0.5%  
**After:** T1=3.0%, T2=1.8%, T3=0.7%

**Effect @ $500 NAV:**
- T1: $10.00 → **$15.00** (clears $9 min with headroom)
- T2: $6.00 → **$9.00** (clears min notional)
- T3: $2.50 → **$3.50** (still small but viable)

### 3. Higher Position Caps
**Before:** T1=4.0%, T2=3.0%, T3=2.0%  
**After:** T1=7.0%, T2=4.5%, T3=2.5%

**Effect @ $500 NAV:**
- T1 cap: $20 → **$35** (allows $15 initial + $20 adds)
- T2 cap: $15 → **$22.50** (room for pyramiding)
- T3 cap: $10 → **$12.50** (enough for small positions)

### 4. Lower Min Trade Notional
**Before:** 
- Risk: `min_trade_notional_usd: 5`
- Execution: `min_notional_usd: 9.0` ❌ **MISMATCH**

**After:**
- Risk: `min_trade_notional_usd: 5`
- Execution: `min_notional_usd: 5.0` ✅ **ALIGNED**

**Effect:** Conviction-scaled $7-8 trades now **pass** both risk and execution checks

## Expected Behavior Changes

### Before (Swing Trader Profile)
```
Cycle 1:
  Triggers: XRP (2.5%), XLM (2.8%)
  Proposals: XRP $7.19 (1.4%), XLM $4.11 (0.8%)
  Risk: REJECT XRP (below_min_after_caps: $7.19 < $9.00)
        REJECT XLM (position_size_with_pending: 4.54% > 4.0%)
  Result: 0 trades

Cycle 2: (same pattern)
Cycle 3: (same pattern)
...
```

### After (Day Trader Profile)
```
Cycle 1:
  Triggers: XRP (2.5%), XLM (2.8%)
  Proposals: XRP $10.79 (2.1%), XLM $6.17 (1.2%)  ← LARGER
  Risk: APPROVE XRP ($10.79 > $5.00, under 7% cap)
        APPROVE XLM ($6.17 > $5.00, under 4.5% cap)
  Result: 2 trades executed ✅

Cycle 2:
  Triggers: XRP (1.2%), DOGE (3.1%)
  Proposals: XRP add $8.50, DOGE $11.20
  Risk: APPROVE XRP (total 3.8% < 7% cap)
        APPROVE DOGE (new position 2.2% < 7% cap)
  Result: 2 more trades ✅
```

**Key Differences:**
- More proposals pass conviction bar (0.30 vs 0.34)
- Larger trade sizes clear min notional
- More room for adds and pyramiding
- Higher activity = more like actual day trading

## Safety Guardrails (Still Active)

Even with day_trader profile, you're still protected by:

✅ **No leverage** (100% cash collateral)  
✅ **Per-asset caps** (7% max per T1 coin)  
✅ **Global cap** (95% max total exposure)  
✅ **Stop losses** (1.5% per position)  
✅ **Daily loss limit** (-3% NLV → halt)  
✅ **Maker-first orders** (post-only TTL)  
✅ **Min notional** ($5 floor, no dust)  
✅ **Cooldowns** (30min after loss per symbol)

## Risk Assessment

**Account Size:** ~$500

**Worst-Case Scenarios:**

1. **Max drawdown (all stops hit):**
   - 12 positions × 1.5% stop = **-18% NLV** = -$90
   - Daily stop (-3%) should trigger before this

2. **Single bad trade:**
   - Largest position: $35 (7% cap)
   - Stop loss: 1.5% = **-$0.53** (-0.1% NLV)

3. **Overtrading fees:**
   - 10 trades/day × $10 avg × 0.6% taker = **-$0.60/day**
   - Covered by win rate if >52%

**Comparison to Human Day Trader:**
- Human with $500: might do 3-5% per trade, no stops, yolo into momentum
- Your bot: 1.5-3% per trade, hard stops, maker orders, cooldowns

**Assessment:** This is **still conservative** for active trading. The "day trader" label is relative to your previous ultra-strict swing profile.

## Performance Expectations

### What Should Improve
- ✅ Trade frequency: 0-2 → **5-10 trades/day**
- ✅ Fill rate: 5% → **40-60%** (proposals actually execute)
- ✅ Capital efficiency: 10-20% → **50-70% deployed**

### What Won't Change
- ⚠️ Win rate (still depends on triggers/regime)
- ⚠️ Slippage costs (still using maker orders)
- ⚠️ Overnight gaps (no 24/7 monitoring built in)

### Success Metrics (1 week)
- **Good:** 30+ trades, 52%+ win rate, -1% to +3% net return
- **Acceptable:** 20-30 trades, 48-52% win rate, -2% to +1% net
- **Bad:** <15 trades (still too restrictive) OR >60 trades (overtrading)

## Switching Profiles

To revert to swing trader mode:

```yaml
# At top of config/policy.yaml
profile: swing_trader  # Change from day_trader
```

Then restart the bot. All dependent settings will revert to conservative values.

## Files Modified

1. **config/policy.yaml**
   - Added `profile` selector (line 4)
   - Added `profiles` definitions (lines 7-38)
   - Updated `risk.min_trade_notional_usd: 5`
   - Updated `risk.max_position_size_pct: 7.0`
   - Updated `strategy.base_position_pct` (3.0% / 1.8% / 0.7%)
   - Updated `strategy.min_conviction_to_propose: 0.30`
   - Updated `execution.min_notional_usd: 5.0`

## Next Steps

1. **Restart bot:** `./app_run_live.sh --loop`
2. **Watch first 5 cycles:**
   - Verify proposals are larger ($9-15 range)
   - Verify risk approvals increase
   - Check for overtrading (should be <4/hour)
3. **Monitor for 24 hours:**
   - Count total trades
   - Check win rate
   - Watch for excessive fees
4. **Tune if needed:**
   - Too many trades → raise `min_conviction` to 0.32
   - Still too few → check if universe is too restrictive
   - Stops hitting too often → widen stop % or check regime

---

**Status:** Ready for live testing with day_trader profile active! 🚀
