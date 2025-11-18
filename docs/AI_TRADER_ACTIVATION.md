# AI Trader Activation Guide

**Date:** 2025-11-18  
**Status:** ✅ READY FOR TESTING  
**Environment:** LIVE sandbox ($199.58 USDC, ~$257 total portfolio)

---

## What Changed

### 1. Config Updates (`config/app.yaml`)

**Added top-level `ai_trader` section (lines 53-63):**
```yaml
ai_trader:
  enabled: false              # User-facing toggle (documentation)
  sandbox: true               # Sandbox mode flag
  model: "gpt-4o-mini"        # LLM model
  max_total_capital_to_use_pct: 0.40  # AI can target up to 40% of equity
  max_decisions: 5            # Max positions per cycle
  min_confidence: 0.55        # Confidence threshold
  min_rebalance_delta_pct: 0.35  # Min rebalance delta
  max_position_pct: 7.0       # Single position cap
  max_single_trade_pct: 4.0   # Single trade cap
```

**Enabled `ai.trader_agent` section (lines 176-188):**
```yaml
ai:
  trader_agent:
    enabled: true              # ✅ ACTIVATED (was false)
    provider: "openai"         # ✅ Using real OpenAI (was mock)
    model: "gpt-4o-mini"
    timeout_s: 10.0
    max_decisions: 5
    min_confidence: 0.55
    min_rebalance_delta_pct: 0.35
    max_position_pct: 7.0
    max_single_trade_pct: 4.0
```

---

## How It Works

### Current Flow (Rules-Only, Before)
```
Step 1-7: Portfolio snapshot, universe, triggers
Step 8: Scan for triggers
  ├─ If 0 triggers → NO_TRADE ❌
  └─ If triggers > 0:
      Step 9: rules_engine.propose_trades()
      Step 10: risk_engine.filter_and_size()
      Step 11: execution_engine.execute()
```

### New Flow (AI Trader Enabled, After)
```
Step 1-7: Portfolio snapshot, universe, triggers
Step 8: Scan for triggers
Step 9: Generate proposals
  ├─ Local: strategy_registry.aggregate_proposals()
  └─ IF ai_trader_agent exists:
      Step 9A: 🤖 AI Trader Agent
        ├─ Build snapshot (universe, positions, triggers, guardrails)
        ├─ Call LLM: get_ai_trader_decision(snapshot)
        ├─ Parse response: TraderDecision with target allocations
        ├─ Convert to TradeProposal[] (delta sizing)
        ├─ Merge with local proposals
        └─ Return combined set
Step 10: risk_engine.filter_and_size() [AI proposals pass through same checks]
Step 11: execution_engine.execute()
```

### Key AI Trader Behaviors

**Inputs to LLM:**
- Universe snapshot (price, volume, spread, tier, changes 1h/24h, volatility)
- Current positions (symbol, size, PnL $, PnL %)
- Available capital USD
- Market regime (CHOP/TREND/CRASH)
- Guardrails (max at risk %, max position %, min notional, max trades/cycle)
- Recent triggers (symbol, type, strength, confidence)
- Metadata (cycle number, timestamp, mode, config hash)

**LLM Output:**
```json
{
  "decisions": [
    {
      "symbol": "SOL-USD",
      "action": "BUY",
      "target_weight_pct": 3.5,
      "confidence": 0.75,
      "stop_loss_pct": 8.0,
      "take_profit_pct": 18.0,
      "rationale": "Strong momentum + regime favorable"
    }
  ]
}
```

**Conversion to TradeProposal:**
1. Calculate delta: `target_weight_pct - current_position_pct`
2. If delta < `min_rebalance_delta_pct` (0.35%) → skip (too small)
3. Clamp trade size to `max_single_trade_pct` (4%)
4. Clamp position to `max_position_pct` (7%)
5. Create `TradeProposal(symbol, side, size_pct, reason, confidence, ...)`

**Safety Guarantees:**
- AI proposals **ALWAYS** pass through `RiskEngine` (same as rules_engine)
- Risk caps enforced: max 25% total at risk, max 7% per position
- Circuit breakers active: data staleness, exchange health, drawdown limits
- Kill switch: `data/KILL_SWITCH` → NO_TRADE immediately
- Fail-closed: any LLM error/timeout → zero proposals (no trades)

---

## What Will Happen Next Cycle

When the bot runs its next cycle (60s intervals):

1. **Step 8: Trigger scan**
   - Current state: 0 triggers detected
   - **Before:** NO_TRADE immediately ❌
   - **After:** Continue to AI trader even with 0 triggers ✅

2. **Step 9A: AI Trader**
   - Build snapshot with current portfolio ($257 NAV, 21.6% exposure)
   - Call OpenAI GPT-4o-mini with context
   - AI evaluates: rebalance opportunities, trim losers, add winners
   - Generate 0-5 proposals

3. **Step 10: Risk Check**
   - Enforce exposure caps (25% total, 7% per symbol)
   - Verify no cooldowns active
   - Check circuit breakers (data staleness, API health, volatility)
   - Apply fee-aware sizing

4. **Step 11: Execution**
   - Place orders via Coinbase Advanced Trade API
   - Use post-only limit orders (maker fee 0.40%)
   - Reconcile fills after 0.75s wait
   - Update StateStore positions

---

## Expected Behaviors

### Scenario 1: AI Sees No Opportunities
```
AI trader agent produced no actionable proposals this cycle
✅ NO_TRADE (safe - no forced trades)
```

### Scenario 2: AI Proposes Rebalance
```
AI trader agent generated 2 proposals
  ├─ BUY SOL-USD 2.5% (target 4.0% from 1.5%)
  └─ SELL DOGE-USD 1.2% (target 2.0% from 3.2%)
Risk engine approved 2/2 proposals
Executing 2 orders...
```

### Scenario 3: AI Blocked by Risk
```
AI trader agent generated 3 proposals
Risk engine approved 1/3 proposals
  ├─ ✅ BUY BTC-USD 3.0%
  ├─ ❌ BUY ETH-USD 5.0% (would exceed 25% total at risk)
  └─ ❌ BUY SOL-USD 2.0% (symbol on cooldown after recent fill)
Executing 1 order...
```

### Scenario 4: LLM Failure
```
AI trader agent failed to get decisions: OpenAI timeout after 10.0s
⚠️  Falling back to local proposals only
```

---

## Monitoring & Observability

### Logs to Watch
```bash
tail -f logs/247trader-v2.log | grep -E "AI trader|Step 9A"
```

**Key log lines:**
- `🧠 Step 9A: AI trader agent evaluating portfolio allocations...`
- `✅ AI trader agent contributed N proposal(s); merged total=M`
- `AI trader agent produced no actionable proposals this cycle`
- `AI trader agent failed to get decisions: <error>`

### Audit Trail
Every cycle writes to audit log with:
- Config hash (detect config drift)
- Universe snapshot
- Trigger signals
- Proposals (local + AI)
- Risk decisions (approved/rejected)
- Final orders + fills

### Metrics (Prometheus)
- `trader_ai_proposals_total{source="ai_trader_agent"}`
- `trader_ai_decisions_latency_seconds`
- `trader_ai_errors_total{reason="timeout|parse|network"}`

---

## Safety Nets

### 1. Kill Switch
```bash
touch data/KILL_SWITCH
```
- Detected within 3 seconds (MTTD <3s per REQ-K1)
- Cancels all open orders within 10 seconds
- Fires CRITICAL alert within 5 seconds
- Bot continues running but NO_TRADE every cycle

### 2. Read-Only Mode
```yaml
exchange:
  read_only: true  # In app.yaml
```
- Blocks all order placement
- Bot runs full cycle but skips execution

### 3. Position Caps
Current policy:
- **Max total at risk:** 25% of equity
- **Max per symbol:** 7% of equity
- **Max per trade:** 4% of equity
- **Min notional:** $5 USD

### 4. Auto-Trim
If portfolio exposure exceeds 25%:
- Auto-converts excess holdings to USDC
- Uses maker limit orders (TWAP if large)
- Runs before AI trader proposals

---

## Rollback Plan

### If AI trades are undesirable:

**Option 1: Disable AI trader (keep bot running)**
```yaml
# config/app.yaml line 177
ai:
  trader_agent:
    enabled: false  # ← Change to false
```
Restart not required - next cycle will skip AI.

**Option 2: Emergency stop (kill switch)**
```bash
touch data/KILL_SWITCH
```
Immediate halt (detection <3s). Remove file to resume.

**Option 3: Full stop**
```bash
pkill -f app_run_live.sh
# or
./scripts/stop_bot.sh
```

---

## Testing Checklist

Before enabling in production:

- [x] Config validation (`yaml.safe_load` passed)
- [x] AI trader agent initialization wired
- [x] Proposal conversion logic (`_decisions_to_proposals`)
- [x] Risk engine integration
- [x] Fail-closed behavior on LLM errors
- [ ] **Run 1 cycle with enabled=true in sandbox**
- [ ] Verify proposals generated
- [ ] Verify risk caps enforced
- [ ] Monitor for LLM latency (<10s)
- [ ] Check audit logs for AI decisions

---

## Cost Estimate

**GPT-4o-mini pricing (as of 2024):**
- Input: $0.15 per 1M tokens
- Output: $0.60 per 1M tokens

**Per cycle estimate:**
- Input snapshot: ~2,000 tokens (universe + positions + triggers)
- Output decision: ~500 tokens (JSON response)
- **Cost per cycle:** ~$0.00045 (< $0.001)

**Daily cost (1-minute intervals):**
- 1,440 cycles/day
- ~$0.65/day
- ~$20/month

**Note:** Sandbox portfolio ~$250, so AI cost is 0.26% of capital per month.

---

## Next Steps

1. **Monitor next cycle:**
   ```bash
   tail -f logs/247trader-v2.log
   ```

2. **Watch for:**
   - AI trader agent initialization success
   - LLM call latency (<10s)
   - Proposals generated (if any)
   - Risk approval decisions

3. **If successful:**
   - Let run for 24 hours in sandbox
   - Analyze trade quality vs rules_engine baseline
   - Compare PnL, hit rate, drawdown

4. **Gradual scale-up:**
   - Keep `max_total_capital_to_use_pct: 0.40` (40%)
   - Keep `max_position_pct: 7.0` (7%)
   - Monitor for 1 week before increasing limits

---

## Questions & Debugging

**Q: AI trader not generating proposals?**
- Check logs for "AI trader agent produced no actionable proposals"
- Verify LLM returned valid JSON (check error logs)
- Confirm `min_rebalance_delta_pct` not too high (0.35% default)

**Q: All AI proposals rejected by risk?**
- Check exposure: `portfolio.total_exposure_pct` < 25%?
- Check cooldowns: recent fills on proposed symbols?
- Check circuit breakers: data staleness, API errors?

**Q: LLM timeouts?**
- Increase `timeout_s` to 15.0 or 20.0
- Check OpenAI API status
- Verify `OPENAI_API_KEY` environment variable set

**Q: How to see AI reasoning?**
- Audit logs include AI `rationale` field
- Set logging level to DEBUG for full LLM responses
- Check `logs/247trader-v2.log` for decision context

---

**Status:** Ready for testing. Start bot and monitor first cycle with AI trader enabled.
