Here’s a rewritten, upgraded version of the spec that assumes you’ll be **combining “best features” from other repos** into this bot, while keeping your safety / risk architecture as the non-negotiable core.

---

# 247trader-v2 — Core Requirements for Multi-Strategy Coinbase Spot Bot

**Repo snapshot:** `247trader-V2-main`
**Version:** v0.2 (requirements refresh)
**Date:** 2025-11-11
**Method:** Reverse-engineered from the current codebase, then hardened into **atomic, SHALL-style requirements** with SLAs/SLOs, go/no-go gates, and explicit statuses (**Implemented / Partial / Planned**).

> Goal: Define the stable, testable “contract” for 247trader-v2 so you can safely plug in additional strategies and lifted code from other repos *without* breaking safety or determinism.

---

## 1) Scope

**Purpose.**
A **rules-first, multi-strategy, spot-only** crypto trading bot for **Coinbase** that:

* Runs **DRY_RUN → PAPER → LIVE** with explicit gates.
* Uses **deterministic, configurable strategies** (including adapted “best features” from other repos).
* Enforces a central **Risk Engine** and **Execution Engine** as the only path to the exchange.

**Out of scope (current build).**

* Derivatives, margin, leverage, or shorts.
* Cross-exchange routing.
* Autonomous black-box AI trading (strategies must reduce to deterministic `TradeProposal`s).

---

## 2) Definitions & Acronyms

* **NLV** – Net Liquidation Value (total account equity in USD).
* **OHLCV** – Open/High/Low/Close/Volume bar data.
* **PnL** – Profit & Loss (realized + unrealized).
* **SLA** – Service Level Agreement (hard bound on detection/response).
* **SLO** – Service Level Objective (performance/latency target).
* **Circuit Breaker** – Automated trading halt under unsafe conditions.
* **Kill-switch** – Operator or flag-based immediate halt + order cancel.
* **Strategy Module** – A self-contained component that reads market data/state and outputs `TradeProposal`s, but never talks directly to the exchange.

---

## 3) System Overview

The system is composed of:

1. **Universe Manager**
   Builds the eligible symbol list with tier, volume, spread, and theme constraints.

2. **Data Layer**
   Fetches OHLCV, quotes, and product/exchange status with staleness checks.

3. **Strategy Modules**
   One or more independent strategies (mean-reversion, momentum, event-driven, etc.) that consume the same market snapshot and emit `TradeProposal`s.

4. **Strategy Integrator**
   Aggregates proposals from all strategies, applies per-strategy budgets and priority rules, then forwards candidates to the Risk Engine.

5. **Risk Engine**
   Enforces exposure caps, staleness breakers, product health, cooldowns, outlier guards, and drawdown limits. This is the final gate before orders.

6. **Execution Engine**
   Previews orders, places idempotent orders on Coinbase, reconciles fills/fees, and records a complete audit trail.

7. **State & Logging**
   Persists positions, exposure, cooldowns, order metadata, configuration version, and risk/execution decisions; redacts secrets.

8. **Modes & Gates**
   Enforces DRY_RUN → PAPER → LIVE progression with tests, rehearsals, and operator confirmation.

---

## 4) Functional Requirements

Status legend: **Implemented** | **Partial** | **Planned**

### 4.1 Universe & Regime

**REQ-U1 (Universe eligibility)**
The system **SHALL** build an eligible symbol list from configuration, enforcing:

* Min 24h volume per tier.
* Max spread (bps) per tier.
* Per-tier allocation caps.

**Acceptance:**
Symbols breaching thresholds are excluded; logs contain `symbol`, `tier`, and `exclusion_reason`.
**Status:** Implemented.

---

**REQ-U2 (Cluster/theme caps)**
The system **SHALL** enforce a max exposure per theme/cluster (e.g., L2, MEME), configurable per policy.

* Example default: `max_theme_exposure_pct = 10%`.

**Acceptance:**
When proposed + pending exposure for a theme would exceed the cap, new proposals are rejected with `risk_reason='theme_cap'`.
**Status:** Implemented.

---

**REQ-U3 (Regime multipliers)**
The system **SHALL** apply regime multipliers `{bull, chop, bear, crash} = {1.2, 1.0, 0.6, 0.0}` to sizing and caps unless overridden in config.

**Acceptance:**
Changing regime in config/logical state immediately alters effective caps/sizes and is visible in logs.
**Status:** Implemented.

---

### 4.2 Strategies, Signals & Rules

#### 4.2.1 Strategy Module Contract

**REQ-STR1 (Pure strategy interface)**
Each strategy module **SHALL** expose a pure interface:

```text
generate_proposals(market_snapshot, state) -> list[TradeProposal]
```

and **SHALL NOT**:

* Call exchange APIs directly.
* Mutate global process state outside its own namespace.
* Modify Risk or Execution Engine configuration.

**Acceptance:**
Static/code search and tests confirm that strategies only import allowed interfaces and only return `TradeProposal` objects or empty lists.
**Status:** Planned (contract partly implicit today).

---

**REQ-STR2 (Feature flags)**
Each strategy **SHALL** be toggleable by config (e.g., `strategy.<name>.enabled: true|false`) and **SHALL default to false** when first added.

**Acceptance:**

* When disabled, strategy logs show “skipped (disabled)” and emit **no** proposals.
* When enabled, logs show strategy-specific metrics (e.g., proposals_count).
  **Status:** Planned.

---

**REQ-STR3 (Per-strategy risk budgets)**
The system **SHALL** enforce per-strategy caps such as:

* `max_strategy_at_risk_pct`.
* `max_trades_per_cycle_per_strategy`.

These caps are applied **before** global caps in the Risk Engine.

**Acceptance:**
If a single strategy attempts to exceed its own budget, excess proposals are dropped or downscaled with `risk_reason='strategy_cap'`, even if global caps are still available.
**Status:** Planned.

---

#### 4.2.2 Signals & Rules

**REQ-S1 (Deterministic triggers)**
The system **SHALL** compute triggers (e.g., breakout, volume spike) with configurable lookbacks, scoring, and a `max_triggers_per_cycle` per strategy.

**Acceptance:**
Synthetic OHLCV feeds cause trigger firing only when score ≥ threshold; counts capped by `max_triggers_per_cycle`.
**Status:** Implemented.

---

**REQ-R1 (No shorts)**
The system **SHALL NOT** propose or execute short positions.

**Acceptance:**

* Any proposal with negative quantity/notional or `side='SELL'` without an existing long is rejected.
* Risk logs show `risk_reason='shorting_disallowed'`.
  **Status:** Implemented.

---

**REQ-R2 (TradeProposal schema)**
Each `TradeProposal` **SHALL** include:

```text
{symbol, side, notional_pct, stop_loss_pct, take_profit_pct, conviction}
```

and respect:

* Tier base sizes.
* `min_conviction_to_propose`.
* Policy bounds on stops/targets.

**Acceptance:**
Unit tests and runtime guards reject proposals missing any required field or violating bounds, with explicit error logs.
**Status:** Implemented.

---

### 4.3 Risk & Safety Gates

**REQ-K1 (Global kill-switch SLA)**
On kill-switch activation, the system **SHALL**:

1. Stop generating new proposals immediately (same cycle).
2. Cancel all working orders within **≤10s**.
3. Emit a CRITICAL alert within **≤5s**.
4. Persist `halt_reason` and timestamp.

Mean time to detect (MTTD) kill-switch changes **≤3s**.

**Acceptance:**
Simulation flips kill flag; metrics/logs confirm the timing bounds above.
**Status:** Partial (wiring must be proven end-to-end).

---

**REQ-E1 (Exposure caps)**
Total at-risk, per-asset, and per-theme exposures **SHALL NOT** exceed configured caps.

* Defaults (unless overridden):

  * `max_total_at_risk_pct = 15%`
  * `max_position_size_pct = 5%`

**Acceptance:**
Any proposal/order that would breach a cap is rejected with a structured risk record (`risk_reason`, `cap_type`, `current_pct`, `proposed_pct`).
**Status:** Implemented.

---

**REQ-E2 (Pending exposure counted)**
Exposure calculations **SHALL** include:

* Existing filled positions, and
* All open/working orders at **worst-case notional**.

**Acceptance:**
Creating a working order that pushes exposure near the cap blocks further proposals even if the order is not filled yet.
**Status:** Partial (ensure inclusion across all caps and all code paths).

---

**REQ-ST1 (Data staleness breaker)**
If:

* Latest quote age > `max_quote_age_seconds` (default **5s**), **OR**
* Latest 1m OHLCV close age > `max_ohlcv_age_seconds` (default **90s**),

the system **SHALL**:

* Block new proposals and order placement, and
* Emit a `STALENESS` alert within **≤5s**.

**Acceptance:**
Aged data in test harness triggers an immediate halt and a logged alert; no new orders appear while stale.
**Status:** Implemented.

---

**REQ-EX1 (Exchange/product health)**
If the exchange or a product is reported as down/disabled, trading **SHALL** be blocked for affected symbols.

**Acceptance:**
Toggling a product/exchange health flag to “disabled” causes new proposals/orders for that symbol to be rejected with `risk_reason='product_disabled'`.
**Status:** Implemented.

---

**REQ-O1 (Outlier guards)**
Ticks with:

* Absolute mid-price jump > **6σ** over a 60s window, **or**
* Spread > `max_spread_bps`,

**SHALL** be rejected for decision making. Thresholds **SHALL** be configurable.

**Acceptance:**
Synthetic spikes trigger risk rejections and log outlier metrics.
**Status:** Implemented.

---

**REQ-CD1 (Cooldowns)**
After:

* Any fill, or
* A stop-out,

a per-symbol cooldown **SHALL** block new entries for:

* `cooldown_minutes` (normal fills),
* `cooldown_after_stop_minutes` (stops).

**Acceptance:**
Entry attempts within cooldown windows are rejected with `risk_reason='cooldown'` for all entry paths (all strategies).
**Status:** Partial (ensure central enforcement).

---

**REQ-DD1 (Drawdown breaker)**
If `max_drawdown_pct` (rolling or session) is breached, the system **SHALL**:

* Halt new proposals/orders, and
* Emit a CRITICAL alert within **≤5s**.

**Acceptance:**
Backtest/replay or simulated PnL dip beyond limit triggers halt and alert; no new trades appear after breach.
**Status:** Implemented.

---

### 4.4 Execution

**REQ-X1 (Idempotent orders)**
Client Order IDs **SHALL** be unique across restarts for at least **7 days**, e.g.:

```text
<botId>-<symbol>-<timestamp>-<nonce>
```

Retries **SHALL NOT** create duplicate live orders on Coinbase.

**Acceptance:**
Replay a retry storm; exchange shows only one active order per intended order, with retries mapping to the same client ID.
**Status:** Implemented.

---

**REQ-X2 (Preview → place → reconcile)**
For every order, the system **SHALL**:

1. Preview cost/size vs. min notional and risk.
2. Place the order (market/limit per policy).
3. Reconcile fills and fees.
4. Emit a full audit record (proposal → order → fill → PnL).

**Acceptance:**
After any trade, position and PnL reflect fees, and audit logs contain the full chain.
**Status:** Implemented.

---

**REQ-X3 (Fee-aware sizing)**
Sizing **SHALL** include maker/taker fees consistently in:

* Min-notional checks (risk layer), and
* Execution sizing and PnL calculations.

**Acceptance:**
Edge cases near min notional behave identically in risk and execution; tests confirm fee assumptions are identical.
**Status:** Partial (align all call sites).

---

### 4.5 Configuration, Modes & Concurrency

**REQ-C1 (Config validation)**
Invalid, missing, or obviously unsafe values in `app`, `policy`, `universe`, or `signals` configs **SHALL** cause startup to fail fast with a non-zero exit code and human-readable errors.

**Acceptance:**
Corrupt, missing, or out-of-bounds configs in tests cause immediate abort with clear messages.
**Status:** Implemented.

---

**REQ-M1 (Mode gating)**
The default mode **SHALL** be read-only (no real orders). LIVE mode **SHALL** require:

* Explicit config enablement,
* Passing all safety gates defined in §8, and
* Operator confirmation.

**Acceptance:**
Attempting to run in LIVE without satisfying gates or without explicit confirmation is blocked with a clear log/error.
**Status:** Implemented.

---

**REQ-SI1 (Single instance)**
The system **SHALL** prevent multiple concurrent trading loops.

**Acceptance:**
Starting two instances concurrently results in exactly one proceeding; the other exits with a “single-instance lock” message.
**Status:** Implemented.

---

### 4.6 Observability & Alerts

**REQ-AL1 (Alert SLA & dedupe)**
On any safety breach (kill-switch, staleness, health, exposure, drawdown), the system **SHALL**:

* Emit an alert within **≤5s**.
* Deduplicate identical alerts for **60s**.
* Escalate (e.g., higher severity or additional channel) if unresolved for **≥2m**.

**Acceptance:**
Simulated breaches produce timely alerts with dedupe behavior; unresolved breaches escalate.
**Status:** Partial (alert wiring must be finalized).

---

**REQ-OB1 (Latency telemetry)**
The system **SHALL** collect and expose telemetry for:

* p95 and p99 decision cycle latency.
* p95 Coinbase REST latency.
* Percentage of cycles with degraded safety.

Targets:

* p95 decision cycle ≤ **1.0s**.
* p99 decision cycle ≤ **2.0s**.
* ≤ **0.5%** of cycles in a “safety degraded” state.

**Acceptance:**
Telemetry is visible in CI or a dashboard; CI fails if SLOs are violated beyond a configured tolerance.
**Status:** Planned.

---

**REQ-SCH1 (Jittered scheduling)**
Decision cycles **SHALL** apply random jitter (0–10%) to their nominal timing to avoid lockstep behavior with the exchange.

**Acceptance:**
Cycle timestamps in logs show randomized offsets around the configured interval.
**Status:** Planned.

---

### 4.7 Backtesting & Determinism

**REQ-BT1 (Deterministic backtests)**
Backtests **SHALL** be deterministic (fixed seed, deterministic data ordering) so repeated runs with the same inputs yield identical outputs.

---

**REQ-BT2 (Backtest report format)**
Backtests **SHALL** output a machine-readable JSON report including:

* Trade list with timestamps, side, size, price.
* PnL time series.
* Max drawdown.
* Exposure by theme/asset.

---

**REQ-BT3 (Regression gate)**
CI **SHALL** compare key backtest metrics to a baseline and fail if deviation exceeds **±2%** for predefined metrics (e.g., total PnL, max drawdown, trade count).

**Status (BT1-3):** Partial (baseline backtest exists; determinism and CI gate need finishing).

---

### 4.8 Security & Compliance

**REQ-SEC1 (Secrets handling)**

* API keys **SHALL** be loaded from environment or a secret store.
* The app **SHALL** refuse to start if secrets appear in plaintext configs.
* Logs **SHALL** redact secrets with 100% coverage (verified by a secret-scanner).

**Status:** Implemented.

---

**REQ-SEC2 (Secret rotation)**
Secrets **SHALL** be rotated at least every **90 days**, and the rotation event **SHALL** be logged (without exposing secret values).

**Status:** Planned.

---

**REQ-TIME1 (Clock sync gate)**
The host clock **SHALL** be NTP-synced with drift < **100ms** relative to a trusted source; otherwise the app **SHALL** refuse to start.

**Status:** Planned.

---

**REQ-RET1 (Data retention)**

* Logs/state **SHALL** be retained for **90 days** by default.
* PII **SHALL NOT** be collected.
* Logs **SHALL** be deletable on operator request.

**Status:** Implemented.

---

### 4.9 Exchange Integration

**REQ-CB1 (Retry policy)**
For Coinbase REST 429/5xx responses, the system **SHALL**:

* Use exponential backoff with full jitter:

  * Base: 200ms, cap: 5s, max 6 attempts.
* Abort retries for non-idempotent ambiguity (unknown order state).

**Acceptance:**
Fault-injection tests show compliant retry patterns and correct abort behavior on ambiguous failures.
**Status:** Planned.

---

## 5) Non-Functional Requirements (SLOs)

* **Performance**

  * p95 decision cycle ≤ **1.0s**, p99 ≤ **2.0s**.
* **Reliability**

  * Single-instance guarantee.
  * Graceful shutdown.
  * State restored within one decision cycle after restart.
* **Safety**

  * No orders placed when any safety gate is active (kill, drawdown, staleness, exchange health).
* **Observability**

  * Structured, redact-safe logs with rotation and basic metrics for each strategy and core subsystem.

---

## 6) Operating Modes & Go/No-Go Gates

Transition **DRY_RUN → PAPER → LIVE** **SHALL** require:

1. **CI Green**

   * 100% unit/contract tests pass.
   * Config validation passes.

2. **Paper Rehearsal**

   * ≥ **7 days** of PAPER trading with **0** unhandled safety-gate breaches.
   * Evidence of alert SLA (AL1) functioning.

3. **Kill-Switch Drill**

   * Manually triggering kill-switch cancels all orders within **≤10s** and blocks proposals immediately.

4. **Telemetry Online**

   * Latency telemetry (OB1) active.
   * Jittered scheduling (SCH1) enabled.
   * Alerts wired and verified.

5. **Canary LIVE**

   * One tier-1 asset traded LIVE at **≤50%** of normal caps for at least **48h**.
   * Continuous monitoring of exposure and alerts.

---

## 7) Verification & Acceptance

Each requirement (`REQ-*`) includes an acceptance statement. Implement tests and/or harnesses to verify:

* **Kill-switch timing** (REQ-K1).
* **Pending exposure caps** with open orders (REQ-E2).
* **Data staleness breaker** by aging quotes/ohlcv (REQ-ST1).
* **Retry policy** via fault-injection for 429/5xx (REQ-CB1).
* **Backtest determinism and regression** (REQ-BT1-3).
* **Single instance lock** by starting concurrent processes (REQ-SI1).
* **Secret redaction** via a secret-scanner on logs (REQ-SEC1).
* **Per-strategy caps and isolation** (REQ-STR1-3).

---

## 8) Requirements Traceability Matrix (RTM — Template)

To be populated in CI:

| REQ-ID    | Test IDs / Procedure           | Evidence Artifact           | Status      |
| --------- | ------------------------------ | --------------------------- | ----------- |
| REQ-K1    | `test_kill_switch_timings`     | logs/alerts with timestamps | Partial     |
| REQ-E2    | `test_pending_exposure_caps`   | risk decision logs          | Partial     |
| REQ-ST1   | `test_data_staleness_breaker`  | alert payload               | Implemented |
| REQ-X1    | `test_idempotent_orders_retry` | venue order count           | Implemented |
| REQ-AL1   | `test_alert_sla_and_dedupe`    | alert events                | Partial     |
| REQ-OB1   | `test_latency_slos`            | telemetry export/dashboard  | Planned     |
| REQ-BT1-3 | `backtest_regression_suite`    | JSON report diff            | Partial     |
| REQ-CB1   | `retry_policy_fault_injection` | request traces              | Planned     |
| REQ-STR1  | `test_strategy_interface_pure` | strategy tests/logs         | Planned     |
| REQ-STR3  | `test_strategy_risk_budgets`   | risk decisions log          | Planned     |

---

## 9) Known Gaps (Blockers Before Serious LIVE)

1. **Kill-switch (K1)** – Prove end-to-end timing and universal coverage.
2. **Pending exposure (E2)** – Ensure all caps include working orders.
3. **Cooldowns (CD1)** – Enforce centrally across all strategies.
4. **Fee-aware sizing (X3)** – Unify fee assumptions across risk + execution.
5. **Alert routing (AL1)** – Implement dedupe + escalation.
6. **Latency telemetry (OB1)** – Wire metrics + SLO alarms.
7. **Jittered scheduling (SCH1)** – Implement and verify cycle jitter.
8. **Retry/backoff with jitter (CB1)** – Implement proper backoff.
9. **Clock sync & secret rotation (TIME1, SEC2)** – Add startup gates/policy.
10. **Strategy isolation and caps (STR1-3)** – Finalize contracts and tests.

---

### TL;DR

* This is now a **multi-strategy Coinbase spot bot spec** with clear safety invariants.
* Any “best feature” you import from other repos must:

  * Plug in as a **pure, toggleable strategy module**;
  * Output only `TradeProposal`s;
  * Respect central **Risk & Execution Engines**;
  * Stay within **per-strategy + global risk caps**.
* Close the **Partial/Planned** items (especially kill-switch, pending exposure, cooldowns, retry/backoff, and telemetry) before putting real money at scale behind it.


Kill switch status drift – APP_REQUIREMENTS.md (lines 79-82) still labels REQ‑K1 “Partial”, yet the code paths are already wired end-to-end: the risk engine blocks trading and fires alerts when data/KILL_SWITCH exists (core/risk.py (lines 997-1017)), the main loop surfaces the flag via the governance config (runner/main_loop.py (lines 462-485)), and the “Critical Gaps Fixed” write-up documents the completed alert wiring (docs/CRITICAL_GAPS_FIXED.md (lines 94-129)).
Pending exposure actually enforced – REQ‑E2 is marked “Partial” in APP_REQUIREMENTS.md (lines 88-90), but the automated tests cover all combinations of portfolio exposure plus working orders, including exchange open-order fallbacks (tests/test_pending_exposure.py (lines 53-217)). The same suite hydrates pending orders from persisted state, so the acceptance criteria are already satisfied.
Fee-aware sizing implemented – REQ‑X3 remains “Partial” in APP_REQUIREMENTS.md (lines 118-124), even though the execution engine provides dedicated helpers for gross/net sizing with maker/taker fees (core/execution.py (lines 423-468)) and regression tests verify the fill math and quote reconciliation (tests/test_execution_fill_math.py (lines 1-92)).
Retry/backoff listed as “Planned” – REQ‑CB1 is still “Planned” in APP_REQUIREMENTS.md (lines 175-179), but the Coinbase connector already performs exponential backoff with jitter for 429/5xx/network faults and skips non-idempotent errors (core/exchange_coinbase.py (lines 278-395)), and the production-readiness report explicitly calls this requirement implemented (docs/PRODUCTION_READINESS_FINAL.md (lines 31-59)).
Newly discovered gaps missing – The latest readiness assessment highlights risks such as naive datetime usage, lack of operational observability, incomplete alert matrix, and documentation drift (docs/PRODUCTION_READINESS_FINAL.md (lines 14-27)). None of those appear in the requirements’ “Known Gaps” (§9), so the doc isn’t reflecting the current go/no-go gates.
Next steps

Reconcile APP_REQUIREMENTS.md with the live implementation/tests, updating each REQ status and the RTM to match the verified evidence above.
Extend §9/§10 to incorporate the newer audit findings (datetime handling, observability, alert coverage) so the requirements remain a reliable single source of truth.






