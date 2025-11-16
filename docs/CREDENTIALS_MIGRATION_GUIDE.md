# Credentials Migration Guide

**Date:** 2025-11-15 (Updated: 2025-01-15)  
**Change:** Enforced environment-only credentials (file-based loading removed from application code)  
**Impact:** Application now requires credentials in environment variables  

---

## ⚠️ IMPORTANT: Environment-Only Enforcement (2025-01-15)

**Application code NO LONGER loads credentials from files directly.**

All credentials MUST be provided via environment variables:
- `CB_API_KEY` or `COINBASE_API_KEY`
- `CB_API_SECRET` or `COINBASE_API_SECRET`

**Helper script available:** `scripts/load_credentials.sh` can load from JSON file into environment.

**Why:** Security hardening - reduces risk of accidental credential exposure.

---

## What Changed

### Before (Deprecated)
```bash
export CB_API_SECRET_FILE="/path/to/cb_api.json"
python -m runner.main_loop
```

System would read JSON file directly.

### After (Current)
```bash
export CB_API_KEY="organizations/xxx/apiKeys/yyy"
export CB_API_SECRET="-----BEGIN EC PRIVATE KEY-----..."
python -m runner.main_loop
```

System reads from environment variables only (more secure).

---

## Quick Start

### Option 1: Use Updated Launch Script (Automatic)
```bash
./app_run_live.sh --loop
```

The script now automatically:
1. Loads credentials from `/Users/ahmed/coding-stuff/trader/cb_api.json`
2. Exports as `CB_API_KEY` and `CB_API_SECRET` environment variables
3. Passes them to the trading system

**No manual changes needed!**

### Option 2: Manual Environment Variables
```bash
# Load credentials helper
source scripts/load_credentials.sh

# Verify loaded
echo "Key: ${CB_API_KEY:0:30}..."
echo "Secret: ${CB_API_SECRET:0:30}..."

# Run system
python -m runner.main_loop --interval 60
```

### Option 3: Export Directly
```bash
export CB_API_KEY=$(jq -r '.name' /Users/ahmed/coding-stuff/trader/cb_api.json)
export CB_API_SECRET=$(jq -r '.privateKey' /Users/ahmed/coding-stuff/trader/cb_api.json)

python -m runner.main_loop --interval 60
```

---

## Verification

### 1. Check Credentials Are Loaded
```bash
echo "CB_API_KEY set: ${CB_API_KEY:+YES}"
echo "CB_API_SECRET set: ${CB_API_SECRET:+YES}"
```

Expected output:
```
CB_API_KEY set: YES
CB_API_SECRET set: YES
```

### 2. Test API Connection
```bash
source .venv/bin/activate
export CB_API_KEY=$(jq -r '.name' /Users/ahmed/coding-stuff/trader/cb_api.json)
export CB_API_SECRET=$(jq -r '.privateKey' /Users/ahmed/coding-stuff/trader/cb_api.json)

python3 -c "
from core.exchange_coinbase import CoinbaseExchange
ex = CoinbaseExchange()
accounts = ex.get_accounts()
print(f'✅ Connected! Found {len(accounts)} accounts')
"
```

Expected output:
```
✅ Connected! Found X accounts
```

---

## Troubleshooting

### Error: "COINBASE_API_KEY and COINBASE_API_SECRET required"

**Cause:** Environment variables not set

**Solution:**
```bash
# Check if variables are set
env | grep CB_API

# If empty, load credentials
source scripts/load_credentials.sh

# Or use the launch script which does this automatically
./app_run_live.sh --loop
```

### Error: "jq: command not found"

**Cause:** JSON parser not installed

**Solution:**
```bash
# macOS
brew install jq

# Linux
sudo apt-get install jq  # Debian/Ubuntu
sudo yum install jq      # RedHat/CentOS
```

### Error: "Cannot read file" when loading credentials

**Cause:** Credentials file path incorrect

**Solution:**
```bash
# Check file exists
ls -la /Users/ahmed/coding-stuff/trader/cb_api.json

# If different location, update CRED_FILE in scripts
nano scripts/load_credentials.sh
nano app_run_live.sh
```

### Credentials Load But API Still Fails

**Cause:** JSON structure mismatch

**Solution:**
```bash
# Check JSON structure
jq '.' /Users/ahmed/coding-stuff/trader/cb_api.json

# Should have:
# {
#   "name": "organizations/.../apiKeys/...",
#   "privateKey": "-----BEGIN EC PRIVATE KEY-----..."
# }

# If different, adjust jq commands in scripts
```

---

## Security Notes

### Why This Change?

1. **No files in repo:** Environment variables prevent accidental commits
2. **Process isolation:** Each process gets own credentials
3. **Better rotation:** Change env vars without touching files
4. **Cloud-native:** Compatible with Docker, Kubernetes secrets
5. **Industry standard:** Follows 12-factor app methodology

### Best Practices

✅ **DO:**
- Keep `cb_api.json` outside repo (in parent directory)
- Use environment variables in production
- Rotate credentials every 90 days
- Use different keys for dev/staging/prod

❌ **DON'T:**
- Commit credentials to git
- Share credentials via Slack/email
- Use production keys in development
- Log credentials (they're automatically redacted)

---

## Production Deployment

### Docker
```dockerfile
# Dockerfile
FROM python:3.12
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

# Expects CB_API_KEY and CB_API_SECRET from environment
CMD ["python", "-m", "runner.main_loop", "--interval", "60"]
```

```bash
# Run with secrets
docker run -e CB_API_KEY="..." -e CB_API_SECRET="..." trader
```

### Kubernetes
```yaml
# secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: coinbase-creds
type: Opaque
stringData:
  api-key: "organizations/.../apiKeys/..."
  api-secret: "-----BEGIN EC PRIVATE KEY-----..."
```

```yaml
# deployment.yaml
env:
  - name: CB_API_KEY
    valueFrom:
      secretKeyRef:
        name: coinbase-creds
        key: api-key
  - name: CB_API_SECRET
    valueFrom:
      secretKeyRef:
        name: coinbase-creds
        key: api-secret
```

### systemd Service
```ini
# /etc/systemd/system/247trader.service
[Service]
Environment="CB_API_KEY=organizations/.../apiKeys/..."
Environment="CB_API_SECRET=-----BEGIN EC PRIVATE KEY-----..."
ExecStart=/usr/local/bin/247trader
```

---

## Migration Checklist

- [x] Updated `app_run_live.sh` to load credentials from JSON
- [x] Created `scripts/load_credentials.sh` helper
- [x] Tested credential loading with `jq`
- [x] Verified API connection works
- [ ] Update any custom scripts to use env vars
- [ ] Update deployment configurations (if any)
- [ ] Test in all environments (dev, staging, prod)

---

## Rollback Plan

If issues occur, temporarily revert secrets hardening:

```bash
# 1. Checkout previous version of exchange_coinbase.py
git diff HEAD~1 core/exchange_coinbase.py

# 2. Manually add back CB_API_SECRET_FILE support
# (See docs/SECRETS_HARDENING_2025-11-15.md for before/after code)

# 3. Use old launch method
export CB_API_SECRET_FILE="/Users/ahmed/coding-stuff/trader/cb_api.json"
python -m runner.main_loop
```

---

## Support

**Issues?** Check logs for specific error messages:
```bash
tail -f logs/247trader-v2_audit.jsonl
```

**Need help?** See:
- `docs/SECRETS_HARDENING_2025-11-15.md` - Full implementation details
- `docs/COINBASE_API_SETUP.md` - Credential setup guide
- Test file: `tests/test_secrets_hardening.py` - 18 test cases
