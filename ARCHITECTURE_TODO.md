# Architecture TODO - Profitability Validation & Production Readiness

**Purpose:** System and code changes needed to validate profitability and prepare for real money trading.

**Priority Legend:**
- **P0** = Critical blocker (must have before live trading with real capital)
- **P1** = High priority (needed for confidence and risk management)
- **P2** = Important but can be added incrementally

---

## A. Backtesting & Simulation Engine (P0)

**Goal:** Validate strategy profitability with realistic historical simulation before risking real money.

### Backtesting Infrastructure

- [ ] **Add dedicated backtest runner module** (P0)
  - Create `backtest/engine.py` with `BacktestEngine` class
  - Reuses existing pipeline: `Universe → Triggers → Rules → Risk → Execution`
  - Pluggable mock exchange instead of live CoinbaseExchange
  - **Status:** Partial - `backtest/engine.py` exists but needs MockExchange integration
  - **Owner:** Core team
  - **Estimate:** 2-3 days

- [ ] **Implement MockExchange** (P0)
  - Simulates maker/taker fills based on OHLCV and spread
  - Handles partial fills and no-fill scenarios (post_only rejection, liquidity)
  - Realistic `post_only_ttl` behavior (cancel after timeout)
  - Time-based simulation (advance clock, process pending orders)
  - Pluggable in place of `CoinbaseExchange` (same interface)
  - **Location:** `backtest/mock_exchange.py`
  - **Estimate:** 3-4 days

- [ ] **Historical data adapter** (P0)
  - Reads OHLCV (and optionally depth) from CSV/Parquet/DB
  - Provides candles + best bid/ask snapshot per step to backtester
  - Handles missing data and resampling
  - **Location:** `backtest/data_loader.py` (exists, needs enhancement)
  - **Estimate:** 1-2 days

- [ ] **Centralized cost model** (P0)
  - Single source of truth for fees/slippage modeling:
    - Maker fee (e.g. 40 bps)
    - Taker fee (e.g. 60 bps)
    - Spread & slippage estimation based on tier
  - Used by both:
    - Backtest (simulation)
    - Live PnL attribution (reporting)
  - **Location:** `core/cost_model.py`
  - **Estimate:** 1 day

---

## B. Strategy / Signals Layer (P0–P1)

**Goal:** Make signals modular, testable, and regime-aware for better edge identification.

### Signal Architecture

- [ ] **Refactor signals into modular Strategy/Signal classes** (P0)
  - Create `strategy/signals.py` with signal classes:
    - `PriceMoveSignal` (current volume spike logic)
    - `MomentumSignal` (trend continuation)
    - `MeanReversionSignal` (fade extreme moves)
    - Future: `EventDrivenSignal`, `RegimeSignal`
  - Each signal has: `scan()`, `strength()`, `confidence()`
  - **Current:** Logic mixed in `triggers.py`, needs extraction
  - **Estimate:** 2-3 days

- [ ] **Regime-aware behavior** (P1)
  - Encode regime → behavior mapping in `policy.yaml`:
    ```yaml
    regimes:
      trend:
        allowed_signals: [momentum_breakout, trend_follow]
        sizing_multiplier: 1.0
      chop:
        allowed_signals: [mean_reversion, fade_spike]
        sizing_multiplier: 0.5  # reduce size in choppy markets
      bear:
        allowed_signals: []  # cash gang
    ```
  - RulesEngine filters signals based on active regime
  - **Estimate:** 2 days

- [ ] **Plug-in signal registry** (P1)
  - Registry pattern to enable/disable signals via config
  - Example:
    ```python
    SIGNAL_REGISTRY = {
        "momentum_breakout": MomentumSignal,
        "mean_reversion": MeanReversionSignal,
    }
    active_signals = [SIGNAL_REGISTRY[s] for s in config["enabled_signals"]]
    ```
  - Makes A/B testing in backtests easier
  - **Estimate:** 1 day

---

## C. Risk & Trade Pacing Controls (P0)

**Goal:** Prevent over-trading and ensure profitability isn't eaten by fees/slippage from excessive churn.

### Trade Frequency Management

- [ ] **Dedicated trade pacing module** (P0)
  - Create `core/trade_limits.py` handling:
    - `max_trades_per_hour`
    - `max_trades_per_day`
    - `min_seconds_between_trades` (global spacing)
    - `per_symbol_cooldown_seconds`
  - Centralized logic instead of scattered checks
  - **Current:** Partially in RiskEngine, needs extraction
  - **Estimate:** 1-2 days

- [ ] **Global trade spacing logic** (P0)
  - In `RiskEngine`:
    - Maintain `last_trade_ts_global` in StateStore
    - Reject new trades if `now - last_trade_ts_global < min_seconds_between_trades`
    - Reason: `global_trade_spacing`
  - Prevents burst trading that kills edge via fees
  - **Estimate:** 1 day

- [ ] **Enhanced per-symbol cooldowns** (P0)
  - Store `last_trade_ts[symbol]` in StateStore
  - Reject proposals with reason `symbol_cooldown_active` if inside cooldown
  - Different cooldown after:
    - Winning trade (shorter, e.g. 10 min)
    - Losing trade (longer, e.g. 30-60 min)
  - **Current:** Partial implementation exists
  - **Estimate:** 1 day

- [ ] **Make hourly/daily caps last-resort** (P1)
  - Keep existing `max_trades_per_day` counter
  - Design as **hard stop**, not primary pacing mechanism
  - Primary pacing = spacing + cooldowns
  - Daily cap catches pathological behavior
  - **Estimate:** 0.5 days (config adjustment)

---

## D. Metrics, PnL & Analytics (P1 but critical for validation)

**Goal:** Understand where edge comes from, track live vs backtest performance, identify decay.

### Trade Analytics

- [ ] **Comprehensive trade log with PnL attribution** (P1)
  - Persistent log of each trade:
    - Entry/exit time, price, size, fees, slippage
    - Trigger, rule, conviction, tier
    - Pre/post position snapshot, NAV before/after
  - Daily and per-trade PnL decomposed into:
    - **Edge** (price move in your favor)
    - **Fees** (maker/taker costs)
    - **Slippage** (execution vs mid-price)
  - **Location:** Extend `core/audit_log.py` or new `analytics/trade_log.py`
  - **Estimate:** 2-3 days

- [ ] **Analytics module** (P1)
  - Script/notebook that reads logs and produces:
    - Win rate, avg R/return per trade
    - Net PnL before/after fees
    - PnL by signal type, symbol, regime, tier
    - Sharpe ratio, max drawdown
    - Hold time distribution
    - Maker vs taker fill ratio
  - **Location:** `analytics/performance_report.py` or notebook
  - **Estimate:** 2 days

- [ ] **Backtest vs Live comparison dashboard** (P1)
  - Same metrics computed for:
    - Historical backtest runs
    - Live trading logs
  - Side-by-side comparison to detect:
    - Edge decay (live < backtest)
    - Slippage underestimation
    - Fee model inaccuracy
  - Alert if live performance deviates > X% from backtest expectations
  - **Integration:** Grafana panels or Jupyter notebook
  - **Estimate:** 2 days

---

## E. Testing & Safety (P0–P1)

**Goal:** Catch bugs before they cost money. Ensure critical safety features work as designed.

### Test Coverage

- [ ] **Unit tests for RiskEngine critical paths** (P0)
  - Test cases for:
    - `min_notional` enforcement
    - Position size caps (per-symbol, total exposure)
    - Hourly/daily trade limits
    - Trade spacing & cooldown logic
    - Circuit breakers (drawdown, data staleness)
  - Mock StateStore, portfolio snapshots
  - **Target:** 90%+ coverage on risk logic
  - **Estimate:** 2-3 days

- [ ] **Unit tests for ExecutionEngine** (P0)
  - Test cases for:
    - TTL behavior (cancel after timeout)
    - Post-only retries (INVALID_LIMIT_PRICE_POST_ONLY)
    - Slippage rejection
    - Idempotency (duplicate client_order_id)
    - Fee calculation accuracy
  - **Target:** 80%+ coverage on execution logic
  - **Estimate:** 2 days

- [ ] **Unit tests for UniverseManager** (P1)
  - Test cases for:
    - Liquidity filters (volume, spread, depth)
    - Tier assignment logic
    - Dynamic discovery overrides
    - Halal compliance filtering
  - **Estimate:** 1 day

- [ ] **Integration tests: one-cycle E2E** (P0)
  - Fake market snapshot → verify:
    - Correct triggers fired
    - Proposal sizing matches expected
    - Risk accept/reject reasons correct
    - No orders placed when rejected
  - Run in CI on every commit
  - **Current:** `tests/test_core.py` partially covers this
  - **Estimate:** 1-2 days to enhance

- [ ] **Backtest regression tests** (P1)
  - For fixed historical slice (e.g. 2024 Q4):
    - Assert number of trades within expected range
    - Assert total PnL sign (positive/negative)
    - Assert no rule violations (caps, limits breached)
  - Catches inadvertent changes that break profitability
  - **Location:** `tests/test_backtest_regression.py`
  - **Estimate:** 1 day

---

## F. Operational Readiness (P1–P2)

**Goal:** Production-grade infrastructure for monitoring, debugging, and safety.

### Monitoring & Observability

- [ ] **Enhanced health checks** (P1)
  - Endpoint returns:
    - Last cycle time, duration
    - Number of open positions, pending orders
    - Data staleness (OHLCV age)
    - Risk engine state (circuit breakers active?)
  - Alerts when unhealthy
  - **Current:** Basic healthcheck exists at port 8080
  - **Estimate:** 1 day

- [ ] **Anomaly detection alerts** (P1)
  - Alert when:
    - No trades for long periods despite signals (possible bug)
    - Trade rate spikes above expectation (risk of fee bleed)
    - Win rate drops below threshold (edge decay?)
    - Data staleness exceeds limit
  - Integration with AlertService → email/Slack
  - **Estimate:** 1-2 days

- [ ] **Config transparency & audit trail** (P1)
  - All critical knobs in `policy.yaml` (not hardcoded)
  - Config hash logged with each trade
  - Ability to answer: "What were the rules when we made this trade?"
  - **Current:** Config hash already computed in main_loop
  - **Estimate:** 0.5 days (ensure consistency)

- [ ] **Trade auditability** (P1)
  - For any live trade, can answer:
    - "Why did we enter?" (trigger, rule, conviction)
    - "Why this size?" (tier, caps, sizing formula)
    - "Why this price & route?" (execution plan, maker vs taker)
  - Stored in audit log with each trade
  - **Current:** Partial - audit log exists but needs enhancement
  - **Estimate:** 1 day

---

## Summary: Architecture Implementation Roadmap

**Phase 1: Validation Foundation (P0 items)**
1. MockExchange + BacktestEngine integration (5-7 days)
2. Cost model centralization (1 day)
3. Trade pacing controls (3-4 days)
4. Critical RiskEngine + ExecutionEngine tests (4-5 days)

**Total Phase 1:** ~3 weeks

**Phase 2: Analytics & Confidence (P1 items)**
1. Signal refactoring (2-3 days)
2. Trade log + PnL attribution (2-3 days)
3. Analytics module (2 days)
4. Backtest vs Live comparison (2 days)
5. Enhanced monitoring (2-3 days)

**Total Phase 2:** ~2 weeks

**Phase 3: Polish & Scale (P2 items)**
- Additional regime logic
- Advanced anomaly detection
- Capital scaling automation

---

## Next Steps

1. Review & prioritize with team
2. Create GitHub issues/tickets for each item
3. Assign owners and start with Phase 1 P0 items
4. Run first full backtest once MockExchange is ready
5. Validate profitability before proceeding to PAPER mode with real capital

**Decision Gate:** Do NOT proceed to LIVE mode with significant capital until:
- ✅ All P0 items complete
- ✅ Backtest shows positive net PnL after fees across multiple regimes
- ✅ At least 1 month of successful PAPER trading matching backtest expectations
