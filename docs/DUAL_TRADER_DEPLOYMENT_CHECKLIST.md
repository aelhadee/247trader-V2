# Dual-Trader Deployment Checklist

**System**: 247trader-v2 Dual-Trader Architecture  
**Status**: ✅ Implementation Complete, Ready for Deployment Testing  
**Date**: 2025-11-17

---

## ✅ Implementation Status

### Core Components (All Complete)
- ✅ `ai/llm_client.py` - AI trader client with OpenAI/Anthropic/Mock providers
- ✅ `ai/snapshot_builder.py` - Market snapshot builder for AI context
- ✅ `strategy/ai_trader_strategy.py` - AI trader implementing BaseStrategy
- ✅ `strategy/meta_arb.py` - Deterministic meta-arbitration layer
- ✅ `ai/arbiter_client.py` - Optional AI arbiter (Model #2)

### Integration (Complete)
- ✅ `runner/main_loop.py` - Dual-trader mode in Step 9
- ✅ `config/app.yaml` - Configuration section added
- ✅ `core/audit_log.py` - Arbitration logging enhanced

### Testing & Documentation (Complete)
- ✅ `tests/test_dual_trader.py` - 14/14 tests passing
- ✅ `docs/DUAL_TRADER_ARCHITECTURE.md` - Full architecture documentation
- ✅ All configuration validated by `config_validator.py`

---

## 🚀 Deployment Phases

### Phase 1: Mock Mode Testing (Duration: 1-2 days)

**Objective**: Validate integration without external API calls

#### Steps:
1. **Update Configuration**
   ```yaml
   # config/app.yaml
   ai:
     dual_trader:
       enabled: true
       provider: "mock"
       max_decisions: 5
       arbitration:
         min_ai_confidence: 0.6
         ai_override_threshold: 0.7
   ```

2. **Start Bot in DRY_RUN Mode**
   ```bash
   # Ensure DRY_RUN mode in app.yaml
   exchange:
     mode: "DRY_RUN"
   
   # Start bot
   ./app_run_live.sh --loop
   ```

3. **Monitor Logs**
   ```bash
   # Watch arbitration decisions
   tail -f logs/247trader-v2.log | grep "⚖️"
   
   # Check audit trail
   tail -f logs/audit.jsonl | jq '.arbitration'
   ```

4. **Validation Checklist**
   - [ ] Bot starts without errors
   - [ ] Mock AI decisions appear in logs
   - [ ] Arbitration logic executes (see "⚖️" symbols)
   - [ ] Final proposals passed to RiskEngine
   - [ ] Audit trail includes arbitration entries
   - [ ] No crashes or exceptions after 24h runtime

5. **Expected Metrics** (from logs/audit)
   - `proposals_local`: >0 (rules engine active)
   - `proposals_ai`: >0 (mock AI active)
   - `proposals_final`: >0 (arbitration produces output)
   - Arbitration resolutions: mix of SINGLE/BLEND/LOCAL/AI/NONE

---

### Phase 2: Live API with Deterministic Arbitration (Duration: 3-5 days)

**Objective**: Test real AI proposals in production without AI arbiter

#### Prerequisites:
- ✅ Phase 1 completed successfully
- ✅ API keys set in environment: `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`
- ✅ Baseline metrics captured from local-only mode

#### Steps:

1. **Set API Keys**
   ```bash
   # Add to .env or export directly
   export OPENAI_API_KEY="sk-proj-..."
   # OR
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

2. **Update Configuration**
   ```yaml
   # config/app.yaml
   ai:
     dual_trader:
       enabled: true
       provider: "openai"  # or "anthropic"
       model: "gpt-5-mini-2025-08-07"  # or "claude-sonnet-4-5-20250929"
       api_key: "${OPENAI_API_KEY}"
       timeout_s: 2.0
       max_decisions: 5
       min_confidence: 0.0
       
       arbitration:
         min_ai_confidence: 0.6
         ai_override_threshold: 0.7
         local_weak_conviction: 0.35
         ai_confidence_advantage: 0.25
         blend_mode: "conservative"
       
       arbiter:
         enabled: false  # Keep deterministic only for Phase 2
   ```

3. **Start in PAPER Mode** (Recommended)
   ```yaml
   # config/app.yaml
   exchange:
     mode: "PAPER"  # Paper trading first
   ```
   ```bash
   ./app_run_live.sh --loop
   ```

4. **Monitor Key Metrics**
   ```bash
   # AI latency
   grep "ai_trader_latency" logs/247trader-v2.log | tail -20
   
   # Arbitration distribution
   grep "arbitration.*resolution" logs/audit.jsonl | jq -r '.arbitration[].resolution' | sort | uniq -c
   
   # Proposal counts
   tail -f logs/audit.jsonl | jq '{local: .proposals_local, ai: .proposals_ai, final: .proposals_final}'
   ```

5. **Validation Checklist**
   - [ ] AI latency consistently <2s (99th percentile)
   - [ ] No timeout errors (or <1% timeout rate)
   - [ ] Arbitration resolution distribution reasonable:
     - SINGLE (local-only): 30-50%
     - SINGLE (AI-only): 10-30%
     - BLEND: 10-30%
     - LOCAL (conflict): 5-15%
     - AI (conflict): 0-10%
     - NONE (stand down): 0-10%
   - [ ] Final proposal count sensible (not always 0 or always max)
   - [ ] No API cost surprises (track OpenAI/Anthropic billing)
   - [ ] RiskEngine rejections logged properly
   - [ ] Audit trail complete for all arbitration decisions

6. **Performance Comparison** (vs local-only baseline)
   ```bash
   # Generate reports
   python analytics/performance_report.py \
     --baseline baseline/2024_q4_baseline.json \
     --recent logs/audit.jsonl
   ```
   
   Compare:
   - Win rate
   - Average win/loss size
   - Max drawdown
   - Sharpe ratio
   - Trade count

7. **Rollback Trigger**
   If any of these occur, disable dual-trader:
   - AI latency >5s sustained for >10 cycles
   - API error rate >10%
   - Arbitration logic crashes
   - Performance degrades >20% vs baseline
   - Unexpected API costs (>$50/day)

   **Rollback Command**:
   ```yaml
   # config/app.yaml
   ai:
     dual_trader:
       enabled: false
   ```
   Restart bot → falls back to local-only mode

---

### Phase 3: AI Arbiter (Optional, Duration: 1-2 weeks)

**Objective**: Use Model #2 for tie-breaking unresolved conflicts

#### Prerequisites:
- ✅ Phase 2 completed successfully for ≥5 days
- ✅ Deterministic arbitration performance acceptable
- ✅ Second API key available (Anthropic if using OpenAI for trader, or vice versa)

#### Steps:

1. **Set Second API Key**
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."  # If OpenAI is trader
   # OR
   export OPENAI_API_KEY="sk-proj-..."    # If Anthropic is trader
   ```

2. **Update Configuration**
   ```yaml
   # config/app.yaml
   ai:
     dual_trader:
       enabled: true
       # ... trader config unchanged ...
       
       arbiter:
         enabled: true
         provider: "anthropic"  # Different from trader
         model: "claude-sonnet-4-5-20250929"
         api_key: "${ANTHROPIC_API_KEY}"
         timeout_s: 1.5
   ```

3. **Monitor Arbiter Usage**
   ```bash
   # Check when arbiter is invoked
   grep "AI arbiter invoked" logs/247trader-v2.log
   
   # Arbiter decisions vs deterministic
   grep "arbiter.*resolution" logs/audit.jsonl | jq -r '.arbitration[] | select(.arbiter_used) | {symbol, deterministic: .resolution, arbiter: .arbiter_resolution}'
   ```

4. **Validation Checklist**
   - [ ] Arbiter only called for unresolved conflicts (NONE cases)
   - [ ] Arbiter latency <1.5s (99th percentile)
   - [ ] Arbiter override rate reasonable (<30% of calls)
   - [ ] Combined latency (AI trader + arbiter) <3.5s
   - [ ] No double API cost surprises
   - [ ] Performance stable or improved vs Phase 2

5. **A/B Test** (Optional)
   - Run arbiter-enabled for 1 week
   - Run arbiter-disabled for 1 week
   - Compare performance metrics
   - Keep arbiter enabled only if clear improvement

---

## 🔍 Monitoring & Alerting

### Key Metrics to Track

1. **Latency**
   - `ai_trader_latency_ms`: <2000ms (P99)
   - `ai_arbiter_latency_ms`: <1500ms (P99)
   - Total cycle time: unchanged from baseline

2. **Proposal Counts**
   - `proposals_local`: Should match historical rates
   - `proposals_ai`: 0-5 per cycle
   - `proposals_final`: Reasonable blend of above

3. **Arbitration Resolution Distribution**
   ```bash
   # Check daily
   grep "resolution" logs/audit.jsonl | \
     jq -r '.arbitration[].resolution' | \
     sort | uniq -c | sort -rn
   ```

4. **Error Rates**
   - AI timeout rate: <1%
   - API error rate (4xx/5xx): <5%
   - JSON parsing errors: 0%

5. **Cost Tracking**
   - OpenAI API cost/day
   - Anthropic API cost/day
   - Cost per trade generated

### Alert Thresholds

Set up alerts (Grafana/Prometheus) for:
- 🚨 AI latency >5s for >5 consecutive cycles
- 🚨 API error rate >10% over 10min
- 🚨 Arbitration logic exception
- 🚨 Zero AI proposals for >30min (if enabled)
- ⚠️ API cost >$100/day
- ⚠️ Performance degradation >15% vs baseline

---

## 🛠 Troubleshooting

### Issue: AI Latency Spikes

**Symptoms**: ai_trader_latency_ms >5s

**Diagnosis**:
```bash
grep "ai_trader_latency" logs/247trader-v2.log | \
  awk '{print $NF}' | sort -n | tail -20
```

**Solutions**:
1. Check OpenAI/Anthropic status page
2. Reduce `max_decisions` to 3
3. Increase `timeout_s` to 3.0
4. Switch provider (OpenAI ↔ Anthropic)
5. Temporarily disable dual-trader

---

### Issue: Poor Arbitration Decisions

**Symptoms**: Performance worse than local-only baseline

**Diagnosis**:
```bash
# Check resolution distribution
grep "resolution" logs/audit.jsonl | jq -r '.arbitration[].resolution' | sort | uniq -c

# Check AI confidence vs local conviction
grep "arbitration" logs/audit.jsonl | \
  jq '.arbitration[] | {symbol, ai_conf: .ai_confidence, local_conv: .local_confidence, resolution}'
```

**Solutions**:
1. Adjust thresholds:
   - Increase `min_ai_confidence` to 0.7 (be more selective)
   - Increase `ai_override_threshold` to 0.8 (trust local more)
   - Decrease `local_weak_conviction` to 0.3 (trust local less often)
2. Change `blend_mode` to "average" (less conservative)
3. Enable AI arbiter for tie-breaking
4. Collect more data before tuning

---

### Issue: High API Costs

**Symptoms**: Unexpected OpenAI/Anthropic billing

**Diagnosis**:
```bash
# Count API calls per day
grep "Calling AI trader client" logs/247trader-v2.log | \
  awk '{print $1}' | uniq -c

# Calculate calls per cycle
# (should be 1 call/cycle if dual_trader enabled)
```

**Solutions**:
1. Reduce `max_decisions` to 3
2. Increase cycle interval (if appropriate)
3. Switch to smaller model (e.g., gpt-4o-mini)
4. Implement request caching (future enhancement)

---

### Issue: JSON Parsing Errors

**Symptoms**: Logs show "Failed to parse AI response"

**Diagnosis**:
```bash
grep "Failed to parse" logs/247trader-v2.log -A 5
```

**Solutions**:
1. Check AI response format in logs
2. Verify JSON schema in prompt matches `AiTradeDecision`
3. Switch model (some models better at JSON)
4. Add retry logic (future enhancement)

---

## 🔐 Security Checklist

- [ ] API keys stored in environment variables only (never in code/config)
- [ ] API keys not logged or committed to git
- [ ] Read-only mode enforced during testing phases
- [ ] API rate limiting configured (avoid runaway costs)
- [ ] Timeout enforcement prevents hanging requests
- [ ] Error handling prevents secret leakage in stack traces

---

## 📊 Success Criteria

### Phase 1 (Mock Mode)
- ✅ Bot runs 24h without crashes
- ✅ Arbitration logic executes correctly
- ✅ Audit trail complete

### Phase 2 (Live API)
- ✅ AI latency <2s (P99)
- ✅ API error rate <5%
- ✅ Performance ≥baseline (or within -5%)
- ✅ API costs <$50/day
- ✅ No RiskEngine bypass detected

### Phase 3 (AI Arbiter)
- ✅ Arbiter invoked only for conflicts
- ✅ Arbiter latency <1.5s (P99)
- ✅ Performance ≥Phase 2 (or within -3%)

---

## 📝 Rollback Plan

**Immediate Rollback** (any critical issue):
```bash
# 1. Edit config
vim config/app.yaml
# Set: ai.dual_trader.enabled = false

# 2. Restart bot
pkill -f "python -m runner.main_loop"
./app_run_live.sh --loop
```

**Verification**:
```bash
# Confirm local-only mode
tail -f logs/247trader-v2.log | grep "dual_trader_enabled"
# Should show: "dual_trader_enabled=False"
```

Bot falls back to local-only rules engine immediately.

---

## 📞 Support

- **Architecture**: `docs/DUAL_TRADER_ARCHITECTURE.md`
- **Tests**: `tests/test_dual_trader.py`
- **Config**: `config/app.yaml` (ai.dual_trader section)
- **Logs**: `logs/247trader-v2.log`, `logs/audit.jsonl`

---

## ✅ Final Deployment Decision

**Go/No-Go Criteria**:

| Criterion | Status | Notes |
|-----------|--------|-------|
| All tests passing | ✅ 14/14 | |
| Config validated | ✅ | |
| Documentation complete | ✅ | |
| Mock mode validated | ⏳ Pending | Phase 1 |
| Live API validated | ⏳ Pending | Phase 2 |
| Performance acceptable | ⏳ Pending | Phase 2 |
| Cost acceptable | ⏳ Pending | Phase 2 |

**Current Recommendation**: **PROCEED with Phase 1 (Mock Mode)**

System is production-ready for testing. Begin with mock mode to validate integration, then proceed to live API testing after 24-48h of stable mock operation.

---

**Last Updated**: 2025-11-17  
**Next Review**: After Phase 1 completion
