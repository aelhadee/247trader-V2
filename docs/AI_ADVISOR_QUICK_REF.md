# AI Advisor Quick Reference

**One-page operational guide for AI-enhanced trading**

---

## Quick Config

### Enable AI (Mock Mode - Safe Testing)
```yaml
# config/app.yaml
ai:
  enabled: true
  provider: "mock"
  allow_risk_mode_override: false
```

### Enable AI (Live with Anthropic Claude)
```yaml
# config/app.yaml
ai:
  enabled: true
  provider: "anthropic"
  model: "claude-3-5-sonnet-20241022"
  api_key: "${ANTHROPIC_API_KEY}"
  timeout_s: 1.0
  max_scale_up: 1.0  # NEVER >1.0
  fallback_on_error: true
  allow_risk_mode_override: false
```

```bash
# API keys already in .env
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."  # Alternative provider
```

---

## Safety Checklist

✅ **BEFORE enabling AI in LIVE:**

1. `max_scale_up` is ≤ 1.0 (AI cannot increase sizes)
2. `fallback_on_error: true` (safe on errors)
3. `timeout_s` ≤ 2.0 (fast fail)
4. `allow_risk_mode_override: false` initially (AI only filters)
5. Test in DRY_RUN for 24h first
6. Review audit logs for AI decisions

---

## Key Commands

### Check AI Status
```bash
grep "AI Advisor initialized" logs/247trader-v2.log
```

### Monitor AI Decisions
```bash
# See all AI actions
grep "AI SKIP\|AI REDUCE\|AI risk mode" logs/247trader-v2.log | tail -20

# Count decision types
grep -c "AI SKIP" logs/247trader-v2.log
grep -c "AI REDUCE" logs/247trader-v2.log
```

### Validate Safety Constraints
```bash
# Should be ZERO matches (no size increases)
grep "size_factor.*1\.[1-9]" logs/247trader-v2.log

# Check max_scale_up config
grep "max_scale_up" config/app.yaml
```

---

## Emergency Actions

### Disable AI Immediately
```yaml
# config/app.yaml
ai:
  enabled: false
```
No restart needed. Next cycle bypasses AI.

### Force Defensive Mode
```yaml
ai:
  default_risk_mode: "DEFENSIVE"
  allow_risk_mode_override: false
```
All trades sized at 0.5×.

### Fallback to Mock
```yaml
ai:
  provider: "mock"
```
Safe defaults, no API calls.

---

## Risk Modes

| Mode       | Size | Exposure | When to Use                    |
|------------|------|----------|--------------------------------|
| OFF        | 0%   | 0%       | Emergency kill switch          |
| DEFENSIVE  | 50%  | ≤10%     | High volatility, drawdown      |
| NORMAL     | 100% | ≤15%     | Standard operation             |
| AGGRESSIVE | 100% | ≤15%     | Future: allow higher sizing    |

**Note:** All modes respect `policy.yaml` caps as ceiling.

---

## Metrics to Watch

### AI Health
- **Latency:** Should be <500ms P50, <2s P99
- **Error Rate:** Should be <1%
- **Decision Distribution:** ~70% accept, ~20% reduce, ~10% skip

### Impact
- **Proposals Filtered:** 10-30% typical
- **Average Size Reduction:** 0.7-0.9 (when reducing)
- **Risk Mode:** Should be NORMAL 80%+ of time

### Alerts
- AI error rate >5% → Check API health
- All proposals skipped 3× → Review AI reasoning
- Latency >2s → Consider faster model

---

## Testing Workflow

### Phase 1: Mock Mode (1 day)
```yaml
ai:
  enabled: true
  provider: "mock"
```
✅ Validates integration without API calls

**Phase 2: Live API** (2-3 days)
```yaml
ai:
  enabled: true
  provider: "anthropic"
  model: "claude-3-5-sonnet-20241022"
  api_key: "${ANTHROPIC_API_KEY}"
  allow_risk_mode_override: false  # AI only filters
```

### Phase 3: Risk Mode Influence (ongoing)
```yaml
ai:
  allow_risk_mode_override: true
```
✅ AI can suggest risk modes (still capped by policy)

---

## Troubleshooting

### AI Always Skipping
**Symptom:** No trades executed, all skipped by AI  
**Check:** `grep "AI SKIP" logs/*.log`  
**Fix:** Review AI comments, may be too conservative

### High Latency
**Symptom:** Cycles delayed, AI taking >1s  
**Check:** `grep "AI advisor completed" logs/*.log`  
**Fix:** Reduce `timeout_s` or switch to faster model

### Rate Limits
**Symptom:** "429" errors in logs  
**Fix:** Reduce cycle frequency or upgrade API tier

### No AI Activity
**Symptom:** No "Step 9.5" in logs  
**Check:** `ai.enabled` in config, API key set  
**Fix:** Verify config, restart with `--loop`

---

## Log Patterns

### Successful AI Call
```
🤖 Step 9.5: AI advisor reviewing 3 proposal(s)...
AI advisor completed in 324.5ms: risk_mode=NORMAL, decisions=3/3
AI advisor filtered: 2/3 kept (skipped: 1, reduced: 0)
```

### AI Reduced Size
```
AI REDUCE: BTC-USD BUY - 2.00% → 1.00% (0.50x) - Uncertain market conditions
```

### AI Skipped Proposal
```
AI SKIP: ETH-USD BUY - Low conviction + choppy regime
```

### Fallback on Error
```
AI advisor error after 1032.1ms: timeout
Falling back to no-AI mode (safe)
```

---

## File Locations

```
ai/
├── __init__.py          # Module init
├── advisor.py           # Core service
├── model_client.py      # API clients
├── risk_profile.py      # Risk mode mappings
└── schemas.py           # Data structures

config/app.yaml          # AI configuration
tests/test_ai_advisor.py # Unit tests
docs/AI_ADVISOR_ARCHITECTURE.md  # Full documentation
```

---

## Support

**Issue:** Unexpected behavior  
**Action:**
1. Collect last 100 log lines: `tail -100 logs/247trader-v2.log > ai_issue.log`
2. Check AI config: `grep -A 20 "^ai:" config/app.yaml`
3. Review audit: `tail -10 logs/247trader-v2_audit.jsonl`
4. File GitHub issue with logs attached

**Escalation:** If AI making unsafe decisions (size >1.0):
1. Disable immediately: `ai.enabled: false`
2. Capture audit trail
3. Report to maintainer with full cycle logs

---

**Last Updated:** 2025-11-16  
**Version:** 1.0
