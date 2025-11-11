# Production Launcher - Quick Reference

## 🚀 Quick Start

### Run Once (Single Cycle)
```bash
./run_live.sh
```

### Run Continuously (15min intervals)
```bash
./run_live.sh --loop
```

### Test in Paper Mode
```bash
./run_live.sh --paper --loop
```

### Test in Dry-Run Mode
```bash
./run_live.sh --dry-run
```

---

## 📋 What the Script Does

The `run_live.sh` script handles:

1. ✅ **Virtual Environment Activation** - Automatically sources `.venv/bin/activate`
2. ✅ **Credential Loading** - Uses `CB_API_SECRET_FILE` environment variable
3. ✅ **Pre-flight Checks**:
   - Virtual environment exists
   - Coinbase credentials found
   - Config files present
   - Read-only mode warning (if LIVE mode)
4. ✅ **Account Balance Display** - Shows USDC balance before trading
5. ✅ **Safety Confirmation** - Requires typing "YES" for LIVE mode
6. ✅ **Logging** - All output saved to `logs/live_YYYYMMDD_HHMMSS.log`
7. ✅ **Clean Shutdown** - Handles Ctrl+C gracefully

---

## 🛡️ Safety Features

### Pre-flight Checks
- Verifies virtual environment exists
- Confirms API credentials are present
- Warns if `read_only: true` is set (prevents real trades)
- Shows account balance before starting
- Requires explicit "YES" confirmation for LIVE mode

### Logging
All activity is logged to timestamped files:
```
logs/live_20251110_213519.log
```

### Read-Only Mode Protection
If `config/app.yaml` has `read_only: true`, the script will:
1. Show a warning
2. Explain real orders won't execute
3. Ask for confirmation to continue

---

## 📝 Usage Examples

### 1. Test in DRY_RUN (No Orders)
```bash
./run_live.sh --dry-run
```
- Connects to live API
- Detects triggers
- Generates proposals
- **Does NOT place orders**

### 2. Paper Trading (Simulated)
```bash
./run_live.sh --paper --loop
```
- Uses live market data
- Simulates order fills
- Tracks PnL (not real money)
- Runs every 15 minutes

### 3. Live Trading (Real Money)
```bash
# First, edit config/app.yaml:
#   mode: LIVE
#   read_only: false

./run_live.sh --loop
```
⚠️ **WARNING:** This places real orders with real money!

### 4. Single Test Cycle
```bash
./run_live.sh --dry-run
```
- Runs once and exits
- Good for testing before continuous operation

---

## ⚙️ Configuration

### Environment Variables
```bash
# Set credentials location (optional - auto-detects)
export CB_API_SECRET_FILE="/path/to/cb_api.json"

# Run the script
./run_live.sh
```

### Edit Mode in Config
**File:** `config/app.yaml`

```yaml
app:
  mode: "DRY_RUN"  # Change to PAPER or LIVE

exchange:
  read_only: true   # Change to false for live trading
```

---

## 🔍 Monitoring

### Check Logs
```bash
# Latest log file
ls -lt logs/ | head -1

# Tail live logs
tail -f logs/live_*.log
```

### View State
```bash
# Current positions, trades, PnL
cat data/.state.json | jq .
```

### Stop Running System
Press **Ctrl+C** - the script handles graceful shutdown

---

## 🚨 Troubleshooting

### Script won't start
```bash
# Check virtual environment
ls -la .venv/

# Recreate if missing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Credentials not found
```bash
# Set explicitly
export CB_API_SECRET_FILE="/Users/ahmed/coding-stuff/trader/cb_api.json"

# Or place file in default location
cp your_credentials.json /Users/ahmed/coding-stuff/trader/cb_api.json
```

### "Command not found"
```bash
# Make executable
chmod +x run_live.sh

# Run with bash explicitly
bash run_live.sh --dry-run
```

### API errors
```bash
# Test connection
source .venv/bin/activate
python3 -c "
from core.exchange_coinbase import CoinbaseExchange
ex = CoinbaseExchange()
print('Connected:', len(ex.get_accounts()), 'accounts')
"
```

---

## 📊 Expected Output

### Successful Run
```
═══════════════════════════════════════════════════════════
[2025-11-10 21:35:19] 247trader-v2 Production Launcher
═══════════════════════════════════════════════════════════

[2025-11-10 21:35:19] Mode: DRY_RUN
[2025-11-10 21:35:19] Run mode: once

[2025-11-10 21:35:19] Running pre-flight checks...
[2025-11-10 21:35:19] ✅ Virtual environment found
[2025-11-10 21:35:19] ✅ Using credentials from: /Users/ahmed/coding-stuff/trader/cb_api.json
[2025-11-10 21:35:19] ✅ Configuration file found
[2025-11-10 21:35:19] ✅ Pre-flight checks complete

[2025-11-10 21:35:19] Activating virtual environment...
[2025-11-10 21:35:19] ✅ Virtual environment activated: /path/to/.venv/bin/python3

[2025-11-10 21:35:19] Fetching account balance...
[2025-11-10 21:35:20] ✅ Account balance: $410.66 USDC

[2025-11-10 21:35:20] Running single cycle...
[2025-11-10 21:35:20] Launching trading system...

... [System output] ...

[2025-11-10 21:35:24] ✅ Cycle completed successfully
[2025-11-10 21:35:24] Session ended
[2025-11-10 21:35:24] Full logs saved to: logs/live_20251110_213519.log
```

---

## 🎯 Recommended Workflow

### Day 1: DRY_RUN Testing
```bash
# Run 10-20 cycles to observe signals
./run_live.sh --dry-run    # Run once, repeat manually
```

### Week 1: PAPER Trading
```bash
# Continuous simulation
./run_live.sh --paper --loop
```
- Check `data/.state.json` daily
- Monitor simulated PnL
- Validate trigger quality

### Week 2+: LIVE Trading (if validated)
```bash
# Edit config/app.yaml first:
#   mode: LIVE
#   read_only: false

# Start with small size
./run_live.sh --loop
```
- Monitor closely for first 5-10 trades
- Keep position sizes small initially
- Scale up gradually

---

## 📁 Files Created

```
logs/
  live_20251110_213519.log    # Session logs (timestamped)
  live_20251110_214823.log
  ...

data/
  .state.json                 # Trading state (auto-updated)
```

---

## 🔐 Security Notes

- API credentials loaded from `CB_API_SECRET_FILE` (not in repo)
- Credentials path is shown but content is never logged
- Script requires explicit "YES" confirmation for LIVE mode
- Read-only mode prevents accidental real trading

---

## 💡 Tips

1. **Test first**: Always run `--dry-run` before `--paper` before LIVE
2. **Monitor logs**: Use `tail -f logs/live_*.log` to watch in real-time
3. **Check state**: Review `data/.state.json` after each cycle
4. **Start small**: Use small position sizes initially (1-2% per trade)
5. **Watch balance**: Script shows USDC balance before starting

---

## 🆘 Support

If issues persist:
1. Check logs in `logs/` directory
2. Review state in `data/.state.json`
3. Test API connection manually (see Troubleshooting)
4. Verify credentials have proper permissions (View + Trade)

---

**Remember:** Start with DRY_RUN, graduate to PAPER, then carefully move to LIVE! 🚀
