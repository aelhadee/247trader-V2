# AI Safeguard Design - Preventing Total Trade Vetoes

## Overview

The **AI SAFEGUARD** is a safety mechanism that prevents the AI advisor from completely freezing trading activity in non-crisis market conditions. While the AI can be very conservative in its recommendations, a **total veto of all proposals** is only permitted during extreme market regimes (crash/panic).

## Design Philosophy

### The Problem
Without a safeguard, an overly conservative AI model could:
- Veto 100% of trade proposals during normal market choppiness
- Create prolonged "no-trading" periods even when signals are valid
- Prevent the system from building positions during legitimate opportunities
- Lead to opportunity cost and failure to execute the strategy

### The Solution
**Selective intervention**: Allow AI to be highly conservative, but enforce a minimum engagement rule:
- **In crash/panic regimes**: Total veto is allowed (safety first)
- **In chop/bear/normal/bull regimes**: At least one proposal must proceed (with conservative sizing)

This creates a "safety floor" while respecting AI judgment about sizing and risk levels.

---

## Implementation

### Location
`runner/main_loop.py` - `_apply_ai_decisions()` method (lines ~1985-2006)

### Logic Flow

```python
# After AI filtering
proposals = self._apply_ai_decisions(proposals, ai_output)

# AI Safeguard: Prevent 100% veto in non-crisis conditions
if not proposals and original_proposal_count > 0:
    market_regime = ai_input.market.regime
    
    # Allow total veto only in crash/panic regimes
    if market_regime not in ("crash", "panic"):
        # Get original proposals sorted by conviction/confidence
        original_proposals_sorted = sorted(
            proposals_before_ai,
            key=lambda p: getattr(p, 'confidence', getattr(p, 'conviction', 0)),
            reverse=True
        )
        
        if original_proposals_sorted:
            # Rescue top proposal with 50% size reduction
            rescued = original_proposals_sorted[0]
            rescued.size_pct = rescued.size_pct * 0.5
            proposals = [rescued]
            proposals_count = len(proposals)  # Update count after rescue
            
            logger.warning(
                f"⚠️  AI SAFEGUARD: Rescued {rescued.symbol} at 50% size "
                f"(AI vetoed all {original_proposal_count} proposals in {market_regime} regime, "
                f"but total veto only allowed in crash/panic)"
            )
```

### Key Components

1. **Trigger Condition**: AI returned zero proposals after filtering
2. **Regime Check**: Market regime is NOT in ("crash", "panic")
3. **Proposal Selection**: Highest conviction/confidence from original proposals
4. **Size Reduction**: Additional 50% haircut beyond AI's recommendation
5. **Logging**: Clear warning that safeguard was triggered

---

## Regime-Specific Behavior

| Regime | AI Can Veto All? | Safeguard Action | Rationale |
|--------|------------------|------------------|-----------|
| **CRASH** | ✅ Yes | None | System should be in pure preservation mode |
| **PANIC** | ✅ Yes | None | Extreme risk-off is appropriate |
| **BEAR** | ❌ No | Rescue top proposal at 50% size | Still need to build positions on dips |
| **CHOP** | ❌ No | Rescue top proposal at 50% size | Normal consolidation, not crisis |
| **NORMAL** | ❌ No | Rescue top proposal at 50% size | Standard market conditions |
| **BULL** | ❌ No | Rescue top proposal at 50% size | Opportunities should be captured |

### Rationale by Regime

**CRASH/PANIC** (Total veto allowed):
- Market structure breaking down
- Liquidity evaporating
- Preservation of capital is paramount
- Better to wait for stability than force trades

**CHOP/BEAR/NORMAL/BULL** (Safeguard active):
- Market functioning normally (even if choppy)
- Valid signals should result in some action
- Conservative sizing (50% of already-reduced size) manages risk
- Prevents total paralysis from overly cautious AI

---

## Example Scenarios

### Scenario 1: CHOP Regime (23:58 cycle from logs)

**Initial Proposals** (5 total):
- AVAX 1.0% (conviction: 0.60)
- SOL 2.1% (conviction: 0.53)
- DOGE 1.4% (conviction: 0.51)
- XRP 1.4% (conviction: 0.51)
- BTC 1.7% (conviction: 0.54)

**AI Decision** (DEFENSIVE mode):
```
AI SKIP: AVAX-USD BUY - Low conviction + choppy market
AI SKIP: SOL-USD BUY - Low conviction + choppy market
AI SKIP: DOGE-USD BUY - Low conviction + choppy market
AI SKIP: XRP-USD BUY - Low conviction + choppy market
AI SKIP: BTC-USD BUY - Low conviction + choppy market
AI advisor filtered: 0/5 kept (skipped: 5, reduced: 0)
```

**Safeguard Intervention**:
```
⚠️  AI SAFEGUARD: Rescued SOL-USD at 50% size
(AI vetoed all 5 proposals in chop regime, but total veto only allowed in crash/panic)
```

**Result**:
- Original SOL proposal: **2.1%** of NAV
- After safeguard: **1.05%** of NAV (2.1% × 0.5)
- Proceeds to risk engine for final approval

**Why SOL was chosen**: Highest conviction among the 5 proposals (tied with BTC at 0.54, selected first by sort)

---

### Scenario 2: CRASH Regime (Hypothetical)

**Initial Proposals** (3 total):
- BTC 1.5% (conviction: 0.45)
- ETH 1.2% (conviction: 0.42)
- SOL 0.8% (conviction: 0.38)

**AI Decision** (EXTREME DEFENSIVE):
```
AI SKIP: BTC-USD BUY - Market crash, extreme volatility
AI SKIP: ETH-USD BUY - Market crash, extreme volatility
AI SKIP: SOL-USD BUY - Market crash, extreme volatility
AI advisor filtered: 0/3 kept (skipped: 3, reduced: 0)
```

**Safeguard Intervention**: **NONE**
```
Market regime = crash → Total veto allowed
```

**Result**:
- Zero proposals forwarded to risk engine
- No trades executed
- System goes into preservation mode

**Rationale**: In crash conditions, it's safer to do nothing than force trades into a collapsing market

---

### Scenario 3: NORMAL Regime with Mixed AI Decisions

**Initial Proposals** (4 total):
- ETH 2.5% (conviction: 0.72)
- SOL 2.0% (conviction: 0.68)
- AVAX 1.5% (conviction: 0.45)
- DOGE 1.2% (conviction: 0.38)

**AI Decision** (NORMAL mode):
```
AI REDUCE: ETH-USD BUY - 2.50% → 2.00% (0.80x) - High conviction, proceed with slight caution
AI REDUCE: SOL-USD BUY - 2.00% → 1.60% (0.80x) - Good setup
AI SKIP: AVAX-USD BUY - Low conviction for current conditions
AI SKIP: DOGE-USD BUY - Low conviction for current conditions
AI advisor filtered: 2/4 kept (skipped: 2, reduced: 2)
```

**Safeguard Intervention**: **NONE**
```
AI returned 2 proposals → safeguard not triggered
```

**Result**:
- ETH 2.0% and SOL 1.6% forwarded to risk engine
- No safeguard intervention needed
- AI filtering worked as intended

---

## Safeguard Parameters

### Size Reduction: 50%

**Why 50%?**
- **Conservative**: Adds another layer of protection beyond AI's judgment
- **Meaningful**: Still large enough to be worth executing (not dust)
- **Simple**: No complex calculations, easy to audit

**Alternative considered**: Variable reduction based on market conditions
- **Rejected**: Adds complexity without clear benefit
- 50% provides consistent, predictable behavior

### Proposal Selection: Highest Conviction

**Why top conviction?**
- AI already evaluated all proposals and found them wanting
- If we must rescue one, pick the "least bad" option
- Conviction combines signal strength, confidence, and quality factors

**Alternatives considered**:
1. **Lowest volatility**: Too conservative, may miss opportunities
2. **Largest liquidity**: Doesn't account for setup quality
3. **Random**: Reduces predictability and auditability

---

## Edge Cases & Handling

### Edge Case 1: All Proposals Have Zero Conviction

**Scenario**: Rule engine generates proposals but all have conviction = 0

**Handling**:
```python
original_proposals_sorted = sorted(
    proposals_before_ai,
    key=lambda p: getattr(p, 'confidence', getattr(p, 'conviction', 0)),
    reverse=True
)
```

**Result**: First proposal by order will be selected (deterministic)

**Impact**: Minimal - if all proposals have zero conviction, they shouldn't have been generated

---

### Edge Case 2: Rescued Proposal Fails Risk Engine

**Scenario**: Safeguard rescues SOL at 1.05%, but risk engine rejects for other reasons (e.g., hourly trade limit, per-symbol cap)

**Handling**: Risk engine rejection is **independent** and **correct**

**Result**: No trade executed (expected behavior)

**Impact**: None - safeguard ensures at least one proposal reaches risk engine, but doesn't guarantee approval

**Log Example**:
```
AI SAFEGUARD: Rescued SOL-USD at 50% size
Risk engine BLOCKED all proposals: Hourly trade limit reached (3/3)
```

This is **correct behavior** - safeguard prevents AI total paralysis, risk engine enforces limits

---

### Edge Case 3: Multiple Proposals with Same Conviction

**Scenario**: 3 proposals all have conviction = 0.52

**Handling**: Python `sorted()` is **stable** - maintains original order for ties

**Result**: First proposal in original list is selected

**Impact**: Deterministic and reproducible behavior

---

### Edge Case 4: AI Returns Empty List for Different Reason

**Scenario**: AI returns empty list not because it skipped all, but due to error or timeout

**Handling**: Safeguard still triggers (cannot distinguish intent)

**Result**: Top proposal rescued

**Impact**: Acceptable - if AI fails, falling back to rule engine's top pick is reasonable

---

## Testing Scenarios

### Test 1: CHOP Regime Total Veto
```python
def test_safeguard_rescues_in_chop():
    # Setup: 5 proposals, AI skips all, regime=chop
    # Expected: Safeguard rescues top proposal at 50% size
    # Assertion: len(final_proposals) == 1
    # Assertion: final_proposals[0].size_pct == original_top_size * 0.5
```

### Test 2: CRASH Regime Total Veto
```python
def test_no_safeguard_in_crash():
    # Setup: 3 proposals, AI skips all, regime=crash
    # Expected: No safeguard intervention
    # Assertion: len(final_proposals) == 0
```

### Test 3: Partial AI Filtering
```python
def test_no_safeguard_when_ai_keeps_some():
    # Setup: 4 proposals, AI keeps 2, regime=chop
    # Expected: No safeguard intervention
    # Assertion: len(final_proposals) == 2 (AI's output unchanged)
```

### Test 4: Rescued Proposal Properties
```python
def test_rescued_proposal_metadata():
    # Setup: AI skips all in CHOP
    # Expected: Rescued proposal has correct metadata
    # Assertions:
    #   - size_pct reduced by 50%
    #   - notes include 'ai_safeguard_rescue': True
    #   - conviction/confidence preserved from original
```

---

## Monitoring & Alerts

### Metrics to Track

1. **Safeguard Trigger Rate**:
   - Frequency: How often does safeguard activate?
   - By regime: Track separately for chop/bear/normal/bull
   - **Alert if**: Triggers > 30% of cycles (indicates overly aggressive AI)

2. **Rescued Proposal Outcomes**:
   - **Approval rate**: % of rescued proposals that pass risk engine
   - **Execution rate**: % that actually execute
   - **PnL**: Performance of safeguard-rescued trades vs normal trades

3. **Regime-Specific Behavior**:
   - In CRASH/PANIC: Confirm zero proposals forwarded
   - In CHOP: Track safeguard trigger rate vs market volatility
   - In NORMAL/BULL: Should rarely trigger (AI shouldn't be that defensive)

### Log Patterns to Monitor

**Healthy Pattern**:
```
AI advisor filtered: 3/5 kept (skipped: 2, reduced: 3)
[No safeguard message]
Risk checks complete: approved=True, filtered=3/5
```

**Acceptable Pattern** (occasional safeguard):
```
AI advisor filtered: 0/5 kept (skipped: 5, reduced: 0)
⚠️  AI SAFEGUARD: Rescued SOL-USD at 50% size
Risk checks complete: approved=True, filtered=1/5
```

**Warning Pattern** (frequent safeguard triggers):
```
Cycle 1: ⚠️  AI SAFEGUARD: Rescued BTC-USD at 50% size
Cycle 2: ⚠️  AI SAFEGUARD: Rescued ETH-USD at 50% size
Cycle 3: ⚠️  AI SAFEGUARD: Rescued SOL-USD at 50% size
```
**Action**: Review AI model temperature, prompt, or risk mode calibration

**Critical Pattern** (safeguard rescue, then immediate stop-loss):
```
⚠️  AI SAFEGUARD: Rescued DOGE-USD at 50% size
[Execution]
[2 cycles later]
EXIT SIGNAL: DOGE-USD stop-loss hit (-8.5%)
```
**Action**: Evaluate if AI skip logic was correct and safeguard is forcing bad trades

---

## Tuning Parameters

### Current Settings
- **Size reduction**: 50% (multiplicative)
- **Regime whitelist for total veto**: ["crash", "panic"]
- **Proposal selection**: Max conviction/confidence

### Potential Adjustments

**If safeguard triggers too often** (>30% of cycles):
- **Tune AI model**: Adjust temperature, reduce conservativeness in prompts
- **Revise conviction thresholds**: Lower `min_conviction_to_propose` in strategy config
- **Review regime classification**: Ensure market isn't mis-labeled as CHOP too often

**If rescued trades underperform significantly**:
- **Increase size reduction**: 50% → 75% (more conservative)
- **Add additional filters**: Only rescue T1/T2 assets, not T3
- **Restrict by conviction floor**: Only rescue if original conviction > 0.50

**If safeguard never triggers**:
- **AI may be too permissive**: Check if DEFENSIVE mode is working
- **Or signals are weak**: Verify trigger engine is firing appropriately

---

## Design Rationale & Alternatives

### Why Not: Let AI Have Full Control?

**Rejected because**:
- AI models can be overly conservative
- Prompt engineering is imperfect
- External factors (model updates, API changes) could break calibration
- A stuck system (no trades for days) is a failure mode

### Why Not: Hard Minimum Proposal Count?

**Example**: "AI must keep at least 2 proposals from any batch"

**Rejected because**:
- Forces AI to rank proposals even when all are bad
- Removes AI's ability to express strong conviction against trading
- Creates perverse incentives (AI may artificially differentiate to meet quota)

**Safeguard approach is better**:
- AI can still veto everything (in crash/panic)
- Rescue is explicit, logged, and auditable
- System owner (human) sets regime rules, not AI

### Why Not: Variable Size Reduction by Regime?

**Example**: CHOP = 50%, BEAR = 75%, NORMAL = 25%

**Rejected because**:
- Adds complexity without clear benefit
- 50% is conservative enough for all non-crisis regimes
- Simpler is more auditable and predictable

### Why Not: Rescue Multiple Proposals?

**Example**: Rescue top 2 proposals instead of just 1

**Rejected because**:
- Increases risk if AI judgment was correct
- Creates portfolio concentration risk (what if both rescued trades correlate?)
- Single rescue is sufficient to prevent total paralysis

---

## Interaction with Other Safety Systems

### Trade Limits (Post-Safeguard)
```
AI SAFEGUARD: Rescued SOL-USD
→ Risk Engine: Hourly trade limit reached (3/3)
→ Result: No trade executed
```
**Outcome**: Safeguard ensures proposal reaches risk engine, but rate limits still apply ✅

### Per-Symbol Caps (Post-Safeguard)
```
AI SAFEGUARD: Rescued ETH-USD at 1.5%
→ Risk Engine: position_size_with_pending (2.8% > 2.4% cap)
→ Result: No trade executed
```
**Outcome**: Safeguard doesn't bypass risk engine position sizing rules ✅

### Min Position Size (Post-Safeguard)
```
AI SAFEGUARD: Rescued AVAX-USD at 0.5% (originally 1.0%)
→ Step 10 Filter: 0.5% × $255 NAV = $1.28 > $1.00 min_notional ✅
→ Risk Engine: 0.5% > 0.25% min_position_size_pct ✅
→ Result: Proposal proceeds to execution
```
**Outcome**: 50% reduction keeps most proposals above minimum thresholds ✅

### AI Sizing Floor (Post-Safeguard)
```
Original proposal: 0.8% (conviction: 0.35)
AI would reduce: 0.8% × 0.2 = 0.16% → AI skips (below 0.25% floor)
Safeguard rescues: 0.8% × 0.5 = 0.40% → Above 0.25% floor ✅
```
**Outcome**: Safeguard 50% reduction is more conservative than AI min (0.2x), so rescued proposals typically pass AI floor ✅

---

## Success Criteria

### Short-Term (Next 50 Cycles)
- [ ] Safeguard triggers in <20% of CHOP cycles
- [ ] When triggered, rescued proposals pass risk engine >50% of time
- [ ] Zero safeguard triggers in CRASH/PANIC regimes (if encountered)
- [ ] No "stuck" periods >4 hours without any trading

### Medium-Term (Next 500 Cycles / 1 Week)
- [ ] Rescued trade PnL is neutral to positive (not significantly worse than normal)
- [ ] Safeguard trigger rate decreases over time (AI learns/calibrates)
- [ ] Clear correlation between safeguard triggers and market choppiness
- [ ] No incidents of "safeguard + immediate stop-loss" >10% of rescued trades

### Long-Term (1 Month+)
- [ ] Safeguard is rarely needed (<5% of cycles) as AI calibration improves
- [ ] When needed, it successfully prevents prolonged trading freezes
- [ ] System maintains 70-80% uptime (trading at least once per day)
- [ ] Safeguard contributes positively to risk-adjusted returns (by preventing opportunity cost)

---

## Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2025-11-18 | 1.0 | GitHub Copilot | Initial documentation after log analysis |

---

## Related Documentation

- `CONFIGURATION_DEAD_ZONE_FIX_2025-11-17.md` - Sizing constraint fixes
- `CRITICAL_FIX_size_in_quote_2025-11-17.md` - Fill parsing bug fix
- `AI_ADVISOR_ARCHITECTURE.md` - Overall AI advisor design
- `ARCHITECTURE_IMPLEMENTATION_COMPLETE.md` - System architecture overview

---

## Summary

The AI SAFEGUARD is a **conservative backstop** that prevents the AI from completely freezing trading during normal market conditions. Key principles:

1. **Regime-aware**: Total veto only allowed in crash/panic
2. **Conservative rescue**: 50% size reduction on top proposal
3. **Independent from risk engine**: Safeguard ensures proposal reaches risk checks, doesn't guarantee approval
4. **Auditable**: Clear logging when triggered
5. **Tunable**: Can adjust size reduction and regime rules based on performance data

**This is not a fix for bad AI judgment** - it's a safety net that ensures the system remains operational while respecting AI conservativeness within reasonable bounds.
