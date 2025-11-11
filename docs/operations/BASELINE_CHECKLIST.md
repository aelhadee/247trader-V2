# Phase 0 Operational Baseline Checklist

## 1. Logging & Observability
- [ ] Confirm log directory rotation strategy (e.g., logrotate or TimedRotatingFileHandler).
- [ ] Standardize log format (timestamp, level, module, cycle_id, symbol, order_id).
- [ ] Enable structured logging for order events (JSON or key=value pairs).
- [ ] Define log retention policy and archival location.

## 2. Configuration Management
- [x] Implement schema validation for `app.yaml`, `policy.yaml`, `universe.yaml` (pydantic or voluptuous).
- [ ] Document configuration defaults and override hierarchy.
- [x] Add configuration self-check command (`python -m tools.config_check`).
- [ ] Store sample `.env` / secrets template (without credentials) for onboarding.

## 3. Secrets & Credentials
- [ ] Decide on secrets backend (env vars, Vault, AWS Secrets Manager, etc.).
- [ ] Document key rotation process and cadence.
- [ ] Add runtime guard that refuses LIVE mode without HMAC credentials.
- [ ] Provide redaction utilities for logs and support dumps.

## 4. Deployment & Runtime
- [ ] Containerize application or provide reproducible environment script.
- [ ] Introduce process supervision (systemd, Supervisor, or Kubernetes manifest).
- [ ] Establish restart policy and health-check endpoints/commands.
- [ ] Document rollback procedure (stop bot, revert config, restart).

## 5. Monitoring & Alerting
- [ ] Define critical alerts (execution failures, repeated rejects, account balance anomalies).
- [ ] Integrate with notification channels (Slack webhook/email).
- [ ] Track key metrics baseline (cycle duration, fill rate, PnL, rejection count).
- [ ] Schedule regular operational reviews (log audit, balance verification).

## 6. Testing & Quality Gates
- [ ] Convert pytest return-based checks to assertions; enforce coverage threshold.
- [ ] Set up CI job for tests + lint.
- [ ] Provide sandbox/paper smoke test script before LIVE deployment.
- [ ] Maintain changelog for production deployments.

## 7. Documentation & Runbooks
- [ ] Create operator runbook (start/stop, log locations, emergency steps).
- [ ] Document incident response workflow and escalation contacts.
- [ ] Update README with Phase 0 artifacts and quickstart instructions.
- [ ] Keep roadmap and architecture docs synchronized with implementation.

---
Use this checklist to gate Phase 0 completion before progressing to Phase 1. Update status weekly and assign owners per item.
