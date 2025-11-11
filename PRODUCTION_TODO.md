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
| 🟢 Done | Enforce Coinbase product constraints (base/quote increments, min size, min market funds) before submission. | N/A | ExecutionEngine.enforce_product_constraints() checks metadata and rounds sizes; integrated into _execute_live() before order placement. |
| 🟢 Done | Track realized PnL per position from actual fill prices. | N/A | StateStore.record_fill() tracks positions with weighted average entry prices and calculates realized PnL on position closes. Accounts for exit fees and proportional entry fees. Updates pnl_today, pnl_week, consecutive_losses. Integrated into ExecutionEngine.reconcile_fills(). Surfaced in audit logs. 11 comprehensive tests added (test_pnl_tracking.py). All 120 tests passing. See implementation in infra/state_store.py (lines ~470-595). |
| � Done | Block trading on products flagged POST_ONLY, LIMIT_ONLY, or CANCEL_ONLY via exchange status circuit breaker. | N/A | RiskEngine._filter_degraded_products() blocks POST_ONLY, LIMIT_ONLY, CANCEL_ONLY, and OFFLINE statuses using cached product metadata; fail-closed on errors; configurable via `circuit_breakers.check_product_status`; 9 comprehensive tests in test_exchange_status_circuit.py. All tests passing. |

## Order Management & Execution

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🟢 Done | Generate deterministic client order IDs per proposal and dedupe retries. | N/A | ExecutionEngine.generate_client_order_id() creates SHA256-based IDs from symbol/side/size/timestamp (minute granularity); prevents duplicate orders on retries; StateStore deduplication working. |
| 🟢 Done | Implement explicit order state machine (`NEW → PARTIAL → FILLED | CANCELED | EXPIRED`) with timers. | N/A | OrderStateMachine in core/order_state.py with 8 lifecycle states (NEW, OPEN, PARTIAL_FILL, FILLED, CANCELED, EXPIRED, REJECTED, FAILED), transition validation, fill tracking, auto-transitions, stale order detection, and telemetry hooks. Integrated into ExecutionEngine (DRY_RUN, PAPER, LIVE modes). 25 comprehensive tests added. All 72 tests passing. |
| 🟢 Done | Auto-cancel stale post-only orders (re-quoting not implemented). | N/A | Enhanced manage_open_orders() uses OrderStateMachine.get_stale_orders() for reliable age tracking; transitions canceled orders to CANCELED state; updates StateStore; handles batch and individual cancellation with proper error handling. Comprehensive test coverage with 11 new tests (test_manage_open_orders.py) covering DRY_RUN skip, disabled cancellation, single/batch cancel, fallback logic, API failures, terminal order handling, and StateStore integration. All 72 tests passing. Re-quoting feature deferred (would require market-making logic). |
| 🟢 Done | Cache Coinbase product metadata (increments, status, min funds) for execution validation. | N/A | CoinbaseExchange.get_product_metadata() refreshes every 5 minutes and feeds ExecutionEngine constraint checks. |
## Critical TODOs (Production Safety)

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🟢 Done | Poll fills each cycle and reconcile positions/fees before updating risk exposure. | N/A | CoinbaseExchange.list_fills() + ExecutionEngine.reconcile_fills() refresh orders, capture multi-fill orders, and sync StateStore; 12 regression tests in test_reconcile_fills.py. |
| 🟢 Done | Ensure graceful shutdown cancels live orders, flushes state, and exits cleanly. | N/A | runner/main_loop._handle_stop() cancels via OrderStateMachine, syncs StateStore, and exits safely; covered by test_graceful_shutdown.py. |
| 🟡 Pending Validation | Use real PnL for circuit breakers. | TBD | Realized PnL tracking exists; need to wire RiskEngine daily/weekly stops to StateStore metrics and rehearse live. |
| 🔴 TODO | Include fee-adjusted rounding when enforcing minimum notional on the quote side. | TBD | Prevents relisting orders below thresholds after fee deduction. |
| 🔴 TODO | Add latency accounting for API calls, decision cycle, and submission pipeline. | TBD | Required for watchdog timers and alerting accuracy. |
| 🔴 TODO | Introduce jittered scheduling to avoid synchronized bursts with other bots. | TBD | Randomize sleep interval per loop respecting policy gates. |
| 🟡 Pending Validation | Run PAPER/LIVE read-only smoke to observe `_post_trade_refresh` against real fills. | TBD | Confirms reconcile timing against Coinbase latency with live data. |
| 🟡 Pending Validation | Tune `execution.post_trade_reconcile_wait_seconds` based on observed settle time. | TBD | Default 0.5s may be too short during volatility; measure and adjust. |
| 🟢 Done | Enforce spread and 20bps depth checks before order placement. | N/A | ExecutionEngine.preview_order rejects on thin books; logs context to audit. |
| 🟢 Done | Default to post-only order routing unless policy overrides. | N/A | `execution.default_order_type` is `limit_post_only`; override requires explicit policy flag. |

## Data Integrity

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🟢 Done | Fail-closed gating when critical data (accounts, quotes, orders) is unavailable. | N/A | `TradingLoop._abort_cycle_due_to_data` returns NO_TRADE. |

| 🔴 TODO | Maintain canonical symbol mapping (`BTC-USD` vs `BTCUSD`) across modules. | TBD | Prevents mismatches between exchange and strategy layers. |
| 🔴 TODO | Enforce UTC/monotonic time sanity, including explicit bar windowing. | TBD | Replace remaining `datetime.utcnow()` usage and align candles. |
| 🔴 TODO | Add outlier/bad-tick guards before trigger evaluation. | TBD | Reject candles or quotes with extreme moves lacking volume confirmation to prevent false breakouts. |
| 🟢 Done | Abort cycle if partial snapshot detected during reconcile. | N/A | `_reconcile_exchange_state` raises `CriticalDataUnavailable`. |
| 🟢 Done | **Reject quotes older than max_quote_age_seconds** before trading decisions. | N/A | Implemented `_validate_quote_freshness()` in ExecutionEngine; validates timestamps at 3 critical points (preview_order, _execute_live, _find_best_trading_pair); uses policy `microstructure.max_quote_age_seconds` (30s default); handles timezone-aware/naive timestamps, detects clock skew; 14 comprehensive tests; all 109 tests passing. See `docs/STALE_QUOTE_REJECTION.md`. |

## Universe Governance

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🔴 TODO | Integrate `exclusions.red_flags` and `temporary_ban_hours` into UniverseManager. | TBD | Persist flagged assets in StateStore and suppress from universe selection for the configured cool-off window. |

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
| 🔴 TODO | Add CI gate that runs unit tests plus short backtest before LIVE deploys. | TBD | Fail pipeline on backtest crashes or invalid metrics to prevent regressions. |

## Rate Limits & Retries

| Status | Task | Owner | Notes |
| ------ | ---- | ----- | ----- |
| 🟢 Done | Apply exponential backoff with jitter for 429/5xx and network faults. | N/A | `CoinbaseExchange._req` retries with capped backoff. |
| 🔴 TODO | Track per-endpoint rate budgets (public vs private) and pause before exhaustion. | TBD | Prevents API bans during spikes. |
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
| 🔴 TODO | Enforce staging vs production runtime gates. | TBD | Require explicit ENV flag: staging limited to DRY_RUN/PAPER, production demands secrets, alerting, single-instance lock, and all critical 🔴 cleared. |
