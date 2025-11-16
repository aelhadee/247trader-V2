# Pytest TMPDIR Fix

**Date:** 2025-11-15  
**Issue:** Pytest hanging/failing due to no writable temporary directory

---

## Problem

Pytest was appearing to hang indefinitely when running the test suite. The root cause was:

```
FileNotFoundError: [Errno 2] No usable temporary directory found in [..., '/Users/ahmed/coding-stuff/trader/247trader-v2']
```

Pytest requires a writable temporary directory for:
- Capturing stdout/stderr
- Creating temporary test files
- Storing internal state

It checks these locations in order:
1. `$TMPDIR` environment variable
2. `/tmp`
3. `/var/tmp`
4. `/usr/tmp`
5. Current working directory

In certain environments (like VS Code Copilot workspace), none of these locations may be writable, causing pytest to abort immediately.

---

## Solution

### 1. Created Local Temp Directory

```bash
mkdir -p .pytest_tmp
```

Added to `.gitignore`:
```
.pytest_tmp/
```

### 2. Created Test Runner Script

Created `run_tests.sh` that automatically sets `TMPDIR`:

```bash
#!/bin/bash
# Ensure pytest temp directory exists
PYTEST_TMP="${SCRIPT_DIR}/.pytest_tmp"
mkdir -p "${PYTEST_TMP}"
export TMPDIR="${PYTEST_TMP}"

# Run pytest
python -m pytest ${ARGS}
```

### 3. Usage

```bash
# Run all tests
./run_tests.sh

# Run specific test file
./run_tests.sh tests/test_core.py

# Run with options
./run_tests.sh tests/ -v --durations=20

# Run matching pattern
./run_tests.sh -k test_config
```

Or manually set TMPDIR:
```bash
TMPDIR=$PWD/.pytest_tmp python -m pytest tests/
```

---

## Test Performance

With TMPDIR fixed, we can now measure test performance:

**Core tests (test_core.py):**
```
5.77s - test_trigger_scanning (fetches live market data)
5.67s - test_rules_engine (processes triggers)
5.60s - test_full_cycle (full trading loop)
4.99s - test_universe_building (Coinbase API calls)
0.47s - test_config_loading
0.03s - test_risk_checks
```

**Total:** 22.54s for 6 tests

The slower tests make actual API calls to Coinbase for universe discovery and market data. This is expected for integration tests.

---

## CI/CD Integration

For CI environments, add to workflow:

```yaml
- name: Run tests
  env:
    TMPDIR: ${{ github.workspace }}/.pytest_tmp
  run: |
    mkdir -p .pytest_tmp
    python -m pytest tests/
```

---

## Benefits

1. **No More Hangs:** Pytest can now create temp files
2. **Local Temp Storage:** Controlled cleanup, .gitignored
3. **Portable:** Works across different environments
4. **Convenient:** `run_tests.sh` handles setup automatically
5. **Performance Insight:** Can now measure test durations with `--durations`

---

## Related Files

- `.gitignore` - Added `.pytest_tmp/` exclusion
- `run_tests.sh` - Test runner with TMPDIR setup
- `docs/PYTEST_TMPDIR_FIX.md` - This documentation
