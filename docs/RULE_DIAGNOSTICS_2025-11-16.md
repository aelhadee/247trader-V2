# Rule Diagnostics Enhancement (2025-11-16)

## TL;DR
Added detailed logging to `strategy/rules_engine.py` to diagnose why triggers don't generate proposals. Now logs BEFORE conviction calculation to reveal which rule logic checks fail.

## Problem
Third LIVE run showed 0 proposals despite 3-4 triggers per cycle (XRP, SOL, HBAR, XLM). Root cause: rules_engine filtering all triggers BEFORE conviction calculation, so existing conviction logging never ran.

## Solution
Added two-stage diagnostic logging:

### Stage 1: Rule Logic Rejection (NEW)
**When**: Trigger fails rule logic checks (e.g., `_rule_price_move()` returns None)
**Log Pattern**:
```
✗ Rule filter: XRP-USD price_move str=0.65 conf=0.80 pct_chg=+2.3% regime=chop (failed rule logic checks)
```

**Reveals**:
- Which triggers fail rule logic
- Trigger strength/confidence at rejection
- Price change direction/magnitude
- Regime context

### Stage 2: Conviction Rejection (EXISTING)
**When**: Proposal created but conviction < min_conviction
**Log Pattern**:
```
✗ Rejected: XRP-USD conf=0.26 < min_conviction=0.28 reason='min_conviction'
```

### Stage 3: Summary (NEW)
**When**: After processing all triggers
**Log Pattern**:
```
📊 Trigger summary: 4 total → 3 failed rule logic, 1 failed min_conviction, 0 final proposals
```

**Reveals**:
- Where the bottleneck is (rule logic vs conviction threshold)
- Percentage of triggers that reach each stage

## Example Diagnostic Session

### Scenario 1: Rules Too Strict
```
✗ Rule filter: XRP-USD price_move str=0.65 conf=0.80 pct_chg=+2.3% regime=chop (failed rule logic checks)
✗ Rule filter: SOL-USD price_move str=0.72 conf=0.75 pct_chg=+1.8% regime=chop (failed rule logic checks)
✗ Rule filter: HBAR-USD momentum str=0.35 conf=0.50 pct_chg=-2.1% regime=chop (failed rule logic checks)
✗ Rule filter: XLM-USD volume_spike str=0.55 conf=0.60 pct_chg=+1.2% regime=chop (failed rule logic checks)
📊 Trigger summary: 4 total → 4 failed rule logic, 0 failed min_conviction, 0 final proposals
```

**Diagnosis**: All triggers rejected by rule logic (100% fail at stage 1)
**Pattern**: All upward price_move in chop regime rejected
**Action**: Relax rule filters OR add exception for T1 assets in chop

### Scenario 2: Conviction Too High
```
CONVICTION XRP-USD (price_move): 0.26 = base 0.80 + strength 0.13 + regime -0.02 ... threshold=0.28
✗ Rejected: XRP-USD conf=0.26 < min_conviction=0.28 reason='min_conviction'
CONVICTION SOL-USD (price_move): 0.27 = base 0.75 + strength 0.14 + regime -0.02 ... threshold=0.28
✗ Rejected: SOL-USD conf=0.27 < min_conviction=0.28 reason='min_conviction'
📊 Trigger summary: 4 total → 0 failed rule logic, 4 failed min_conviction, 0 final proposals
```

**Diagnosis**: All triggers pass rule logic (100% reach stage 2) but fail conviction threshold
**Pattern**: Conviction barely under threshold (0.26-0.27 vs 0.28 required)
**Action**: Lower min_conviction 0.28→0.24 or add conviction boosts

### Scenario 3: Balanced (Working System)
```
CONVICTION BTC-USD (momentum): 0.65 = base 0.80 + strength 0.25 + regime 0.10 + tier_boost 0.02 ... threshold=0.28
✅ Proposal: BUY BTC-USD $10.00 (2.0% NAV) confidence=0.65
✗ Rule filter: DOGE-USD volume_spike str=0.45 conf=0.55 pct_chg=+0.8% regime=chop (failed rule logic checks)
CONVICTION ETH-USD (price_move): 0.32 = base 0.70 + strength 0.15 + regime 0.05 + tier_boost 0.02 ... threshold=0.28
✅ Proposal: BUY ETH-USD $8.00 (1.6% NAV) confidence=0.32
📊 Trigger summary: 3 total → 1 failed rule logic, 0 failed min_conviction, 2 final proposals
```

**Diagnosis**: 66% trigger success rate (2/3 become proposals)
**Pattern**: T1 assets pass, meme coin (DOGE) rejected by rules
**Action**: No changes needed, system working as designed

## Usage

### 1. Restart Bot
```bash
./app_run_live.sh --loop
```

### 2. Watch Logs
```bash
tail -f logs/bot.log | grep -E "(✗ Rule filter|✗ Rejected|📊 Trigger summary|CONVICTION|✅ Proposal)"
```

### 3. Analyze Patterns
Count rejection types:
```bash
grep "✗ Rule filter" logs/bot.log | wc -l        # Stage 1 failures
grep "✗ Rejected" logs/bot.log | wc -l           # Stage 2 failures
grep "✅ Proposal" logs/bot.log | wc -l          # Successes
```

Check regime patterns:
```bash
grep "✗ Rule filter" logs/bot.log | grep -o "regime=[a-z]*" | sort | uniq -c
```

Check price_change_pct patterns:
```bash
grep "✗ Rule filter" logs/bot.log | grep -o "pct_chg=[+-][0-9.]*%" | sort -n
```

### 4. Tune Based on Findings

**If Stage 1 dominates (rule logic rejections > 80%)**:
- Check `_rule_price_move()`, `_rule_volume_spike()` implementations
- Look for regime-specific gates in signals.yaml
- Consider adding permissive rules for low-exposure scenarios

**If Stage 2 dominates (conviction rejections > 80%)**:
- Lower min_conviction in policy.yaml
- Add conviction boosts for core assets
- Check if regime/volatility penalties too harsh

**If Mixed**:
- Hybrid approach (lower min_conviction + relax one filter)
- A/B test with different profiles (day_trader vs swing_trader)

## Code Changes

### File: strategy/rules_engine.py

**Lines ~220-240** - Stage 1 Logging (Rule Logic Rejection):
```python
if not proposal:
    # Log when rule logic rejects trigger (before conviction)
    logger.info(
        f"✗ Rule filter: {trigger.symbol} {trigger.trigger_type} "
        f"str={trigger.strength:.2f} conf={trigger.confidence:.2f} "
        f"pct_chg={getattr(trigger, 'price_change_pct', 'n/a')} "
        f"regime={regime} (failed rule logic checks)"
    )
    continue
```

**Lines ~305-325** - Stage 3 Logging (Summary):
```python
# Summary of trigger outcomes
if triggers:
    passed_rule_logic = sum(1 for p in proposals if p)
    rejected_conviction = skipped + canary_count
    rejected_rule_logic = len(triggers) - passed_rule_logic - rejected_conviction
    
    logger.info(
        f"📊 Trigger summary: {len(triggers)} total → "
        f"{rejected_rule_logic} failed rule logic, "
        f"{rejected_conviction} failed min_conviction, "
        f"{len(proposals)} final proposals"
    )
```

**Lines ~639-669** - Stage 2 Logging (Conviction Rejection - EXISTING):
```python
def _log_conviction(self, proposal, breakdown, threshold):
    formula = f"{proposal.confidence:.3f} = base {base:.3f} + strength {str_comp:.3f} + ..."
    logger.info(f"CONVICTION {symbol} ({trigger_type}): {formula} [boosts] threshold={threshold}")
```

## Configuration Tuning Examples

### Example 1: Lower min_conviction (Stage 2 Fix)
```yaml
# config/policy.yaml
profiles:
  day_trader:
    min_conviction: 0.24  # Down from 0.28 (14% reduction)
```

**When to use**: Conviction scores barely miss threshold (0.25-0.27 vs 0.28 required)
**Risk**: More marginal trades, higher false positive rate
**Benefit**: Increased activity for small accounts, more learning data

### Example 2: Relax regime gates (Stage 1 Fix)
```yaml
# config/signals.yaml
triggers:
  price_move:
    chop_mode:
      allow_upward_moves: true  # Was false
      min_strength: 0.55        # Down from 0.70
```

**When to use**: All upward moves rejected in chop regime
**Risk**: May buy into false breakouts during consolidation
**Benefit**: Catches early trend changes, more opportunities

### Example 3: Add conviction boosts (Stage 2 Fix)
```yaml
# config/policy.yaml
profiles:
  day_trader:
    conviction_boosts:
      tier_1_assets: 0.05  # BTC, ETH, SOL get +0.05
      low_exposure_mode: 0.03  # +0.03 when exposure < 10%
```

**When to use**: Want to favor high-quality assets or increase activity when underutilized
**Risk**: May create bias toward specific assets
**Benefit**: Targeted aggressiveness without lowering global threshold

## Success Metrics

**Before Enhancement**: 
- 0 proposals, no visibility into why
- User: "Bot is alive but picky, not sure where to tune"

**After Enhancement**:
- Clear rejection reasons logged every cycle
- User can identify: "80% fail rule logic → relax filters" or "90% fail conviction → lower threshold"
- Data-driven config tuning instead of guessing

**Expected Outcomes** (next 3-5 LIVE runs):
- Identify bottleneck (rule logic vs conviction)
- Tune 1-2 config parameters based on patterns
- Achieve 1-2 trades per day with $250 NAV
- Validate $5 min_notional works in production

## Related Docs
- `SMALL_ACCOUNT_CALIBRATION_2025-11-16.md` - Config changes for $250-$1k accounts
- `SMALL_ACCOUNT_CALIBRATION_QUICK_REF.md` - TL;DR version
- `AUTO_PURGE_OPERATIONAL_GUIDE.md` - Purge failure handling
- `PURGE_HARDENING_2025-11-16.md` - Purge enhancements summary

## Timeline
- **2025-11-16 12:00**: Third LIVE run showed 0 proposals, user identified rules_engine bottleneck
- **2025-11-16 12:15**: Added Stage 1 logging (rule logic rejections)
- **2025-11-16 12:20**: Added Stage 3 logging (summary)
- **2025-11-16 12:25**: Validated changes compile, created this doc
- **Next**: User restarts bot, observes rejection patterns, tunes config
