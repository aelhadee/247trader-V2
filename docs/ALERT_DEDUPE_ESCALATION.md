# Alert Deduplication & Escalation

**Status:** ✅ Implemented and tested (REQ-AL1)

## Overview

AlertService implements intelligent alert deduplication and escalation to prevent notification storms while ensuring critical issues get visibility:

- **Deduplication (60s)**: Suppress identical alerts within 60-second window
- **Escalation (2m)**: Boost severity and optionally route to separate webhook after 2 minutes unresolved
- **Fingerprinting**: SHA256 hash of `severity|title|message` for unique alert identity
- **Lifecycle management**: Alerts have complete lifecycle from first occurrence through escalation to resolution

## Configuration

```yaml
monitoring:
  alerts_enabled: true
  alerts:
    webhook_url: "https://slack.webhook.url/alert"
    min_severity: "warning"
    
    # Deduplication window (seconds)
    dedupe_seconds: 60.0
    
    # Escalation window (seconds) - alert escalates if unresolved
    escalation_seconds: 120.0
    
    # Optional separate webhook for escalated alerts
    escalation_webhook_url: "https://pagerduty.webhook.url"
    
    # Severity boost on escalation (1 = one level up)
    escalation_severity_boost: 1  # INFO→WARNING→CRITICAL
```

## Behavior

### Deduplication

**Fixed 60s window from first occurrence:**

```python
# Example timeline
t=0s:   Alert "Circuit breaker tripped" → sends webhook
t=30s:  Same alert fires → deduped (within 60s)
t=59s:  Same alert fires → deduped (within 60s)
t=61s:  Same alert fires → sends webhook (window expired, resets)
```

**Fingerprinting:**
- Alerts with same `(severity, title, message)` tuple get same fingerprint
- Different severity = different alert (WARNING vs CRITICAL treated separately)
- Deduped alerts still update `last_seen` and increment `count` for analysis

### Escalation

**2-minute unresolved window:**

```python
# Example timeline
t=0s:    Alert "API errors" (WARNING) → sends webhook
t=30s:   Same alert → deduped
t=60s:   Same alert → deduped
t=121s:  Same alert → ESCALATES (>2min unresolved)
         - Severity boosted: WARNING → CRITICAL
         - Title prefixed: "🚨 ESCALATED: API errors"
         - Message appended: "(unresolved for 121s, 3 occurrences)"
         - Sent to escalation_webhook_url (if configured) or primary webhook
t=180s:  Same alert → deduped (already escalated, no spam)
```

**Escalation metadata:**
- `escalated: true` flag in context
- `first_seen_seconds_ago`: duration since first occurrence
- `occurrence_count`: how many times alert fired before escalation

**Prevention of re-escalation:**
Once escalated, further identical alerts are deduped until:
1. Alert is marked resolved via `resolve_alert()`, OR
2. 5+ minutes pass with no occurrences (stale, lifecycle resets)

### Resolution

```python
# In risk engine or main loop
if circuit_breaker_cleared:
    alert_service.resolve_alert(
        severity=AlertSeverity.WARNING,
        title="Circuit breaker tripped",
        message=f"Triggered at {timestamp}"
    )
```

Resolved alerts:
- Stop escalation clock (won't escalate even if >2m)
- Allow new occurrences after window expires (treated as new issue)

## Alert Lifecycle

```
NEW → DEDUPED → ESCALATED → RESOLVED/STALE → [reset] → NEW
      (0-60s)   (2m unresolved)  (5m or resolve)
```

**State transitions:**
1. **NEW**: First occurrence, `first_seen` timestamp set, sent immediately
2. **DEDUPED**: Within 60s of first occurrence, suppressed but counted
3. **ESCALATED**: After 2m unresolved, severity boosted and sent to escalation webhook
4. **RESOLVED**: Manually marked resolved, stops escalation
5. **STALE**: 5+ minutes with no activity, lifecycle resets on next occurrence

## Operational Guidelines

### When to Enable

**Enable in PAPER and LIVE modes:**
- Deduplication prevents Slack/PagerDuty spam during volatile market conditions
- Escalation ensures unresolved issues get elevated visibility

**Disable in DRY_RUN:**
- Set `alerts.dry_run: true` to suppress actual webhook calls while logging behavior

### Alert Routing Strategy

**Primary webhook** (e.g., Slack):
- Normal severity alerts (INFO, WARNING)
- Good for awareness and monitoring

**Escalation webhook** (e.g., PagerDuty):
- Critical/unresolved issues requiring immediate attention
- Set `escalation_webhook_url` to route escalated alerts separately

**Example setup:**
```yaml
alerts:
  webhook_url: "${SLACK_WEBHOOK_URL}"  # Normal alerts
  escalation_webhook_url: "${PAGERDUTY_WEBHOOK_URL}"  # Escalations only
```

### Tuning Parameters

**Dedupe window (60s default):**
- Too short: Alert storms in volatile markets
- Too long: Miss important state changes
- 60s is sweet spot for 1-minute loop cycles

**Escalation window (120s default):**
- Too short: False alarms for transient issues
- Too long: Real problems go unnoticed
- 120s = 2 loop cycles, reasonable for identifying persistent issues

**Severity boost (1 default):**
- `0`: No boost, only route to escalation webhook
- `1`: One level up (INFO→WARNING, WARNING→CRITICAL)
- `2`: Two levels up (INFO→CRITICAL)

## Testing

**Test suite:** `tests/test_alert_sla.py` (18 tests)

**Coverage:**
- ✅ Deduplication within 60s window
- ✅ Window expiry and reset
- ✅ Different fingerprints not deduped
- ✅ Severity affects fingerprinting
- ✅ Escalation after 2m unresolved
- ✅ Severity boosting on escalation
- ✅ Resolved alerts skip escalation
- ✅ Prevention of re-escalation
- ✅ Metadata inclusion in escalated alerts
- ✅ Configuration loading
- ✅ History tracking
- ✅ Cleanup of old alerts

**Run tests:**
```bash
pytest tests/test_alert_sla.py -v
```

## Integration Points

### RiskEngine

```python
# Kill switch alerts
if kill_switch_active:
    alert_service.notify(
        AlertSeverity.CRITICAL,
        "Kill switch activated",
        f"Trading halted by {reason}",
        context={"reason": reason, "timestamp": now}
    )

# When cleared
if kill_switch_cleared:
    alert_service.resolve_alert(
        AlertSeverity.CRITICAL,
        "Kill switch activated",
        f"Trading halted by {reason}"
    )
```

### Circuit Breakers

```python
# Breaker trip
if breaker_tripped:
    alert_service.notify(
        AlertSeverity.WARNING,
        f"Circuit breaker: {breaker_name}",
        f"Triggered: {condition}",
        context={"breaker": breaker_name, "value": value, "threshold": threshold}
    )

# Auto-resolve on recovery
if breaker_recovered:
    alert_service.resolve_alert(
        AlertSeverity.WARNING,
        f"Circuit breaker: {breaker_name}",
        f"Triggered: {condition}"
    )
```

### API/Exchange Errors

```python
# Persistent errors trigger escalation
if exchange_error_count > 3:
    alert_service.notify(
        AlertSeverity.WARNING,
        "Exchange API errors",
        f"{error_count} consecutive failures",
        context={"last_error": str(error), "error_count": error_count}
    )
    # If errors persist >2m, auto-escalates to CRITICAL
```

## Monitoring

**Alert metrics to track:**
- Total alerts sent (by severity)
- Deduped alerts count
- Escalated alerts count
- Average time to escalation
- Average occurrences before escalation
- Resolved vs. stale alerts ratio

**Logs to watch:**
```
INFO  - Alert sent: <title> [<severity>]
DEBUG - Alert deduped: <title> (fingerprint=<hash>)
WARNING - Escalating alert: <title> (unresolved for <N>s)
DEBUG - Alert resolved: <title>
```

## Implementation Details

**Fingerprinting:**
```python
fingerprint = sha256(f"{severity.name}|{title}|{message}".encode()).hexdigest()
```

**History cleanup:**
- Periodic cleanup every 60s
- Removes alerts >5 minutes old
- Prevents memory leaks in long-running processes

**Thread safety:**
- Not thread-safe (assumes single-threaded main loop)
- Add locking if calling from multiple threads

## Future Enhancements

**Potential improvements:**
- [ ] Alert grouping (multiple related alerts → single escalation)
- [ ] Configurable escalation chains (WARNING after 1m, CRITICAL after 5m)
- [ ] Alert acknowledgment (pause escalation when human responds)
- [ ] Rich formatting (Slack blocks, PagerDuty structured payloads)
- [ ] Alert history API (query past alerts, generate reports)

## References

- **APP_REQUIREMENTS.md**: REQ-AL1 (Alert SLA & dedupe)
- **infra/alerting.py**: Implementation
- **tests/test_alert_sla.py**: Test suite
- **core/risk.py**: Integration in RiskEngine
