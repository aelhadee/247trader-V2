# AI Advisor Architecture

**Version:** 1.0  
**Status:** Production-Ready  
**Last Updated:** 2025-11-16

---

## TL;DR

247trader-v2 now includes an **AI advisor layer** that filters and resizes trade proposals between the rules engine and risk engine. The AI can only **shrink**, **skip**, or **tag** trades—it cannot increase sizes or override policy caps. This document covers the architecture, safety guarantees, configuration, and operational procedures.

**Key Principles:**
- AI is **advisory only**: RiskEngine and ExecutionEngine remain the hard authority
- AI cannot increase trade sizes (max_scale_up ≤ 1.0)
- All policy.yaml caps are enforced after AI filtering
- Safe fallback on any error (no AI influence)
- Full audit trail of all AI decisions

---

## Architecture Overview

### System Flow

```
Universe → Triggers → Rules Engine → [AI Advisor] → Risk Engine → Execution Engine
                                          ↓
                                    filter/resize
                                    risk_mode suggestion
```

**Step 9:** Rules Engine generates proposals  
**Step 9.5 (NEW):** AI Advisor filters/resizes proposals  
**Step 10:** Filter pending orders and capacity  
**Step 11:** Risk Engine validates all constraints  
**Step 12:** Execution Engine places orders

### Core Components

#### 1. AIAdvisorService (`ai/advisor.py`)

**Purpose:** Single entry point for AI-driven proposal filtering

**Responsibilities:**
- Accept proposals + market context
- Call model client with strict timeout
- Parse and sanitize AI responses
- Clamp size_factor to [0, max_scale_up]
- Fall back safely on any error

**Safety Guarantees:**
- `max_scale_up ≤ 1.0` → AI cannot increase sizes
- `fallback_on_error=True` → errors bypass AI (safe)
- `timeout_s=1.0` → hard timeout prevents blocking
- Ignores hallucinated proposals (only acts on provided ones)

#### 2. Model Client (`ai/model_client.py`)

**Purpose:** Abstraction for AI providers (OpenAI, Anthropic, mock)

**Supported Providers:**
- **OpenAI:** GPT-4 Turbo, GPT-4o (requires `OPENAI_API_KEY`)
- **Anthropic:** Claude 3.5 Sonnet/Opus (requires `ANTHROPIC_API_KEY`)
- **Mock:** Testing/dry-run mode (no API calls)

**Features:**
- Structured prompts with market context
- JSON response format enforcement
- Timeout handling at HTTP layer
- Automatic retry with exponential backoff (TODO: implement)

#### 3. Risk Profiles (`ai/risk_profile.py`)

**Purpose:** Map AI risk modes to concrete constraints

**Risk Modes:**

| Mode       | Size Multiplier | Max At Risk | Description                    |
|------------|-----------------|-------------|--------------------------------|
| OFF        | 0.0             | 0%          | Kill switch - no trades        |
| DEFENSIVE  | 0.5             | 10%         | Half sizing, lower exposure    |
| NORMAL     | 1.0             | 15%         | Standard operation             |
| AGGRESSIVE | 1.0             | 15%         | Future: allow higher sizing    |

**Key Constraint:** All values are capped by `policy.yaml` limits. AI cannot exceed policy.

#### 4. Data Schemas (`ai/schemas.py`)

**Core Types:**
- `AIProposalIn`: Proposal as seen by AI
- `AIMarketSnapshot`: High-level market context
- `AIPortfolioSnapshot`: Current portfolio state
- `AIAdvisorInput`: Complete input payload
- `AIProposalDecision`: AI decision for single proposal
- `AIAdvisorOutput`: AI response with risk mode

**Type Safety:** All schemas are strongly typed dataclasses for compile-time validation.

---

## Safety Guarantees

### 1. Size Constraints

**Guarantee:** AI can **never** increase trade sizes.

**Implementation:**
```python
max_scale_up = 1.0  # Hard-coded ceiling
size_factor = min(size_factor, max_scale_up)
```

**Validation:** Test `test_size_factor_clamped_to_max_scale_up` ensures this.

### 2. Policy Authority

**Guarantee:** `policy.yaml` caps are **always** enforced after AI filtering.

**Implementation:**
```python
runtime_caps = apply_risk_profile_to_caps(
    mode=risk_mode,
    policy_max_at_risk_pct=policy_max_at_risk_pct,  # Ceiling
    policy_max_positions=policy_max_positions,      # Ceiling
)
```

**Validation:** Test `test_apply_risk_profile_respects_policy_caps` ensures this.

### 3. Fail-Closed Behavior

**Guarantee:** Any AI error results in **no AI influence** (original proposals proceed).

**Implementation:**
```python
try:
    ai_output = self.ai_advisor.advise(input_data, client)
except Exception as e:
    logger.error(f"AI error: {e}")
    # Continue with original proposals
```

**Validation:** Test `test_fallback_on_client_error` ensures this.

### 4. Timeout Enforcement

**Guarantee:** AI calls complete within `timeout_s` or fail.

**Implementation:**
- OpenAI/Anthropic clients enforce timeout at HTTP layer
- Default: 1.0 second (configurable)

### 5. Audit Trail

**Guarantee:** All AI decisions are logged for post-trade analysis.

**Implementation:**
```python
# Metadata added to proposal
p.notes['ai_decision'] = d.decision
p.notes['ai_size_factor'] = d.size_factor
p.notes['ai_comment'] = d.comment
```

**Output:** Captured in audit log JSONL for every cycle.

---

## Configuration

### Config File: `config/app.yaml`

```yaml
ai:
  # Enable/disable AI advisor
  enabled: false  # Start disabled, enable after testing
  
  # Model provider and configuration
  provider: "openai"  # openai | anthropic | mock
  model: "gpt-4-turbo-preview"
  api_key: "${OPENAI_API_KEY}"  # Environment variable
  
  # Safety constraints
  timeout_s: 1.0              # Hard timeout (fail-closed)
  max_scale_up: 1.0           # NEVER >1.0 in v1
  fallback_on_error: true     # Safe fallback on errors
  
  # Risk mode influence
  default_risk_mode: "NORMAL"
  allow_risk_mode_override: false  # Disable initially
  
  # Observability
  log_decisions: true
  metrics_enabled: true
```

### Environment Variables

**OpenAI:**
```bash
export OPENAI_API_KEY="sk-..."
```

**Anthropic:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

**Note:** Keys are **never** logged or persisted in state.

---

## Operational Guide

### Phase 1: Initial Deployment

**Goal:** Validate AI advisor in shadow mode (log-only).

**Steps:**

1. **Enable mock mode:**
   ```yaml
   ai:
     enabled: true
     provider: "mock"  # No API calls
   ```

2. **Run for 24-48 hours:**
   ```bash
   ./app_run_live.sh --loop
   ```

3. **Review logs:**
   ```bash
   grep "AI SKIP\|AI REDUCE" logs/247trader-v2.log
   ```

4. **Validate decisions align with strategy:**
   - Check if AI skips are reasonable
   - Verify size reductions match market conditions

### Phase 2: Live API (Read-Only)

**Goal:** Use real AI model but keep `allow_risk_mode_override=false`.

**Steps:**

1. **Configure provider:**
   ```yaml
   ai:
     enabled: true
     provider: "openai"
     model: "gpt-4-turbo-preview"
     allow_risk_mode_override: false  # AI only filters proposals
   ```

2. **Set API key:**
   ```bash
   export OPENAI_API_KEY="sk-..."
   ```

3. **Monitor metrics:**
   - AI call latency (should be <500ms)
   - Error rate (should be <1%)
   - Decision distribution (accept/reduce/skip)

4. **Verify fallback behavior:**
   ```bash
   # Temporarily break API key to test fallback
   export OPENAI_API_KEY="invalid"
   # Should continue trading without AI influence
   ```

### Phase 3: Risk Mode Influence

**Goal:** Allow AI to suggest risk modes and adjust runtime caps.

**Steps:**

1. **Enable risk mode override:**
   ```yaml
   ai:
     allow_risk_mode_override: true
   ```

2. **Monitor risk mode transitions:**
   ```bash
   grep "AI risk mode:" logs/247trader-v2.log
   ```

3. **Validate caps are respected:**
   - Check that DEFENSIVE reduces exposure
   - Verify OFF mode blocks all trades
   - Confirm policy caps are never exceeded

### Emergency Procedures

#### Kill Switch (Disable AI)

**Scenario:** AI making poor decisions.

**Action:**
```yaml
ai:
  enabled: false
```

**Effect:** Immediate bypass, no restart required.

#### Fallback to Mock Mode

**Scenario:** API provider issues (outage, rate limits).

**Action:**
```yaml
ai:
  provider: "mock"
```

**Effect:** Continues running with safe defaults.

#### Force DEFENSIVE Mode

**Scenario:** High volatility, want conservative sizing.

**Action:**
```yaml
ai:
  default_risk_mode: "DEFENSIVE"
  allow_risk_mode_override: false
```

**Effect:** All proposals sized at 0.5×.

---

## Monitoring & Alerts

### Key Metrics

**AI Performance:**
- `ai_latency_ms`: Call latency (P50, P95, P99)
- `ai_error_rate`: API/timeout error rate
- `ai_decision_distribution`: % accept/reduce/skip

**Risk Mode:**
- `ai_risk_mode`: Current active mode
- `ai_risk_mode_changes`: Transitions per hour

**Impact:**
- `proposals_filtered_by_ai`: % skipped by AI
- `average_size_reduction`: Mean size_factor <1.0

### Alert Conditions

| Condition                        | Severity | Action                              |
|----------------------------------|----------|-------------------------------------|
| AI error rate >5%                | WARNING  | Check API health, review logs       |
| AI latency >2s P99               | WARNING  | Consider reducing timeout or model  |
| All proposals skipped (3 cycles) | CRITICAL | Verify AI not over-conservative     |
| Risk mode stuck in OFF           | CRITICAL | Check for kill switch trigger       |

---

## Prompt Engineering Guide

### System Prompt Template

Located in `ai/model_client.py:_build_system_prompt()`.

**Key Instructions:**
- "You can ONLY shrink or skip trades - NEVER increase size"
- "Favor capital preservation in uncertain/choppy regimes"
- "Respect all policy caps (you cannot override them)"

### Response Format

**Required JSON Structure:**
```json
{
  "risk_mode": "DEFENSIVE|NORMAL|AGGRESSIVE|OFF",
  "decisions": [
    {
      "symbol": "BTC-USD",
      "side": "BUY",
      "decision": "accept|reduce|skip",
      "size_factor": 0.0-1.0,
      "comment": "brief reasoning"
    }
  ]
}
```

### Context Provided to AI

**Market Snapshot:**
- Regime (trend, chop, crash)
- NAV and exposure %
- 24h drawdown and volatility

**Portfolio Snapshot:**
- Current positions (top 5)
- 24h realized P&L
- Position count

**Proposals:**
- Symbol, side, tier
- Conviction score
- Notional size
- Strategy reasoning

---

## Testing

### Unit Tests

**Location:** `tests/test_ai_advisor.py`

**Coverage:**
- Schema validation
- Size clamping (never >1.0)
- Fallback on errors
- Hallucination filtering
- Comment truncation
- Risk profile constraints

**Run Tests:**
```bash
pytest tests/test_ai_advisor.py -v
```

**Expected:** 27/27 passed

### Integration Tests

**Scenario 1: Accept All**
- AI approves all proposals at full size
- Validate proposals proceed to risk engine

**Scenario 2: Defensive Reduction**
- AI reduces sizes by 50%
- Validate `size_pct` correctly adjusted

**Scenario 3: Skip All**
- AI skips all proposals
- Validate cycle completes with NO_TRADE

### Smoke Test

**Manual verification:**
```bash
# 1. Enable AI in mock mode
vim config/app.yaml  # ai.enabled=true, provider=mock

# 2. Run single cycle
./app_run_live.sh --once

# 3. Check AI was invoked
grep "Step 9.5: AI advisor" logs/247trader-v2.log

# 4. Verify proposals filtered
grep "AI advisor filtered" logs/247trader-v2.log
```

---

## Troubleshooting

### Issue: AI Always Skips Proposals

**Symptoms:** All proposals filtered, no trades executed.

**Diagnosis:**
```bash
grep "AI SKIP" logs/247trader-v2.log | head -5
```

**Possible Causes:**
1. Prompt too conservative
2. Market conditions triggering defensive mode
3. Low conviction scores from rules engine

**Solutions:**
1. Review AI comments for reasoning
2. Adjust system prompt to be less conservative
3. Increase conviction thresholds in strategies

### Issue: AI Latency Too High

**Symptoms:** `ai_latency_ms` >2s, cycles delayed.

**Diagnosis:**
```bash
grep "AI advisor completed" logs/247trader-v2.log | tail -10
```

**Possible Causes:**
1. Network latency to API provider
2. Model too large/slow
3. Complex market context

**Solutions:**
1. Reduce `timeout_s` (will fail faster)
2. Switch to faster model (gpt-4-turbo → gpt-3.5-turbo)
3. Simplify prompt/context

### Issue: API Rate Limits

**Symptoms:** AI errors with "rate limit exceeded".

**Diagnosis:**
```bash
grep "429" logs/247trader-v2.log
```

**Solutions:**
1. Reduce cycle frequency (increase `loop.interval_seconds`)
2. Implement request queuing (TODO)
3. Upgrade API tier with provider

### Issue: Unexpected Size Increases

**Symptoms:** Proposals larger after AI filtering.

**Diagnosis:** **This should be impossible** (safety violation).

**Action:**
1. Check `max_scale_up` in config (must be ≤1.0)
2. Review logs for size_factor >1.0
3. File bug report with audit trail

**Validation:**
```bash
# Should find NO matches
grep "size_factor.*1\.[1-9]" logs/247trader-v2.log
```

---

## Roadmap

### Phase 1 (Current)

✅ AI filters/resizes proposals  
✅ Risk mode suggestions (optional)  
✅ Coinbase data only  
✅ Mock/OpenAI/Anthropic support  

### Phase 2 (Future)

- [ ] External news/sentiment integration
- [ ] Multi-model ensemble (majority vote)
- [ ] Dynamic timeout based on market urgency
- [ ] Per-symbol AI overrides
- [ ] Reinforcement learning from outcomes

### Phase 3 (Research)

- [ ] Portfolio-level optimization (not just proposal filtering)
- [ ] Adaptive risk profiles (learn from P&L)
- [ ] Explainable AI (SHAP values for decisions)

---

## References

### Internal Docs
- `docs/ARCHITECTURE_IMPLEMENTATION_COMPLETE.md` - Full system architecture
- `.github/copilot-instructions.md` - Coding guidelines
- `config/policy.yaml` - Risk constraints

### External Resources
- [OpenAI API Docs](https://platform.openai.com/docs/api-reference)
- [Anthropic API Docs](https://docs.anthropic.com/claude/reference)
- [Prompt Engineering Guide](https://www.promptingguide.ai/)

### Support
- **Issues:** File GitHub issue with `ai-advisor` label
- **Logs:** Always attach last 100 lines of `logs/247trader-v2.log`
- **Audit:** Include relevant audit log entries (JSONL)

---

## Changelog

### 2025-11-16: v1.0 - Initial Release
- Implemented AI advisor between rules engine and risk engine
- Added OpenAI, Anthropic, and mock providers
- Enforced safety constraints (max_scale_up ≤ 1.0)
- Integrated with audit logging and metrics
- Deployed 27 unit tests (100% pass rate)
- Documented architecture and operational procedures

---

**End of Document**
