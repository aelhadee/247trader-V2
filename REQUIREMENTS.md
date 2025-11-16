# Requirements TODO - Profitability & Production Validation

**Purpose:** Conditions that must be TRUE before trusting the system with real money.

**These are validation gates, not implementation tasks.** Each requirement should be verified and documented before proceeding to the next stage of capital deployment.

---

## A. Strategy & Profitability Requirements (P0–P1)

**Goal:** Prove the system can generate positive edge after all costs.

### Backtest Validation

- [ ] **Backtest edge requirement** (P0)
  - **Requirement:** Net PnL **after fees and slippage** > 0
  - **Tested over:** Multiple market regimes (bull, chop, bear)
  - **Target metrics:**
    - Sharpe ratio > 1.0
    - Max drawdown < 15% of starting capital
    - Win rate > 45% (or avg winner > 2x avg loser if lower)
  - **Validation:** Run backtests on 2024 Q3-Q4 data (different regimes)
  - **Evidence:** Backtest reports saved in `backtest/results/`
  - **Status:** ⏳ Pending MockExchange completion

- [ ] **Consistency requirement** (P0)
  - **Requirement:** Positive net PnL in at least **3 distinct time windows**
  - **Examples:** 
    - Sep 2024 (bull), Oct 2024 (chop), Nov 2024 (correction)
    - Different market conditions, not cherry-picked
  - **Why:** Ensures strategy isn't overfitted to one regime
  - **Validation:** Separate backtest runs for each window
  - **Status:** ⏳ Pending

- [ ] **Signal diversity requirement** (P1)
  - **Requirement:** No more than 40% of PnL from:
    - Single symbol (avoid "just riding XRP/SOL")
    - Single signal type (avoid one-trick pony)
  - **Validation:** PnL attribution analysis
  - **Status:** ⏳ Pending analytics module

- [ ] **Cost realism validation** (P0)
  - **Requirement:** Backtest uses:
    - Actual Coinbase fee tiers (or conservative estimates)
    - Realistic spreads from historical data
    - Slippage based on orderbook depth (if available)
  - **Not acceptable:** Zero-fee, zero-slippage backtests
  - **Validation:** Cost model review + backtest logs
  - **Status:** ⏳ Pending cost model implementation

---

## B. Risk & Exposure Requirements (P0)

**Goal:** Hard limits that prevent catastrophic loss, enforced in code.

### Position & Exposure Limits

- [ ] **Per-symbol exposure cap enforcement** (P0)
  - **Requirement:** `max_position_size_pct` never exceeded
  - **Example:** Tier 1 = 5-8% of NAV per symbol
  - **Validated in:**
    - Backtest logs (assert no violations)
    - Live trading (monitored in real-time)
  - **Test:** Unit tests for RiskEngine position sizing
  - **Status:** ✅ Implemented in `core/risk.py`, needs comprehensive testing

- [ ] **Total exposure cap enforcement** (P0)
  - **Requirement:** Total net exposure across all symbols ≤ `max_total_at_risk_pct`
  - **Example:** Sum of all positions ≤ 50-70% of NAV
  - **Includes:** Open positions + pending BUY orders
  - **Validated in:** Backtest + live monitoring
  - **Status:** ✅ Implemented, needs validation

- [ ] **No leverage / margin** (P0)
  - **Requirement:** Bot NEVER:
    - Borrows funds
    - Uses margin products
    - Opens leveraged positions
  - **Verification:**
    - Only spot trading on Coinbase
    - Balance checks before each trade
    - No negative balance possible
  - **Status:** ✅ Enforced by design (spot-only)

- [ ] **Drawdown guardrails** (P0)
  - **Requirement:** Stop new trading if:
    - Daily drawdown > 5% of NAV
    - Weekly drawdown > 10% of NAV
  - **Behavior:** 
    - Block new entries
    - Allow exits only
    - Alert operator
  - **Validated in:** Backtest scenarios + live circuit breakers
  - **Status:** ✅ Implemented as circuit breakers, needs drawdown-specific tests

---

## C. Execution & Cost Requirements (P1)

**Goal:** Ensure execution quality matches backtest assumptions.

### Maker vs Taker Balance

- [ ] **Maker ratio requirement** (P1)
  - **Requirement:** ≥ 70% of trades filled as maker (post-only)
  - **Why:** Strategy profitability assumes low fees (40 bps vs 60 bps)
  - **If violated:** Strategy may not be profitable in live trading
  - **Validation:** Track in StateStore, report in analytics
  - **Status:** ⏳ Pending analytics module

- [ ] **Slippage monitoring** (P1)
  - **Requirement:** Average slippage ≤ X bps (set based on backtest)
  - **Measured:** Execution price vs mid-price at decision time
  - **Validation:** Compare live vs backtest slippage
  - **Alert if:** Live slippage consistently > backtest assumptions
  - **Status:** ⏳ Pending slippage tracking in ExecutionEngine

- [ ] **Fill rate requirement** (P1)
  - **Requirement:** ≥ 85% of orders filled (not canceled unfilled)
  - **Low fill rate indicates:**
    - Aggressive post-only pricing
    - Missing alpha (orders sit too far from market)
  - **Validation:** Track orders_filled / orders_placed
  - **Status:** ⏳ Pending Prometheus metrics

---

## D. Trade Pacing & Churn Requirements (P0–P1)

**Goal:** Prevent over-trading that eats profitability via fees.

### Trade Frequency Limits

- [ ] **Max trades per symbol per day** (P0)
  - **Requirement:** ≤ 8 trades per symbol per day
  - **Why:** More = likely fee bleed, not edge
  - **Enforced by:** `per_symbol_cooldown` in RiskEngine
  - **Validated in:** Backtest logs + live monitoring
  - **Status:** ⏳ Needs testing

- [ ] **Average hold time requirement** (P1)
  - **Requirement:** Median hold time > 30 minutes
  - **Why:** Day trader, not scalper
  - **Indicates:** Positions given time to work
  - **Validation:** Histogram of hold times in analytics
  - **Status:** ⏳ Pending analytics

- [ ] **Global trade spacing** (P0)
  - **Requirement:** Minimum X seconds between ANY trades
  - **Example:** ≥ 60 seconds prevents burst trading
  - **Enforced by:** `min_seconds_between_trades` in risk config
  - **Status:** ⏳ Needs implementation

---

## E. Monitoring & Ops Requirements (P1)

**Goal:** Visibility and control during live operation.

### Observability

- [ ] **Real-time metrics dashboard** (P1)
  - **Requirement:** Dashboard showing:
    - Account value, daily PnL
    - Open positions, pending orders
    - Exposure %, risk utilization
    - Trade count today
    - Circuit breaker status
  - **Tool:** Grafana (already integrated)
  - **Status:** ✅ Infrastructure ready, needs dashboard refinement

- [ ] **Alerting on critical events** (P1)
  - **Requirement:** Alerts for:
    - Circuit breaker triggered
    - Daily stop loss hit
    - Data staleness > 5 minutes
    - No trades for 4+ hours (when signals active)
    - Trade rate spike (> 2x expected)
  - **Channel:** Email + Slack via AlertService
  - **Status:** ⏳ Partial (AlertService exists, needs event wiring)

- [ ] **Kill switch accessibility** (P0)
  - **Requirement:** Can stop trading in < 10 seconds
  - **Mechanism:** `touch data/KILL_SWITCH` (already implemented)
  - **Verified:** Tested in rehearsal
  - **Status:** ✅ Implemented and tested

---

## F. Validation & Confidence Requirements (P0–P1)

**Goal:** Build confidence through progressive validation before scaling capital.

### Pre-PAPER Requirements

- [ ] **All P0 architecture items complete** (P0)
  - MockExchange, cost model, trade pacing, tests
  - See `ARCHITECTURE_TODO.md` Phase 1
  - **Status:** ⏳ In progress

- [ ] **Positive backtest across 3+ regimes** (P0)
  - Net PnL > 0 after fees in each regime
  - Sharpe > 1.0, drawdown acceptable
  - **Status:** ⏳ Pending

### PAPER Mode Requirements (before real money)

- [ ] **1 week of PAPER mode validation** (P0)
  - **Requirement:** Bot runs in PAPER mode successfully for 1 week
  - **Validates:**
    - No crashes or rule violations
    - Execution logic works (simulated fills)
    - Risk limits enforced
    - Monitoring functional
  - **Status:** ⏳ Ready to start after backtest validation

- [ ] **PAPER mode profitability** (P1)
  - **Requirement:** PAPER mode shows positive PnL trajectory
  - **Note:** PAPER uses simulated fills, so exact numbers may differ from live
  - **Purpose:** Catch obvious bugs/misconfigurations
  - **Status:** ⏳ Pending

### LIVE Mode Requirements (small capital)

- [ ] **Backtest-to-PAPER correlation** (P1)
  - **Requirement:** PAPER mode results roughly align with backtest expectations
  - **Metric:** PnL, trade count, win rate within ±20% of backtest
  - **If violated:** Investigate before LIVE
  - **Status:** ⏳ Pending

- [ ] **Initial LIVE capital limit** (P0)
  - **Requirement:** Start LIVE mode with ≤ $1000-2000 total capital
  - **Why:** Limit downside while validating live execution
  - **Duration:** At least 2 weeks or 50+ trades
  - **Status:** ⏳ Pending

- [ ] **LIVE validation period** (P0)
  - **Requirement:** Successful LIVE operation for 2-4 weeks before scaling
  - **Success criteria:**
    - Net PnL ≥ 0 (not requiring profitability yet, but not bleeding)
    - No risk violations or crashes
    - Maker ratio ≥ 70%
    - Execution quality matches expectations
  - **Status:** ⏳ Pending

---

## G. Capital Scaling Requirements (P2)

**Goal:** Safe, data-driven approach to increasing capital.

### Scaling Gates

- [ ] **LIVE-to-backtest validation** (P2)
  - **Requirement:** After 1-3 months LIVE, performance doesn't deviate catastrophically from backtest
  - **Metric:** Sharpe, win rate, PnL/trade within reasonable range
  - **If violated:** Don't scale, investigate edge decay
  - **Status:** ⏳ Pending LIVE operation

- [ ] **Stability requirement** (P2)
  - **Requirement:** No unexplained crashes or rule violations in last 30 days
  - **Evidence:** Clean audit logs, no alerts
  - **Status:** ⏳ Pending

- [ ] **Capital scaling policy** (P2)
  - **Policy:** Double capital only after:
    - ✅ 3+ months of positive, stable LIVE results
    - ✅ No major risk incidents (breaches, fat-finger errors)
    - ✅ Sharpe ratio maintained at ≥ 0.8
  - **Maximum scaling:** Never more than 2x per quarter
  - **Status:** ⏳ To be enforced manually

---

## Decision Gates Summary

| Stage | Requirements Must Pass | Minimum Duration | Max Capital |
|-------|----------------------|------------------|-------------|
| **BACKTEST** | All A + B (P0 items) | N/A | $0 (simulation) |
| **PAPER** | Backtest + 1 week clean operation | 1 week | $0 (simulated) |
| **LIVE (initial)** | PAPER validation + no crashes | 2-4 weeks | $1000-2000 |
| **LIVE (scale 1)** | 3 months positive + stability | 3 months | $5000 |
| **LIVE (scale 2)** | 6 months positive + Sharpe maintained | 6 months | $10,000+ |

---

## Checklist: Ready for Each Stage?

### ✅ Ready for BACKTEST when:
- [x] MockExchange implemented
- [x] Cost model integrated
- [ ] Historical data loaded
- [ ] Backtest can run end-to-end

### ✅ Ready for PAPER when:
- [ ] Backtest profitable (Sharpe > 1.0) in 3+ regimes
- [ ] All P0 risk limits tested
- [ ] Monitoring dashboard functional
- [ ] Alert system working

### ✅ Ready for LIVE when:
- [ ] 1 week successful PAPER operation
- [ ] Initial capital limited to $1000-2000
- [ ] Kill switch tested and accessible
- [ ] Emergency contact established
- [ ] Ready to monitor 24/7 initially

### ✅ Ready to SCALE when:
- [ ] 3+ months positive LIVE results
- [ ] Performance matches backtest within tolerances
- [ ] No major incidents or violations
- [ ] Operator confident in system behavior

---

## Current Status: 2025-11-15

**Stage:** Pre-BACKTEST (implementing MockExchange + cost model)

**Blockers:**
1. MockExchange not yet implemented
2. Cost model needs centralization
3. Historical data pipeline needs completion

**Next Milestone:** Complete Phase 1 P0 items from `ARCHITECTURE_TODO.md` (~3 weeks)

**Target Date for First Backtest:** December 2025

**Target Date for PAPER Mode:** January 2026 (if backtest validates)

**Target Date for LIVE Mode:** February 2026 (if PAPER validates)
