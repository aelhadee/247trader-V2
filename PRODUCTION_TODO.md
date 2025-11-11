# Production Launch TODOs

Status tracker for the final blockers and follow-ups before enabling LIVE trading with real capital.

Legend: 🔴 TODO = work outstanding, 🟡 Pending Validation = feature coded but needs live rehearsal, 🟢 Done = verified and complete.

## Safety & Risk Controls

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🟢 Done | Count pending exposure (fills + open orders) toward per-asset/theme/total caps in risk checks. | N/A | RiskEngine now folds pending buy exposure into global/theme/position limits. |
| 🟢 Done | Implement circuit breakers for data staleness, API flaps, exchange health, and crash regimes. | N/A | Circuit breakers in RiskEngine with policy.yaml config; tracks API errors, rate limits, exchange health. |
| 🟢 Done | Wire a global kill switch (env/DB flag) that runner and executor honor immediately. | N/A | Kill switch via `data/KILL_SWITCH` file already exists and is checked in risk engine. |
| 🟢 Done | Enforce per-symbol cooldowns after fills/stop-outs using `StateStore.cooldowns`. | N/A | RiskEngine.apply_symbol_cooldown() sets cooldowns; _filter_cooled_symbols() filters proposals; main_loop applies after SELL orders. |
| 🟢 Done | Make sizing fee-aware so Coinbase maker/taker fees are reflected in min notional and PnL math. | N/A | ExecutionEngine uses configurable maker (40bps) and taker (60bps) fees; provides estimate_fee(), size_after_fees(), size_to_achieve_net(), get_min_gross_size() helpers. |
| � Done | Enforce Coinbase product constraints (base/quote increments, min size, min market funds) before submission. | N/A | ExecutionEngine.enforce_product_constraints() checks metadata and rounds sizes; integrated into _execute_live() before order placement. |
| � Done | Track realized PnL per position from actual fill prices. | N/A | StateStore.record_fill() tracks positions with weighted average entry prices and calculates realized PnL on position closes. Accounts for exit fees and proportional entry fees. Updates pnl_today, pnl_week, consecutive_losses. Integrated into ExecutionEngine.reconcile_fills(). Surfaced in audit logs. 11 comprehensive tests added (test_pnl_tracking.py). All 120 tests passing. See implementation in infra/state_store.py (lines ~470-595). |

## Order Management & Execution

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🟢 Done | Generate deterministic client order IDs per proposal and dedupe retries. | N/A | ExecutionEngine.generate_client_order_id() creates SHA256-based IDs from symbol/side/size/timestamp (minute granularity); prevents duplicate orders on retries; StateStore deduplication working. |
| 🟢 Done | Implement explicit order state machine (`NEW → PARTIAL → FILLED | CANCELED | EXPIRED`) with timers. | N/A | OrderStateMachine in core/order_state.py with 8 lifecycle states (NEW, OPEN, PARTIAL_FILL, FILLED, CANCELED, EXPIRED, REJECTED, FAILED), transition validation, fill tracking, auto-transitions, stale order detection, and telemetry hooks. Integrated into ExecutionEngine (DRY_RUN, PAPER, LIVE modes). 25 comprehensive tests added. All 72 tests passing. |
| 🟢 Done | Auto-cancel stale post-only orders (re-quoting not implemented). | N/A | Enhanced manage_open_orders() uses OrderStateMachine.get_stale_orders() for reliable age tracking; transitions canceled orders to CANCELED state; updates StateStore; handles batch and individual cancellation with proper error handling. Comprehensive test coverage with 11 new tests (test_manage_open_orders.py) covering DRY_RUN skip, disabled cancellation, single/batch cancel, fallback logic, API failures, terminal order handling, and StateStore integration. All 72 tests passing. Re-quoting feature deferred (would require market-making logic). |
## Critical TODOs (Production Safety)

🟢 Done | **Poll fills each cycle** and reconcile positions/fees **before** updating risk exposure. Implemented `list_fills()` in CoinbaseExchange (queries `/orders/fills` endpoint with filters for order_id, product_id, time range). Implemented `reconcile_fills()` in ExecutionEngine that polls recent fills (configurable lookback window), matches to OrderStateMachine orders, accumulates fill details (handles multiple fills per order), detects partial vs complete fills (95% threshold), transitions order states, and syncs StateStore. Handles unmatched fills (fills with no tracked order). Returns comprehensive summary (fills_processed, orders_updated, total_fees, fills_by_symbol). Error resilient with comprehensive error handling. 12 comprehensive tests added (test_reconcile_fills.py). All 84 tests passing. Ready for integration into main_loop._post_trade_refresh().

🟢 Done | Enhanced shutdown: **cancel all live orders**, **flush StateStore**, close connections, clean exit. Implemented comprehensive `_handle_stop()` handler in runner/main_loop.py that: (1) Cancels all active orders from OrderStateMachine (batch cancel for multiple orders, single for one order), (2) Transitions canceled orders to CANCELED state with StateStore sync, (3) Flushes StateStore to disk, (4) Logs cleanup summary (orders canceled, errors, state flush status). Error resilient (continues cleanup even if individual steps fail). Mode-aware (skips order cancellation in DRY_RUN). 11 comprehensive tests added (test_graceful_shutdown.py). All 95 tests passing. Production-ready for clean shutdown on SIGTERM/SIGINT.

� Pending Validation | **Use real PnL for circuit breakers**. Replace percent-based stops with realized PnL from exchange fills. Implement drawdown limits and daily/max loss circuit breakers using tracked PnL. Realized PnL tracking complete, now needs integration into RiskEngine for stop logic.
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

| 🔴 TODO | Maintain canonical symbol mapping (`BTC-USD` vs `BTCUSD`) across modules. | TBD | Prevents mismatches between exchange and strategy layers. |
| 🔴 TODO | Enforce UTC/monotonic time sanity, including explicit bar windowing. | TBD | Replace remaining `datetime.utcnow()` usage and align candles. |
| 🟢 Done | Abort cycle if partial snapshot detected during reconcile. | N/A | `_reconcile_exchange_state` raises `CriticalDataUnavailable`. |
| 🟢 Done | **Reject quotes older than max_quote_age_seconds** before trading decisions. | N/A | Implemented `_validate_quote_freshness()` in ExecutionEngine; validates timestamps at 3 critical points (preview_order, _execute_live, _find_best_trading_pair); uses policy `microstructure.max_quote_age_seconds` (30s default); handles timezone-aware/naive timestamps, detects clock skew; 14 comprehensive tests; all 109 tests passing. See `docs/STALE_QUOTE_REJECTION.md`. |

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
| 🟢 Done | Validate YAML configs against schemas (Pydantic/JSON Schema) on startup. | N/A | Implemented config_validator with Pydantic schemas for policy.yaml, universe.yaml, signals.yaml. Validates on TradingLoop init. Fails fast on misconfiguration. 12 comprehensive tests added (test_config_validation.py). All 132 tests passing. See tools/config_validator.py. |
| 🔴 TODO | Enforce secrets via environment/secret store only (no file fallbacks in repo). | TBD | Lock down credential handling. |
| 🔴 TODO | Stamp config version/hash into each audit log entry. | TBD | Enables provenance tracking. |
| 🔴 TODO | Add config sanity checks (theme vs asset caps, totals coherence). | TBD | Prevents contradictory limits. |


2. Coinbase Product & Status Integration (make it explicit)

You mention product constraints + health in TODOs conceptually, but I’d call out two concrete items:

Product metadata cache (MUST-HAVE):

On startup + periodically:

fetch Coinbase products,

store:

base_increment

quote_increment

min_market_funds

status (online/offline/limit_only/etc).

Execution must refuse:

disabled markets,

orders violating increments / min size.

Exchange status circuit breaker:

If Coinbase status API / product status says:

POST_ONLY, LIMIT_ONLY, CANCEL_ONLY, or degraded:

clamp or halt new entries in those products.

If global incident:

trigger kill / no-new-trades.

Right now this is implied under “exchange health”, but it needs to be explicit so nobody skips it.

3. Outlier / Bad-Tick Protection

You have triggers based on % moves. Without outlier guards:

One bad tick / stale candle can:

fire triggers,

create fake breakouts,

spam trades.

Add:

Before using price/volume for triggers:

Reject any candle/quote where:

move > X% vs previous AND

no confirming volume / orderbook.

Or:

Require N consecutive bars / snapshots confirming the move.

Small change, big protection.

4. Config & Env Separation: Staging vs Production

You mention config validation + secrets, good.

Add one more hard rule:

Environment flag: ENV=staging|production.

Behavior:

staging:

DRY_RUN or PAPER only,

can use same code + different config.

production:

requires:

valid secrets,

alerting enabled,

lock acquired,

all 🔴 critical TODOs resolved.

This avoids someone “just testing” with production keys and half-finished safety.

5. Backtest/CI Gate (Make it a launch blocker)

You have TODOs for backtest parity + artifacts, which is good.

Make one more explicit:

Before enabling LIVE:

CI must:

run unit tests,

run at least one short backtest with current config,

fail the pipeline if:

backtest crashes,

or produces obviously invalid metrics (NaN equity, etc).

This prevents config/logic regressions from silently shipping.

6. Red-Flag & Ban Hook-Up

You defined:

exclusions.red_flags:
  - recent_exploit
  - regulatory_action
  - delisting_rumors
  - team_rug
temporary_ban_hours: 168


But there’s no explicit TODO to apply these.

Add:

A task: “Integrate red-flag / temporary_ban into UniverseManager”.

Once a symbol is flagged, exclude for temporary_ban_hours.

Make it purely config/log driven (no AI required at first).

Not as critical as exposure/OMS, but if you wrote it down, wire it.

Sanity Check

If you implement:

✅ pending exposure in risk

✅ kill switch

✅ product constraints + status checks

✅ order state machine + reconciliation

✅ fail-closed data gating (already there)

✅ alerts for stops / failures

✅ backtest parity + CI gate

✅ single-instance lock

…then you’re in legit production-bot territory for a single-exchange rules engine.