# Rule Diagnostics Quick Ref

## What Changed
Added 3-stage diagnostic logging to `strategy/rules_engine.py`:

1. **Stage 1**: Rule logic rejection (NEW)
   ```
   ✗ Rule filter: XRP-USD price_move str=0.65 conf=0.80 pct_chg=+2.3% regime=chop
   ```

2. **Stage 2**: Conviction rejection (EXISTING)
   ```
   ✗ Rejected: XRP-USD conf=0.26 < min_conviction=0.28
   ```

3. **Stage 3**: Summary (NEW)
   ```
   📊 Trigger summary: 4 total → 3 failed rule logic, 1 failed min_conviction, 0 proposals
   ```

## Why
Third LIVE run: 3-4 triggers/cycle but 0 proposals. Needed visibility into WHERE filtering happens (rule logic vs conviction threshold).

## How to Use

### Watch Logs
```bash
tail -f logs/bot.log | grep -E "(✗ Rule filter|✗ Rejected|📊 Trigger summary)"
```

### Analyze Patterns
```bash
# Count rejections by stage
grep "✗ Rule filter" logs/bot.log | wc -l   # Stage 1 (rule logic)
grep "✗ Rejected" logs/bot.log | wc -l      # Stage 2 (conviction)

# Check regime patterns
grep "✗ Rule filter" logs/bot.log | grep -o "regime=[a-z]*" | sort | uniq -c

# Check price direction patterns
grep "✗ Rule filter" logs/bot.log | grep -o "pct_chg=[+-][0-9.]*%" | sort -n
```

## Decision Tree

**If Stage 1 > 80% rejections** (rule logic bottleneck):
- **Pattern**: "All upward price_move in chop rejected"
- **Fix**: Relax rule filters OR add exception for T1 assets
- **Files**: `strategy/rules_engine.py` (_rule_price_move), `config/signals.yaml`

**If Stage 2 > 80% rejections** (conviction bottleneck):
- **Pattern**: "Conviction 0.26-0.27 vs 0.28 required"
- **Fix**: Lower min_conviction 0.28→0.24 OR add boosts
- **Files**: `config/policy.yaml` (profiles.day_trader.min_conviction)

**If Mixed** (50/50):
- **Fix**: Hybrid (lower min_conviction + relax one filter)
- **Test**: Run 3-5 cycles, reassess

## Quick Fixes

### Option A: Lower Conviction Threshold
```yaml
# config/policy.yaml
profiles:
  day_trader:
    min_conviction: 0.24  # Down from 0.28 (14% less strict)
```

### Option B: Add Conviction Boosts
```yaml
# config/policy.yaml
profiles:
  day_trader:
    conviction_boosts:
      tier_1_assets: 0.05  # BTC/ETH/SOL get +0.05
      low_exposure_mode: 0.03  # +0.03 when exposure < 10%
```

### Option C: Relax Rule Filters
```yaml
# config/signals.yaml (if this exists and has regime gates)
triggers:
  price_move:
    chop_mode:
      allow_upward_moves: true  # Allow both directions
      min_strength: 0.55        # Down from 0.70
```

## Expected Results
- **Before**: 0 proposals, no visibility
- **After 1-2 cycles**: Clear bottleneck identified
- **After tuning**: 1-2 trades/day with $250 NAV

## Next Action
User restarts bot → observes logs → shares patterns → agent suggests config changes

## Full Doc
See `RULE_DIAGNOSTICS_2025-11-16.md` for detailed examples, code changes, and configuration tuning guide.
