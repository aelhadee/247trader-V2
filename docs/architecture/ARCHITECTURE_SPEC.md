# 247trader-v2 Architecture Baseline (Phase 0)

## 1. System Overview
- **Execution Model**: Deterministic polling loop (`runner/main_loop.py`) driving universe→triggers→rules→risk→execution.
- **Primary Exchange Adapter**: `core/exchange_coinbase.CoinbaseExchange` wrapping Coinbase Advanced Trade REST APIs.
- **Strategy Layer**: Rules-first implementation (`strategy/rules_engine.py`, `core/triggers.py`) producing TradeProposals.
- **Risk Layer**: `core/risk.py` applies policy constraints (`config/policy.yaml`).
- **State & Audit**: `infra/state_store.py` (JSON state), `core/audit_log.py` (JSONL audit).
- **Config**: YAML triplet (`config/app.yaml`, `policy.yaml`, `universe.yaml`).

## 2. Module Boundaries
- **Runner Layer**
  - Role: Orchestration, scheduling, error containment, logging.
  - Dependencies: Universe manager, trigger engine, risk engine, execution engine, state store, audit logger.
  - Entry Points: CLI (`python -m runner.main_loop`).
- **Exchange Layer**
  - Role: Market data, account data, order lifecycle, conversions.
  - External Dependency: Coinbase API; uses `requests` + HMAC auth.
- **Strategy Layer**
  - Role: Transform triggers into `TradeProposal` objects with conviction/size metadata.
  - Dependencies: Universe snapshot, triggers, policy thresholds.
- **Risk Layer**
  - Role: Validate proposals against exposure, cooldowns, liquidity requirements.
  - Data Inputs: Portfolio state, policy config, proposals.
- **Execution Layer**
  - Role: Capital allocation, quote selection, order placement, auto-convert for stables, open-order maintenance.
  - Dependencies: Exchange adapter, policy, state store (indirect via runner).
- **Universe/Triggers**
  - Role: Asset discovery (dynamic tiers) and signal generation.
  - Dependencies: Exchange metadata queries, policy thresholds.
- **Infrastructure**
  - `infra/state_store`: Persist portfolio counters.
  - `core/audit_log`: Structured log for compliance.

## 3. Data Flow
1. Runner loads configs and instantiates modules.
2. Universe manager fetches tradable pairs and filters by tiers + policy.
3. Trigger engine evaluates price/volume changes → emits triggers.
4. Rules engine maps triggers to proposals (side, symbol, size_pct, confidence).
5. Risk engine filters proposals ⇒ approved list.
6. Execution engine adjusts for capital, locates trading pair, places orders.
7. State store updates counters; audit logger records cycle.

## 4. Configuration Hierarchy
- **app.yaml**: Global mode (DRY_RUN/PAPER/LIVE), logging, exchange read_only.
- **policy.yaml**: Risk, strategy sizing, liquidity, execution parameters.
- **universe.yaml**: Static tier overrides, exclusions, custom allocations.
- **Environment Vars**: API keys (COINBASE_API_KEY/SECRET), logging overrides.

## 5. Key Interfaces (Phase 0 Target)
- `ExchangeAdapter`: upcoming abstraction for Coinbase & future connectors.
- `Strategy`: eventual base class to standardize proposal generation.
- `RiskManager`: should expose `check_all(proposals, portfolio, regime)` contract.
- `ExecutionEngine`: already close to final interface; will adapt to abstract exchange.

## 6. Cross-Cutting Concerns
- **Logging**: Python logging configured in runner with File + Stream handlers.
- **Error Handling**: Runner catches exceptions per cycle and records NO_TRADE.
- **State Persistence**: JSON file (needs migration to DB in later phases).
- **Testing**: `tests/test_core.py` covers high-level flows; no unit coverage for adapters.

## 7. Extension Points Identified for Later Phases
- Multi-strategy orchestration (Phase 5) requires a scheduler + capital allocator.
- Event-driven execution (Phase 4) will replace tight loop with dispatcher.
- Backtesting (Phase 2) needs historical data abstraction and simulation harness.

## 8. Immediate TODOs (Phase 0 Scope)
- Finalize Phase 0 deliverables: this architecture spec, system audit, baseline ops checklist.
- Document module responsibilities and known tech debt.
- Ensure logging, config validation, and graceful shutdown behavior documented for operators.
