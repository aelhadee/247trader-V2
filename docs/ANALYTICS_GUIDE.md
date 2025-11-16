# 247trader-v2 Analytics Guide

**Comprehensive guide to the analytics and performance tracking system**

---

## Overview

The analytics system provides complete trade lifecycle tracking, performance analysis, and automated reporting for production trading operations.

### Core Modules

1. **TradeLimits** - Trade pacing, spacing, and cooldown enforcement
2. **TradeLog** - Complete trade entry/exit logging with PnL attribution
3. **PerformanceReport** - Comprehensive performance metrics and analysis

---

## TradeLimits: Trade Pacing & Cooldowns

**Location:** `core/trade_limits.py`

### Purpose

Enforces trade pacing rules to prevent:
- Overtrading (too many trades too quickly)
- Revenge trading (rapid re-entry after losses)
- Resource exhaustion (API rate limits, liquidity drain)

### Key Features

- **Global spacing**: Minimum time between ANY trades
- **Per-symbol spacing**: Cooldown per specific asset
- **Frequency limits**: Max trades per hour/day
- **Outcome-based cooldowns**: Different cooldowns for wins/losses/stop-losses

### Configuration

In `config/policy.yaml`:

```yaml
risk:
  # Global trade pacing
  min_seconds_between_trades: 180  # 3 minutes between any trades
  per_symbol_trade_spacing_seconds: 900  # 15 minutes per symbol
  
  # Frequency limits
  max_trades_per_hour: 5
  max_trades_per_day: 120
  
  # Differentiated cooldowns by outcome
  per_symbol_cooldown_win_minutes: 10    # Short cooldown after wins
  per_symbol_cooldown_loss_minutes: 60   # Longer after losses
  per_symbol_cooldown_after_stop: 120    # 2 hours after stop-loss
  
  # Consecutive loss protection
  cooldown_after_loss_trades: 3  # Cooldown after N losses
  cooldown_minutes: 60  # Duration of cooldown
```

### Integration

**Initialization:**
```python
from core.trade_limits import TradeLimits
from infra.state_store import StateStore

state_store = StateStore('data/state.json')
trade_limits = TradeLimits(
    config=policy_config,
    state_store=state_store
)
```

**Risk Pipeline Integration:**
```python
# In RiskEngine.check_all()
timing_result = self.trade_limits.check_all(
    proposals=proposals,
    trades_today=portfolio.trades_today,
    trades_this_hour=portfolio.trades_this_hour,
    consecutive_losses=portfolio.consecutive_losses,
    last_loss_time=portfolio.last_loss_time,
    current_time=portfolio.current_time
)

if not timing_result.approved:
    return RiskCheckResult(approved=False, reason=timing_result.reason)

# Filter proposals by per-symbol timing
approved_timing, rejected_timing = self.trade_limits.filter_proposals_by_timing(proposals)
proposals = approved_timing
```

**Cooldown Application:**
```python
# After trade exit (in ExecutionEngine)
outcome = "win" if pnl_net > 0 else "loss"
if exit_reason == "stop_loss":
    outcome = "stop_loss"

risk_engine.trade_limits.apply_cooldown(symbol, outcome=outcome)
```

**Trade Recording:**
```python
# After successful fill (in ExecutionEngine)
risk_engine.trade_limits.record_trade(symbol)
```

### State Persistence

Trade history and cooldowns are persisted in `StateStore`:

```json
{
  "last_trade_time": 1700000000.0,
  "trades_today": 5,
  "trades_this_hour": 2,
  "cooldowns": {
    "BTC-USD": {
      "until": 1700003600.0,
      "reason": "loss",
      "duration_minutes": 60
    }
  },
  "trade_history": [
    {"symbol": "BTC-USD", "timestamp": 1700000000.0},
    {"symbol": "ETH-USD", "timestamp": 1700000180.0}
  ]
}
```

### Best Practices

1. **Start conservative**: Use longer spacing/cooldowns initially
2. **Monitor rejections**: Track `TradeLimits` rejection reasons in audit logs
3. **Tune gradually**: Reduce spacing only after proving profitability
4. **Respect cooldowns**: Never bypass except for emergency liquidations

---

## TradeLog: Trade Lifecycle Tracking

**Location:** `analytics/trade_log.py`

### Purpose

Complete trade lifecycle logging with:
- Entry/exit timing and pricing
- PnL calculation and attribution
- Strategy context (trigger, confidence, regime)
- Execution details (maker/taker, fees, slippage)

### Key Features

- **Dual backend**: CSV (portability) + SQLite (queryability)
- **PnL attribution**: Decompose returns into edge/fees/slippage
- **Automatic calculations**: PnL, hold time, MAE/MFE
- **Query interface**: Filter by symbol, date, outcome, exit reason

### Configuration

```python
from analytics.trade_log import TradeLog

trade_log = TradeLog(
    log_dir="data/trades",
    backend="csv",  # or "sqlite"
    enable_sqlite=True  # Enable both backends
)
```

### Trade Lifecycle

**1. Entry Logging (BUY fills):**
```python
from analytics.trade_log import TradeRecord
from datetime import datetime, timezone

trade_record = TradeRecord(
    trade_id=client_order_id,
    symbol=symbol,
    side="buy",
    entry_time=datetime.now(timezone.utc),
    entry_price=filled_price,
    entry_mid_price=quote["mid"],
    size_quote=filled_value,
    entry_fee=fees,
    entry_is_maker=use_maker,
    trigger_type="momentum",
    confidence=0.75,
    volatility=1.2
)

trade_log.log_entry(trade_record)
```

**2. Exit Logging (SELL fills):**
```python
# Update existing trade record
trade_record.exit_time = datetime.now(timezone.utc)
trade_record.exit_price = exit_price
trade_record.exit_mid_price=quote["mid"]
trade_record.exit_fee = exit_fees
trade_record.exit_is_maker = use_maker
trade_record.exit_reason = "take_profit"  # or "stop_loss", "max_hold", "manual"

# Calculate PnL and attribution
trade_record.calculate_pnl()
trade_record.calculate_attribution()

# Log the completed trade
trade_log.log_exit(trade_record)
```

### PnL Attribution

**What gets calculated:**

```python
trade_record.pnl_gross  # Exit value - entry value
trade_record.pnl_net    # After all fees
trade_record.pnl_pct    # Return percentage

# Attribution breakdown
trade_record.edge_pnl   # Price improvement vs mid
trade_record.fees_total # All fees (entry + exit)
trade_record.slippage   # Adverse price movement
```

**Example:**
```
Entry: $100 @ $50,000 (mid: $50,010)
Exit:  $105 @ $52,500 (mid: $52,490)
Entry fee: $0.40 (maker)
Exit fee: $0.63 (taker)

pnl_gross = $105 - $100 = $5.00
pnl_net = $5.00 - $0.40 - $0.63 = $3.97
pnl_pct = ($3.97 / $100) * 100 = 3.97%

edge_pnl = ($50,000 - $50,010) + ($52,500 - $52,490) = -$10 + $10 = $0
fees_total = $0.40 + $0.63 = $1.03
slippage = negligible (tight entry/exit vs mid)
```

### Querying Trades

**CSV Backend:**
```python
# Trades are logged to: data/trades/YYYYMMDD.csv
import pandas as pd

df = pd.read_csv('data/trades/20241115.csv')
winning_trades = df[df['pnl_pct'] > 0]
stop_losses = df[df['exit_reason'] == 'stop_loss']
```

**SQLite Backend:**
```python
trade_log.query_trades(
    start_date=datetime(2024, 11, 1),
    end_date=datetime(2024, 11, 15),
    symbol="BTC-USD",
    min_pnl_pct=5.0
)
```

### Best Practices

1. **Log immediately**: Call `log_entry()` right after fill confirmation
2. **Track open trades**: Maintain `open_trades` dict for exit matching
3. **Enrich context**: Add trigger type, confidence, regime when available
4. **Backup regularly**: CSV files are append-only and portable
5. **Query for insights**: Use SQLite for performance analysis

---

## PerformanceReport: Comprehensive Analytics

**Location:** `analytics/performance_report.py`

### Purpose

Generate comprehensive performance reports with:
- Returns metrics (PnL, win rate, profit factor)
- Risk metrics (Sharpe, max drawdown, volatility)
- Execution metrics (maker ratio, slippage, fees)
- Strategy breakdown (per-signal, per-regime, per-symbol)

### Key Features

- **Multi-timeframe**: Daily, weekly, monthly, custom periods
- **Benchmark comparison**: vs BTC buy-and-hold
- **Risk-adjusted returns**: Sharpe, Sortino, Calmar ratios
- **Output formats**: JSON (machine), Markdown (human), CSV (spreadsheet)

### Configuration

```python
from analytics.performance_report import ReportGenerator

report_generator = ReportGenerator(trade_log=trade_log)
```

### Daily Reports

**Automatic Generation (integrated in TradingLoop):**
```python
# In runner/main_loop.py
# Report generated daily at 23:50-23:59 UTC
current_date = cycle_end.date()
current_hour = cycle_end.hour
current_minute = cycle_end.minute

if current_hour == 23 and current_minute >= 50:
    if self._last_report_date != current_date:
        logger.info("📊 Generating daily performance report...")
        report_file = self.report_generator.generate_daily_report()
        self._last_report_date = current_date
        logger.info(f"✅ Daily report saved: {report_file}")
```

**Manual Generation:**
```python
report_file = report_generator.generate_daily_report(output_dir="reports")
# Saves to: reports/daily_YYYYMMDD.json
```

### Custom Period Reports

```python
from datetime import datetime

metrics = report_generator.analyzer.analyze(
    start_date=datetime(2024, 11, 1),
    end_date=datetime(2024, 11, 15)
)

print(f"Total Return: {metrics.return_pct_total:.2f}%")
print(f"Win Rate: {metrics.win_rate_pct:.1f}%")
print(f"Sharpe Ratio: {metrics.sharpe_ratio:.2f}")
print(f"Max Drawdown: {metrics.max_drawdown_pct:.2f}%")
print(f"Profit Factor: {metrics.profit_factor:.2f}")
```

### Key Metrics Explained

**Returns:**
- `pnl_total`: Total profit/loss in quote currency
- `return_pct_total`: Total return percentage
- `return_pct_annualized`: Annualized return (extrapolated)
- `win_rate_pct`: Percentage of winning trades

**Risk:**
- `sharpe_ratio`: Risk-adjusted return (return / volatility)
- `max_drawdown_pct`: Largest peak-to-trough decline
- `volatility_pct`: Standard deviation of returns
- `mae_avg_pct`: Average Maximum Adverse Excursion
- `mfe_avg_pct`: Average Maximum Favorable Excursion

**Execution:**
- `maker_fill_rate`: Percentage of maker fills
- `avg_slippage_bps`: Average slippage in basis points
- `fee_impact_pct`: Fees as % of total PnL

**Efficiency:**
- `profit_factor`: Gross profits / gross losses
- `avg_win / avg_loss`: Average winning/losing trade size
- `avg_hold_hours`: Average trade duration
- `win_loss_ratio`: Average win size / average loss size

### Report Formats

**JSON (machine-readable):**
```json
{
  "start_date": "2024-11-01T00:00:00Z",
  "end_date": "2024-11-15T23:59:59Z",
  "total_trades": 45,
  "win_rate_pct": 62.2,
  "pnl_total": 347.82,
  "return_pct_total": 3.48,
  "sharpe_ratio": 1.85,
  "max_drawdown_pct": -2.34,
  "profit_factor": 2.15
}
```

**Markdown (human-readable):**
```markdown
# Performance Report: 2024-11-01 to 2024-11-15

## Summary
- Total Trades: 45
- Win Rate: 62.2%
- Total PnL: $347.82
- Return: +3.48%

## Risk Metrics
- Sharpe Ratio: 1.85
- Max Drawdown: -2.34%
- Volatility: 12.3%

## Top Performers
1. BTC-USD: +5.2% (3 trades)
2. ETH-USD: +3.8% (4 trades)
3. SOL-USD: +2.1% (2 trades)
```

### Best Practices

1. **Review daily**: Check daily reports for performance drift
2. **Compare periods**: Track week-over-week and month-over-month trends
3. **Watch drawdown**: Alert if max DD exceeds policy threshold
4. **Analyze losses**: Use loss breakdown to identify systematic issues
5. **Track Sharpe**: Aim for > 1.5 for risk-adjusted profitability

---

## Integration Example: Complete Workflow

```python
from core.trade_limits import TradeLimits
from analytics.trade_log import TradeLog, TradeRecord
from analytics.performance_report import ReportGenerator
from infra.state_store import StateStore
import yaml

# 1. Load config
with open('config/policy.yaml') as f:
    policy = yaml.safe_load(f)

# 2. Initialize state store
state_store = StateStore('data/state.json')

# 3. Initialize TradeLimits
trade_limits = TradeLimits(config=policy, state_store=state_store)

# 4. Initialize TradeLog
trade_log = TradeLog(
    log_dir="data/trades",
    backend="csv",
    enable_sqlite=True
)

# 5. Initialize ReportGenerator
report_generator = ReportGenerator(trade_log=trade_log)

# 6. Trading cycle with full analytics

## Check trade pacing before proposing
timing_result = trade_limits.check_all(
    proposals=proposals,
    trades_today=portfolio.trades_today,
    trades_this_hour=portfolio.trades_this_hour,
    consecutive_losses=portfolio.consecutive_losses,
    last_loss_time=portfolio.last_loss_time,
    current_time=datetime.now(timezone.utc)
)

if timing_result.approved:
    ## Execute trade
    result = executor.execute(symbol="BTC-USD", side="buy", size_usd=300.0)
    
    if result.success:
        ## Log entry
        trade_record = TradeRecord(
            trade_id=result.order_id,
            symbol="BTC-USD",
            side="buy",
            entry_time=datetime.now(timezone.utc),
            entry_price=result.filled_price,
            entry_mid_price=quote["mid"],
            size_quote=result.filled_size * result.filled_price,
            entry_fee=result.fees,
            entry_is_maker=True,
            confidence=0.72
        )
        trade_log.log_entry(trade_record)
        
        ## Record trade for spacing
        trade_limits.record_trade("BTC-USD")

# 7. On trade exit
if exit_condition_met:
    ## Execute exit
    exit_result = executor.execute(symbol="BTC-USD", side="sell", size_usd=value, exit_reason="take_profit")
    
    if exit_result.success:
        ## Update trade record
        trade_record.exit_time = datetime.now(timezone.utc)
        trade_record.exit_price = exit_result.filled_price
        trade_record.exit_fee = exit_result.fees
        trade_record.exit_reason = "take_profit"
        
        ## Calculate PnL
        trade_record.calculate_pnl()
        trade_record.calculate_attribution()
        
        ## Log exit
        trade_log.log_exit(trade_record)
        
        ## Apply cooldown
        outcome = "win" if trade_record.pnl_net > 0 else "loss"
        trade_limits.apply_cooldown("BTC-USD", outcome=outcome)

# 8. Generate daily report (end of day)
if hour == 23 and minute >= 50:
    report_file = report_generator.generate_daily_report()
    print(f"Daily report: {report_file}")
```

---

## Monitoring & Alerts

### Key Metrics to Track

**Trade Pacing:**
- `TradeLimits` rejection rate
- Average time between trades
- Cooldown trigger frequency
- Consecutive loss streaks

**Execution Quality:**
- Maker fill rate (target: > 70%)
- Average slippage (target: < 30bps)
- Fee drag (target: < 2% of PnL)
- Order cancellation rate

**Performance:**
- Daily PnL and return %
- Win rate (target: > 55%)
- Profit factor (target: > 1.5)
- Max drawdown (limit: < 10%)
- Sharpe ratio (target: > 1.5)

### Alert Thresholds

```yaml
alerts:
  trade_pacing:
    max_rejections_per_hour: 5
    min_time_between_trades: 180  # seconds
  
  performance:
    min_win_rate_pct: 50.0
    max_drawdown_pct: 10.0
    min_sharpe_ratio: 1.0
  
  execution:
    max_avg_slippage_bps: 50
    min_maker_fill_rate_pct: 60.0
```

---

## Troubleshooting

### Issue: TradeLimits always rejecting

**Check:**
1. State store has valid timestamps
2. Cooldowns are clearing (check state JSON)
3. Spacing config not too aggressive
4. Daily/hourly counters resetting properly

**Fix:**
```python
# Reset cooldowns manually (emergency only)
state = state_store.load()
state['cooldowns'] = {}
state['last_trade_time'] = 0
state_store.save(state)
```

### Issue: TradeLog entries not appearing

**Check:**
1. `log_dir` exists and writable
2. `log_entry()` called after successful fills
3. CSV files not corrupted
4. SQLite database not locked

**Fix:**
```bash
# Check directory permissions
ls -la data/trades/

# Verify CSV format
head -5 data/trades/20241115.csv

# Check SQLite
sqlite3 data/trades/trades.db "SELECT COUNT(*) FROM trades;"
```

### Issue: Report generation fails

**Check:**
1. TradeLog has data for requested period
2. All required trade fields populated
3. PnL calculations completed
4. Output directory writable

**Fix:**
```python
# Query trades directly
trades = trade_log.query_trades(
    start_date=datetime(2024, 11, 1),
    end_date=datetime(2024, 11, 15)
)
print(f"Found {len(trades)} trades")

# Check for incomplete trades
incomplete = [t for t in trades if t.exit_time is None]
print(f"Incomplete trades: {len(incomplete)}")
```

---

## Production Checklist

Before going live with analytics:

- [ ] TradeLimits config validated (reasonable spacing/cooldowns)
- [ ] TradeLog directory exists and writable
- [ ] SQLite backend initialized (if enabled)
- [ ] ReportGenerator tested with sample data
- [ ] Daily report schedule configured (23:50-23:59 UTC)
- [ ] Monitoring alerts configured
- [ ] Backup strategy for trade logs
- [ ] State store persistence verified
- [ ] Cooldown reset logic tested
- [ ] PnL calculations validated against manual checks

---

## References

- **TradeLimits**: `core/trade_limits.py`
- **TradeLog**: `analytics/trade_log.py`
- **PerformanceReport**: `analytics/performance_report.py`
- **Tests**: `tests/test_trade_limits.py`, `tests/test_trade_log.py`
- **Config**: `config/policy.yaml` (risk section)
- **State**: `data/state.json` (cooldowns, trade history)
- **Logs**: `data/trades/*.csv`, `data/trades/trades.db`
- **Reports**: `reports/daily_*.json`

---

**Last Updated:** 2024-11-16  
**Version:** 1.0.0
