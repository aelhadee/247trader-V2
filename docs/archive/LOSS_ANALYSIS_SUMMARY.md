# Loss Analysis Summary - Exit Timing Optimization

**Analysis Date:** November 10, 2025
**Objective:** Reduce max consecutive losses by improving exit timing

## Key Findings

### Cross-Period Loss Patterns

| Period | Total Trades | Losing Trades | Loss Rate | Max Consecutive | Avg Loss |
|--------|--------------|---------------|-----------|-----------------|----------|
| Bull (Aug-Oct) | 157 | 65 | 41.4% | 15 | -3.46% |
| Bear (Sep) | 54 | 21 | 38.9% | 8 | -3.28% |
| CHOP (Nov) | 8 | 1 | 12.5% | 1 | -0.43% |

### Critical Discovery: Max Hold is the Problem

**Bull Period:**
- 54/65 losses (83%) hit max_hold (48h timeout)
- Only 9/65 losses (14%) hit stop_loss (-8%)
- Average loss: -3.46% (well below stop threshold)

**Bear Period:**
- 16/21 losses (76%) hit max_hold
- Only 3/21 losses (14%) hit stop_loss
- Average loss: -3.28%

**CHOP Period:**
- 1/1 losses (100%) hit max_hold
- Zero stop losses
- Average loss: -0.43%

### Loss Exit Reason Breakdown

**All Periods Combined:**
- **max_hold**: 71/87 losing trades (82%)
- **stop_loss**: 12/87 losing trades (14%)
- **backtest_end**: 4/87 losing trades (5%)

## Root Cause Analysis

The strategy's main loss mechanism is **holding losing positions for too long** (48 hours), not getting stopped out:

1. **Trades slowly bleed** rather than crash
2. Most losses accumulate to -2% to -4% over 48 hours
3. The -8% stop loss rarely triggers (only 14% of losses)
4. Max consecutive losses happen when multiple trades all hit max_hold in sequence

### Why This Happens

Looking at worst losses:
- Bull worst: SOL -12.65% (stop_loss), but most losses are -3% to -5% (max_hold)
- Bear worst: ETH -8.70% (stop_loss), but most losses are -2% to -7% (max_hold)
- Trades that slowly deteriorate don't recover within 48h window

## Recommendations (Priority Order)

### 1. **REDUCE max_hold_hours** (HIGH IMPACT)
**Current:** 48 hours
**Proposed:** 24-30 hours

**Rationale:**
- 82% of losses hit max_hold
- Average loss at max_hold is only -2% to -4%
- Trades that don't profit in 24h rarely recover
- Shorter hold = faster capital rotation = fewer consecutive losses

**Expected Impact:**
- Reduce max consecutive losses from 15 → 8-10
- Cut average loss from -3.4% → -2.5%
- May slightly reduce win rate (currently 58-61%)
- But improve capital efficiency

**Implementation:**
```yaml
# config/policy.yaml
trading_constraints:
  max_hold_hours: 24  # down from 48
```

### 2. **ADD trailing stop after 12h** (MEDIUM IMPACT)
Instead of aggressive trailing stops from entry, add them after trade has time to develop:

**Current:** No trailing stops (tested and removed)
**Proposed:** Activate trailing stop after 12h hold time

**Rationale:**
- Early trailing stops (tested) reduced profitability by 27-72%
- But late-stage trailing stops could protect profits
- Trades that haven't profited after 12h are likely to hit max_hold

**Implementation Logic:**
```python
if trade.hold_hours >= 12:
    if current_pnl_pct > 3.0:  # Only if in profit
        trailing_stop = max_price_seen * 0.95  # 5% trailing
```

### 3. **TIGHTEN stop_loss for low-tier assets** (LOW IMPACT)
**Current:** -8% stop for all assets
**Proposed:** -6% stop for Tier 2/3 assets, keep -8% for Tier 1

**Rationale:**
- Lower-tier assets (DOGE, XRP) show up frequently in worst losses
- These are more volatile and less likely to recover
- Premium assets (BTC, ETH, SOL) can justify wider stops

**Implementation:**
```python
# In risk engine
if asset.tier == 1:
    stop_loss_pct = 8.0
else:
    stop_loss_pct = 6.0  # Tighter for lower tier
```

### 4. **ADD momentum check at 24h mark** (MEDIUM IMPACT)
Force exit at 24h if trade shows negative momentum:

**Logic:**
```python
if hold_hours == 24:
    if current_pnl_pct < -1.0:  # Losing
        exit_reason = "momentum_check"
    elif current_pnl_pct < 2.0:  # Not profitable enough
        exit_reason = "momentum_check"
```

## Testing Plan

### Phase 1: Test max_hold reduction
1. Test max_hold = 36h on all 3 periods
2. Test max_hold = 30h on all 3 periods
3. Test max_hold = 24h on all 3 periods
4. Compare: consecutive losses, avg loss, win rate, profit factor

### Phase 2: Test late trailing stops
1. Add trailing stop after 12h (if > 3% profit)
2. Test 5%, 6%, 7% trailing distances
3. Measure impact on winners vs losers

### Phase 3: Test tiered stops
1. Implement -6% stop for Tier 2/3
2. Keep -8% for Tier 1
3. Measure stop_loss frequency increase

### Phase 4: Momentum check
1. Add 24h momentum exit
2. Test thresholds: -1%, -2%, 0%
3. Measure reduction in max_hold exits

## Expected Outcomes

**Conservative (max_hold = 36h):**
- Max consecutive losses: 15 → 10-12
- Win rate: 58% → 56%
- Profit factor: 2.0 → 1.9

**Aggressive (max_hold = 24h):**
- Max consecutive losses: 15 → 6-8
- Win rate: 58% → 52-54%
- Profit factor: 2.0 → 1.7-1.8
- BUT: Faster capital rotation = more trades = potentially higher total return

## Implementation Priority

**Immediate (Today):**
1. Reduce max_hold_hours to 36h
2. Run full multi-period backtest
3. Compare results

**Next Session:**
1. If 36h works well, test 30h and 24h
2. Implement momentum check at 24h mark
3. Test late-stage trailing stops

**Future:**
1. Tiered stop losses by asset tier
2. Volatility-adjusted hold times
3. Regime-specific max_hold (shorter in CHOP, longer in BULL)

## Key Metrics to Track

- Max consecutive losses (target: < 10)
- Average loss magnitude (target: < -2.5%)
- Win rate (acceptable: > 50%)
- Profit factor (acceptable: > 1.5)
- Trades per period (expect increase with shorter holds)
- Capital efficiency (return / avg capital deployed)

## Conclusion

The strategy's main weakness is **holding losing positions too long**, not getting stopped out too early. By reducing max_hold from 48h to 24-36h, we can:

1. Cut average losses by 20-30%
2. Reduce consecutive losses from 15 to 6-10
3. Improve capital rotation
4. Maintain profitability across all market regimes

**Next Step:** Implement max_hold = 36h and run comprehensive backtest.
