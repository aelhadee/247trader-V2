# AI Advisor Module

**Production-grade AI layer for trade proposal filtering**

---

## Overview

The AI advisor sits between the rules engine and risk engine, providing intelligent filtering and sizing recommendations for trade proposals. It can only **shrink** or **skip** trades, never increase sizes, with all policy caps enforced.

**Core Principle:** AI is advisory only - RiskEngine and ExecutionEngine remain the hard authority.

---

## Quick Start

### 1. Enable Mock Mode (Safe Testing)

```yaml
# config/app.yaml
ai:
  enabled: true
  provider: "mock"
  allow_risk_mode_override: false
```

### 2. Run Trading Loop

```bash
./app_run_live.sh --loop
```

### 3. Check AI Activity

```bash
grep "Step 9.5: AI advisor" logs/247trader-v2.log
```

---

## Module Structure

```
ai/
├── __init__.py          # Module initialization
├── schemas.py           # Type-safe data structures
├── advisor.py           # AIAdvisorService (core logic)
├── model_client.py      # Provider abstractions (OpenAI, Anthropic, Mock)
└── risk_profile.py      # Risk mode → constraint mappings
```

---

## Key Components

### AIAdvisorService (`advisor.py`)

**Purpose:** Single entry point for AI decisions

**Usage:**
```python
from ai.advisor import AIAdvisorService
from ai.schemas import AIAdvisorInput
from ai.model_client import create_model_client

# Create advisor
advisor = AIAdvisorService(
    enabled=True,
    timeout_s=1.0,
    max_scale_up=1.0,
)

# Create client
client = create_model_client(provider="openai", api_key="sk-...")

# Get advice
output = advisor.advise(input_data, client)
```

**Features:**
- Timeout enforcement (default 1.0s)
- Size clamping (never >1.0)
- Fallback on errors
- Hallucination filtering

### Model Clients (`model_client.py`)

**Supported Providers:**

| Provider   | Model                   | API Key Required       |
|------------|-------------------------|------------------------|
| OpenAI     | GPT-4 Turbo, GPT-4o     | `OPENAI_API_KEY`       |
| Anthropic  | Claude 3.5 Sonnet/Opus  | `ANTHROPIC_API_KEY`    |
| Mock       | Testing/dry-run         | None                   |

**Usage:**
```python
from ai.model_client import create_model_client

# OpenAI
client = create_model_client(
    provider="openai",
    api_key="sk-...",
    model="gpt-4-turbo-preview",
)

# Anthropic
client = create_model_client(
    provider="anthropic",
    api_key="sk-ant-...",
    model="claude-3-5-sonnet-20241022",
)

# Mock (testing)
client = create_model_client(provider="mock")
```

### Risk Profiles (`risk_profile.py`)

**Risk Modes:**

```python
from ai.risk_profile import get_risk_profile, apply_risk_profile_to_caps

# Get profile
profile = get_risk_profile("DEFENSIVE")
# {'trade_size_multiplier': 0.5, 'max_at_risk_pct': 10.0, ...}

# Apply with policy caps
caps = apply_risk_profile_to_caps(
    mode="DEFENSIVE",
    policy_max_at_risk_pct=15.0,
    policy_max_positions=5,
)
# Returns: {'trade_size_multiplier': 0.5, 'max_at_risk_pct': 10.0, ...}
```

### Data Schemas (`schemas.py`)

**Core Types:**
```python
from ai.schemas import (
    AIProposalIn,          # Input proposal
    AIMarketSnapshot,      # Market context
    AIPortfolioSnapshot,   # Portfolio state
    AIAdvisorInput,        # Complete input
    AIProposalDecision,    # AI decision
    AIAdvisorOutput,       # AI response
    RiskMode,              # Type alias
)
```

All types are strongly typed dataclasses for safety.

---

## Safety Guarantees

✅ **Size Constraint:** `max_scale_up ≤ 1.0` enforced  
✅ **Policy Authority:** policy.yaml caps are ceiling  
✅ **Fail-Closed:** Errors → no AI influence  
✅ **Timeout:** Hard timeout prevents blocking  
✅ **Audit Trail:** All decisions logged  

---

## Testing

### Run Tests

```bash
pytest tests/test_ai_advisor.py -v
```

**Expected:** 27/27 passed

### Test Coverage

- Schema validation
- Size clamping
- Fallback behavior
- Timeout handling
- Risk profile constraints
- Integration scenarios

---

## Configuration

### Minimal Config

```yaml
ai:
  enabled: true
  provider: "mock"
```

### Production Config

```yaml
ai:
  enabled: true
  provider: "openai"
  model: "gpt-4-turbo-preview"
  api_key: "${OPENAI_API_KEY}"
  timeout_s: 1.0
  max_scale_up: 1.0
  fallback_on_error: true
  default_risk_mode: "NORMAL"
  allow_risk_mode_override: false
  log_decisions: true
  metrics_enabled: true
```

---

## Troubleshooting

### AI Not Active

**Check:**
```bash
grep "AI Advisor initialized" logs/247trader-v2.log
```

**Fix:**
- Verify `ai.enabled: true`
- Check API key set
- Review error logs

### High Latency

**Check:**
```bash
grep "AI advisor completed" logs/247trader-v2.log | tail -10
```

**Fix:**
- Reduce `timeout_s`
- Switch to faster model
- Check network latency

### All Proposals Skipped

**Check:**
```bash
grep "AI SKIP" logs/247trader-v2.log | head -5
```

**Fix:**
- Review AI comments
- Adjust prompt conservativeness
- Increase strategy conviction

---

## Documentation

- **Architecture:** `docs/AI_ADVISOR_ARCHITECTURE.md`
- **Quick Reference:** `docs/AI_ADVISOR_QUICK_REF.md`
- **Implementation Summary:** `docs/AI_ADVISOR_IMPLEMENTATION_SUMMARY.md`

---

## Contributing

### Adding New Provider

1. Subclass `ModelClient` in `model_client.py`
2. Implement `call(request, timeout)` method
3. Add to `create_model_client()` factory
4. Add tests in `tests/test_ai_advisor.py`

### Modifying Risk Profiles

1. Edit `RISK_PROFILE` dict in `risk_profile.py`
2. Ensure all values respect policy caps
3. Add tests for new constraints
4. Update documentation

---

## License

Same as parent project (247trader-v2)

---

**Version:** 1.0  
**Last Updated:** 2025-11-16
