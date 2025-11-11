# Production Launch TODOs

Status tracker for the final blockers and follow-ups before enabling LIVE trading with real capital.

Legend: 🔴 TODO = work outstanding, 🟡 Pending Validation = feature coded but needs live rehearsal, 🟢 Done = verified and complete.

## Safety & Risk Controls

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🔴 TODO | Count pending exposure (fills + open orders) toward per-asset/theme/total caps in risk checks. | TBD | RiskEngine currently ignores outstanding order notional. |
| 🔴 TODO | Implement circuit breakers for data staleness, API flaps, exchange health, and crash regimes. | TBD | Needs aggregated health tracking + policy thresholds. |
| 🔴 TODO | Wire a global kill switch (env/DB flag) that runner and executor honor immediately. | TBD | Allows operator freeze without code deploy. |
| 🔴 TODO | Enforce per-symbol cooldowns after fills/stop-outs using `StateStore.cooldowns`. | TBD | Config exists but enforcement path is missing. |
| 🔴 TODO | Make sizing fee-aware so Coinbase maker/taker fees are reflected in min notional and PnL math. | TBD | Prevents trading sizes where fees dominate. |
| 🔴 TODO | Enforce Coinbase product constraints (base/quote increments, min size, min market funds) before submission. | TBD | Requires product metadata cache + rounding helpers. |
| 🔴 TODO | Replace daily/weekly circuit-breaker inputs with realized PnL history pulled from exchange fills. | TBD | Current percent stops still rely on approximated deltas. |

## Order Management & Execution

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🔴 TODO | Generate deterministic client order IDs per proposal and dedupe retries. | TBD | Prevents duplicate submissions on network retries. |
| 🔴 TODO | Implement explicit order state machine (`NEW → PARTIAL → FILLED | CANCELED | EXPIRED`) with timers. | TBD | Needed for telemetry and reconciliation. |
| 🔴 TODO | Auto-cancel stale post-only orders and re-quote at most *K* times per policy. | TBD | `manage_open_orders` cancels but does not re-quote today. |
| 🔴 TODO | Poll fills each cycle and reconcile positions/fees before updating risk exposure. | TBD | Post-trade refresh exists but still assumes fill snapshots. |
| 🔴 TODO | Ensure graceful shutdown cancels live orders, flushes state, and exits cleanly. | TBD | Current SIGTERM handler only flips `_running` flag. |
| 🔴 TODO | Include fee-adjusted rounding when enforcing minimum notional on the quote side. | TBD | Avoids relisting orders below exchange thresholds. |
| 🔴 TODO | Add latency accounting for API calls, decision cycle, and submission pipeline. | TBD | Required for watchdogs and alerting. |
| 🔴 TODO | Introduce jittered scheduling to avoid synchronized bursts with other bots. | TBD | Randomize sleep interval per loop. |
| 🟡 Pending Validation | Run PAPER/LIVE read-only smoke to observe `_post_trade_refresh` against real fills. | TBD | Confirms immediate reconcile path behaves with Coinbase latency. |
| 🟡 Pending Validation | Tune `execution.post_trade_reconcile_wait_seconds` based on observed settle time. | TBD | Default 0.5s may be too short during high volatility. |
| 🟢 Done | Enforce spread and 20bps depth checks before order placement. | N/A | `ExecutionEngine.preview_order` rejects on thin books. |
| 🟢 Done | Default to post-only order routing unless policy overrides. | N/A | `execution.default_order_type` set to `limit_post_only`. |

## Data Integrity

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🟢 Done | Fail-closed gating when critical data (accounts, quotes, orders) is unavailable. | N/A | `TradingLoop._abort_cycle_due_to_data` returns NO_TRADE. |
| 🔴 TODO | Reject quotes older than `max_quote_staleness_seconds`. | TBD | Need timestamp checks when fetching market data. |
| 🔴 TODO | Maintain canonical symbol mapping (`BTC-USD` vs `BTCUSD`) across modules. | TBD | Prevents mismatches between exchange and strategy layers. |
| 🔴 TODO | Enforce UTC/monotonic time sanity, including explicit bar windowing. | TBD | Replace remaining `datetime.utcnow()` usage and align candles. |
| 🟢 Done | Abort cycle if partial snapshot detected during reconcile. | N/A | `_reconcile_exchange_state` raises `CriticalDataUnavailable`. |

## State & Reconciliation

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🟢 Done | Persist durable state (positions, PnL, cooldowns, open orders) between runs. | N/A | `StateStore` JSON store handles persistence. |
| 🟢 Done | Cold-start reconcile balances, positions, and open orders from Coinbase on boot. | N/A | `TradingLoop._reconcile_exchange_state` trusts exchange snapshot. |
| 🔴 TODO | Add shadow DRY_RUN mode to diff intended vs actual fills during rollout. | TBD | Enables parallel validation before scaling capital. |

## Backtesting Parity

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🔴 TODO | Ensure backtest engine reuses live universe → triggers → risk → execution pipeline. | TBD | Current backtest module diverges from live loop. |
| 🔴 TODO | Implement slippage/fee model (mid ± bps + Coinbase fees) in simulations. | TBD | Needed for realistic equity curves. |
| 🔴 TODO | Emit backtest artifacts (trades.json, equity curve, exposure, hit rate). | TBD | Supports regression analysis and CI checks. |

## Rate Limits & Retries

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🟢 Done | Apply exponential backoff with jitter for 429/5xx and network faults. | N/A | `CoinbaseExchange._req` retries with capped backoff. |
| � TODO | Track per-endpoint rate budgets (public vs private) and pause before exhaustion. | TBD | Prevents API bans during spikes. |
| 🟢 Done | Guard against partial snapshots by skipping decisioning when data missing. | N/A | Shared with fail-closed gating above. |

## Observability & Alerts

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🔴 TODO | Load production alert webhook secrets, fire staging alert, and verify on-call routing. | TBD | Alert service exists but secrets and smoke test pending. |
| 🔴 TODO | Build alert matrix (stop hits, reconcile mismatch, API failures, empty universe, order rejection, exception bursts). | TBD | Configure thresholds and integrate with `AlertService`. |
| 🔴 TODO | Expose metrics for no_trade_reason, exposure, fill ratio, error rates, and latency. | TBD | Needed for dashboards and SLOs. |

## Config & Governance

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| � TODO | Validate YAML configs against schemas (Pydantic/JSON Schema) on startup. | TBD | Fail fast on misconfiguration. |
| 🔴 TODO | Enforce secrets via environment/secret store only (no file fallbacks in repo). | TBD | Lock down credential handling. |
| 🔴 TODO | Stamp config version/hash into each audit log entry. | TBD | Enables provenance tracking. |
| 🔴 TODO | Add config sanity checks (theme vs asset caps, totals coherence). | TBD | Prevents contradictory limits. |
