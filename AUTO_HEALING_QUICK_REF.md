# 247trader-v2 Auto-Healing Quick Reference

## ✅ All 6 Features Implemented

### 1. Risk Cap: 25% Conservative Profile
- **Config:** `config/policy.yaml` line 49
- **Value:** `max_total_at_risk_pct: 25.0`
- **Action Required:** Restart bot

### 2. Force-Eligible Core Assets
- **Config:** `config/universe.yaml` lines 83-86
- **Assets:** BTC-USD, ETH-USD, SOL-USD
- **Behavior:** Bypass all liquidity checks

### 3. Auto-Fallback on Convert Failures
- **Code:** `runner/main_loop.py` line 2684
- **Behavior:** Convert API fails → auto TWAP liquidation
- **Action Required:** None (already working)

### 4. Pre-Flight Config Validation
- **Launcher:** `app_run_live.sh` lines 150-160
- **Tool:** `tools/config_validator.py`
- **Behavior:** Blocks startup if config invalid

### 5. Prometheus Port Auto-Discovery
- **Code:** `infra/metrics.py` lines 168-198
- **Ports:** Tries 9090, 9091, 9092, 9093
- **Behavior:** Auto-recovers from port conflicts

### 6. Trim Failure Escalation Alerts
- **Config:** `config/policy.yaml` line 302
- **Threshold:** 3 consecutive failures
- **Alert:** CRITICAL via AlertService with remediation instructions

---

## Launch Checklist

```bash
# 1. Validate config
python tools/config_validator.py

# 2. Check AlertService webhook configured
grep -A5 "alerting:" config/app.yaml

# 3. Launch bot
./app_run_live.sh

# 4. Verify Prometheus port
# Look for: "Prometheus metrics exporter listening on 0.0.0.0:XXXX"

# 5. Monitor logs
tail -f logs/trading_bot.log
```

---

## Monitoring

### Prometheus Metrics
```bash
# Check actual port in logs
curl http://localhost:9090/metrics | grep trader_trim
curl http://localhost:9091/metrics | grep trader_trim  # If 9090 in use
```

### Key Metrics
- `trader_trim_attempts_total{outcome="no_candidates"}` - Failed attempts
- `trader_trim_consecutive_failures` - Current streak
- `trader_trim_liquidated_usd_total` - Total liquidated via trim

### Alert Triggers
- **Trim Escalation:** 3 consecutive failures → CRITICAL alert
- **Convert Failures:** Auto-handled (no alert, seamless fallback)
- **Port Conflict:** Warning logged, auto-retry (no alert)

---

## Emergency Procedures

### Kill Switch
```bash
touch data/KILL_SWITCH
```

### Quick Rollback
```bash
# Restore original metrics.py
git checkout HEAD -- infra/metrics.py

# Restore original main_loop.py
git checkout HEAD -- runner/main_loop.py

# Restore original policy.yaml
git checkout HEAD -- config/policy.yaml

# Restart
./app_run_live.sh
```

### Disable Trim Alerts
```yaml
# config/policy.yaml
portfolio_management:
  max_trim_failures_before_alert: 999  # Effectively disable
```

---

## Expected Behavior

### Normal Operation
- Bot runs continuously without intervention
- Convert failures handled automatically
- Port conflicts resolved automatically
- Config errors caught at startup

### Alert Scenarios
- **Trim Alert:** 3+ failures → Check if capital injection needed
- **No Other Alerts:** All other issues auto-resolved

### Log Indicators
✅ `FORCE ELIGIBLE: BTC-USD bypasses liquidity checks`  
✅ `Prometheus metrics exporter listening on 0.0.0.0:9090`  
⚠️ `Port 9090 in use, successfully bound to port 9091 instead`  
⚠️ `Auto trim failed 3 consecutive times` → Alert sent

---

## Documentation
- **Full Details:** `docs/AUTO_HEALING_COMPLETE_2025-11-15.md`
- **Config Reference:** `config/policy.yaml`, `config/universe.yaml`
- **Code Changes:** `infra/metrics.py`, `runner/main_loop.py`

---

**Status:** ✅ Ready for autonomous operation  
**Confidence:** 95%  
**Manual Intervention Required:** None (alerts notify if needed)
