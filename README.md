# 247trader-v2

Clean architecture trading bot for Coinbase Advanced Trade.

**Status**: Phase 1 - Core skeleton (no AI, DRY_RUN only)

## Philosophy

- **Rules-first, AI-optional**: System must trade profitably without AI
- **Hard constraints**: Policy.yaml rules cannot be violated by any component
- **Clean separation**: Universe → Triggers → Rules → Risk → Execution
- **Battle-tested patterns**: Inspired by Freqtrade, Jesse, Hummingbot

## Structure

```
247trader-v2/
├── config/          # YAML configurations
│   ├── app.yaml     # App settings (mode, logging, etc.)
│   ├── policy.yaml  # Hard risk constraints
│   └── universe.yaml # 3-tier trading universe
├── core/            # Core deterministic modules
│   ├── exchange_coinbase.py  # Exchange connector
│   ├── universe.py           # Universe manager
│   ├── triggers.py           # Signal detection
│   └── risk.py               # Risk engine
├── strategy/        # Trading logic
│   └── rules_engine.py       # Deterministic rules
├── runner/          # Orchestration
│   └── main_loop.py          # Main trading loop
└── tests/           # Integration tests
    └── test_core.py
```

## Quick Start

### 1. Install Dependencies

```bash
cd 247trader-v2
pip install pyyaml
```

### 2. Configure

Edit `config/app.yaml` and set your Coinbase API keys:

```yaml
exchange:
  api_key: "${COINBASE_API_KEY}"
  api_secret: "${COINBASE_API_SECRET}"
```

Or set environment variables:
```bash
export COINBASE_API_KEY="your_key"
export COINBASE_API_SECRET="your_secret"
```

### 3. Run Tests

```bash
python tests/test_core.py
```

Expected output:
```
✅ Config Loading: PASS
✅ Universe Building: PASS - 12 eligible assets
✅ Trigger Scanning: PASS - 3 triggers detected
✅ Rules Engine: PASS - 2 proposals generated
✅ Risk Checks: PASS
✅ Full Cycle: PASS
```

### 4. Run Once

```bash
python runner/main_loop.py --once
```

Expected JSON output:
```json
{
  "status": "APPROVED_DRY_RUN",
  "universe_size": 12,
  "triggers_detected": 3,
  "proposals_generated": 2,
  "proposals_approved": 2,
  "base_trades": [
    {
      "symbol": "SOL-USD",
      "side": "BUY",
      "size_pct": 3.6,
      "confidence": 0.85,
      "reason": "Momentum up (+8.5% in 24h)"
    }
  ],
  "no_trade_reason": null
}
```

### 5. Run Forever

```bash
python runner/main_loop.py --interval 15
```

Runs every 15 minutes in DRY_RUN mode.

## Configuration

### Mode (app.yaml)

```yaml
app:
  mode: "DRY_RUN"  # DRY_RUN | PAPER | LIVE
```

- **DRY_RUN**: No execution, just logs proposals
- **PAPER**: Simulated execution (Phase 2+)
- **LIVE**: Real execution (Phase 5 only!)

### Risk Constraints (policy.yaml)

Hard limits enforced by Risk Engine:

```yaml
risk:
  daily_stop_loss_pct: 2.0      # Max 2% daily loss
  max_position_size_pct: 5.0    # Max 5% per position
  max_trades_per_day: 10        # Max 10 trades/day
  max_drawdown_pct: 10.0        # Max 10% drawdown
```

### Universe (universe.yaml)

3-tier system:

- **Tier 1 (Core)**: BTC, ETH, SOL - Always eligible, 5-40% allocation
- **Tier 2 (Rotational)**: Major altcoins - Conditional eligibility, 2-20% allocation
- **Tier 3 (Event-Driven)**: Dynamic - Event-triggered, 1-10% allocation, 72h max hold

## How It Works

### 1. Universe Filtering

```python
# Load eligible assets based on tier + liquidity filters
universe = universe_mgr.get_universe(regime="chop")

# Result: 12 eligible assets (3 core + 9 rotational)
# Filtered by: volume > $5M, spread < 1%, depth > $10k
```

### 2. Trigger Detection

```python
# Scan for deterministic signals
triggers = trigger_engine.scan(assets, regime="chop")

# Detects:
# - Volume spikes (>1.5x average)
# - Breakouts (new 7d highs)
# - Reversals (bouncing from lows)
# - Momentum (sustained trends)
```

### 3. Rules Engine

```python
# Generate trade proposals from triggers
proposals = rules_engine.propose_trades(universe, triggers, regime)

# Rules:
# - Volume spike + price up → BUY (continuation)
# - Breakout → BUY (momentum)
# - Reversal → BUY (mean reversion)
# - Sizing: Tier 1 = 5%, Tier 2 = 3%, Tier 3 = 1%
```

### 4. Risk Checks

```python
# Apply hard constraints from policy.yaml
result = risk_engine.check_all(proposals, portfolio, regime)

# Checks:
# - Kill switch
# - Daily stop loss
# - Max drawdown
# - Trade frequency
# - Position size limits
# - Cluster exposure
```

### 5. Execution (Phase 5)

```python
# DRY_RUN: Log only
# PAPER: Simulate fills
# LIVE: Execute on Coinbase
```

## Phase Roadmap

### ✅ Phase 1: Core Skeleton (COMPLETE)

**Status**: Complete - All 6/6 tests passing

**What was built**:
- `core/exchange_coinbase.py` - Exchange connector (Coinbase public API)
- `core/universe.py` - Universe manager with 3-tier system
- `core/triggers.py` - Deterministic signal detection (volume spikes, breakouts, reversals, momentum)
- `core/risk.py` - Hard constraint enforcement from policy.yaml
- `strategy/rules_engine.py` - Pure rule-based trading logic
- `runner/main_loop.py` - Main orchestration loop
- `config/*.yaml` - App, policy, and universe configurations
- `tests/test_core.py` - Full integration test suite

**Success criteria** (achieved):
- JSON summaries showing: universe → candidates → base_trades → no_trade_reason ✅
- All modules load and run without errors ✅
- Risk constraints enforced ✅
- No AI dependencies ✅

**Key achievement**: System can detect opportunities and propose trades using only deterministic rules. Foundation is solid.

---

### ✅ Phase 2: Backtest Harness (COMPLETE)

**Status**: Infrastructure complete - Ready for parameter tuning

**What was built**:
- `backtest/engine.py` - Full backtest simulation engine
- `backtest/data_loader.py` - Historical OHLCV fetcher from Coinbase public API
- `backtest/run_backtest.py` - CLI runner with performance metrics
- Updated `config/policy.yaml` with concrete parameters from trading_parameters.md

**Features**:
- Position tracking with stops, targets, max hold times
- Performance metrics: win rate, profit factor, drawdown, consecutive losses
- Daily PnL tracking and trade frequency limits
- Real historical data integration (tested: BTC/ETH Nov 2024)

**Current status**:
- ✅ Backtest infrastructure works end-to-end
- ✅ Loads real Coinbase historical data
- ✅ Risk limits enforced in backtest
- ⏳ 0 trades (triggers not firing - needs parameter tuning)

**Success criteria**:
- [x] Backtest infrastructure complete
- [x] Historical data loading works
- [x] Position tracking with stops/targets
- [x] Metrics calculation
- [ ] Triggers fire in backtest (needs tuning - loosen thresholds)
- [ ] Strategy profitable on at least one market regime
- [ ] Win rate > 40%, profit factor > 1.2

**Next steps for tuning**:
1. Lower trigger thresholds: volume spike 1.5x → 2.0x, momentum 3% → 4-6%
2. Integrate historical data into trigger calculations
3. Add regime detection (bull/chop/bear/crash)
4. Test across different market conditions (bull Oct 2024, bear Sep 2024)

**Usage**:
```bash
# Run backtest
python backtest/run_backtest.py --start 2024-11-01 --end 2024-11-10 --interval 60

# Expected output: trades, win rate, profit factor, verdict
```

---

### 🔲 Phase 3: Add News + M1 (NOT STARTED)

**Goal**: Layer AI on top of deterministic rules. AI can only adjust/veto, never create.

**What to build**:
- `ai/news_fetcher.py` - Fetch news from allowlist sources only
  - Sources: coindesk.com, theblock.co, cointelegraph.com
  - Rate limits: max 60 AI calls/hour
  - Cost controls: max $2/cycle
- `ai/m1_fundamental.py` - GPT-4o fundamental analyst
  - Inputs: Rule proposals + news context
  - Outputs: Adjusted conviction (-0.15 to +0.15), veto flag, reasoning
  - **Hard constraint**: Cannot create symbols outside universe/triggers

**Integration**:
- Trigger fires → Rule proposes trade → M1 reviews with news → Adjust or veto
- M1 can boost conviction if strong catalyst (CEX listing, mainnet launch)
- M1 can cut conviction if ambiguous/stale news

**Success criteria**:
- [ ] News fetcher works (allowlist only, rate limited)
- [ ] M1 can adjust rule proposals by ±15% conviction
- [ ] M1 cannot create trades for assets without triggers
- [ ] Cost per cycle < $2
- [ ] Improved win rate vs rules-only baseline

---

### 🔲 Phase 4: Add M2 & M3 (NOT STARTED)

**Goal**: Full AI governance with strict policy enforcement.

**What to build**:
- `ai/m2_quant.py` - Claude Sonnet quantitative analyst
  - Checks: liquidity, tape health, parabolic moves
  - Vetoes: illiquid (spread > 60bps), overextended (>30% 24h move on thin book)
  - Cannot override policy.yaml constraints
  
- `ai/m3_arbitrator.py` - o3-mini governor
  - Final policy cop: checks all proposals against policy.yaml
  - Requires: combined conviction ≥ 0.6, no risk violations
  - Outputs: JSON with decision + violated_checks list if NO_TRADE
  - **Zero tolerance**: Any policy violation = automatic rejection

**Workflow**:
```
Trigger → Rule (base) → M1 (news) → M2 (tape) → M3 (governor) → Execute
```

**Success criteria**:
- [ ] M2 vetoes illiquid/parabolic moves
- [ ] M3 enforces policy.yaml (cannot be overridden)
- [ ] M3 outputs violated_checks on NO_TRADE
- [ ] Combined conviction threshold works (≥0.6)
- [ ] Cost per cycle < $2 (total for M1+M2+M3)

---

### 🔲 Phase 5: Live Small (NOT STARTED)

**Goal**: Deploy to production with tiny positions. No unexplained behavior = no bugs.

**Sequence**:
1. **DRY_RUN** (current): Log proposals only, no execution
2. **PAPER**: Simulated execution with mock fills
3. **LIVE**: Real execution with $50-$100 positions

**What to build**:
- `core/execution.py` - Order placement and tracking
  - Integrate official `coinbase-advanced-py` SDK
  - Limit orders with post-only flag
  - Partial fill handling
  - Order lifecycle: pending → filled → closed
  
- Enhanced monitoring:
  - Real-time PnL tracking
  - Alert on unexpected behavior (Slack webhook)
  - Daily summary reports

**Safety features**:
- Kill switch file: `data/KILL_SWITCH` halts all trading
- Daily stop loss: -3% NLV
- Max position size: $500 (5% of $10k account)
- Require fill confirmation before moving on

**Success criteria**:
- [ ] Paper trading works (simulated fills)
- [ ] Live execution with tiny positions ($50-$100)
- [ ] No unexplained trades (every decision has clear reasoning)
- [ ] Logs show: trigger → rule → AI → risk → execution path
- [ ] Can run for 1 week without manual intervention
- [ ] Win rate and profit factor match backtest within 10%

**Progression**:
1. Run DRY_RUN for 1 week → validate decisions
2. Run PAPER for 1 week → validate fill simulation
3. Run LIVE tiny ($50-100) for 2 weeks → validate real execution
4. If all good: scale to 1-5% positions, monitor for 1 month
5. If still good: full allocation per policy.yaml

---

## Current Status Summary

| Phase | Status | Key Milestone |
|-------|--------|---------------|
| Phase 1: Core Skeleton | ✅ Complete | 6/6 tests passing, DRY_RUN works |
| Phase 2: Backtest | ✅ Infrastructure done | Runs on real data, needs tuning |
| Phase 3: News + M1 | 🔲 Not started | AI layer (veto/adjust only) |
| Phase 4: M2 + M3 | 🔲 Not started | Full AI governance |
| Phase 5: Live Small | 🔲 Not started | Production deployment |

**Next action**: Either tune Phase 2 parameters (make rules profitable) OR proceed to Phase 3 (add AI layer)

## Success Criteria (Phase 1)

If you run the test suite and see:

```
✅ Config Loading: PASS
✅ Universe Building: PASS
✅ Trigger Scanning: PASS
✅ Rules Engine: PASS
✅ Risk Checks: PASS
✅ Full Cycle: PASS
Total: 6/6 tests passed
```

**Then Phase 1 is complete.** 

The system can:
- Load universe from config
- Detect triggers deterministically
- Generate rule-based trade proposals
- Enforce hard risk constraints
- Output structured JSON summaries

Next step: Phase 2 (backtesting)

## Reference Code

See `reference_code/` for patterns borrowed from:

- **Freqtrade**: Config structure, protections, dry-run/backtest/live modes
- **Jesse**: Clean strategy lifecycle
- **Hummingbot**: Robust execution & connectors
- **Coinbase SDK**: Official API integration

## License

MIT
