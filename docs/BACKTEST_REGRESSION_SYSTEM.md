# Backtest Regression System

**Status:** ✅ Implemented (REQ-BT1-3)  
**Tests:** 17 passing (`tests/test_backtest_regression.py`)  
**Date:** 2025-11-15

## Overview

The backtest regression system ensures trading strategy changes don't accidentally degrade performance. It provides:

1. **Deterministic backtests** (REQ-BT1) - Same seed → same results
2. **Machine-readable reports** (REQ-BT2) - JSON export for CI/CD integration
3. **Automated regression gate** (REQ-BT3) - Fail CI if key metrics deviate > ±2%

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                     Backtest Workflow                           │
│                                                                 │
│  1. BacktestEngine(seed=42) ─┐                                 │
│                               │                                 │
│  2. Run backtest              ├──→ Deterministic results        │
│     (historical OHLCV data)   │    (REQ-BT1: Fixed seed)       │
│                               │                                 │
│  3. export_json()             └──→ baseline.json               │
│                                    (REQ-BT2: Machine-readable)  │
│                                                                 │
│  4. compare_baseline.py           ┌─────────────────────┐      │
│     --baseline baseline.json      │ Key Metrics (±2%):  │      │
│     --current  results.json   ───→│ • total_trades      │      │
│                                    │ • win_rate          │      │
│                                    │ • total_pnl_pct     │      │
│                                    │ • max_drawdown_pct  │      │
│                                    │ • profit_factor     │      │
│                                    └─────────────────────┘      │
│                                          │                      │
│                                          ▼                      │
│                                     Exit Code:                  │
│                                     0 = PASS                    │
│                                     1 = FAIL (>±2%)             │
│                                     2 = ERROR                   │
└────────────────────────────────────────────────────────────────┘
```

## REQ-BT1: Deterministic Backtests

### Problem

Backtests must be reproducible for debugging and regression testing. Random elements (trigger selection, universe filtering, etc.) can cause different results between runs.

### Solution

Fixed random seed for all random operations:

```python
engine = BacktestEngine(seed=42, initial_capital=10000.0)
```

When `seed` is provided:
- Python's `random` module is seeded
- All random operations become deterministic
- Repeated runs with same seed + same data = identical results

### Usage

**Deterministic backtest:**
```bash
python backtest/engine.py --start 2024-01-01 --end 2024-12-31 --seed 42
```

**Allow randomness (testing robustness):**
```bash
python backtest/engine.py --start 2024-01-01 --end 2024-12-31
# No --seed argument = non-deterministic
```

### Tests

3 tests in `test_backtest_regression.py::TestDeterministicBacktests`:
- ✅ Same seed produces identical results
- ✅ Different seeds may differ
- ✅ No seed allows randomness

## REQ-BT2: Machine-Readable JSON Reports

### Problem

Backtest results need structured export for:
- CI/CD integration
- Automated comparison
- Long-term performance tracking
- Audit trails

### Solution

Comprehensive JSON report with 4 sections:

**1. Metadata** (run context):
```json
{
  "metadata": {
    "version": "1.0",
    "generated_at": "2024-11-15T10:30:00",
    "seed": 42,
    "initial_capital_usd": 10000.0,
    "final_capital_usd": 10523.45,
    "config_dir": "config"
  }
}
```

**2. Summary** (aggregate metrics):
```json
{
  "summary": {
    "total_trades": 45,
    "winning_trades": 28,
    "losing_trades": 17,
    "win_rate": 0.6222,
    "total_pnl_usd": 523.45,
    "total_pnl_pct": 5.23,
    "avg_win_pct": 3.2,
    "avg_loss_pct": -1.8,
    "max_drawdown_pct": -8.5,
    "max_consecutive_losses": 4,
    "profit_factor": 1.85,
    "sharpe_ratio": 0.92
  }
}
```

**3. Trades** (full trade history):
```json
{
  "trades": [
    {
      "symbol": "BTC-USD",
      "side": "BUY",
      "entry_time": "2024-01-05T09:00:00",
      "entry_price": 45000.0,
      "exit_time": "2024-01-06T15:30:00",
      "exit_price": 46350.0,
      "exit_reason": "take_profit",
      "size_usd": 1000.0,
      "pnl_usd": 30.0,
      "pnl_pct": 3.0,
      "hold_time_hours": 30.5
    }
  ]
}
```

**4. Regression Keys** (CI comparison):
```json
{
  "regression_keys": {
    "total_trades": 45,
    "win_rate": 0.6222,
    "total_pnl_pct": 5.23,
    "max_drawdown_pct": -8.5,
    "profit_factor": 1.85
  }
}
```

### Usage

**Export JSON report:**
```python
engine = BacktestEngine(seed=42)
metrics = engine.run(start_date, end_date, data_loader)
engine.export_json("results/backtest_2024.json")
```

**Command-line export:**
```bash
python backtest/engine.py \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --seed 42 \
  --output results/baseline.json
```

### Tests

6 tests in `test_backtest_regression.py::TestJSONReportExport`:
- ✅ File creation
- ✅ Report structure (4 sections)
- ✅ Metadata fields
- ✅ Summary metrics
- ✅ Trades list
- ✅ Regression keys

## REQ-BT3: Regression Gate (CI Integration)

### Problem

Code changes can accidentally degrade strategy performance. Manual comparison is error-prone and slow. Need automated gate to catch regressions before deployment.

### Solution

**compare_baseline.py** script compares key metrics with ±2% tolerance:

```bash
python backtest/compare_baseline.py \
  --baseline baseline.json \
  --current results.json
```

**Exit codes:**
- `0` = PASS (all metrics within ±2%)
- `1` = FAIL (one or more metrics > ±2%)
- `2` = ERROR (missing files, invalid JSON)

### Regression Metrics (±2% tolerance)

1. **total_trades** - Trade count shouldn't change drastically
2. **win_rate** - Win percentage must remain stable
3. **total_pnl_pct** - Overall return shouldn't regress
4. **max_drawdown_pct** - Drawdown shouldn't worsen
5. **profit_factor** - Risk-adjusted return must stay healthy

### Example Output

**PASS (within tolerance):**
```
================================================================================
BACKTEST REGRESSION COMPARISON (REQ-BT3)
================================================================================
Tolerance: ±2%

total_trades         ✅ PASS
  Baseline:         100.0000
  Current:          101.0000
  Deviation:        +1.00% (limit: ±2%)

win_rate             ✅ PASS
  Baseline:           0.6000
  Current:            0.6100
  Deviation:        +1.67% (limit: ±2%)

total_pnl_pct        ✅ PASS
  Baseline:          10.0000
  Current:           10.1500
  Deviation:        +1.50% (limit: ±2%)

max_drawdown_pct     ✅ PASS
  Baseline:          -5.0000
  Current:           -5.0500
  Deviation:        +1.00% (limit: ±2%)

profit_factor        ✅ PASS
  Baseline:           2.0000
  Current:            2.0300
  Deviation:        +1.50% (limit: ±2%)

================================================================================
✅ REGRESSION TEST PASSED
================================================================================
```

**FAIL (exceeds tolerance):**
```
total_trades         ❌ FAIL
  Baseline:         100.0000
  Current:          110.0000
  Deviation:       +10.00% (limit: ±2%)

win_rate             ✅ PASS
  Baseline:           0.6000
  Current:            0.5850
  Deviation:        -2.50% (limit: ±2%)  ← FAIL

================================================================================
❌ REGRESSION TEST FAILED

One or more metrics deviated beyond ±2% tolerance.
Review changes that may have affected backtest results.
================================================================================
```

### CI Integration

**GitHub Actions workflow:**

```yaml
name: Backtest Regression

on: [pull_request]

jobs:
  backtest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run current backtest
        run: |
          python backtest/engine.py \
            --start 2024-01-01 \
            --end 2024-12-31 \
            --seed 42 \
            --output results/current.json
      
      - name: Compare against baseline
        run: |
          python backtest/compare_baseline.py \
            --baseline baseline/2024_baseline.json \
            --current results/current.json
```

**Exit code 1 → CI fails → PR blocked until fixed**

### When Baseline Should Change

Intentional strategy improvements may require updating baseline:

1. **Run new backtest:**
   ```bash
   python backtest/engine.py --start 2024-01-01 --end 2024-12-31 --seed 42 --output new_baseline.json
   ```

2. **Review metrics carefully:**
   - Did win_rate improve?
   - Did max_drawdown_pct decrease (better)?
   - Did profit_factor increase?

3. **If improvements are real, update baseline:**
   ```bash
   cp new_baseline.json baseline/2024_baseline.json
   git add baseline/2024_baseline.json
   git commit -m "Update backtest baseline after strategy optimization"
   ```

4. **Document changes:**
   - Add entry to CHANGELOG.md
   - Include before/after metrics
   - Explain what changed and why

### Tests

8 tests in `test_backtest_regression.py::TestRegressionGate`:
- ✅ Deviation calculation (positive/negative)
- ✅ Zero baseline handling
- ✅ Within ±2% tolerance (PASS)
- ✅ Exceeds ±2% tolerance (FAIL)
- ✅ None value handling
- ✅ All regression keys checked
- ✅ Full workflow integration test

## Usage Examples

### 1. Initial Baseline Creation

```bash
# Run backtest with fixed seed
python backtest/engine.py \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --capital 10000 \
  --seed 42 \
  --output baseline/2024_baseline.json

# Verify baseline looks good
cat baseline/2024_baseline.json | jq '.summary'
```

### 2. Testing Strategy Changes

```bash
# Make changes to strategy/rules_engine.py or policy.yaml

# Run new backtest (same seed for determinism)
python backtest/engine.py \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --capital 10000 \
  --seed 42 \
  --output results/test_changes.json

# Compare against baseline
python backtest/compare_baseline.py \
  --baseline baseline/2024_baseline.json \
  --current results/test_changes.json

# Exit code 0 = safe to merge
# Exit code 1 = review changes carefully
```

### 3. Testing Robustness (Multiple Seeds)

```bash
# Run with different seeds to test stability
for seed in 42 123 456 789; do
  python backtest/engine.py \
    --start 2024-01-01 \
    --end 2024-12-31 \
    --seed $seed \
    --output results/seed_${seed}.json
done

# Compare variance across seeds
python backtest/analyze_variance.py results/seed_*.json
```

## Test Coverage

**17 tests passing** in `tests/test_backtest_regression.py`:

- **Deterministic Backtests (3 tests)**
  - Same seed = same results
  - Different seeds = may differ
  - No seed = random

- **JSON Report Export (6 tests)**
  - File creation
  - Report structure
  - Metadata section
  - Summary metrics
  - Trades list
  - Regression keys

- **Regression Gate (8 tests)**
  - Deviation calculations
  - Tolerance checks
  - Pass/fail logic
  - Edge cases (None, zero)
  - Full integration workflow

## Best Practices

### Deterministic Testing

✅ **DO:**
- Use fixed seed for baseline and regression tests
- Document seed in baseline filename (e.g., `baseline_seed42.json`)
- Test robustness with multiple seeds after passing regression

❌ **DON'T:**
- Compare results from different seeds
- Change seed without updating all related baselines
- Use production seed in regression tests (keep separate)

### Baseline Management

✅ **DO:**
- Store baselines in version control
- Create new baseline for each major strategy change
- Document why baseline changed in commit message
- Keep historical baselines for comparison

❌ **DON'T:**
- Update baseline to make tests pass without review
- Delete old baselines (keep for historical analysis)
- Use same baseline across different time periods

### CI Integration

✅ **DO:**
- Run regression gate on every PR
- Block merges on regression failures
- Cache baseline files for faster CI
- Alert team on baseline updates

❌ **DON'T:**
- Skip regression gate for "small" changes
- Allow manual override without review
- Run on main branch only (too late)
- Use long backtest periods in CI (slow)

## Troubleshooting

### "Regression test failed but I improved the strategy"

**Solution:** Review metrics carefully. If improvements are real (better win_rate, lower drawdown, higher profit_factor), update baseline with documentation.

### "Results differ slightly between runs with same seed"

**Possible causes:**
1. External data source changed (OHLCV data)
2. Non-deterministic code path (fix with seed)
3. Floating-point precision differences (use rounding)

**Fix:** Ensure all data sources are deterministic or snapshot historical data.

### "Baseline file missing or corrupt"

**Solution:**
```bash
# Regenerate baseline
python backtest/engine.py --start 2024-01-01 --end 2024-12-31 --seed 42 --output baseline/2024_baseline.json

# Validate JSON
python -m json.tool baseline/2024_baseline.json > /dev/null
echo "✅ Valid JSON" || echo "❌ Invalid JSON"
```

### "CI runs are too slow"

**Solution:**
- Use shorter backtest period for CI (3 months instead of 1 year)
- Cache baseline files
- Run full backtest nightly instead of per-PR
- Use faster data loader

## Future Enhancements

### Near-Term
1. **Multiple baselines** - Compare against last 3 baselines to detect trends
2. **Visual diff** - Generate charts showing metric changes over time
3. **Automated baseline updates** - PR bot suggests baseline updates when improvements detected

### Medium-Term
4. **Walk-forward analysis** - Test on rolling time windows to detect overfitting
5. **Monte Carlo simulation** - Randomize trade order to test robustness
6. **Parameter sensitivity** - Automatically test strategy with varied parameters

### Long-Term
7. **ML-based anomaly detection** - Flag unusual metric combinations
8. **Auto-generated explanation** - AI summary of why metrics changed
9. **Historical baseline library** - Track strategy evolution over time

## Compliance

**Requirement Tracking:**

| Requirement | Status | Tests | Evidence |
|-------------|--------|-------|----------|
| REQ-BT1 (Deterministic backtests) | ✅ Implemented | 3 tests | BacktestEngine(seed=42) |
| REQ-BT2 (JSON report format) | ✅ Implemented | 6 tests | export_json() with 4 sections |
| REQ-BT3 (Regression gate) | ✅ Implemented | 8 tests | compare_baseline.py with ±2% tolerance |

**Test Evidence:**
- `tests/test_backtest_regression.py`: 17/17 passing
- Coverage: 100% of backtest regression requirements

**Documentation:**
- Architecture diagrams
- Usage examples
- Best practices
- Troubleshooting guide
- CI integration instructions

---

**Implementation Date:** 2025-11-15  
**Requirements:** REQ-BT1 (Deterministic), REQ-BT2 (JSON Reports), REQ-BT3 (Regression Gate)  
**Tests:** 17/17 passing  
**Status:** ✅ Production Ready
