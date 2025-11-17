# Dual-Trader Architecture

**AI Trader vs Local Rules → Arbitration → Risk/Execution Pipeline**

Last Updated: 2025-11-17  
Status: ✅ Production-Ready  
Test Coverage: 14/14 passing

---

## Executive Summary

247trader-v2 now supports **dual-trader mode**: an AI model generates independent trade proposals in parallel with local rules, then a deterministic arbiter merges them before the existing RiskEngine/ExecutionEngine pipeline.

**Key Properties:**
- **Non-bypassing**: All proposals (local + AI + blended) go through RiskEngine → ExecutionEngine
- **Fail-safe**: AI failures → fall back to local-only mode  
- **Testable**: Deterministic arbitration logic, full test coverage
- **Auditable**: Complete arbitration log in audit trail

---

## Architecture

```
┌────────────────┐     ┌────────────────┐
│ Local Rules    │     │ AI Trader      │
│ (RulesEngine)  │     │ (LLM Model #1) │
└────────┬───────┘     └────────┬───────┘
         │                      │
         │ proposals            │ proposals
         ▼                      ▼
    ┌────────────────────────────────┐
    │   Meta-Arbitrator              │
    │   (Deterministic Logic)        │
    │   ┌─────────────────────────┐  │
    │   │ Optional: AI Arbiter    │  │
    │   │ (LLM Model #2)          │  │
    │   └─────────────────────────┘  │
    └──────────────┬─────────────────┘
                   │ merged proposals
                   ▼
            ┌──────────────┐
            │  RiskEngine  │  ← UNCHANGED
            └──────┬───────┘
                   │ approved
                   ▼
            ┌──────────────┐
            │ ExecutionEngine│ ← UNCHANGED
            └──────────────┘
```

---

## Components

### 1. AI Trader Client (`ai/llm_client.py`)

**Purpose**: Structured LLM interface for trade decisions

**Input**: Market snapshot with:
- Universe (price/vol/spread per symbol)
- Current positions & PnL
- Regime (chop/trend/crash)
- Guardrails summary (max position size, max at risk, etc.)
- Recent triggers

**Output**: List of `AiTradeDecision`:
```python
@dataclass
class AiTradeDecision:
    symbol: str
    action: Literal["BUY", "SELL", "HOLD", "NONE"]
    target_weight_pct: float      # Final position size % of NAV
    confidence: float             # 0–1
    time_horizon_minutes: int
    rationale: str
```

**Safety**: Automatic clamping (target_weight_pct ≤100%, confidence ≤1.0), timeout enforcement, JSON schema validation

**Providers**: OpenAI, Anthropic, Mock (for testing)

---

### 2. AI Trader Strategy (`strategy/ai_trader_strategy.py`)

**Purpose**: Implements `BaseStrategy` interface to generate proposals from AI decisions

**Flow**:
1. Build rich snapshot from `StrategyContext`
2. Call AI client with snapshot
3. Convert `AiTradeDecision` → `TradeProposal`
4. Filter by `min_confidence`
5. Validate symbols in universe
6. Return proposals

**Configuration**:
```yaml
max_decisions: 5         # Max proposals per cycle
min_confidence: 0.0      # Filter threshold
enable_hold_signals: false
```

---

### 3. Meta-Arbitrator (`strategy/meta_arb.py`)

**Purpose**: Deterministic merging of local + AI proposals

**Input**: Two proposal lists (local, AI)  
**Output**: Single merged list + arbitration log

**Logic** (per symbol):

| Scenario | Rule | Resolution |
|----------|------|------------|
| Only local | Always accept | `SINGLE` (local) |
| Only AI | Accept if `confidence ≥ min_ai_confidence` | `SINGLE` (AI) or `NONE` |
| Agreement (same side) | Blend sizes conservatively | `BLEND` (min or average) |
| Conflict + low AI conf | Trust local | `LOCAL` |
| Conflict + weak local + strong AI | Trust AI | `AI` |
| Conflict (ambiguous) | Stand down | `NONE` |

**Configuration**:
```yaml
arbitration:
  min_ai_confidence: 0.6         # Filter for AI-only proposals
  ai_override_threshold: 0.7     # AI conf needed to override local
  local_weak_conviction: 0.35    # Local considered "weak"
  ai_confidence_advantage: 0.25  # Gap needed for AI override
  blend_mode: "conservative"     # conservative (min) | average
```

**Output Example**:
```
⚖️  BTC-USD: BLEND - Agreement: both buy, blended 3.0%
⚖️  ETH-USD: SINGLE - AI only: sell 2.0% (conf=0.70)
⚖️  SOL-USD: LOCAL - Conflict: AI conf 0.65 < override threshold
⚖️  AVAX-USD: NONE - Conflict unresolved - standing down
```

---

### 4. AI Arbiter (`ai/arbiter_client.py`) - **Optional**

**Purpose**: Model #2 for tie-breaking unresolved conflicts

**When to use**: After deterministic rules fail to resolve conflict

**Input**:
```python
@dataclass
class ArbiterInput:
    symbol: str
    market_snapshot: Dict  # price, vol, regime
    local_decision: Dict    # side, size, conviction
    ai_decision: Dict       # side, size, confidence
    guardrails: Dict        # caps, limits
```

**Output**:
```python
@dataclass
class ArbiterOutput:
    resolution: Literal["LOCAL", "AI", "BLEND", "NONE"]
    final_size_pct: float
    comment: str
```

**Design**: Narrow scope - can only choose from known options, cannot invent new trades

---

## Integration in Main Loop

**Step 9 (Modified)**:

```python
# Generate local proposals
local_proposals = strategy_registry.aggregate_proposals(context)

# If dual-trader enabled:
if dual_trader_enabled:
    # Generate AI proposals
    ai_proposals = ai_trader_strategy.generate_proposals(context)
    
    # Arbitrate
    proposals, arbitration_log = meta_arbitrator.aggregate_proposals(
        local_proposals=local_proposals,
        ai_proposals=ai_proposals,
    )
    
    # Store for audit
    self._current_arbitration_log = arbitration_log
else:
    proposals = local_proposals
```

**Steps 10-13**: Unchanged (RiskEngine, ExecutionEngine work identically)

---

## Configuration (`config/app.yaml`)

```yaml
ai:
  dual_trader:
    enabled: false  # Start disabled
    
    # AI Trader (Model #1)
    provider: "openai"  # openai | anthropic | mock
    model: "gpt-5-mini-2025-08-07"
    api_key: "${OPENAI_API_KEY}"
    timeout_s: 2.0
    max_decisions: 5
    min_confidence: 0.0
    
    # Arbitration (Deterministic)
    arbitration:
      min_ai_confidence: 0.6
      ai_override_threshold: 0.7
      local_weak_conviction: 0.35
      ai_confidence_advantage: 0.25
      blend_mode: "conservative"
    
    # AI Arbiter (Model #2) - Optional
    arbiter:
      enabled: false
      provider: "anthropic"
      model: "claude-sonnet-4-5-20250929"
      api_key: "${ANTHROPIC_API_KEY}"
      timeout_s: 1.5
```

---

## Safety Guarantees

1. **RiskEngine is Final Authority**  
   - AI cannot bypass caps (max_position_size_pct, max_total_at_risk_pct)
   - AI cannot bypass cooldowns
   - AI cannot bypass min_notional or exchange constraints

2. **Fail-Closed**  
   - AI client timeout → return []  
   - AI parsing error → return []  
   - Main loop catches exceptions → falls back to local-only

3. **Deterministic Arbitration v1**  
   - All logic is rule-based (no black-box AI arbitration by default)
   - Optional AI arbiter (Model #2) only for unresolved conflicts

4. **Full Audit Trail**  
   - Every arbitration decision logged with reason
   - Local vs AI proposals preserved
   - Resolution type (BLEND/LOCAL/AI/SINGLE/NONE) recorded

---

## Deployment Phases

### Phase 1: Mock Mode (1-2 days)
```yaml
dual_trader:
  enabled: true
  provider: "mock"
```

**Goal**: Validate integration without API calls  
**Validation**: Check logs for arbitration decisions, confirm proposals reach RiskEngine

---

### Phase 2: Live API, Deterministic Arbitration (3-5 days)
```yaml
dual_trader:
  enabled: true
  provider: "openai"  # or anthropic
  model: "gpt-5-mini-2025-08-07"
  api_key: "${OPENAI_API_KEY}"
  arbiter:
    enabled: false  # Keep deterministic only
```

**Goal**: Test AI proposals in production without AI arbiter  
**Monitoring**:
- AI latency (should be <2s)
- Proposal counts (local vs AI vs final)
- Arbitration resolutions (BLEND/LOCAL/AI distribution)
- Error rates

---

### Phase 3: AI Arbiter (Optional, 1-2 weeks after Phase 2)
```yaml
dual_trader:
  enabled: true
  arbiter:
    enabled: true
    provider: "anthropic"
    model: "claude-sonnet-4-5-20250929"
```

**Goal**: Use Model #2 for tie-breaking conflicts  
**Validation**: Compare arbiter decisions vs deterministic rules, track override rate

---

## Testing

**Test Suite**: `tests/test_dual_trader.py` (14 tests, all passing)

**Coverage**:
- ✅ AI client (decisions, clamping, mock)
- ✅ AI trader strategy (proposal generation, filtering)
- ✅ Meta-arbitration (single-source, agreement, conflicts)
- ✅ AI arbiter (mock)
- ✅ Integration (end-to-end flow)

**Run Tests**:
```bash
python -m pytest tests/test_dual_trader.py -v
```

---

## Metrics & Observability

**Key Metrics**:
- `ai_trader_latency_ms` - AI client call duration
- `proposals_local` - Count from rules engine
- `proposals_ai` - Count from AI trader
- `proposals_final` - Count after arbitration
- `arbitration_resolution` - BLEND/LOCAL/AI/SINGLE/NONE counts

**Audit Log Fields** (added):
```json
{
  "arbitration": [
    {
      "symbol": "BTC-USD",
      "resolution": "BLEND",
      "reason": "Agreement: both buy, blended 3.0%",
      "local_side": "buy",
      "local_size_pct": 3.0,
      "local_confidence": 0.6,
      "ai_side": "buy",
      "ai_size_pct": 5.0,
      "ai_confidence": 0.85,
      "final_side": "buy",
      "final_size_pct": 3.0
    }
  ]
}
```

---

## Operational Procedures

### Enable Dual-Trader Mode
1. Set API keys in environment: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
2. Update `config/app.yaml`:
   ```yaml
   ai:
     dual_trader:
       enabled: true
       provider: "openai"  # or mock for testing
   ```
3. Restart bot: `./app_run_live.sh --loop`

### Disable Dual-Trader Mode
```yaml
ai:
  dual_trader:
    enabled: false
```
Restart → falls back to local-only mode

### Monitor Health
```bash
# Check arbitration decisions in logs
tail -f logs/247trader-v2.log | grep "⚖️"

# Check AI latency
grep "ai_trader_latency" logs/247trader-v2.log

# Check audit trail
jq '.arbitration' logs/audit.jsonl | head
```

### Rollback Plan
1. Set `enabled: false` in config
2. Restart bot
3. Bot operates in local-only mode (no AI calls)

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| AI latency >2s | Timeout enforced, falls back to local |
| AI hallucination | JSON schema validation, numeric clamping |
| Conflicting decisions | Deterministic arbitration rules |
| API costs | Rate limiting, max_decisions cap |
| Model drift | Regular backtesting, monitoring metrics |

---

## Future Enhancements

1. **Confidence calibration**: Track AI confidence vs actual outcomes, adjust thresholds
2. **Regime-specific arbitration**: Different rules for chop vs trend
3. **Multi-model ensemble**: Run multiple AI models, vote on decisions
4. **Adaptive arbitration**: Learn optimal arbitration params from history

---

## Summary

✅ **Production-ready dual-trader system**:
- Clean architecture with fail-safe defaults
- RiskEngine/ExecutionEngine remain unchanged (no bypass)
- Full test coverage (14/14 passing)
- Comprehensive audit trail
- 3-phase rollout plan (mock → live API → arbiter)

✅ **Safety**:
- AI can only suggest, never guarantee
- Deterministic arbitration v1 (no AI arbiter required)
- Timeout/error handling
- Full observability

✅ **Next Steps**:
1. Enable Phase 1 (mock mode) for integration validation
2. Monitor Phase 2 (live API) for 3-5 days
3. Optional Phase 3 (AI arbiter) if deterministic rules insufficient

---

**Questions? Check:**
- `ai/README.md` - AI advisor (original filter layer)
- `strategy/README.md` - Multi-strategy framework
- `tests/test_dual_trader.py` - Test examples
