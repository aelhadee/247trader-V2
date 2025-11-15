# 24-Hour PAPER Mode Rehearsal Guide 🎯

**Date:** 2025-11-15  
**Status:** 🟡 **IN PROGRESS**  
**Duration:** 24 hours (86,400 seconds = 1,440 minutes at 1min interval)  
**Expected Cycles:** ~1,440 cycles

---

## Objectives

Validate production readiness before LIVE deployment by observing:

1. **Fill Reconciliation** - `_post_trade_refresh` timing against real Coinbase fills
2. **Alert Delivery** - All 9 alert types functional with proper deduplication
3. **Circuit Breakers** - Proper triggering and recovery behavior
4. **Order Execution** - Maker/taker routing, post-only → IOC fallback
5. **Config Consistency** - Single config hash throughout session
6. **Metrics Recording** - All 16 metrics properly tracked
7. **Exception Handling** - No unhandled errors or crashes
8. **PnL Tracking** - Accurate realized PnL from paper fills

---

## Pre-Rehearsal Setup

### 1. Configuration Check
```bash
# Verify PAPER mode active
grep "mode:" config/app.yaml
# Expected: mode: "PAPER"

# Validate config
python3 tools/config_validator.py
# Expected: ✅ All config files validated successfully

# Record config hash
python3 -c "from runner.main_loop import TradingLoop; loop = TradingLoop(config_dir='config'); print(f'Config hash: {loop.config_hash}')"
# Record this hash - should stay constant for 24 hours
```

### 2. Clean State
```bash
# Backup existing state (optional)
cp data/.state.json data/.state_backup_$(date +%Y%m%d_%H%M%S).json

# Clear audit log for fresh start
> logs/247trader-v2_audit.jsonl

# Remove PID file if stale
rm -f data/247trader-v2.pid

# Ensure no kill switch
rm -f data/KILL_SWITCH
```

### 3. System Resources
```bash
# Keep system awake for 24 hours (macOS)
caffeinate -t 86400 &
CAFFEINATE_PID=$!
echo "Caffeinate PID: $CAFFEINATE_PID" > data/caffeinate.pid

# Monitor disk space
df -h .
# Ensure >500MB free for logs
```

---

## Launch Procedure

### Start PAPER Mode
```bash
# Terminal 1: Launch bot
./app_run_live.sh --loop --paper

# Expected output:
# ═══════════════════════════════════════════════════════════
# 247trader-v2 Production Launcher
# ═══════════════════════════════════════════════════════════
# Mode: PAPER
# Run mode: loop
# ...
# ✅ System started (Press Ctrl+C to stop)
```

### Start Monitoring
```bash
# Terminal 2: Launch monitor (in new terminal window)
./scripts/monitor_paper_rehearsal.sh

# Expected: Real-time dashboard refreshing every 60s
```

---

## Monitoring Checklist

### Hour 1 (Burn-in - 0-60 min)
- [ ] Bot started successfully (PID file created)
- [ ] Config hash logged at startup
- [ ] First cycle completed without errors
- [ ] Audit log entries being written
- [ ] No unhandled exceptions in logs
- [ ] Metrics recording operational

**Action Items:**
- Monitor logs/live_*.log for startup errors
- Verify audit log structure with `tail -1 logs/247trader-v2_audit.jsonl | jq`
- Check config hash: `tail -1 logs/247trader-v2_audit.jsonl | jq -r '.config_hash'`

### Hours 2-4 (Alert Testing - 60-240 min)
- [ ] Test kill switch (create `data/KILL_SWITCH`, verify alert fires <5s)
- [ ] Verify alert deduplication (60s window)
- [ ] Observe order rejection handling
- [ ] Check empty universe handling (if triggered)
- [ ] Validate latency tracking

**Action Items:**
```bash
# Test kill switch
touch data/KILL_SWITCH
# Watch logs for CRITICAL alert
# Remove after test: rm data/KILL_SWITCH

# Check alert summary
grep "ALERT" logs/live_*.log | wc -l
```

### Hours 4-8 (Execution Flow - 240-480 min)
- [ ] Observe maker order placement (post-only)
- [ ] Verify maker → taker fallback (if orders rejected)
- [ ] Confirm fill reconciliation timing
- [ ] Check partial fill handling
- [ ] Validate order cancellation (stale orders)

**Action Items:**
```bash
# Check order flow
grep "order_type" logs/247trader-v2_audit.jsonl | jq -r '.execution.order_type' | sort | uniq -c

# Check fill reconciliation
grep "reconcile_fills" logs/live_*.log | tail -20
```

### Hours 8-16 (Steady State - 480-960 min)
- [ ] Config hash remains constant
- [ ] No circuit breaker trips from bugs
- [ ] PnL tracking accurate
- [ ] Daily/weekly PnL percentages updating
- [ ] Cooldowns enforced correctly

**Action Items:**
```bash
# Verify config consistency
jq -r '.config_hash' logs/247trader-v2_audit.jsonl | sort | uniq
# Expected: Single hash

# Check PnL tracking
tail -10 logs/247trader-v2_audit.jsonl | jq '.pnl'
```

### Hours 16-24 (Endurance - 960-1440 min)
- [ ] No memory leaks (check RSS with `ps aux | grep python`)
- [ ] Log rotation working (if configured)
- [ ] StateStore syncing correctly
- [ ] No stale data warnings
- [ ] Graceful handling of API rate limits

**Action Items:**
```bash
# Check memory usage
ps aux | grep "python.*main_loop" | awk '{print "RSS: " $6/1024 " MB"}'

# Verify StateStore updates
ls -lh data/.state.json
```

---

## Success Criteria

### ✅ Must-Pass Criteria
1. **Zero unhandled exceptions** - No crashes or traceback errors
2. **Config hash constant** - Single hash throughout 24 hours
3. **All alerts functional** - Kill switch, stops, latency, API errors all fire correctly
4. **Fill reconciliation working** - No "unknown order" errors after fills
5. **Metrics complete** - All 16 metrics recorded in every cycle
6. **Circuit breakers operational** - Proper fail-closed behavior

### ✅ Performance Targets
1. **Cycle completion rate:** >95% (allow for occasional API delays)
2. **Average cycle latency:** <30s (policy limit: 45s)
3. **Fill reconciliation accuracy:** 100% (no missed fills)
4. **Alert delivery time:** <5s from trigger
5. **Memory stability:** <500MB RSS after 24 hours

### ⚠️ Acceptable Issues
1. **NO_TRADE cycles** - Expected during low volatility
2. **Empty universe** - Expected if market conditions poor
3. **API 429 rate limits** - Circuit breaker should handle gracefully
4. **Maker order rejections** - Should fallback to taker (IOC)

---

## Data Collection

### Logs to Preserve
```bash
# At end of 24h, archive all logs
REHEARSAL_DATE=$(date +%Y%m%d)
mkdir -p logs/paper_rehearsal_$REHEARSAL_DATE

cp logs/247trader-v2_audit.jsonl logs/paper_rehearsal_$REHEARSAL_DATE/
cp logs/live_*.log logs/paper_rehearsal_$REHEARSAL_DATE/
cp data/.state.json logs/paper_rehearsal_$REHEARSAL_DATE/state_final.json

# Create summary
cd logs/paper_rehearsal_$REHEARSAL_DATE
wc -l 247trader-v2_audit.jsonl
jq -s 'group_by(.status) | map({status: .[0].status, count: length})' 247trader-v2_audit.jsonl > summary.json
```

### Metrics to Extract
```bash
# Total cycles
TOTAL_CYCLES=$(wc -l < logs/247trader-v2_audit.jsonl)

# Status breakdown
echo "Status Distribution:"
jq -r '.status' logs/247trader-v2_audit.jsonl | sort | uniq -c

# NO_TRADE reasons
echo "NO_TRADE Reasons:"
jq -r 'select(.status=="NO_TRADE") | .no_trade_reason' logs/247trader-v2_audit.jsonl | sort | uniq -c

# Alert counts
echo "Alerts Fired:"
grep "ALERT" logs/live_*.log | grep -o 'type=[^ ]*' | sort | uniq -c

# Config hash stability
echo "Config Hashes:"
jq -r '.config_hash' logs/247trader-v2_audit.jsonl | sort | uniq -c

# Average cycle latency
echo "Average Cycle Latency:"
jq -r '.stage_latencies.total_cycle // 0' logs/247trader-v2_audit.jsonl | \
    awk '{sum+=$1; count++} END {printf "%.2fs\n", sum/count}'
```

---

## Troubleshooting

### Issue: Bot Crashes or Stops
**Symptoms:** PID file exists but process not running
**Actions:**
```bash
# Check last log entries
tail -50 logs/live_*.log

# Check for Python errors
grep -A 10 "Traceback" logs/live_*.log

# Restart if safe
./app_run_live.sh --loop --paper
```

### Issue: Config Hash Changes
**Symptoms:** Multiple unique hashes in audit log
**Actions:**
```bash
# Find when hash changed
jq -r '[.timestamp, .config_hash] | @tsv' logs/247trader-v2_audit.jsonl | \
    awk '{if(prev && $2!=prev) print "Change at", $1, "from", prev, "to", $2; prev=$2}'

# Check git status
git status config/

# Verify no uncommitted changes
git diff config/
```

### Issue: Excessive NO_TRADE Cycles
**Symptoms:** >90% NO_TRADE status
**Actions:**
```bash
# Check NO_TRADE reasons
jq -r 'select(.status=="NO_TRADE") | .no_trade_reason' logs/247trader-v2_audit.jsonl | \
    sort | uniq -c | sort -rn

# Common reasons:
# - "no_candidates_from_triggers" = Low volatility (expected)
# - "circuit_breaker_tripped" = Check why circuit tripped
# - "empty_universe" = Asset quality filters too strict
```

### Issue: Memory Growth
**Symptoms:** RSS increases over time
**Actions:**
```bash
# Monitor memory every hour
while true; do
    echo "$(date) - $(ps aux | grep 'python.*main_loop' | awk '{print "RSS: " $6/1024 " MB"}')"
    sleep 3600
done > logs/memory_tracking.log

# If >1GB after 24h, investigate:
# - Audit log size (should be manageable)
# - StateStore size (should be <1MB)
# - Check for leaked objects in code
```

---

## Post-Rehearsal Analysis

### Generate Report
```bash
# Create comprehensive report
cat > logs/paper_rehearsal_report.md << 'EOF'
# PAPER Mode Rehearsal Report

**Date:** $(date)
**Duration:** 24 hours
**Config Hash:** $(tail -1 logs/247trader-v2_audit.jsonl | jq -r '.config_hash')

## Summary
- Total Cycles: $(wc -l < logs/247trader-v2_audit.jsonl)
- Unhandled Exceptions: $(grep -c "Traceback" logs/live_*.log || echo "0")
- Circuit Breaker Trips: $(jq -r 'select(.circuit_breaker_tripped==true)' logs/247trader-v2_audit.jsonl | wc -l)
- Alerts Fired: $(grep -c "ALERT" logs/live_*.log || echo "0")

## Status Distribution
$(jq -r '.status' logs/247trader-v2_audit.jsonl | sort | uniq -c)

## NO_TRADE Reasons (Top 5)
$(jq -r 'select(.status=="NO_TRADE") | .no_trade_reason' logs/247trader-v2_audit.jsonl | sort | uniq -c | sort -rn | head -5)

## Performance
- Average Cycle Latency: $(jq -r '.stage_latencies.total_cycle // 0' logs/247trader-v2_audit.jsonl | awk '{sum+=$1; count++} END {printf "%.2fs", sum/count}')
- P95 Cycle Latency: $(jq -r '.stage_latencies.total_cycle // 0' logs/247trader-v2_audit.jsonl | sort -n | awk '{a[NR]=$1} END {print a[int(NR*0.95)]}')s

## Issues Encountered
[Document any issues here]

## Recommendations
[Document tuning recommendations]

EOF

# View report
cat logs/paper_rehearsal_report.md
```

### Decision Matrix

| Criterion | Pass | Action if Fail |
|-----------|------|----------------|
| Zero unhandled exceptions | Required | Debug and fix before LIVE |
| Config hash constant | Required | Investigate config changes |
| All alerts functional | Required | Fix alert delivery |
| Fill reconciliation 100% | Required | Tune `post_trade_reconcile_wait_seconds` |
| <5% circuit breaker trips | Recommended | Review trip reasons |
| <30s average latency | Recommended | Optimize slow stages |

**GO/NO-GO Decision:**
- **GO to LIVE:** All "Required" criteria pass + <5 "Recommended" issues
- **NO-GO:** Any "Required" criterion fails OR >10 "Recommended" issues

---

## Tuning Recommendations

Based on rehearsal observations, tune these parameters:

### 1. Reconciliation Wait Time
```yaml
# config/policy.yaml
execution:
  post_trade_reconcile_wait_seconds: 0.5  # Increase to 1.0-2.0 if fills delayed
```

**When to increase:**
- Frequent "unknown order" errors after fills
- Fills take >500ms to appear in Coinbase API
- Observed during high volatility periods

### 2. Latency Thresholds
```yaml
# config/policy.yaml
latency:
  api_thresholds:
    list_accounts: 2.0  # Increase if API consistently slow
    get_product: 1.0
```

**When to increase:**
- Frequent latency violation alerts
- P95 latency > threshold by consistent margin (not spikes)

### 3. Circuit Breaker Sensitivity
```yaml
# config/policy.yaml
circuit_breaker:
  api_error_threshold: 5  # Increase if too many false trips
  api_error_window_minutes: 10
```

**When to increase:**
- Circuit breaker trips from transient API issues
- Trips during known Coinbase maintenance windows

---

## Next Steps

### If Rehearsal PASSES ✅
1. Archive rehearsal data: `logs/paper_rehearsal_YYYYMMDD/`
2. Document any tuning applied
3. Update `config/app.yaml` mode to `LIVE`
4. Set `exchange.read_only: false`
5. Proceed to **LIVE deployment with $100-$500 capital**

### If Rehearsal FAILS ❌
1. Document all failures in `logs/paper_rehearsal_report.md`
2. Create GitHub issues for each failure
3. Fix issues and re-run rehearsal
4. DO NOT proceed to LIVE until all "Required" criteria pass

---

## Continuous Monitoring Commands

### Quick Health Check
```bash
# One-liner health check
echo "Bot: $([ -f data/247trader-v2.pid ] && echo 'RUNNING' || echo 'STOPPED') | Cycles: $(wc -l < logs/247trader-v2_audit.jsonl) | Errors: $(grep -c ERROR logs/live_*.log || echo 0)"
```

### Real-Time Cycle Status
```bash
# Watch last cycle status
watch -n 5 'tail -1 logs/247trader-v2_audit.jsonl | jq "{timestamp, status, no_trade_reason, config_hash}"'
```

### Alert Stream
```bash
# Follow alerts in real-time
tail -f logs/live_*.log | grep --line-buffered "ALERT"
```

### Memory Tracking
```bash
# Track memory every 5 minutes
watch -n 300 'ps aux | grep "python.*main_loop" | awk "{print \"RSS: \" \$6/1024 \" MB\"}"'
```

---

## Emergency Stop Procedure

### Graceful Stop
```bash
# Send SIGINT (Ctrl+C equivalent)
kill -2 $(cat data/247trader-v2.pid)

# Wait for clean shutdown (up to 30s)
sleep 30

# Verify stopped
ps -p $(cat data/247trader-v2.pid) || echo "Bot stopped cleanly"
```

### Force Stop
```bash
# Only if graceful stop fails
kill -9 $(cat data/247trader-v2.pid)
rm data/247trader-v2.pid
```

### Kill Switch Activation
```bash
# Immediate trading halt (proposals blocked same cycle)
touch data/KILL_SWITCH

# Verify kill switch active
tail -5 logs/247trader-v2_audit.jsonl | jq '.no_trade_reason' | grep -q "kill_switch" && echo "Kill switch ACTIVE"
```

---

**Document Version:** 1.0  
**Last Updated:** 2025-11-15  
**Status:** Ready for 24-hour PAPER rehearsal
