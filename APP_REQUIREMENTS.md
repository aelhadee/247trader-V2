# 247trader‑v2 — Bottom‑Up Requirements (Rewritten for Best Practices)

**Repo snapshot:** `247trader‑V2‑main` (as provided)
**Version:** v0.2
**Date:** 2025‑11‑11
**Method:** Reverse‑engineered from the current implementation; rewritten as **atomic, testable SHALL‑style requirements** with clear SLAs/SLOs and go/no‑go gates. Where behavior is not fully present today, status is **Partial** or **Planned**, and acceptance criteria specify what must be proven before LIVE.

> **Goal:** This spec mirrors *what exists now* (bottom‑up) while tightening language and adding explicit thresholds so each item is verifiable.

---

## 1) Scope

**Purpose.** A rules‑first crypto trading bot for Coinbase spot, with deterministic signals, risk policy gates, and safe execution across modes: **DRY_RUN → PAPER → LIVE**.

**Out of scope (current build).** Derivatives, margin/shorts, cross‑exchange routing, autonomous AI decision‑making (beyond deterministic rules).

---

## 2) Definitions & Acronyms

* **NLV** (Net Liquidation Value): Total account equity in USD.
* **OHLCV** (Open/High/Low/Close/Volume): Candlestick market data.
* **PnL** (Profit & Loss): Realized + unrealized profit/loss.
* **SLA** (Service Level Agreement): Bound on detection/response times.
* **SLO** (Service Level Objective): Performance/latency targets.
* **Circuit Breaker:** Automated trading halt under unsafe conditions.
* **Kill‑switch:** Operator/flagged immediate stop that cancels orders.

---

## 3) System Overview

1. **Universe Manager** builds eligible symbols with tier/cluster constraints and regime multipliers.
2. **Trigger Engine** computes deterministic signals from OHLCV/quotes.
3. **Rules Engine** converts signals to `TradeProposal`s with stops/targets.
4. **Risk Engine** enforces exposure caps, staleness, product/exchange status, cooldowns, outlier guards, and drawdown rules.
5. **Execution** previews, places, and reconciles orders, ensuring idempotency and fee‑aware sizing.
6. **State & Logging** persist exposures, cooldowns, orders, and redact secrets.
7. **Modes & Gates** guard DRY_RUN/PAPER/LIVE transitions.

---

## 4) Functional Requirements (Atomic, Testable)

Status legend: **Implemented** | **Partial** | **Planned**

### 4.1 Universe & Regime

**REQ‑U1 (Universe eligibility):** The system **SHALL** build an eligible symbol list from configuration, enforcing tier constraints (min 24h volume, max spread bps) and per‑tier allocation caps.
**Acceptance:** For inputs breaching thresholds, symbols are excluded and a reason is logged.
**Status:** Implemented.

**REQ‑U2 (Cluster/theme caps):** The system **SHALL** enforce max exposure per theme/cluster (e.g., L2, MEME).
**Defaults:** Config‑driven; example default 10% per theme.
**Acceptance:** When proposed + pending exposures exceed the cap, proposals are rejected with `risk_reason='theme_cap'`.
**Status:** Implemented.

**REQ‑U3 (Regime multipliers):** The system **SHALL** apply regime multipliers **{bull, chop, bear, crash} = {1.2, 1.0, 0.6, 0.0}** to both sizing and caps unless overridden in config.
**Acceptance:** Switching regime changes effective caps/sizes accordingly.
**Status:** Implemented.

### 4.2 Signals & Rules

**REQ‑S1 (Deterministic triggers):** The system **SHALL** compute triggers (e.g., breakout/volume‑spike) with configurable lookbacks, scoring, and a `max_triggers_per_cycle`.
**Acceptance:** Synthetic OHLCV causes trigger firing only when score ≥ threshold; capped by `max_triggers_per_cycle`.
**Status:** Implemented.

**REQ‑R1 (No shorts):** The system **SHALL NOT** propose short positions.
**Acceptance:** Any negative‑quantity or short side proposal is rejected.
**Status:** Implemented.

**REQ‑R2 (TradeProposal contents):** Each proposal **SHALL** include `{symbol, side, notional_pct, stop_loss_pct, take_profit_pct, conviction}` and respect tier base sizes and `min_conviction_to_propose`.
**Acceptance:** Unit check for fields present and bounds respected.
**Status:** Implemented.

### 4.3 Risk & Safety Gates

**REQ‑K1 (Kill‑switch SLA):** On kill‑switch activation the system **SHALL** (a) stop new proposals immediately, (b) cancel all working orders **≤10s**, (c) emit a CRITICAL alert **≤5s**, and (d) persist `halt_reason`. Detection MTTD **≤3s**.
**Acceptance:** Simulation flips kill flag; verify timings and logs.
**Status:** **Partial** (global flag/wiring to all paths must be proven end‑to‑end).

**REQ‑E1 (Exposure caps):** Total at‑risk, per‑asset, and per‑theme exposures **SHALL** not exceed configured caps.
**Defaults:** `max_total_at_risk_pct=15%`, `max_position_size_pct=5%` unless overridden.
**Acceptance:** Any order exceeding caps is rejected with a structured reason.
**Status:** Implemented.

**REQ‑E2 (Pending exposure counts):** Exposure calculations **SHALL** include both filled positions **and** all working orders at worst‑case notional.
**Acceptance:** Creating a working order that would cause a cap breach prevents further proposals for that asset/theme/total.
**Status:** **Partial** (ensure uniform inclusion across all caps and paths).

**REQ‑ST1 (Data staleness breaker):** If latest quote age > `max_quote_age_seconds` (default **5s**) **OR** latest 1m OHLCV close age > `max_ohlcv_age_seconds` (default **90s**), the system **SHALL** block proposing/placing orders and emit a `STALENESS` alert **≤5s**.
**Acceptance:** Aged data triggers halt + alert.
**Status:** Implemented.

**REQ‑EX1 (Exchange/product health):** If exchange or product status is down/disabled, trading **SHALL** be blocked for affected symbols.
**Acceptance:** Health flag toggled → proposals/execution blocked with reason.
**Status:** Implemented.

**REQ‑O1 (Outlier guards):** Ticks with absolute mid‑price jump > **6σ over 60s** or spread > `max_spread_bps` **SHALL** be rejected. Values **SHALL** be configurable.
**Acceptance:** Synthetic spikes cause risk rejection.
**Status:** Implemented.

**REQ‑CD1 (Cooldowns):** After a fill or stop‑out, a per‑symbol cooldown **SHALL** prevent new entries for `cooldown_minutes` and, if stopped, `cooldown_after_stop_minutes`.
**Acceptance:** Attempts within cooldown are rejected.
**Status:** **Partial** (verify enforcement on all entry paths).

**REQ‑DD1 (Drawdown):** If configured `max_drawdown_pct` is breached (rolling or session), proposals and new orders **SHALL** halt and an alert **SHALL** emit **≤5s**.
**Acceptance:** Backtest/replay triggers halt and alert.
**Status:** Implemented.

### 4.4 Execution

**REQ‑X1 (Idempotent orders):** Client Order IDs **SHALL** be unique across restarts for ≥7 days using `<botId>-<symbol>-<ts>-<nonce>` or equivalent. Retries **SHALL NOT** create duplicate exchange orders.
**Acceptance:** Retry storm test shows single live order on venue.
**Status:** Implemented.

**REQ‑X2 (Preview → place → reconcile):** The system **SHALL** preview order cost/size, place (market/limit per policy), reconcile fills and fees, and publish a complete audit record.
**Acceptance:** After fills, position and PnL reflect fees.
**Status:** Implemented.

**REQ‑X3 (Fee‑aware sizing):** Sizing **SHALL** include maker/taker fees for min‑notional checks and PnL math **consistently in both risk and execution**.
**Acceptance:** Edge cases near min notional pass/fail identically in both layers.
**Status:** **Partial** (ensure uniformity).

### 4.5 Configuration, Modes, and Concurrency

**REQ‑C1 (Config validation):** Invalid or unsafe values in app/policy/universe/signals configs **SHALL** fail fast with non‑zero exit and clear errors.
**Acceptance:** Corrupt configs cause startup failure.
**Status:** Implemented.

**REQ‑M1 (Mode gating):** Default `read_only=true`. LIVE mode **SHALL** require explicit enablement and passing all safety gates.
**Acceptance:** Attempt to trade LIVE without gates → blocked.
**Status:** Implemented.

**REQ‑SI1 (Single instance):** The system **SHALL** prevent concurrent trading loops.
**Acceptance:** Start two instances simultaneously → exactly one proceeds; the other exits with explicit reason.
**Status:** Implemented.

### 4.6 Observability & Alerts

**REQ‑AL1 (Alert SLA):** On any safety breach (kill, staleness, health, cap/drawdown) the system **SHALL** emit an alert within **≤5s**, dedupe identical alerts for **60s**, and escalate if unresolved within **2m**.
**Acceptance:** Simulated breaches produce timely alerts with dedupe/escalation.
**Status:** **Partial** (wiring bug must be fixed and proven).

**REQ‑OB1 (Telemetry SLOs):** p95 decision cycle ≤ **1.0s**; p95 Coinbase REST ≤ **300ms**; ≤ **0.5%** cycles with degraded safety.
**Acceptance:** Telemetry collected and checked in CI; alarms when SLOs are violated.
**Status:** **Planned** (latency telemetry missing).

**REQ‑SCH1 (Jittered scheduling):** Decision cycles **SHALL** apply jitter (0–10%) to avoid synchronicity.
**Acceptance:** Cycle timestamps show randomized offsets.
**Status:** **Planned**.

### 4.7 Backtesting & Determinism

**REQ‑BT1 (Determinism):** Backtests **SHALL** be deterministic (fixed seed) across runs.
**REQ‑BT2 (Report):** Backtests **SHALL** output JSON with trades, PnL, max drawdown, exposure by theme.
**REQ‑BT3 (Regression gate):** CI **SHALL** fail if backtest metrics deviate > **2%** from baseline.
**Status:** **Partial** (baseline backtest exists; determinism/report/gate to be finalized).

### 4.8 Security & Compliance

**REQ‑SEC1 (Secrets):** API keys **SHALL** be loaded from env/secret store; app **SHALL** refuse to start if keys appear in plaintext configs; logs **SHALL** redact secrets (100% coverage by secret‑scanner).
**Status:** Implemented.

**REQ‑SEC2 (Rotation):** Secrets **SHALL** be rotated at least every **90 days**; rotation date **SHALL** be logged (without revealing secrets).
**Status:** Planned.

**REQ‑TIME1 (Clock sync):** Host clock **SHALL** be NTP‑synced with drift < **100ms**; otherwise app **SHALL** refuse to start.
**Status:** Planned.

**REQ‑RET1 (Retention):** Logs/state **SHALL** be retained **90 days** by default and be deletable on request; PII **SHALL NOT** be collected.
**Status:** Implemented.

### 4.9 Exchange Integration

**REQ‑CB1 (Retry policy):** 429/5xx **SHALL** use exponential backoff with full jitter (base 200ms, cap 5s, max 6 attempts) and abort on non‑idempotent ambiguity.
**Acceptance:** Fault‑injection test shows compliant retry pattern.
**Status:** Planned.

---

## 5) Non‑Functional Requirements (SLOs)

* **Performance:** p95 decision cycle ≤ **1.0s**; p99 ≤ **2.0s**.
* **Reliability:** Single‑instance guarantee; graceful shutdown; state restored within one cycle after restart.
* **Safety:** No orders when any safety gate is active.
* **Observability:** Structured logs with rotation; redaction coverage = **100%** (tested).

---

## 6) Operating Modes & Go/No‑Go Gates

**DRY_RUN → PAPER → LIVE** progression **SHALL** require:

1. **CI Green:** 100% unit/contract tests pass; config validator pass.
2. **Paper Rehearsal:** ≥ **7 days** with **0** safety‑gate breaches and alert SLA evidence.
3. **Kill‑switch Drill:** Cancels all orders ≤10s and halts proposals immediately.
4. **Telemetry Online:** OB1 SLO collection active; alerts wired; jitter enabled.
5. **Canary LIVE:** One tier‑1 position at 50% normal caps for 48h with continuous monitoring.

---

## 7) Verification & Acceptance (How to Prove)

Each REQ above includes an **Acceptance** statement. Build automated tests or scripts to:

* Flip kill‑switch and measure timings (REQ‑K1).
* Create pending orders to test cap breaches (REQ‑E2).
* Age quotes/ohlcv to trip staleness (REQ‑ST1).
* Inject 429/5xx to validate backoff/jitter (REQ‑CB1).
* Run deterministic backtests and compare JSON metrics (REQ‑BT1‑3).
* Start two instances to verify lock (REQ‑SI1).
* Secret‑scanner on logs to prove redaction (REQ‑SEC1).

---

## 8) Requirements Traceability Matrix (RTM — Template)

> Populate in CI: each `REQ‑*` maps to test(s) and build a status dashboard.

| REQ‑ID    | Test IDs / Procedure            | Evidence Artifact           | Status      |
| --------- | ------------------------------- | --------------------------- | ----------- |
| REQ‑K1    | test_kill_switch_timings        | logs/alerts with timestamps | Partial     |
| REQ‑E2    | test_pending_exposure_caps      | risk decisions log          | Partial     |
| REQ‑ST1   | test_data_staleness_breaker     | alert payload               | Implemented |
| REQ‑X1    | test_idempotent_orders_retry    | venue order count           | Implemented |
| REQ‑AL1   | test_alert_sla_and_dedupe       | alert events                | Partial     |
| REQ‑OB1   | test_latency_slos               | telemetry dashboard export  | Planned     |
| REQ‑BT1‑3 | backtest_determinism_regression | JSON report diff            | Partial     |
| REQ‑CB1   | retry_policy_fault_injection    | request traces              | Planned     |

---

## 9) Known Gaps (Blockers Before LIVE)

1. **Alert wiring** must satisfy REQ‑AL1.
2. **Latency telemetry & SLOs** (REQ‑OB1) not yet operational.
3. **Jittered scheduling** (REQ‑SCH1) missing.
4. **Global kill‑switch** must be proven end‑to‑end (REQ‑K1).
5. **Pending exposure** inclusion must be uniform (REQ‑E2).
6. **Per‑symbol cooldowns** enforced in all entry paths (REQ‑CD1).
7. **Fee‑aware sizing** uniformity (REQ‑X3).
8. **Retry/backoff with jitter** (REQ‑CB1).
9. **Clock sync gate** (REQ‑TIME1) and **secret rotation policy** (REQ‑SEC2).

---

## 10) TL;DR

* Requirements are now **atomic, numeric, and testable** with SLAs/SLOs.
* Several items are **Partial/Planned**—close §9 before LIVE.
* Keep this as a **living RTM**: CI should fail if any SHALL lacks proof or regresses.

