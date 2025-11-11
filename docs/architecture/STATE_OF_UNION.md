# Phase 0 State of the Union

## 1. Module Inventory & Health
- **runner/main_loop.py**
  - Pros: clear cycle structure, audit logging, purge + rebalance hooks.
  - Gaps: synchronous design, hard-coded component wiring, minimal fault telemetry.
- **core/execution.py**
  - Pros: recent upgrades (stable top-up, pending-order awareness, cooldown logic).
  - Gaps: tightly coupled to CoinbaseExchange, lacks async handling, no plug-in order routes.
- **core/exchange_coinbase.py**
  - Pros: comprehensive REST coverage (quotes, books, orders, convert, open orders).
  - Gaps: HMAC-only (no websocket streaming), rate-limit heuristics naive, no adapter interface.
- **core/risk.py**
  - Pros: enforces policy-defined exposure/cooldowns, integrates with state store.
  - Gaps: no persistent position ledger, limited awareness of pending orders before Phase 0 changes, lacks VaR or scenario analysis.
- **core/universe.py & core/triggers.py**
  - Pros: dynamic universe discovery, tiering, deterministic trigger rules.
  - Gaps: relies on live API; no caching or historical snapshot support, UTC handling uses deprecated `datetime.utcnow`.
- **strategy/rules_engine.py**
  - Pros: simple rules-based scoring with policy thresholds.
  - Gaps: single strategy only, no parameterization hooks, limited diagnostics on proposal rejections.
- **infra/state_store.py**
  - Pros: straightforward JSON persistence, easy to inspect.
  - Gaps: not crash-safe, no locking, inadequate for multi-process usage.
- **tests/test_core.py**
  - Pros: smoke coverage for key flows.
  - Gaps: over-reliance on returns vs asserts, lacks component-level unit tests, slow due to network access.

## 2. Operational Baseline Status
- **Logging**: Combined file/console logging in runner; execution layer logs key events. Need log rotation and structured output for production.
- **Configuration Hygiene**: YAML configs manually edited; no schema validation or defaults enforcement.
- **Secrets**: Coinbase keys loaded from JSON file or env vars; no vault integration or rotation process.
- **Deployment**: Currently manual via `./run_live.sh`; no containerization or process supervision.
- **Monitoring**: No metrics exporter; manual log review required. No alert escalation.
- **Resilience**: Single process with blocking HTTP calls; partial cooldown logic prevents spam but no watchdog for stuck components.

## 3. Immediate Technical Debt (Phase 0 Focus)
1. **Configuration Validation**: Build schema checks (possibly with `pydantic`) to fail fast on malformed YAML.
2. **Logging Framework**: Define logging format, rotation policy, and baseline structured fields (cycle_id, mode, symbol).
3. **UTC Handling**: Replace `datetime.utcnow()` usage with timezone-aware timestamps to eliminate warnings.
4. **Test Hygiene**: Convert pytest return-based checks to asserts; introduce unit tests for key helpers.
5. **State Store**: Evaluate replacing JSON with simple SQLite (or add file locking) for crash safety.

## 4. Phase 0 Deliverables Checklist
- [x] Architecture spec (module boundaries & data flow).
- [x] State of union summary (this document).
- [ ] Operational baseline checklist & owner assignments.
- [ ] Tracking issue list for Phase 0 technical debt.

## 5. Risks & Open Questions
- **Async Migration Path**: Need decision on asyncio vs multiprocessing for Phase 4 to avoid rework.
- **Backtesting Data Source**: Identify provider (Coinbase historical, third-party) and storage format.
- **CI/CD Direction**: Choose GitHub Actions vs self-hosted runner to plan pipeline design.
- **Secret Management**: Determine whether to adopt 1Password/HashiCorp Vault/AWS Secrets Manager.

## 6. Next Actions
1. Draft operational baseline checklist (docs/operations) covering logging, config validation, deployment steps.
2. Raise GitHub issues for Technical Debt items listed above.
3. Align on technology choices (async framework, storage, secrets) before starting Phase 1.
