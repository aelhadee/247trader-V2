# AI Trader Agent Overview

The **AI Trader Agent** lets a large language model act as a portfolio allocator while still honoring the universe → triggers → rules → risk → execution contract. The agent produces `TradeProposal` objects just like the deterministic rules engine, so the RiskEngine remains the single authority for guardrails.

## Where it lives in the cycle

```
Universe → Triggers → Local Strategies ┐
                                     ├─ merge → AI Advisor → Risk Engine → Execution
AI Trader Agent (Step 9A) ───────────┘
```

- Dual-trader mode (AI trader vs. local + arbitration) stays mutually exclusive with the trader agent. Only one “AI proposals” path runs per cycle.
- The agent only runs when `nav > 0` and the LLM client is healthy; otherwise the system behaves exactly like the baseline rules-first stack.

## JSON contract handed to the model

`AiTraderAgent` builds a rich context payload via `ai.snapshot_builder.build_ai_snapshot`:

```jsonc
{
  "universe_snapshot": [
    {
      "symbol": "SOL",
      "tier": 1,
      "volume_24h": 956000000,
      "spread_pct": 0.06,
      "allocation_min_pct": 2.5,
      "allocation_max_pct": 7.5,
      "change_1h_pct": -0.4,
      "change_24h_pct": 6.3,
      "volatility": 1.8
    }
  ],
  "positions": [
    {
      "symbol": "BONK",
      "usd": 1425.0,
      "size": 6800000,
      "average_price": 0.00021,
      "unrealized_pnl_pct": -3.8
    }
  ],
  "available_capital_usd": 5320.42,
  "guardrails": {
      "max_total_at_risk_pct": 20.0,
      "max_position_size_pct": 5.0,
      "max_trades_per_cycle": 3,
      "min_trade_notional": 5.0
  },
  "triggers": [
    {"symbol": "SOL", "type": "momentum", "strength": 0.8, "confidence": 0.76, "volatility": 1.3}
  ],
  "metadata": {
    "cycle_number": 2417,
    "mode": "DRY_RUN",
    "timestamp": "2025-01-05T04:57:21.834219Z",
    "config_hash": "a7b9d3a1"
  }
}
```

The prompt (defined in `ai/llm_client.py`) instructs the LLM to respond with a JSON array of `{symbol, action, target_weight_pct, confidence, time_horizon_minutes, rationale}` entries without ever shorting or exceeding guardrails.

## Translating decisions into trades

`AiTraderAgent._decisions_to_proposals` enforces the deterministic clamps below before producing `TradeProposal` objects:

1. **Confidence floor** – ignore allocations below `min_confidence` (`config.ai.trader_agent.min_confidence`).
2. **Delta sizing** – convert the target weight into a delta vs. current NAV weight; skip if the rebalance delta is below `min_rebalance_delta_pct`.
3. **Max per-symbol weight** – clamp `target_weight_pct` to the min of policy `max_position_size_pct`, per-asset `allocation_max_pct`, and the agent’s own cap.
4. **Single-trade ceiling** – even if the new target is large, each proposal’s `size_pct` is capped by `max_single_trade_pct`.
5. **No synthetic sells** – SELL decisions only trigger when there is an existing position; sizes never exceed the current allocation.
6. **Tagging & metadata** – proposals contain `tags=['ai_trader_agent']` plus `ai_target_pct`, `ai_delta_pct`, and `ai_time_horizon_minutes` for downstream audit.

## Configuration quick start (`config/app.yaml`)

```yaml
ai:
  trader_agent:
    enabled: true
    provider: "openai"          # or mock/anthropic
    model: "gpt-4o-mini"
    api_key: "${OPENAI_API_KEY}"
    timeout_s: 2.5
    max_decisions: 4
    min_confidence: 0.55
    min_rebalance_delta_pct: 0.35
    max_position_pct: 3.0
    max_single_trade_pct: 2.0
    tag: "ai_trader_agent"
```

Operational guidance:

1. **Start in mock mode** – leave `provider: mock` to exercise the pipeline without any external calls.
2. **Toggle on in DRY_RUN** – flip `enabled: true` only when `dual_trader.enabled` is `false`.
3. **Set API keys via env vars** – `${OPENAI_API_KEY}` or `${ANTHROPIC_API_KEY}` must exist before switching to a live provider.
4. **Watch the logs** – look for `"Step 9A: AI trader agent"` followed by `"contributed N proposal(s)"` lines to confirm the agent is live.

## Guardrails & safety invariants

- **Fail closed** – any exception while calling the LLM produces 0 proposals and the rest of the loop proceeds unchanged.
- **Policy-first** – guardrails snapshot mirrors `policy.yaml` caps (max position, max trades, min notional). The RiskEngine still performs full verification.
- **Kill switch awareness** – if `data/KILL_SWITCH` is present or NAV ≤ 0, the agent suppresses all trades.
- **No secret sprawl** – `_resolve_secret_value` only reads `${ENV}` placeholders; credentials continue to flow through environment variables.

## Validating the feature

- Unit tests: `pytest tests/test_ai_trader_agent.py`
- Full regression: `./run_tests.sh` (adds advisor/dual-trader coverage)
- Smoke run: `python runner/main_loop.py --once --config-dir config` with `ai.trader_agent.enabled=true` and `app.mode=DRY_RUN`

Logs are tagged with `strategy_source=ai_trader_agent`, making it easy to slice metrics or audit trails for AI-originated trades.
