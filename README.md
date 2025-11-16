# 247trader-v2

Clean architecture trading bot for Coinbase Advanced Trade.

**Status**: Production-Ready | 178 Tests Passing | 4/4 Critical Blockers Complete ✅

> **Current Phase:** Safety features complete, ready for PAPER mode validation (see `PRODUCTION_TODO.md` for detailed status)

## Philosophy

- **Rules-first, AI-optional**: System must trade profitably without AI
- **Hard constraints**: Policy.yaml rules cannot be violated by any component
- **Clean separation**: Universe → Triggers → Rules → Risk → Execution
- **Battle-tested patterns**: Inspired by Freqtrade, Jesse, Hummingbot

## Current Status (Nov 15, 2025)

**Phase 1: Core Engine ✅ COMPLETE**
- All 178 tests passing
- 4/4 critical production blockers resolved
- Safety features: Kill-switch, circuit breakers, environment gates, fee-adjusted sizing

**Phase 2: Paper Trading ⏳ READY**
- DRY_RUN mode validated with live data
- PAPER mode implemented (simulated execution)
- Next: 1-week PAPER validation before LIVE

**Phase 3: Live Trading 🔲 PLANNED**
- After PAPER validation passes
- Small position scaling ($50-100 initially)
- See `PRODUCTION_TODO.md` for go/no-go gates

**Documentation:**
- `README.md` - This file (quick start, architecture overview)
- `APP_REQUIREMENTS.md` - Formal specification (34 REQ-* items with SLAs)
- `PRODUCTION_TODO.md` - Current blockers and implementation status

## Architecture

**Core Components:**
- **Universe Manager** (`core/universe.py`) - 3-tier asset filtering with dynamic discovery
- **Trigger Engine** (`core/triggers.py`) - Volume spikes, breakouts, reversals, momentum
- **Rules Engine** (`strategy/rules_engine.py`) - Deterministic trade proposals
- **Risk Engine** (`core/risk.py`) - Hard constraint enforcement (kill-switch, stops, limits)
- **Execution Engine** (`core/execution.py`) - DRY_RUN/PAPER/LIVE modes with fee-aware sizing
- **Exchange Connector** (`core/exchange_coinbase.py`) - JWT + HMAC auth, all Coinbase endpoints
- **Main Loop** (`runner/main_loop.py`) - Universe → Triggers → Rules → Risk → Execution

**Monitoring Stack:**
- **Prometheus** (`infra/prometheus_exporter.py`) - Metrics collection (trades, PnL, risk, system health)
- **Grafana** (port 3000) - Real-time dashboards with 10 operational panels
- **HealthCheck** (port 8080) - HTTP liveness endpoint for container orchestration
- See `docs/MONITORING_SETUP.md` for full setup guide

**Safety Features:**
- ✅ Kill-switch (<3s detection, <10s cancel, <5s alert)
- ✅ Circuit breakers (data staleness, exchange health, slippage violations)
- ✅ Environment gates (DRY_RUN → PAPER → LIVE with read_only validation)
- ✅ Fee-adjusted sizing (maker/taker fees included in min notional checks)
- ✅ Outlier/bad-tick guards (rejects >10% price moves without volume confirmation)

See `PRODUCTION_TODO.md` for detailed implementation status and `APP_REQUIREMENTS.md` for formal specifications.

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

> **Safety First:** `config/app.yaml` now ships with `app.mode=DRY_RUN` and `exchange.read_only=true`. Flip to PAPER or LIVE and disable read-only *only* after the regression tests/backtest gate pass and you've confirmed credentials.

### 1. Install Dependencies

```bash
cd 247trader-v2
pip install -r requirements.txt
```

Key dependencies: `pyyaml`, `requests`, `PyJWT`, `cryptography`

### 2. Configure Coinbase Cloud API

Create a `.env` file:

```bash
# Coinbase Cloud API (recommended - supports JWT authentication)
CB_API_SECRET_FILE=/path/to/your/coinbase_cloud_api_secret.json
```

The JSON file should contain your Coinbase Cloud API credentials:
```json
{
  "name": "organizations/{org_id}/apiKeys/{key_id}",
  "privateKey": "-----BEGIN EC PRIVATE KEY-----\n...\n-----END EC PRIVATE KEY-----"
}
```

**OR** use legacy HMAC authentication:
```bash
export COINBASE_API_KEY="your_legacy_key"
export COINBASE_API_SECRET="your_legacy_secret"
```

**Note**: System auto-detects authentication method. Cloud API (JWT) is recommended for new projects.

### 3. Validate Configuration

```bash
python -m tools.config_check
```

Expected output:

```
✓ config/app.yaml valid
✓ config/policy.yaml valid
✓ config/universe.yaml valid
```

### 4. Run Tests

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

### 5. Run Once (DRY_RUN)

```bash
python runner/main_loop.py --once
```

Expected JSON output with live Coinbase data:
```json
{
  "status": "APPROVED_DRY_RUN",
  "universe_size": 3,
  "triggers_detected": 2,
  "proposals_generated": 2,
  "proposals_approved": 2,
  "base_trades": [
    {
      "symbol": "HBAR-USD",
      "side": "BUY",
      "size_pct": 1.75,
      "confidence": 0.78,
      "reason": "Momentum up (+7.8% in 24h)"
    },
    {
      "symbol": "XRP-USD",
      "side": "BUY",
      "size_pct": 1.75,
      "confidence": 0.72,
      "reason": "Momentum up (+3.5% in 24h)"
    }
  ],
  "no_trade_reason": null
}
```

### 6. Production Launcher (Recommended)

```bash
# DRY_RUN mode (logs only, no execution)
./run_live.sh --dry-run --loop

# PAPER mode (simulated execution for validation)
./run_live.sh --paper --loop

# LIVE mode (real trading - requires confirmation)
./run_live.sh --loop
```

**Safety features:**
- Shows account balance before starting
- LIVE mode requires typing "YES" to confirm
- Reads `interval_minutes` from `config/app.yaml` (default: 0.5 = 30 seconds)
- Timestamped logs to `logs/live_YYYYMMDD_HHMMSS.log`
- Press Ctrl+C to stop gracefully

**OR** run manually:

```bash
# Run every 15 minutes
python runner/main_loop.py --interval 15
```

## Maintenance Utilities

### Rebuild Positions (fills → state)

If the `positions` block inside `data/.state.json` drifts from actual Coinbase fills (for example after restoring a backup or hitting a reconciliation bug), run the documented maintenance script:

```bash
# Preview only (no writes)
python scripts/rebuild_positions.py --hours 72 --state data/.state.json --dry-run

# Persist repaired quantities once satisfied
python scripts/rebuild_positions.py --hours 72 --state data/.state.json
```

The utility replays recent fills in read-only mode, recomputes base units/entry prices, and saves via `StateStore`. See `docs/REBUILD_POSITIONS.md` for the full workflow, safety checklist, and rollback steps.

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

3-tier system with **dynamic discovery**:

- **Tier 1 (Core)**: BTC, ETH, SOL - Always eligible, 5-40% allocation
- **Tier 2 (Rotational)**: Major altcoins - Conditional eligibility, 2-20% allocation
- **Tier 3 (Event-Driven)**: Dynamic - Event-triggered, 1-10% allocation, 72h max hold

**Dynamic Discovery** (optional):
```yaml
universe:
  method: dynamic_discovery  # or static_tiered
  dynamic_config:
    tier1_min_volume_usd: 100_000_000  # $100M+ daily volume
    tier2_min_volume_usd: 20_000_000   # $20M+ daily volume
    tier3_min_volume_usd: 5_000_000    # $5M+ daily volume
```

Automatically fetches all USD pairs from Coinbase and categorizes by 24h volume. Recent test found 11 tier1 assets including high-volume meme coins (BONK, PEPE, PUMP).

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

## Monitoring

Real-time visibility into trading operations via Prometheus + Grafana:

```bash
# Start/restart monitoring stack (stops existing, then starts fresh)
./scripts/start_monitoring.sh

# Works with either Docker or Homebrew installations
# Automatically detects and uses what you have installed

# Access dashboards
# Grafana: http://localhost:3000 (admin/admin)
# Prometheus: http://localhost:9091 (Homebrew) or :9090 (Docker)
# Bot Metrics: http://localhost:9090/metrics (Homebrew) or :8000 (Docker)
```

**Dashboard Panels:**
- Account Value & Daily PnL %
- Open Positions & Exposure Gauge (with thresholds)
- Max Drawdown & Total Trades
- Risk Rejections by Reason
- API Latency (p95) & Circuit Breaker Trips
- Cycle Duration (p95)

**Key Metrics:**
- `trader_account_value_usd` - Portfolio value over time
- `trader_daily_pnl_usd` / `trader_pnl_pct` - Performance tracking
- `trader_exposure_pct` - Current risk exposure
- `trader_risk_rejections_total` - Why trades were blocked
- `trader_circuit_breaker_trips_total` - Safety shutdowns
- `trader_api_latency_seconds` - Exchange response times

See `docs/MONITORING_SETUP.md` for full setup guide, alerts configuration, and PromQL queries.

## Next Steps

See `PRODUCTION_TODO.md` for:
- Current production blockers and resolution status
- Requirements matrix (REQ-* IDs with test coverage)
- Go/No-Go gates for PAPER → LIVE transition

See `APP_REQUIREMENTS.md` for:
- Formal requirements specification (34 REQ-* items)
- SLA/SLO targets (kill-switch, alerts, latency)
- Acceptance criteria per requirement

## Reference Code

See `reference_code/` for patterns borrowed from:

- **Freqtrade**: Config structure, protections, dry-run/backtest/live modes
- **Jesse**: Clean strategy lifecycle
- **Hummingbot**: Robust execution & connectors
- **Coinbase SDK**: Official API integration

## License

MIT
