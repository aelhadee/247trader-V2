# Alert System Setup Guide

## Overview

The 247trader-v2 alert system notifies operators of critical trading events via webhooks (Slack, PagerDuty, email, etc.). This guide covers setup, testing, and production configuration.

## Quick Start

### 1. Configure Webhook URL

**Slack (Recommended for Phase 0):**
```bash
# Create incoming webhook in Slack: https://api.slack.com/messaging/webhooks
export ALERT_WEBHOOK_URL='https://hooks.slack.com/services/YOUR/WEBHOOK/URL'
```

**PagerDuty:**
```bash
export ALERT_WEBHOOK_URL='https://events.pagerduty.com/v2/enqueue'
# Requires additional headers - see PagerDuty integration below
```

**Generic Webhook:**
```bash
export ALERT_WEBHOOK_URL='https://your-webhook-endpoint.com/alerts'
```

### 2. Test Alert Delivery

**Dry run (no actual webhooks sent):**
```bash
python scripts/test_alerts.py --dry-run
```

**Live test to Slack/webhook:**
```bash
python scripts/test_alerts.py
```

**Production scenario tests:**
```bash
python scripts/test_alerts.py --scenarios-only
```

### 3. Enable in Trading System

Edit `config/app.yaml`:
```yaml
alerting:
  enabled: true
  webhook_url: "${ALERT_WEBHOOK_URL}"
  min_severity: "warning"  # info | warning | critical
  dry_run: false
  timeout_seconds: 10.0
```

## Alert Severity Levels

| Severity | Use Case | Production Threshold |
|----------|----------|---------------------|
| **INFO** | Routine events (cycle start, config load) | Usually disabled |
| **WARNING** | Degraded state, approaching limits | Enabled (log + notify) |
| **CRITICAL** | System halt, breaches, failures | Always enabled |

**Production recommendation:** Set `min_severity: warning` to avoid alert fatigue.

## Alert Matrix (Production Events)

### 🚨 CRITICAL Alerts (Immediate Response Required)

| Event | Trigger | Context |
|-------|---------|---------|
| **Kill Switch Activated** | `data/KILL_SWITCH` file detected | Open positions, orders, PnL state |
| **Daily Stop Loss Hit** | PnL < -3% for day | Current PnL, threshold, NAV |
| **Weekly Stop Loss Hit** | PnL < -7% for week | Weekly PnL, threshold, NAV |
| **Max Drawdown Breached** | Drawdown > 10% | Peak NAV, current NAV, DD% |
| **Exchange Circuit Breaker** | API errors > 3 consecutive | Error count, last error, action taken |
| **Data Staleness** | OHLCV/quotes > max age | Age, threshold, affected symbols |
| **Reconciliation Failure** | Position mismatch with exchange | Symbol, local vs exchange quantities |
| **Order Explosion** | Rejected orders > threshold | Rejection count, reasons, window |

### ⚠️ WARNING Alerts (Monitor but Not Urgent)

| Event | Trigger | Context |
|-------|---------|---------|
| **Approaching Daily Stop** | PnL < -2.5% (but > -3%) | Current PnL, threshold proximity |
| **High API Error Rate** | 2 API errors in window | Error count, error types |
| **Cooldown Active** | Symbol/global cooldown triggered | Symbol, duration, reason |
| **Low Liquidity** | Eligible assets < threshold | Asset count, excluded reasons |
| **Order Rejection** | Single order rejected | Symbol, rejection reason, order details |
| **Slippage Excessive** | Fill price vs quote > threshold | Symbol, expected vs actual, slippage % |

### ℹ️ INFO Alerts (Disabled by Default)

| Event | Trigger | Context |
|-------|---------|---------|
| System Start | Trading loop initialized | Mode, config version, NAV |
| Graceful Shutdown | Clean stop requested | Open positions, orders canceled |
| Config Reload | Configuration updated | Changed settings, validation status |

## Integration Examples

### Slack

1. Create incoming webhook: https://api.slack.com/messaging/webhooks
2. Copy webhook URL
3. Set environment variable:
   ```bash
   export ALERT_WEBHOOK_URL='https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX'
   ```
4. Test:
   ```bash
   python scripts/test_alerts.py
   ```

**Slack payload format:**
```json
{
  "text": "[CRITICAL] 🚨 Kill Switch Activated | Trading halted: data/KILL_SWITCH file detected | context={\"open_positions\": 3, \"unrealized_pnl_pct\": -2.3}"
}
```

### PagerDuty (Advanced)

**Note:** Current implementation uses simple webhook format. For full PagerDuty Events API v2 integration, customize `AlertService._build_payload()`.

Basic setup:
```bash
export ALERT_WEBHOOK_URL='https://events.pagerduty.com/v2/enqueue'
```

For production PagerDuty, extend `AlertService` to include:
- `routing_key` (integration key)
- `event_action` (trigger/acknowledge/resolve)
- `dedup_key` for event grouping
- Custom severity mapping

### Email (via Webhook Service)

Use a webhook-to-email service like:
- **Zapier**: Webhook → Email action
- **IFTTT**: Webhook → Email applet
- **AWS SNS**: Webhook → SNS topic → Email subscription
- **SendGrid/Mailgun**: Direct API integration

## Testing Checklist

Before production launch:

- [ ] **Dry run test passes** (`--dry-run`)
- [ ] **Basic delivery test passes** (3 severity levels delivered)
- [ ] **Scenario tests pass** (5 production scenarios delivered)
- [ ] **Alerts appear in destination** (Slack/PagerDuty/email)
- [ ] **Alert formatting is readable** (severity, title, message, context)
- [ ] **On-call routing works** (correct team/person notified)
- [ ] **Alert response procedures documented** (runbooks for each alert)
- [ ] **Muting/filtering configured** (avoid alert fatigue)

## Configuration Reference

### `config/app.yaml`

```yaml
alerting:
  # Enable/disable alert system
  enabled: true
  
  # Webhook URL (supports env var expansion)
  webhook_url: "${ALERT_WEBHOOK_URL}"
  
  # Alternative: specify env var name to read URL from
  webhook_env: "ALERT_WEBHOOK_URL"
  
  # Minimum severity to send (info | warning | critical)
  min_severity: "warning"
  
  # Dry run mode (log only, no webhooks)
  dry_run: false
  
  # HTTP timeout for webhook calls
  timeout_seconds: 10.0
```

### Environment Variables

```bash
# Primary webhook URL
export ALERT_WEBHOOK_URL='https://your-webhook-url'

# Alternative Slack webhook (fallback)
export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...'

# PagerDuty routing key (if using PagerDuty)
export PAGERDUTY_ROUTING_KEY='your-integration-key'
```

## Alert Response Procedures

### Kill Switch Activated

**Response:**
1. Check why kill switch was triggered (logs, audit trail)
2. Review open positions and orders
3. Assess market conditions
4. Decide: resume trading or liquidate positions
5. Remove `data/KILL_SWITCH` file to resume (if safe)

**Runbook:** `docs/operations/runbook_kill_switch.md` (TODO)

### Daily Stop Loss Hit

**Response:**
1. Verify PnL accuracy (reconcile with exchange)
2. Review losing trades (symbol, entry/exit, conviction)
3. Check if stop was technical issue vs strategy failure
4. Decide: reduce capital, adjust risk params, or pause
5. System will auto-block new trades until daily reset

**Runbook:** `docs/operations/runbook_stop_loss.md` (TODO)

### Exchange Circuit Breaker

**Response:**
1. Check exchange status page (Coinbase status)
2. Review API error logs (rate limits, outages, auth)
3. Verify open orders and positions are intact
4. Wait for circuit breaker cooldown (60s default)
5. Monitor for recurring errors

**Runbook:** `docs/operations/runbook_circuit_breaker.md` (TODO)

### Reconciliation Mismatch

**Response:**
1. Compare local state vs exchange snapshot
2. Check for partial fills or missed webhooks
3. Review recent trade executions
4. System will auto-sync to exchange truth
5. Investigate cause to prevent recurrence

**Runbook:** `docs/operations/runbook_reconciliation.md` (TODO)

## Troubleshooting

### Alerts not appearing

**Check:**
1. `alerting.enabled: true` in `config/app.yaml`
2. `ALERT_WEBHOOK_URL` environment variable is set
3. Webhook URL is valid and accessible
4. `min_severity` threshold allows alert level
5. `dry_run: false` (not in test mode)
6. Firewall/network allows outbound HTTPS to webhook

**Debug:**
```bash
# Test with verbose logging
python scripts/test_alerts.py --dry-run  # Should show log output

# Test actual delivery
python scripts/test_alerts.py  # Should deliver to webhook

# Check webhook URL
echo $ALERT_WEBHOOK_URL
```

### Webhook timeouts

**Symptoms:** Alerts slow, cycle delays, timeout errors

**Fix:**
1. Increase `timeout_seconds` in config (default 10.0)
2. Check webhook endpoint latency
3. Consider async alert delivery (future enhancement)
4. Use faster webhook service

### Alert fatigue

**Symptoms:** Too many alerts, team ignoring notifications

**Fix:**
1. Raise `min_severity` to `critical` only
2. Adjust thresholds (e.g., daily stop -3% → -4%)
3. Add alert debouncing (future enhancement)
4. Use alert aggregation service (PagerDuty)
5. Configure "quiet hours" in webhook service

### Missing context in alerts

**Symptoms:** Alert unclear, insufficient detail for response

**Fix:**
1. Enhance context dict in alert call
2. Add relevant state (positions, orders, PnL)
3. Include timestamps and thresholds
4. Link to audit logs or dashboards

## Security Considerations

### Webhook URL Protection

**DO:**
- Store webhook URLs in environment variables
- Use secret management (AWS Secrets Manager, Vault)
- Rotate webhook URLs periodically
- Restrict webhook URL access (IP allowlist if possible)
- Use HTTPS webhooks only

**DON'T:**
- Commit webhook URLs to git
- Share webhook URLs in chat/email
- Use HTTP (unencrypted) webhooks
- Expose webhook URLs in logs

### Sensitive Data in Alerts

**Guidelines:**
- **Never** include API keys, secrets, or passwords
- **Mask** account identifiers in public channels
- **Redact** PII (personally identifiable information)
- **Limit** position sizes to percentages (not absolute values)
- **Use** private Slack channels for financial alerts

## Next Steps (Phase 0 → Phase 1)

After alert system is verified:

1. **Wire RiskEngine alerts** (kill switch, stops, circuit breakers)
2. **Add ExecutionEngine alerts** (order rejections, fills)
3. **Implement alert matrix** (all production scenarios)
4. **Create runbooks** (response procedures for each alert)
5. **Set up monitoring dashboard** (alert frequency, response time)

**Production readiness gate:** All critical alerts must be:
- Tested in staging
- Routed to on-call
- Documented with runbooks
- Acknowledged < 5 minutes

---

**Status:** Phase 0 - Alert infrastructure ready, webhook testing required  
**Last updated:** 2025-11-11  
**Owner:** Platform Team
