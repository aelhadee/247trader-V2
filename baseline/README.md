# Backtest Baselines

This directory contains baseline backtest results used for regression testing (REQ-BT3).

## Purpose

Baseline files enable automated detection of strategy regressions by comparing current backtest runs against known-good historical results. Any deviation > ±2% in key metrics triggers a CI failure.

## Current Baselines

### `2024_q4_baseline.json`

- **Period:** 2024-09-01 to 2024-11-30 (Q4 2024, 3 months)
- **Seed:** 42 (deterministic)
- **Initial Capital:** $10,000 USD
- **Generated:** 2025-11-15
- **Strategy:** RulesEngine (baseline rules-based strategy)
- **Regime:** Chop (default due to insufficient data)
- **Status:** Zero-trade baseline (no triggers fired during period)

**Notes:**
- This baseline represents a "cold start" reference with no trades executed
- Useful for detecting unintended trade generation from code changes
- Replace with live trading data baseline once production runs accumulate

## Usage

### Generate New Baseline

```bash
# 3-month backtest with deterministic seed
PYTHONPATH=/Users/ahmed/coding-stuff/trader/247trader-v2:$PYTHONPATH \
python backtest/engine.py \
  --start 2024-09-01 \
  --end 2024-11-30 \
  --seed 42 \
  --output baseline/2024_q4_baseline.json
```

### Compare Against Baseline

```bash
# Run current backtest
PYTHONPATH=/Users/ahmed/coding-stuff/trader/247trader-v2:$PYTHONPATH \
python backtest/engine.py \
  --start 2024-09-01 \
  --end 2024-11-30 \
  --seed 42 \
  --output results/current.json

# Compare (exit 0 = pass, 1 = fail)
python backtest/compare_baseline.py \
  --baseline baseline/2024_q4_baseline.json \
  --current results/current.json
```

### Update Baseline (After Intentional Strategy Changes)

```bash
# 1. Review changes carefully
git diff strategy/rules_engine.py config/policy.yaml

# 2. Generate new baseline
PYTHONPATH=/Users/ahmed/coding-stuff/trader/247trader-v2:$PYTHONPATH \
python backtest/engine.py \
  --start 2024-09-01 \
  --end 2024-11-30 \
  --seed 42 \
  --output baseline/2024_q4_baseline_new.json

# 3. Compare metrics
python backtest/compare_baseline.py \
  --baseline baseline/2024_q4_baseline.json \
  --current baseline/2024_q4_baseline_new.json

# 4. If improvements are real, replace baseline
mv baseline/2024_q4_baseline.json baseline/2024_q4_baseline_old.json
mv baseline/2024_q4_baseline_new.json baseline/2024_q4_baseline.json

# 5. Document changes
git add baseline/
git commit -m "Update Q4 baseline after [strategy improvement description]

Metrics changes:
- win_rate: 0.55 → 0.62 (+12.7%)
- max_drawdown_pct: -12.5 → -8.3 (+33.6%)
- profit_factor: 1.8 → 2.1 (+16.7%)

Reason: [explanation of what changed and why]"
```

## Regression Metrics (±2% Tolerance)

The following 5 metrics are compared:

1. **total_trades** - Trade count shouldn't change drastically
2. **win_rate** - Win percentage must remain stable  
3. **total_pnl_pct** - Overall return shouldn't regress
4. **max_drawdown_pct** - Drawdown shouldn't worsen
5. **profit_factor** - Risk-adjusted return must stay healthy

## Baseline Rotation Policy

### When to Create New Baseline

✅ **DO create new baseline when:**
- Intentional strategy improvements (better win_rate, lower DD)
- Major config changes (universe filters, risk caps)
- Adding new strategies or removing old ones
- Significant backtest engine improvements
- Quarterly cadence (end of each quarter)

❌ **DON'T create new baseline for:**
- Bug fixes that should maintain metrics
- Refactoring without logic changes
- Test-only code changes
- Documentation updates

### Naming Convention

Format: `YYYY_qN_baseline.json` where N is the quarter (1-4)

Examples:
- `2024_q4_baseline.json` - Q4 2024 (Oct-Dec)
- `2025_q1_baseline.json` - Q1 2025 (Jan-Mar)
- `2025_q2_baseline.json` - Q2 2025 (Apr-Jun)

### Retention

- **Keep:** Last 4 quarterly baselines (1 year history)
- **Archive:** Older baselines moved to `baseline/archive/`
- **Never delete:** Baselines from major strategy milestones

## CI Integration

See `.github/workflows/backtest_regression.yml` for automated regression testing on pull requests.

**Current Status:**
- ✅ Baseline infrastructure: Complete
- ✅ Comparison script: Complete  
- ✅ Test coverage: 17/17 passing
- 🔴 GitHub Actions workflow: Pending (create workflow file)

## Troubleshooting

### Zero-Trade Baseline

If backtest produces no trades (like current Q4 baseline):
- **Cause:** No triggers fired during period (low volatility, strict filters)
- **Impact:** Still useful for detecting unintended trade generation
- **Solution:** Update baseline once production runs with real trades

### Baseline Comparison Fails After Bug Fix

If you fixed a bug and baseline comparison fails:
- **Expected:** Bug fixes may change behavior
- **Action:** Review metrics carefully - bug fix should improve or maintain quality
- **Decision:** If metrics improve, update baseline and document the bug fix

### Different Seeds Produce Different Results

- **Expected:** Different seeds = different randomness
- **Solution:** Always use same seed (42) for baseline comparisons
- **Note:** Test robustness separately with multiple seeds

---

**Documentation:** See `docs/BACKTEST_REGRESSION_SYSTEM.md` for comprehensive guide  
**Requirements:** REQ-BT1 (Deterministic), REQ-BT2 (JSON Reports), REQ-BT3 (Regression Gate)  
**Tests:** `tests/test_backtest_regression.py` (17 passing)
