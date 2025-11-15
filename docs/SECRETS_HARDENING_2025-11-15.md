# Secrets Hardening - Security Enhancement

**Date:** 2025-11-15  
**Status:** ✅ Complete (18 tests passing)  
**Impact:** HIGH (Security critical for LIVE deployment)

## Summary

Hardened credential handling in `CoinbaseExchange` to **require environment variables** and removed file-based fallback loading for improved security posture.

## Changes Made

### Before (Insecure)
```python
# Load credentials from JSON file if available
secret_file = os.getenv("CB_API_SECRET_FILE")
if secret_file and os.path.exists(secret_file):
    with open(secret_file, 'r') as f:
        creds = json.load(f)
        api_key = creds.get("name")
        api_secret = creds.get("privateKey")
```

**Problems:**
- File-based credentials can be committed to git accidentally
- File permissions errors could expose secrets
- No validation that credentials exist for LIVE mode
- Silent fallback to empty credentials

### After (Secure)
```python
# SECURITY: Credentials MUST come from environment variables or parameters only.
self.api_key = api_key or os.getenv("CB_API_KEY") or os.getenv("COINBASE_API_KEY", "")
secret_raw = (api_secret or os.getenv("CB_API_SECRET") or os.getenv("COINBASE_API_SECRET", ""))

# Validate credentials for non-read-only modes
if not read_only:
    if not self.api_key or not self.api_secret:
        raise ValueError(
            "LIVE mode requires credentials. Set CB_API_KEY and CB_API_SECRET environment variables."
        )
```

**Benefits:**
- Environment variables never committed to version control
- Fail-fast validation for LIVE mode
- Clear error messages with remediation steps
- Compatible with container orchestration (Docker, Kubernetes)

## Environment Variable Setup

### Required for LIVE Trading

```bash
# Coinbase Advanced Trade API credentials
export CB_API_KEY="your_api_key_here"
export CB_API_SECRET="your_api_secret_here"

# Alternative legacy names also supported
export COINBASE_API_KEY="your_api_key_here"
export COINBASE_API_SECRET="your_api_secret_here"
```

**Priority:** `CB_API_*` prefix takes precedence over `COINBASE_API_*`

### PEM Key Support (Cloud API)

For organization/cloud API keys using JWT/ES256 authentication:

```bash
export CB_API_KEY="organizations/YOUR_ORG_ID/apiKeys/YOUR_KEY_ID"
export CB_API_SECRET="-----BEGIN EC PRIVATE KEY-----
MIHcAgEBBEIB...
-----END EC PRIVATE KEY-----"
```

**Note:** PEM keys are automatically detected by the `-----BEGIN` prefix.

### Read-Only Mode (No Credentials Required)

```python
# Safe for testing/development without credentials
exchange = CoinbaseExchange(read_only=True)
```

## Deployment Guide

### Local Development

```bash
# Add to ~/.zshrc or ~/.bashrc
export CB_API_KEY="your_key"
export CB_API_SECRET="your_secret"

# Reload shell
source ~/.zshrc
```

### Production Server

```bash
# Add to systemd service file
[Service]
Environment="CB_API_KEY=your_key"
Environment="CB_API_SECRET=your_secret"
```

### Docker

```dockerfile
# Pass as environment variables
docker run -e CB_API_KEY="..." -e CB_API_SECRET="..." trader
```

Or use Docker secrets:

```bash
# Create secrets
echo "your_key" | docker secret create cb_api_key -
echo "your_secret" | docker secret create cb_api_secret -

# Use in compose
services:
  trader:
    secrets:
      - cb_api_key
      - cb_api_secret
```

### Kubernetes

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: coinbase-credentials
type: Opaque
stringData:
  CB_API_KEY: "your_key"
  CB_API_SECRET: "your_secret"
---
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
      - name: trader
        envFrom:
        - secretRef:
            name: coinbase-credentials
```

## Validation

### Startup Checks

**LIVE Mode (read_only=False):**
- ✅ Validates `CB_API_KEY` and `CB_API_SECRET` exist
- ✅ Fails immediately with clear error if missing
- ✅ Prevents accidental LIVE deployment without credentials

**PAPER/DRY_RUN Mode (read_only=True):**
- ✅ Allows missing credentials (safe for testing)
- ✅ Logs warning if credentials missing but continues
- ✅ No API calls will succeed, but system won't crash

### Error Messages

```python
# Missing credentials in LIVE mode
ValueError: LIVE mode requires credentials. Set CB_API_KEY and CB_API_SECRET 
environment variables. For read-only mode, pass read_only=True to bypass this check.
```

## Test Coverage

**18 comprehensive tests** in `tests/test_secrets_hardening.py`:

| Test Class | Tests | Coverage |
|------------|-------|----------|
| `TestCredentialLoading` | 10 | Parameter/env loading, precedence, validation |
| `TestPEMKeyHandling` | 3 | Cloud API PEM key detection |
| `TestSecurityHardening` | 2 | File-based loading removed |
| `TestReadOnlyMode` | 2 | Credential requirements by mode |
| `TestErrorMessages` | 1 | Clear error guidance |

```bash
$ pytest tests/test_secrets_hardening.py -v
============================== 18 passed in 0.32s ===============================
```

## Migration Guide

### For Existing Deployments Using CB_API_SECRET_FILE

**Old method (deprecated):**
```bash
export CB_API_SECRET_FILE="/path/to/credentials.json"
```

**New method (required):**
```bash
# Extract credentials from file manually
export CB_API_KEY=$(jq -r '.name' /path/to/credentials.json)
export CB_API_SECRET=$(jq -r '.privateKey' /path/to/credentials.json)

# Remove file from disk (after verifying env vars work)
rm /path/to/credentials.json
```

### Backward Compatibility

The following environment variable names are **still supported**:
- `COINBASE_API_KEY` → Falls back if `CB_API_KEY` not set
- `COINBASE_API_SECRET` → Falls back if `CB_API_SECRET` not set

**Recommendation:** Use `CB_API_*` prefix for consistency.

## Security Best Practices

1. **Never commit credentials** to version control
   ```bash
   # Add to .gitignore
   *.env
   credentials.json
   secrets/
   ```

2. **Use secret management** in production
   - AWS Secrets Manager
   - HashiCorp Vault
   - Kubernetes Secrets
   - Docker Secrets

3. **Rotate credentials regularly**
   - Generate new API keys quarterly
   - Track rotation in `infra/secret_rotation.py` (already implemented)

4. **Principle of least privilege**
   - Use read-only keys for PAPER/DRY_RUN modes
   - Restrict LIVE keys to minimum required permissions

5. **Audit access**
   - Log all credential access attempts
   - Monitor for unauthorized API usage
   - Set up alerts for suspicious activity

## Code Changes

**Modified:**
- `core/exchange_coinbase.py` (-15 lines file loading, +10 lines validation)
- Removed `CB_API_SECRET_FILE` file-based loading
- Added fail-fast validation for LIVE mode

**Added:**
- `tests/test_secrets_hardening.py` (+220 lines, 18 tests)
- `docs/SECRETS_HARDENING_2025-11-15.md` (this file)

## Production Readiness

**Status:** ✅ Production-ready for LIVE deployment

**Requirements Met:**
- REQ-SEC1: Secrets via environment only ✅
- No file fallbacks (security risk removed) ✅
- Fail-fast validation for LIVE mode ✅
- Clear error messages ✅
- Comprehensive test coverage (18 tests) ✅
- Backward compatible env var names ✅

**Before LIVE Deployment:**
1. ✅ Set `CB_API_KEY` and `CB_API_SECRET` environment variables
2. ✅ Verify credentials with `python -c "import os; print('OK' if os.getenv('CB_API_KEY') and os.getenv('CB_API_SECRET') else 'MISSING')"`
3. ✅ Test with `read_only=False` mode to trigger validation
4. ✅ Remove any `CB_API_SECRET_FILE` references
5. ✅ Verify credentials.json not in repository

---

**Implementation:** 2025-11-15  
**Author:** GitHub Copilot  
**Reviewed:** Automated testing  
**Status:** ✅ Complete (Security hardened)
