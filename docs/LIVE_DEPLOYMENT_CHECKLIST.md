# LIVE Deployment Checklist 🚀

**Purpose:** Systematic verification before enabling real capital trading  
**Audience:** DevOps/Engineering deploying to production  
**Last Updated:** 2025-11-15

---

## Phase 1: Post-Rehearsal Validation ✅

### 1.1 Run Analysis Script
```bash
./scripts/analyze_rehearsal.sh
```

**Expected Output:**
- ✅ GO for LIVE Deployment
- All 4 success criteria passed
- Report saved to `logs/paper_rehearsal_final_report.md`

**If NO-GO:**
- [ ] Review failure reasons in report
- [ ] Fix issues identified
- [ ] Re-run 24-hour PAPER rehearsal
- [ ] DO NOT proceed to LIVE

### 1.2 Review Success Criteria

Must ALL pass:
- [ ] **Zero unhandled exceptions** (check logs/live_*.log)
- [ ] **Config hash constant** (single hash throughout 1,440 cycles)
- [ ] **Cycle completion rate >95%** (≥1,368 cycles)
- [ ] **Memory <500MB** (check final memory usage)

### 1.3 Review Operational Metrics

Check `logs/paper_rehearsal_final_report.md` for:
- [ ] Average cycle latency <30s (or acceptable for chosen interval)
- [ ] P95 latency <60s
- [ ] Status distribution makes sense (NO_TRADE reasons valid)
- [ ] No unexpected circuit breaker trips
- [ ] Alert system functioning (test alerts received)

---

## Phase 2: Pre-LIVE Configuration 🔧

### 2.1 Update config/app.yaml

```bash
# Backup current config
cp config/app.yaml config/app.yaml.pre_live_backup

# Edit config/app.yaml
code config/app.yaml
```

**Required Changes:**
- [ ] `mode: "LIVE"` (was "PAPER")
- [ ] `exchange.read_only: false` (enable order submission)
- [ ] `loop.interval_minutes: 1` (or desired interval)

**Verify:**
```bash
grep "mode:" config/app.yaml | grep "LIVE"
grep "read_only:" config/app.yaml | grep "false"
```

### 2.2 Verify Policy Configuration

Check `config/policy.yaml` for production-ready values:

```bash
# Conservative defaults should be:
grep -A 5 "risk:" config/policy.yaml
```

**Must Have:**
- [ ] `max_position_size_usd: 50-100` (small for initial deployment)
- [ ] `max_total_exposure_usd: 100-500` (total capital limit)
- [ ] `stop_loss_pct: 2.0-5.0` (reasonable stop loss)
- [ ] `allow_pyramiding: false` (no pyramiding initially)
- [ ] `max_open_orders: 3-5` (limit concurrent positions)

### 2.3 Verify Analytics Configuration

Check trade pacing, logging, and reporting setup:

```bash
# Validate TradeLimits configuration
python -c "
from core.trade_limits import TradeLimits
from infra.state_store import StateStore
import yaml

with open('config/policy.yaml') as f:
    policy = yaml.safe_load(f)

try:
    state_store = StateStore('data/state.json')
    trade_limits = TradeLimits(config=policy['risk'], state_store=state_store)
    print('✅ TradeLimits configuration valid')
    print(f'   Global spacing: {trade_limits.min_global_spacing_sec}s')
    print(f'   Per-symbol spacing: {trade_limits.per_symbol_spacing_sec}s')
    print(f'   Max per hour: {trade_limits.max_trades_per_hour}')
    print(f'   Max per day: {trade_limits.max_trades_per_day}')
except ValueError as e:
    print(f'❌ TradeLimits configuration invalid: {e}')
    exit(1)
"
```

**Analytics Checklist:**
- [ ] **TradeLimits config validated** (passes validation checks)
- [ ] **Trade spacing configured** (min 3min global, 15min per-symbol)
- [ ] **Cooldowns enabled** (10min win, 60min loss, 120min stop-loss)
- [ ] **Daily limit reasonable** (>= hourly limit × 24)
- [ ] **TradeLog directory exists** (`mkdir -p data/trades`)
- [ ] **SQLite backend enabled** (for queryable trade history)
- [ ] **Daily reports scheduled** (23:50-23:59 UTC window)

**Verify Trade Logging:**
```bash
# Ensure trade log directory exists
mkdir -p data/trades

# Check permissions
if [ -w data/trades ]; then
    echo "✅ Trade log directory writable"
else
    echo "❌ Trade log directory not writable"
fi

# Check for existing trade logs
if [ -f data/trades/trades.db ]; then
    echo "✅ SQLite database exists"
    sqlite3 data/trades/trades.db "SELECT COUNT(*) FROM trades;" 2>/dev/null && echo "   Database accessible" || echo "⚠️  Database may be corrupted"
else
    echo "ℹ️  SQLite database will be created on first trade"
fi
```

### 2.3 Generate New Config Hash

```bash
# Will generate new hash for LIVE mode
python -c "from tools.config_validator import validate_all_configs; validate_all_configs('config')"
```

**Expected:**
- [ ] All validations pass (schema + sanity checks)
- [ ] New config hash generated (different from PAPER hash)
- [ ] Save hash for audit tracking

---

## Phase 3: Coinbase Account Verification 💰

### 3.1 Check Account Balance

```bash
# Read-only check (safe)
python -c "
from core.exchange_coinbase import CoinbaseExchangeClient
import yaml

config = yaml.safe_load(open('config/app.yaml'))
exchange = CoinbaseExchangeClient(config['exchange'])
balances = exchange.fetch_balances()
total_usd = sum(b.get('usdValue', 0) for b in balances.values())
print(f'Total Account Value: \${total_usd:.2f} USD')

# Show balances
for asset, bal in balances.items():
    if bal.get('total', 0) > 0:
        print(f'{asset}: {bal[\"total\"]} (≈\${bal.get(\"usdValue\", 0):.2f})')
"
```

**Requirements:**
- [ ] Minimum $100 USD equivalent available
- [ ] Recommended $100-$500 for initial deployment
- [ ] Funds in preferred quote currencies (USDC, USD, USDT)

### 3.2 Verify API Permissions

```bash
# Check API key permissions
python -c "
from core.exchange_coinbase import CoinbaseExchangeClient
import yaml

config = yaml.safe_load(open('config/app.yaml'))
exchange = CoinbaseExchangeClient(config['exchange'])

# Test read access
try:
    exchange.fetch_balances()
    print('✅ Read access: OK')
except Exception as e:
    print(f'❌ Read access: FAILED - {e}')

# Test write access (if read_only=false)
if not config['exchange'].get('read_only', True):
    print('⚠️  Write access enabled - orders will be LIVE')
else:
    print('✅ Write access disabled (read_only mode)')
"
```

**Must Pass:**
- [ ] Read access working (fetch_balances succeeds)
- [ ] API key has trading permissions
- [ ] Wallet allows buy/sell operations

---

## Phase 4: Safety Mechanisms ⚠️

### 4.1 Verify Kill Switch

```bash
# Ensure kill switch file does NOT exist
if [ -f data/KILL_SWITCH ]; then
    echo "❌ Kill switch present - REMOVE before starting"
    rm data/KILL_SWITCH
else
    echo "✅ No kill switch present"
fi
```

**Verify:**
- [ ] `data/KILL_SWITCH` does NOT exist
- [ ] Know how to activate: `touch data/KILL_SWITCH`

### 4.2 Test Alert System

```bash
# Send test alert
python -c "
from infra.alert_service import create_alert_service
import yaml

config = yaml.safe_load(open('config/app.yaml'))
alert_service = create_alert_service(config.get('alerts', {}))

if alert_service:
    alert_service.send_alert(
        alert_type='info',
        message='🚀 LIVE deployment test alert',
        severity='info',
        details={'test': True}
    )
    print('✅ Test alert sent')
else:
    print('⚠️  Alert service not configured')
"
```

**Verify:**
- [ ] Test alert received (email/Slack/webhook)
- [ ] Alerts configured for critical events (exceptions, circuit breakers, fills)

### 4.3 Emergency Stop Procedures

**Document:**
- [ ] Kill switch activation: `touch data/KILL_SWITCH` (stops new trades immediately)
- [ ] Graceful shutdown: `kill -2 $(cat data/247trader-v2.pid)` (completes cycle then stops)
- [ ] Force stop: `kill -9 $(cat data/247trader-v2.pid)` (immediate termination)
- [ ] Emergency liquidation: `python liquidate_to_usdc.py` (closes all positions)

---

## Phase 5: LIVE Deployment 🚀

### 5.1 Final Pre-Flight Checks

```bash
# Run comprehensive validation
./tools/preflight_check.sh  # If exists
# OR manual checks:

echo "=== Pre-Flight Checklist ==="
echo ""

# 1. Mode check
MODE=$(grep "mode:" config/app.yaml | awk '{print $2}' | tr -d '"')
if [ "$MODE" = "LIVE" ]; then
    echo "✅ Mode: LIVE"
else
    echo "❌ Mode: $MODE (expected LIVE)"
fi

# 2. Read-only check
READONLY=$(grep "read_only:" config/app.yaml | awk '{print $2}')
if [ "$READONLY" = "false" ]; then
    echo "✅ Read-only: false (write enabled)"
else
    echo "❌ Read-only: $READONLY (expected false)"
fi

# 3. Kill switch check
if [ ! -f data/KILL_SWITCH ]; then
    echo "✅ Kill switch: absent"
else
    echo "❌ Kill switch: PRESENT (remove before starting)"
fi

# 4. Config validation
python -c "from tools.config_validator import validate_all_configs; validate_all_configs('config')" && echo "✅ Config: valid" || echo "❌ Config: INVALID"

echo ""
```

**All Must Pass:**
- [ ] Mode = LIVE
- [ ] Read-only = false
- [ ] Kill switch absent
- [ ] Config validated

**Analytics Pre-Flight:**

```bash
# Validate analytics system initialization
python -c "
import sys
from core.trade_limits import TradeLimits
from analytics.trade_log import TradeLog
from analytics.performance_report import ReportGenerator
from infra.state_store import StateStore
import yaml

try:
    # Load config
    with open('config/policy.yaml') as f:
        policy = yaml.safe_load(f)
    
    # Initialize components
    state_store = StateStore('data/state.json')
    
    # TradeLimits
    trade_limits = TradeLimits(config=policy['risk'], state_store=state_store)
    print('✅ TradeLimits initialized')
    
    # TradeLog
    import os
    os.makedirs('data/trades', exist_ok=True)
    trade_log = TradeLog(csv_dir='data/trades', use_sqlite=True)
    print('✅ TradeLog initialized')
    
    # ReportGenerator
    os.makedirs('reports', exist_ok=True)
    report_gen = ReportGenerator(trade_log=trade_log, output_dir='reports')
    print('✅ ReportGenerator initialized')
    
    print('')
    print('✨ Analytics system ready for LIVE deployment')
    sys.exit(0)
    
except Exception as e:
    print(f'❌ Analytics initialization failed: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
"
```

**Analytics Checklist:**
- [ ] TradeLimits initialized successfully
- [ ] TradeLog directory exists (`data/trades/`)
- [ ] SQLite database will be created on first trade
- [ ] ReportGenerator directory exists (`reports/`)
- [ ] All config validation passes

### 5.2 Start Bot in LIVE Mode

```bash
# Start with caffeinate to prevent sleep (optional for desktop)
caffeinate -t 259200 ./app_run_live.sh --loop --live &

# Wait for startup
sleep 5

# Verify running
if [ -f data/247trader-v2.pid ]; then
    PID=$(cat data/247trader-v2.pid)
    if ps -p $PID > /dev/null; then
        echo "✅ Bot started (PID: $PID)"
    else
        echo "❌ Bot failed to start"
        exit 1
    fi
fi
```

**Verify:**
- [ ] Process running (check PID)
- [ ] Logs updating: `tail -f logs/live_*.log`
- [ ] First cycle completes successfully

### 5.3 Initial Monitoring (First Hour)

**Watch carefully:**

```bash
# Monitor first 10 cycles
watch -n 60 'tail -1 logs/247trader-v2_audit.jsonl | jq "{cycle: .cycle_count, status: .status, mode: .mode, config_hash: .config_hash}"'
```

**Check for:**
- [ ] Mode = "LIVE" in audit log
- [ ] Config hash matches expected (from Phase 2.3)
- [ ] Cycle completion (1 per minute if interval=1)
- [ ] No exceptions in logs: `tail -100 logs/live_*.log | grep -i exception`
- [ ] Memory stable: `ps -o rss= -p $(cat data/247trader-v2.pid) | awk '{printf "%.1fMB\n", $1/1024}'`

### 5.4 First Trade Verification

**When first TRADE status appears:**

```bash
# Watch for first trade
tail -f logs/247trader-v2_audit.jsonl | jq 'select(.status=="TRADE")'
```

**Verify:**
- [ ] Order submitted successfully (check `submitted_orders`)
- [ ] Fill reconciliation triggered (check `post_trade_reconcile_wait_seconds` delay)
- [ ] PnL tracked correctly (check `pnl` section in audit log)
- [ ] Position added to state: `jq '.open_positions' data/.state.json`
- [ ] Alert sent (if configured for trade confirmations)

**Check Coinbase:**
- [ ] Order appears in Coinbase UI
- [ ] Order size matches proposal
- [ ] Fees applied correctly

---

## Phase 6: 48-72 Hour Burn-In Period 🔥

### 6.1 Continuous Monitoring

**Daily checks (2-3x per day):**

```bash
# Quick health check
./scripts/check_rehearsal.sh  # Works for LIVE too

# Or manual:
echo "=== LIVE Bot Status ==="
PID=$(cat data/247trader-v2.pid)
ps -p $PID -o pid,etime,rss | awk 'NR==2 {printf "PID: %s, Uptime: %s, Memory: %.1fMB\n", $1, $2, $3/1024}'

echo ""
echo "Recent Cycles:"
tail -5 logs/247trader-v2_audit.jsonl | jq -r '{cycle: .cycle_count, status: .status, reason: .no_trade_reason, open: (.open_positions | length)}'

echo ""
echo "PnL Summary:"
jq '.pnl' data/.state.json
```

**Monitor:**
- [ ] Bot uptime (should stay running)
- [ ] Memory growth trend (<500MB)
- [ ] Exception count (should be zero)
- [ ] Trade frequency (as expected for volatility)
- [ ] PnL tracking accuracy (compare to Coinbase)

**Analytics Monitoring:**

```bash
# Check trade logging
echo "=== Trade Logging Status ==="
ls -lh data/trades/ 2>/dev/null || echo "⚠️  Trade directory missing"

# Today's trades
TODAY=$(date +%Y%m%d)
if [ -f "data/trades/${TODAY}.csv" ]; then
    COUNT=$(tail -n +2 "data/trades/${TODAY}.csv" | wc -l)
    echo "✅ $COUNT trades logged today"
    echo "Latest trades:"
    tail -3 "data/trades/${TODAY}.csv"
else
    echo "ℹ️  No trades today yet"
fi

# Check daily report generation
echo ""
echo "=== Daily Reports ==="
ls -lht reports/daily_*.json 2>/dev/null | head -5 || echo "ℹ️  No daily reports yet"

# Check cooldown state
echo ""
echo "=== Trade Pacing State ==="
python -c "
import json
from datetime import datetime
try:
    with open('data/state.json') as f:
        state = json.load(f)
    print(f\"Trades today: {state.get('trade_count_today', 0)}/{state.get('max_trades_per_day', '?')}\")
    print(f\"Trades this hour: {state.get('trade_count_hour', 0)}/{state.get('max_trades_per_hour', '?')}\")
    print(f\"Last trade: {state.get('last_trade_time', 'Never')}\")
    
    if 'cooldowns' in state and state['cooldowns']:
        print(f\"Active cooldowns: {len(state['cooldowns'])}\")
        for symbol, until in list(state['cooldowns'].items())[:5]:
            print(f\"  {symbol}: until {until}\")
    else:
        print('No active cooldowns')
except Exception as e:
    print(f\"❌ Error reading state: {e}\")
"
```

**Analytics Checklist:**
- [ ] **Trades logged to CSV** (`data/trades/YYYYMMDD.csv` created after trades)
- [ ] **Entry/exit pairs complete** (BUY creates entry, SELL creates exit with PnL)
- [ ] **SQLite database updated** (`data/trades/trades.db` if enabled)
- [ ] **Cooldowns applied** (visible in state.json after trades)
- [ ] **Spacing enforced** (check audit logs for "Too soon" rejections)
- [ ] **Daily report generated** (reports/daily_YYYYMMDD.json created 23:50-23:59 UTC)
- [ ] **Report metrics populated** (trade_count, win_rate, total_pnl, sharpe_ratio)
- [ ] **Trade limits respected** (hourly/daily counts don't exceed config)

### 6.2 Success Criteria for Burn-In

**Must achieve ALL within 48-72 hours:**

- [ ] **Stability:** Bot runs continuously without crashes
- [ ] **Fill ratio >80%:** Orders get filled (if market conditions allow)
- [ ] **PnL accuracy:** Tracked PnL matches Coinbase ±1%
- [ ] **Stop enforcement:** Stop losses trigger when hit
- [ ] **Alert delivery:** All configured alerts received
- [ ] **No unexpected blocks:** Risk engine doesn't over-block

**Analytics System Validation:**

- [ ] **Trade logging works:** All trades appear in CSV/SQLite within 1 minute of fill
- [ ] **Entry/exit matching:** Each BUY has corresponding SELL with PnL calculated
- [ ] **Cooldowns enforced:** No trades violate win/loss/stop-loss cooldown periods
- [ ] **Spacing enforced:** No trades violate global or per-symbol spacing rules
- [ ] **Daily report generated:** First daily report appears 23:50-23:59 UTC on Day 1
- [ ] **Report accuracy:** Metrics match manual calculation (trade count, win rate, PnL)
- [ ] **State persistence:** state.json correctly tracks counters/cooldowns across restarts

### 6.3 Issue Response Procedures

**If exception occurs:**
1. Check logs: `tail -100 logs/live_*.log | grep -A 10 "Traceback"`
2. Assess severity: Data issue? Network? Bug?
3. If critical: Activate kill switch (`touch data/KILL_SWITCH`)
4. Fix issue, re-validate, restart

**If PnL mismatch:**
1. Run reconciliation: Check `core/position_manager.py::rebuild_positions_from_exchange()`
2. Compare state: `jq '.open_positions' data/.state.json` vs Coinbase UI
3. If >5% off: STOP, investigate fill reconciliation logic

**If memory leak:**
1. Check growth rate: `watch 'ps -o rss= -p $(cat data/247trader-v2.pid) | awk "{printf \"%.1fMB\\n\", \$1/1024}"'`
2. If >500MB or growing >10MB/hour: Restart bot
3. Investigate caching/data retention in logs

---

## Phase 7: Capital Scale-Up (After 48-72h) 💰

### 7.1 Performance Review

**Generate burn-in report:**

```bash
# Analyze first 48-72 hours
python tools/analyze_live_performance.py --hours 72
```

**Review:**
- [ ] Total trades executed
- [ ] Fill ratio (filled / submitted)
- [ ] Win rate (profitable / total closed)
- [ ] Average win/loss size
- [ ] Max drawdown
- [ ] Sharpe ratio (if enough trades)

### 7.2 Scale-Up Decision

**If ALL criteria met:**
- ✅ Zero critical issues
- ✅ Fill ratio >80%
- ✅ PnL tracking accurate
- ✅ Stop losses working
- ✅ Alert system reliable

**Then:** Increase capital gradually

**Suggested Scale-Up:**
1. **Week 1:** $100-$500 (current)
2. **Week 2:** $1,000-$2,000 (if stable)
3. **Week 3:** $5,000-$10,000 (if profitable)
4. **Month 2+:** Based on performance and risk appetite

**Update `config/policy.yaml`:**
```yaml
risk:
  max_total_exposure_usd: 2000  # Increase gradually
  max_position_size_usd: 200    # 10% of exposure
```

---

## Rollback Procedures 🔄

### Immediate Rollback (Emergency)

```bash
# 1. Stop new trades
touch data/KILL_SWITCH

# 2. Verify kill switch active
tail -1 logs/247trader-v2_audit.jsonl | jq '.kill_switch_active'

# 3. Wait for current cycle to complete (1-2 minutes)

# 4. Stop bot
kill -2 $(cat data/247trader-v2.pid)

# 5. Close open positions (if needed)
python liquidate_to_usdc.py  # Review positions first

# 6. Set to read-only
sed -i '' 's/read_only: false/read_only: true/' config/app.yaml
```

### Graceful Rollback (Non-Emergency)

```bash
# 1. Enable kill switch to prevent new trades
touch data/KILL_SWITCH

# 2. Let open positions resolve naturally (or close manually)
# Monitor: jq '.open_positions' data/.state.json

# 3. Once no open positions, stop bot
kill -2 $(cat data/247trader-v2.pid)

# 4. Revert to PAPER mode
sed -i '' 's/mode: "LIVE"/mode: "PAPER"/' config/app.yaml
sed -i '' 's/read_only: false/read_only: true/' config/app.yaml

# 5. Restart in PAPER mode
./app_run_live.sh --loop --paper &
```

---

## Appendix: Key Commands 📝

### Status Checks
```bash
# Quick status
./scripts/check_rehearsal.sh  # Works for LIVE mode too

# Process status
ps -p $(cat data/247trader-v2.pid) -o pid,etime,rss

# Latest cycle
tail -1 logs/247trader-v2_audit.jsonl | jq '{cycle: .cycle_count, status: .status, mode: .mode}'

# Current positions
jq '.open_positions' data/.state.json

# PnL summary
jq '.pnl' data/.state.json
```

### Emergency Controls
```bash
# Kill switch (stop new trades)
touch data/KILL_SWITCH

# Graceful stop
kill -2 $(cat data/247trader-v2.pid)

# Force kill (emergency only)
kill -9 $(cat data/247trader-v2.pid)

# Liquidate all (emergency only)
python liquidate_to_usdc.py
```

### Log Analysis
```bash
# Recent errors
tail -100 logs/live_*.log | grep -i "error\|exception"

# Cycle latency
jq -r '.stage_latencies.total_cycle' logs/247trader-v2_audit.jsonl | tail -100 | awk '{sum+=$1; count++} END {printf "Avg: %.2fs\n", sum/count}'

# Trade history
jq 'select(.status=="TRADE")' logs/247trader-v2_audit.jsonl | jq -r '{cycle: .cycle_count, trades: (.submitted_orders | length), timestamp: .timestamp}'
```

---

## Sign-Off ✍️

**Deployment Authorized By:** ___________________________  
**Date/Time:** ___________________________  
**Initial Capital:** $___________  
**Config Hash:** ___________________________  

**Post-Deployment Review Scheduled:** ___________________________  

---

**Document Version:** 1.0  
**Last Updated:** 2025-11-15  
**Next Review:** After first LIVE deployment
