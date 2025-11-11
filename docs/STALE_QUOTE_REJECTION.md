# Stale Quote Rejection Implementation

## Summary

Implemented production-grade quote staleness validation to prevent trading decisions based on outdated market data. This critical safety feature ensures all quotes are fresh before execution, reducing risk of poor fills and slippage.

## Implementation Date

January 11, 2025

## Overview

The system now validates quote timestamps at three critical decision points before allowing any trading action. Quotes older than `max_quote_age_seconds` (default 30s) are rejected immediately.

## Architecture

### Core Validation Method

**Location:** `core/execution.py:ExecutionEngine._validate_quote_freshness()`

```python
def _validate_quote_freshness(self, quote, symbol: str) -> Optional[str]:
    """
    Validate quote timestamp is fresh enough for trading decisions.
    
    Returns:
        Error string if stale, None if fresh
    """
```

**Features:**
- Calculates age from `quote.timestamp` to current UTC time
- Handles timezone-aware and naive timestamps (assumes UTC)
- Detects clock skew (future timestamps)
- Returns descriptive error messages with actual age

### Integration Points

1. **`preview_order()`** (line ~817)
   - Validates quote before order preview
   - Returns error dict if stale
   - Blocks order from progressing

2. **`_execute_live()`** (line ~1148)
   - Validates quote before live execution
   - Returns ExecutionResult with error if stale
   - Prevents actual order placement

3. **`_find_best_trading_pair()`** (lines ~709, ~716)
   - Validates quotes during pair selection
   - Skips pairs with stale quotes
   - Logs staleness for debugging

## Configuration

### Policy Settings

**File:** `config/policy.yaml`

```yaml
microstructure:
  max_quote_age_seconds: 30      # Execution-time threshold

circuit_breakers:
  max_quote_age_seconds: 60      # Circuit breaker threshold (future use)
```

**Notes:**
- ExecutionEngine uses `microstructure.max_quote_age_seconds` (30s)
- Circuit breaker threshold (60s) available for RiskEngine integration
- Configurable per deployment needs

## Test Coverage

**Test File:** `tests/test_stale_quotes.py`

**14 comprehensive tests:**

### Validation Logic Tests (9 tests)
- ✅ Fresh quotes pass (< 30s)
- ✅ Stale quotes rejected (> 30s)
- ✅ Boundary case (exactly 30s)
- ✅ Very stale quotes (5 minutes)
- ✅ Future timestamps (clock skew)
- ✅ None quotes rejected
- ✅ Missing timestamp rejected
- ✅ Naive timestamps handled (assumed UTC)
- ✅ Custom thresholds work

### Integration Tests (5 tests)
- ✅ preview_order rejects stale quotes
- ✅ preview_order accepts fresh quotes
- ✅ _execute_live rejects stale quotes
- ✅ _execute_live accepts fresh quotes
- ✅ Circuit breaker threshold documented

**Test Results:** 109/109 passing (14 new + 95 existing)

## Error Handling

### Stale Quote Error Format

```
Quote too stale for BTC-USD: 45.2s old (max: 30s)
```

### Clock Skew Detection

```
Quote timestamp in future for BTC-USD: -5.3s ahead (possible clock skew)
```

### Missing Data

```
Quote missing timestamp for BTC-USD
Quote is None for BTC-USD
```

## Logging

**Debug:** Quote freshness checks logged at DEBUG level
```
Quote freshness OK for BTC-USD: 12.3s old
```

**Warning:** Stale quote rejections logged at WARNING level in _execute_live
```
Stale quote rejected in _execute_live: Quote too stale for BTC-USD: 45.0s old (max: 30s)
```

## Production Benefits

### Risk Reduction
- **Prevents execution on cached data:** No trades on old prices
- **Circuit breaker protection:** 60s threshold available for emergency stops
- **Clock skew detection:** Identifies time sync issues

### Execution Quality
- **Accurate fills:** Fresh quotes ensure current market prices
- **Reduced slippage:** Minimizes price movement between quote and execution
- **Better audit trail:** Staleness errors logged for analysis

### Operational Safety
- **Fail-safe design:** Rejects on any validation failure
- **Clear error messages:** Easy debugging and monitoring
- **Configurable thresholds:** Tune based on network latency

## Fail-Closed Behavior

The implementation follows fail-closed principles:
- Missing timestamp → REJECT
- Null quote → REJECT
- Future timestamp → REJECT
- Age > threshold → REJECT
- Timezone issues → Assume UTC, validate

## Future Enhancements

### Potential Improvements
1. **Circuit Breaker Integration:** Use 60s threshold in RiskEngine
2. **Metrics:** Track staleness distribution and rejection rate
3. **Adaptive Thresholds:** Adjust based on market volatility
4. **Exchange Health:** Correlate staleness with API latency

### Risk Engine Integration
```python
# Future: RiskEngine checks
if quote_age > policy.circuit_breakers.max_quote_age_seconds:
    raise CircuitBreakerTripped("Quote staleness threshold exceeded")
```

## Rollback Plan

If issues arise:

1. **Increase threshold temporarily:**
   ```yaml
   microstructure:
     max_quote_age_seconds: 60  # Double to 60s
   ```

2. **Revert code changes:**
   ```bash
   git revert <commit_hash>
   ```

3. **Monitor logs for false positives:**
   ```bash
   grep "too stale" logs/247trader-v2_audit.jsonl
   ```

## Related Documentation

- `config/policy.yaml` - Configuration settings
- `core/execution.py` - Implementation code
- `tests/test_stale_quotes.py` - Test suite
- `PRODUCTION_TODO.md` - Production checklist

## Verification

Run tests:
```bash
pytest tests/test_stale_quotes.py -v
```

Check integration:
```bash
pytest tests/test_core.py tests/test_exchange.py tests/test_stale_quotes.py -v
```

Expected: 109/109 tests passing

## Author

Implemented January 11, 2025
GitHub Copilot + Human Review
