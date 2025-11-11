# 🚀 PRODUCTION LAUNCH CHECKLIST

**Date:** November 11, 2025  
**Status:** Ready for PAPER → LIVE progression

---

## Phase 1: PAPER Mode Validation (1 Week)

### Start PAPER Mode
```bash
# 1. Set PAPER mode
# Edit config/app.yaml:
app:
  mode: "PAPER"

# 2. Run continuously
CB_API_SECRET_FILE=/path/to/keys.json \
python -m runner.main_loop --interval 15

# 3. Monitor in separate terminal
tail -f logs/247trader-v2.log
```

### Daily Checks (7 days)
- [ ] Day 1: Check logs for errors, verify proposals generated
- [ ] Day 2: Verify simulated fills recorded correctly
- [ ] Day 3: Check PnL tracking is working
- [ ] Day 4: Verify cooldowns applied after losses
- [ ] Day 5: Check risk limits enforced correctly
- [ ] Day 6: Verify reconciliation working (check logs)
- [ ] Day 7: Review full week of data

### Success Criteria
- ✅ 50+ complete cycles executed
- ✅ No critical errors in logs
- ✅ PnL tracking accurate
- ✅ Reconciliation finding fills
- ✅ Risk limits respected
- ✅ Simulated trades reasonable

---

## Phase 2: Live Smoke Test

### Run Before LIVE Mode
```bash
# Full validation (read-only, no orders)
CB_API_SECRET_FILE=/path/to/keys.json \
pytest tests/test_live_smoke.py -v

# Should see:
# ✅ ALL SMOKE TESTS PASSED - SYSTEM READY FOR PAPER/LIVE
```

### Expected Results
- ✅ Coinbase connection works
- ✅ Account balances fetched
- ✅ Quotes are fresh (< 30s old)
- ✅ OHLCV data valid
- ✅ Orderbook depth sufficient
- ✅ Universe builds successfully
- ✅ Preview orders work
- ✅ All 10 checks pass

---

## Phase 3: LIVE Mode (Start Small)

### Pre-Flight Final Checks
```bash
# 1. Verify single instance (should fail if already running)
CB_API_SECRET_FILE=/path/to/keys.json \
python -m runner.main_loop --once
# If running: "ANOTHER INSTANCE IS ALREADY RUNNING"

# 2. Test kill switch
touch data/KILL_SWITCH
CB_API_SECRET_FILE=/path/to/keys.json \
python -m runner.main_loop --once
# Should see: "🚨 KILL SWITCH ACTIVATED"
rm data/KILL_SWITCH

# 3. Check state file
cat data/.state.json | jq .
# Verify pnl_today, pnl_week are reasonable
```

### Configuration for Small Live Start
```yaml
# config/policy.yaml
risk:
  min_trade_notional_usd: 5        # $5 minimum per trade
  max_total_at_risk_pct: 10.0      # Max 10% exposed (conservative)
  max_position_size_pct: 3.0       # Max 3% per asset
  daily_stop_pnl_pct: -3.0         # Stop at -3% loss
  weekly_stop_pnl_pct: -7.0        # Stop at -7% loss

# config/app.yaml
app:
  mode: "LIVE"                     # LIVE mode

exchange:
  read_only: false                 # Allow real orders
```

### Start LIVE
```bash
# Start with logging
CB_API_SECRET_FILE=/path/to/keys.json \
python -m runner.main_loop --interval 15 | tee logs/live_$(date +%Y%m%d).log
```

### Monitor First 20 Trades
- [ ] Trade 1: Verify order placed successfully
- [ ] Trade 2: Check fill price reasonable
- [ ] Trade 3: Verify fees calculated correctly
- [ ] Trade 4: Check position in state store
- [ ] Trade 5: Verify reconciliation updates position
- [ ] ...
- [ ] Trade 20: Review PnL, hit rate, avg slippage

### Stop Conditions (HALT IMMEDIATELY)
- 🚨 Unexpected behavior (orders at wrong prices)
- 🚨 Position size errors (too large)
- 🚨 PnL tracking wrong
- 🚨 Orders not being reconciled
- 🚨 Any critical errors in logs

### How to HALT
```bash
# Option 1: Kill switch
touch data/KILL_SWITCH
# System stops on next cycle

# Option 2: Ctrl+C (graceful)
# Cancels open orders, flushes state

# Option 3: Kill process (emergency)
kill -9 <PID>
# Lock auto-releases, but orders remain open
```

---

## Phase 4: Scale Gradually

### Week 1-2: Validation
- Account size: $100-200
- Per-trade size: $5-10
- Max exposure: 10%
- Goal: Validate system stability

### Week 3-4: Small Scale
- Account size: $500-1000
- Per-trade size: $25-50
- Max exposure: 15%
- Goal: Build confidence

### Month 2+: Production Scale
- Account size: $5000+
- Per-trade size: $100-500
- Max exposure: 15-20%
- Goal: Steady returns

---

## Emergency Procedures

### Kill Switch (IMMEDIATE)
```bash
touch data/KILL_SWITCH
# System halts on next cycle (< 1 min)
```

### Switch to Read-Only (FAST)
```yaml
# config/app.yaml
exchange:
  read_only: true
# Restart bot
```

### Switch to PAPER (SAFE)
```yaml
# config/app.yaml
app:
  mode: "PAPER"
# Restart bot
```

### Cancel All Orders (MANUAL)
```bash
# Use Coinbase web interface
# Or implement batch cancel if needed
```

### Check System Health
```bash
# Recent logs
tail -100 logs/247trader-v2.log

# Audit trail
tail -20 logs/247trader-v2_audit.jsonl | jq .

# Current state
cat data/.state.json | jq .

# Open positions
cat data/.state.json | jq .positions

# PnL
cat data/.state.json | jq '{pnl_today, pnl_week, trades_today}'
```

---

## Monitoring Dashboards (Future)

### Key Metrics to Track
1. **PnL**: Daily/weekly realized PnL
2. **Win Rate**: % of profitable trades
3. **Avg Win/Loss**: Win size vs loss size
4. **Slippage**: Actual vs expected fill prices
5. **Fees**: Total fees paid
6. **Fill Rate**: % of orders that fill
7. **API Health**: Error rate, latency
8. **Circuit Breakers**: How often they fire

### Alerts to Configure
- 🚨 Daily stop loss hit
- 🚨 Weekly stop loss hit
- 🚨 Kill switch activated
- 🚨 Circuit breaker tripped
- ⚠️ API errors spike
- ⚠️ High slippage detected
- ⚠️ Low fill rate
- ℹ️ Daily PnL summary

---

## Quick Reference

### Start Bot
```bash
CB_API_SECRET_FILE=<path> python -m runner.main_loop --interval 15
```

### Run One Cycle (Test)
```bash
CB_API_SECRET_FILE=<path> python -m runner.main_loop --once
```

### Check Status
```bash
# Is it running?
ps aux | grep main_loop

# Check PID lock
cat data/247trader-v2.pid

# Recent activity
tail -f logs/247trader-v2.log
```

### Stop Bot (Graceful)
```bash
# Ctrl+C in terminal
# Or:
kill -TERM <PID>
# Cancels orders, flushes state
```

### Remove Stale Lock
```bash
rm data/247trader-v2.pid
```

### View State
```bash
# Full state
cat data/.state.json | jq .

# Just positions
cat data/.state.json | jq .positions

# Just PnL
cat data/.state.json | jq '{pnl_today, pnl_week}'
```

### Run Tests
```bash
# Unit tests
pytest tests/test_core.py -v

# Live smoke test (read-only)
CB_API_SECRET_FILE=<path> pytest tests/test_live_smoke.py -v
```

---

## Support & Troubleshooting

### Common Issues

**"Another instance is running"**
```bash
# Check if actually running
ps aux | grep main_loop

# If not running, remove stale lock
rm data/247trader-v2.pid
```

**"Quote too stale"**
```bash
# Check network connectivity
ping api.coinbase.com

# Check system clock
date
# Should match UTC
```

**"Insufficient capital"**
```bash
# Check balances
cat data/.state.json | jq .cash_balances

# Or run direct check
CB_API_SECRET_FILE=<path> python3 -c "
from core.exchange_coinbase import CoinbaseExchange
ex = CoinbaseExchange()
accts = ex.get_accounts()
for a in accts:
    bal = float(a.get('available_balance', {}).get('value', 0))
    if bal > 0:
        print(f\"{a['currency']}: {bal:.6f}\")
"
```

**Orders not filling**
```bash
# Check order status
cat data/.state.json | jq .open_orders

# Check if post-only orders too aggressive
# Edit config/policy.yaml:
execution:
  default_order_type: "market"  # Use market orders
```

---

## Success Metrics (First Month)

### Week 1 (PAPER)
- ✅ 50+ cycles executed
- ✅ No critical errors
- ✅ PnL tracking works

### Week 2 (PAPER)
- ✅ 100+ cycles executed  
- ✅ Reconciliation working
- ✅ Risk limits respected

### Week 3 (LIVE - Small)
- ✅ 20+ real trades
- ✅ Fills at expected prices
- ✅ Positions reconciled correctly

### Week 4 (LIVE - Small)
- ✅ 50+ real trades
- ✅ PnL positive or neutral
- ✅ No critical issues

### Month 2+
- ✅ Scale capital gradually
- ✅ Monitor win rate
- ✅ Optimize parameters

---

**Good luck! Trade safely! 🚀💰**
