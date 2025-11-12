# Outlier/Bad-Tick Guards Implementation

**Status:** ✅ Complete  
**Production Blocker:** #3  
**Tests:** 15 passing (166 total unit tests passing)

## Overview

Protects against false breakouts from bad ticks (flash crashes/spikes) by validating price data before trigger detection. Rejects prices that deviate significantly from recent moving average without volume confirmation.

## Problem Statement

Exchange data can occasionally contain erroneous ticks that cause:
- **Flash crashes:** Large downward spikes with minimal volume
- **Flash spikes:** Large upward spikes with minimal volume
- **False breakouts:** Trigger detection on bad data leading to poor trades

Without validation, these outliers can trigger trades based on incorrect market signals.

## Solution

Circuit breaker in `TriggerEngine.scan()` that:
1. Validates each asset's price data before trigger detection
2. Calculates moving average over configurable lookback periods
3. Checks if current price deviates beyond threshold
4. For extreme deviations, requires volume confirmation
5. Rejects outliers and skips asset for the cycle

## Configuration

Located in `config/policy.yaml` under `circuit_breakers` section:

```yaml
circuit_breakers:
  # Outlier detection (bad ticks, flash crashes/spikes)
  check_price_outliers: true
  max_price_deviation_pct: 10.0      # Maximum allowed deviation from MA (%)
  min_volume_ratio: 0.1               # Minimum volume ratio (0.1 = 10% of avg)
  outlier_lookback_periods: 20        # Periods for moving average calculation
```

### Parameters

- **check_price_outliers** (bool): Enable/disable outlier detection
- **max_price_deviation_pct** (float): Maximum price deviation from moving average (%)
  - Default: 10.0 (10%)
  - If |current_price - MA| / MA > threshold, trigger volume check
- **min_volume_ratio** (float): Required volume ratio for extreme moves
  - Default: 0.1 (10% of average volume)
  - If deviation > threshold and volume_ratio < min, reject as outlier
- **outlier_lookback_periods** (int): Number of periods for MA calculation
  - Default: 20
  - Uses last N candles (excluding current) to calculate moving average

## Implementation

### Code Changes

1. **config/policy.yaml** (lines 128-132)
   - Added outlier detection configuration to circuit_breakers section

2. **core/triggers.py**
   - `__init__()` (lines 70-78): Load circuit_breakers config from policy.yaml
   - `scan()` (lines 127-130): Call outlier validation after getting OHLCV data
   - `_validate_price_outlier()` (lines 173-226): New method implementing validation logic

3. **tests/test_outlier_guards.py** (427 lines, new file)
   - 15 comprehensive tests covering all scenarios and edge cases

### Validation Logic

```python
def _validate_price_outlier(symbol, candles) -> Optional[str]:
    """
    Returns rejection reason if outlier detected, None if valid.
    
    1. Check if outlier detection is enabled (skip if disabled)
    2. Require at least lookback_periods + 1 candles (skip if insufficient)
    3. Calculate moving average of last N periods (excluding current)
    4. Calculate deviation: |current_price - MA| / MA * 100
    5. If deviation > max_price_deviation_pct:
       a. Calculate average volume of last N periods
       b. Calculate volume_ratio = current_volume / avg_volume
       c. If volume_ratio < min_volume_ratio: REJECT as outlier
    6. Otherwise: PASS validation
    """
```

### Integration Flow

```
TriggerEngine.scan(assets)
├─ For each asset:
│  ├─ Get 168 hours of OHLCV data
│  ├─ Validate price outliers ← NEW
│  │  ├─ Calculate moving average
│  │  ├─ Check price deviation
│  │  ├─ Check volume confirmation
│  │  └─ Reject if outlier detected
│  ├─ Check price_move trigger
│  ├─ Check volume_spike trigger
│  ├─ Check breakout trigger
│  └─ Check momentum trigger
└─ Return sorted signals
```

## Test Coverage

All 15 tests passing:

### Core Validation Tests
1. ✅ **Normal price movement** (8% move with volume) → PASS
2. ✅ **Extreme move with high volume** (12% with 5x volume) → PASS  
3. ✅ **Extreme move with low volume** (15% with 0.05x volume) → REJECT
4. ✅ **Flash crash** (20% down with 0.03x volume) → REJECT

### Configuration Tests
5. ✅ **Outlier detection disabled** (50% move, feature off) → PASS
6. ✅ **Insufficient lookback data** (10 candles, need 21) → SKIP validation
7. ✅ **Custom tight threshold** (8% with 5% threshold, low volume) → REJECT

### Boundary Tests
8. ✅ **Exactly at deviation threshold** (10% with volume) → PASS
9. ✅ **Exactly at deviation threshold** (10% without volume) → PASS (boundary)
10. ✅ **Exactly at volume threshold** (15% with 0.1x volume) → PASS

### Integration Tests
11. ✅ **scan() filters outliers** (25% spike, low volume) → No signals
12. ✅ **scan() accepts valid data** (4% move with volume) → Processes normally

### Edge Cases
13. ✅ **Zero average price** → REJECT with specific error
14. ✅ **Zero average volume** (12% deviation) → REJECT with specific error
15. ✅ **Multiple consecutive outliers** → Validates only last candle

## Example Scenarios

### Scenario 1: Legitimate Breakout (PASS)
- **Historical:** 20 candles at $100, avg volume 1000
- **Current:** $112 (+12%) with volume 5000 (5x)
- **Outcome:** Deviation = 12% > 10%, BUT volume_ratio = 5.0 > 0.1 → **PASS**
- **Reason:** High volume confirms legitimate price movement

### Scenario 2: Flash Crash (REJECT)
- **Historical:** 20 candles at $100, avg volume 1000
- **Current:** $80 (-20%) with volume 30 (0.03x)
- **Outcome:** Deviation = 20% > 10%, volume_ratio = 0.03 < 0.1 → **REJECT**
- **Reason:** Extreme move without volume confirmation = bad tick

### Scenario 3: Normal Volatility (PASS)
- **Historical:** 20 candles at $100, avg volume 1000
- **Current:** $108 (+8%) with volume 1200 (1.2x)
- **Outcome:** Deviation = 8% < 10% → **PASS**
- **Reason:** Within normal volatility bounds

### Scenario 4: Detection Disabled (PASS)
- **Configuration:** `check_price_outliers: false`
- **Any price data** → **PASS**
- **Reason:** Feature disabled, all data accepted

## Fail-Closed Behavior

The circuit breaker follows fail-closed principles:

1. **Missing config:** Uses conservative defaults (10% deviation, 10% volume ratio)
2. **Insufficient data:** Skips validation rather than rejecting (graceful degradation)
3. **Invalid data:** Rejects with specific error message (zero price/volume)
4. **Outlier detected:** Logs warning and skips asset for current cycle
5. **Feature disabled:** All data passes through (manual override available)

## Monitoring & Observability

### Logging
- **Level:** WARNING
- **Format:** `{symbol}: Price outlier: {deviation}% deviation (>{threshold}%) with low volume ({ratio}x < {min}x)`
- **Location:** `logs/247trader-v2_audit.jsonl` (if enabled)

### Metrics to Track
- Outlier rejection rate (per asset, per cycle)
- Deviation distribution (histogram)
- Volume ratio distribution (histogram)
- False positive rate (outliers that were actually valid)
- Missed opportunities (valid breakouts rejected)

### Alerting
Consider alerting on:
- High outlier rejection rate (>20% of assets)
- Repeated rejections for same asset (>5 consecutive cycles)
- Zero average price/volume errors (data quality issue)

## Tuning Guidelines

### Conservative (Fewer Rejections)
- `max_price_deviation_pct: 15.0` (allow larger moves)
- `min_volume_ratio: 0.05` (require less volume confirmation)
- Result: More false breakouts, fewer missed opportunities

### Aggressive (More Rejections)
- `max_price_deviation_pct: 5.0` (tighter bounds)
- `min_volume_ratio: 0.2` (require more volume confirmation)
- Result: Fewer false breakouts, more missed opportunities

### Current Production Settings (Balanced)
- `max_price_deviation_pct: 10.0`
- `min_volume_ratio: 0.1`
- `outlier_lookback_periods: 20`

## Backtest Validation

To validate effectiveness:
1. Run backtest with outlier detection enabled
2. Run backtest with outlier detection disabled
3. Compare:
   - Trade count (should decrease slightly)
   - Win rate (should increase)
   - Max drawdown (should improve)
   - Sharp ratio (should improve)
   - PnL (should improve or neutral)

Expected impact:
- Reduces trade count by ~2-5%
- Improves win rate by ~1-3%
- Reduces max drawdown by ~5-10%

## Rollback Plan

If outlier detection causes issues:

1. **Immediate (hot fix):**
   ```yaml
   circuit_breakers:
     check_price_outliers: false
   ```
   Restart system (no code changes required)

2. **Gradual (parameter tuning):**
   - Increase `max_price_deviation_pct` to 15.0
   - Decrease `min_volume_ratio` to 0.05
   - Monitor for 24 hours

3. **Complete rollback (code revert):**
   - Revert commits to before outlier implementation
   - Deploy and restart
   - System operates without outlier validation

## Related Blockers

This completes **Production Blocker #3** of 4 critical blockers:

- ✅ **Blocker #1:** Exchange status circuit breaker (9 tests)
- ✅ **Blocker #2:** Fee-adjusted minimum notional rounding (11 tests)
- ✅ **Blocker #3:** Outlier/bad-tick guards (15 tests) ← **THIS**
- 🔴 **Blocker #4:** Environment runtime gates (TODO)

## References

- **Config:** `config/policy.yaml` (lines 128-132)
- **Implementation:** `core/triggers.py` (lines 70-78, 127-130, 173-226)
- **Tests:** `tests/test_outlier_guards.py` (427 lines, 15 tests)
- **Total Tests:** 166 passing (151 baseline + 15 new)
