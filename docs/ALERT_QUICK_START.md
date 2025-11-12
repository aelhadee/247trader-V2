# Alert System Quick Start

## Test Alerts (Phase 0)

### 1. Get a Slack Webhook (Fastest Method)

1. Go to your Slack workspace
2. Navigate to: https://api.slack.com/messaging/webhooks
3. Click "Create your Slack app" → "From scratch"
4. Name it "247trader Alerts" and select your workspace
5. Under "Incoming Webhooks", toggle "Activate Incoming Webhooks" to ON
6. Click "Add New Webhook to Workspace"
7. Choose a channel (create `#247trader-alerts` if needed)
8. Copy the webhook URL

### 2. Set Environment Variable

```bash
export ALERT_WEBHOOK_URL='https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX'
```

### 3. Run Test Script

**Dry run first (no actual webhooks):**
```bash
python scripts/test_alerts.py --dry-run
```

**Live test (sends to Slack):**
```bash
python scripts/test_alerts.py
```

Expected output:
```
======================================================================
  Production Alert System Test
======================================================================

✅ AlertService initialized
   Webhook URL: https://hooks.slack.com/services/...
   Dry Run: False
   Timeout: 10.0s

Running 3 alert delivery tests...

Test 1/3: INFO
   ✅ Alert sent successfully
      Title: 🧪 Test Alert - INFO
      Message: This is a test INFO alert from 247trader-v2

Test 2/3: WARNING
   ✅ Alert sent successfully
      Title: ⚠️ Test Alert - WARNING
      Message: This is a test WARNING alert from 247trader-v2

Test 3/3: CRITICAL
   ✅ Alert sent successfully
      Title: 🚨 Test Alert - CRITICAL
      Message: This is a test CRITICAL alert from 247trader-v2

======================================================================
  Test Results: 3/3 alerts delivered
======================================================================

✅ ALL TESTS PASSED

📬 Check your alert destination to verify delivery!
   (Slack channel, PagerDuty, email, etc.)
```

### 4. Verify in Slack

Check your `#247trader-alerts` channel. You should see 3 test messages + 5 production scenario messages.

### 5. Enable in Production

Edit `config/app.yaml`:
```yaml
alerting:
  enabled: true
  webhook_url: "${ALERT_WEBHOOK_URL}"
  min_severity: "warning"  # Only WARNING and CRITICAL alerts
  dry_run: false
```

## What's Wired

### ✅ Already Alerting
- Kill switch activated (`data/KILL_SWITCH`)
- Daily stop loss hit (< -3% PnL)
- Weekly stop loss hit (< -7% PnL)
- Max drawdown breached (> 10%)

### 🚧 TODO (Phase 1)
- Exchange circuit breaker tripped
- API error bursts
- Position reconciliation mismatches
- Order rejection spikes
- Empty universe (no eligible assets)

## Production Checklist

- [ ] Slack webhook configured
- [ ] Test script passes (`python scripts/test_alerts.py`)
- [ ] Alerts visible in Slack channel
- [ ] Alert formatting is readable
- [ ] `config/app.yaml` has `alerting.enabled: true`
- [ ] On-call team has access to alert channel
- [ ] Response procedures documented (see `docs/ALERT_SYSTEM_SETUP.md`)

## Troubleshooting

**Alerts not appearing?**
```bash
# Check webhook URL
echo $ALERT_WEBHOOK_URL

# Test with dry run (should show logs)
python scripts/test_alerts.py --dry-run

# Verify Slack webhook is valid
curl -X POST $ALERT_WEBHOOK_URL \
  -H 'Content-Type: application/json' \
  -d '{"text":"Test from curl"}'
```

**Need help?** See full documentation: `docs/ALERT_SYSTEM_SETUP.md`
