# 247trader-v2 Production Roadmap

This roadmap keeps the team focused on a resilient, Coinbase-only trading loop first, then layers in research capabilities and expansion once capital is protected. Each phase ends with demoable outcomes, automated checks, and updated documentation before moving forward.

## Phase 0 – Foundations (Complete / Short)
- **Architecture & Audit**: Lock module boundaries, dependency graph, and document the current execution/risk/runner state.
- **Operational Baseline**: Structured logging for universe size, candidates, proposals, risk decisions, and `no_trade_reason` outcomes.
- **Fail-Closed Data Gate**: Centralize snapshot collection; if any critical fetch fails or is partial, the loop records NO_TRADE and exits the cycle.
- **DRY_RUN Only**: Live exchange credentials are read-only; all execution happens in DRY_RUN until later phases explicitly graduate it.

## Phase 1 – Single-Exchange Core (In Flight)
- **Exchange Adapter Abstraction**: Coinbase connector sits behind a stable interface covering auth, market data, orders, and product metadata.
- **Restart-Safe State**: On boot, reconcile balances, open orders, fills, and PnL from Coinbase—never rely on hardcoded NLV outside tests.
- **Order Lifecycle v1**: Deterministic client order IDs, cancel-after enforcement, rejection handling, and idempotent retries (NO_TRADE on failure).
- **Dependency Wiring**: Runner bootstraps components via config-driven factories (no hidden globals) so tests can replace dependencies.

### Milestones
1. Rules engine and strategy run through the adapter-backed exchange in DRY_RUN without manual babysitting.
2. Cold-start reconcile script verifies portfolio state before each trading session.
3. Execution logs show order lifecycle transitions and explicit NO_TRADE reasons when protection trips.

## Phase 2 – Backtesting & Replay (Next)
- **Historical Data Feed**: Deterministic candle/quote loader (CSV/Parquet) keyed by universe and timeframe.
- **Loop Parity**: Backtest reuses the exact universe, triggers, risk, and execution codepaths (simulation-only fills) as live.
- **CLI & Reports**: Single `247trader backtest` command producing trade logs, equity curve, drawdown, hit rate, and exposure metrics.

### Milestones
1. Backtest CLI accepts the same config bundle used in live mode.
2. Trade/equity artifacts emitted per run and archived for CI comparison.
3. Regression test replaying a known scenario passes end-to-end with deterministic results.

## Phase 3 – Risk & Portfolio Resilience
- **Portfolio Ledger**: Persist positions, realized/unrealized PnL, fees, and exposure snapshots across sessions (SQLite/Postgres).
- **Exposure Guards**: Enforce pending exposure (open orders + fills) against per-asset, per-theme, and total caps.
- **Stops & Cooldowns**: Daily/weekly stop losses and cooldown timers promoted from policy to enforced logic.
- **Cold-Start Gate**: The loop refuses to trade if reconciliation fails or exposure cannot be proven within tolerance.

### Milestones
1. Portfolio DB mirrors Coinbase after reconcile with audit logs for adjustments.
2. Risk engine blocks trades exceeding exposure or stop thresholds with explicit NO_TRADE codes.
3. Cold-start gate tested by breaking reconcile inputs and ensuring the loop halts safely.

## Phase 4 – Execution Resilience
- **Order State Machine**: Explicit `NEW → PARTIAL → FILLED | CANCELED | EXPIRED` transitions with telemetry.
- **Retry Discipline**: Centralized retry/backoff for order and convert calls; repeated failure triggers NO_TRADE and alert hooks.
- **Fill Reconciliation**: Cycle-level reconciliation aligns expected vs actual fills; orphan orders are cancelled automatically.
- **Async/Queue Skeleton**: Minimal queue or task model to separate market data fetch, decisioning, and order submission without overbuilding.

### Milestones
1. Execution service publishes order lifecycle metrics and exposes open-order dashboards.
2. Fill reconciliation job closes stale orders and amends portfolio state without manual intervention.
3. Configurable retry/backoff policy validated against simulated 429/5xx faults.

## Phase 5 – Observability & Ops
- **Metrics Stack**: Prometheus exporter covering error rate, NO_TRADE categories, exposure, realized/unrealized PnL, and latency.
- **Alerting Pipeline**: Slack/Telegram/webhook client emitting on daily stop hits, reconcile mismatches, repeated API failures, and empty universe streaks.
- **Runbooks & Health Checks**: Baseline operational docs, readiness probes, and manual recovery checklists.

### Milestones
1. Metrics scraped into Grafana dashboards with alert thresholds codified.
2. End-to-end alert smoke test (e.g., synthetic reconcile failure) notifies the operator within minutes.
3. Runbooks versioned alongside code and referenced by the CI pipeline.

## Phase 6 – AI & Event Intelligence
- **News & Events Ingestion**: Allowlisted feeds transformed into structured signals with provenance and latency tracking.
- **Model Suite**: M1 (fundamental/news scorer), M2 (quant/microstructure sanity checker), M3 (arbitrator) operating only on rule-generated candidates.
- **Guardrails**: AI outputs remain advisory unless policy explicitly enables them; arbitrator cannot violate risk or execution constraints.

### Milestones
1. `ai.enabled` gate toggled in DRY_RUN with explainable JSON outputs persisted for review.
2. Backtest and paper comparisons demonstrate AI+rules outperform rules-only before any live activation.
3. Security review of model inputs/outputs ensures no unvetted content influences trades.

## Phase 7 – Multi-Strategy & Research Tooling
- **Strategy Orchestrator**: Run multiple strategies with shared capital, scheduling, and priority rules.
- **Shared Feature Service**: Centralize indicator calculations and caching for live and backtest parity.
- **Parameter Optimization**: Integrate hyperparameter sweeps/Bayesian optimization with reproducible random seeds and report artifacts.

### Milestones
1. Multi-strategy config assigns capital weights while honoring global risk limits.
2. Feature service benchmarked for latency and correctness across live/backtest modes.
3. Optimization CLI produces parameter sets with tracked provenance and validation metrics.

## Phase 8 – Multi-Exchange & Connectors
- **Adapter Implementations**: Add a second exchange (e.g., Binance/Kraken) adhering to the established exchange interface.
- **Unified Market Data**: Normalize symbols, products, and depth snapshots across venues.
- **Cross-Venue Strategies**: Enable arbitrage/hedging templates that still route through shared risk/execution services.

### Milestones
1. Runtime can swap Coinbase vs secondary exchange via config without code edits.
2. Normalized product/fee metadata registry shared by all adapters.
3. Cross-exchange strategy validated in backtest, paper, and limited live trials.

## Phase 9 – Governance & Compliance
- **Config Governance**: Versioned, approved config releases with change logs and mandatory reviews.
- **Audit Trail Enhancements**: Immutable decision logs capturing inputs, rules, AI contributions, and final actions.
- **Compliance Modes**: Paper/live toggles with guardrails for reporting, including exportable summaries for regulators or partners.

### Milestones
1. Signed config deployment workflow (e.g., GitOps) with enforcement in CI/CD.
2. Audit data schema meeting external review requirements and retention policies.
3. Compliance bundle generated automatically at the end of each trading session or on demand.

## Continuous Workstreams
- **Documentation**: Keep architecture, ops, and AI model cards current with each release.
- **Testing**: Expand unit, integration, backtest regression, and chaos tests as new features land.
- **Security & Secrets**: Rotate credentials, enforce least privilege, and review third-party dependencies regularly.
- **Feedback Loop**: After every phase, review live/backtest results and adjust priorities before committing to the next stage.

---
Target timeline: ~9 months, adjustable as learning dictates. Progression requires the current phase to pass automated checks, manual QA, and operator sign-off.
