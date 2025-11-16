# Pytest Output Options - Quick Reference

## Verbosity Levels

```bash
# No flags = minimal output (just dots)
./run_tests.sh tests/test_core.py

# -v = verbose (show test names)
./run_tests.sh tests/test_core.py -v

# -vv = very verbose (show test names + extra details)
./run_tests.sh tests/test_core.py -vv
```

## Traceback Options (`--tb`)

```bash
# --tb=short (RECOMMENDED) - Short traceback, just the error
./run_tests.sh tests/test_core.py -v --tb=short

# --tb=line - One line per failure (good for overview)
./run_tests.sh tests/test_core.py --tb=line

# --tb=long - Full traceback with all frames
./run_tests.sh tests/test_core.py -vv --tb=long

# --tb=no - No traceback (just pass/fail summary)
./run_tests.sh tests/test_core.py --tb=no

# --tb=native - Python's default traceback
./run_tests.sh tests/test_core.py --tb=native
```

## Output Control

```bash
# -s = Show print statements (no capture)
./run_tests.sh tests/test_core.py -v -s

# -q = Quiet mode (less output)
./run_tests.sh tests/test_core.py -q

# --capture=no = Don't capture output
./run_tests.sh tests/test_core.py --capture=no
```

## Selective Running

```bash
# -x = Stop at first failure
./run_tests.sh tests/test_core.py -v -x

# --maxfail=3 = Stop after 3 failures
./run_tests.sh tests/test_core.py -v --maxfail=3

# --lf = Run last failed tests only
./run_tests.sh --lf -v

# --ff = Failed first, then rest
./run_tests.sh --ff -v

# -k pattern = Run tests matching pattern
./run_tests.sh -k "test_config" -v
```

## Summary Options

```bash
# --durations=10 = Show 10 slowest tests
./run_tests.sh tests/ --durations=10

# --durations=0 = Show all test durations
./run_tests.sh tests/test_core.py --durations=0

# -r chars = Show extra summary
# a = all except passed, A = all, f = failed, E = error, s = skipped
./run_tests.sh tests/ -v -ra  # Show summary of all
```

## Best Combinations

### For Debugging Failures
```bash
# Get full error details for specific test
./run_tests.sh tests/test_auto_trim.py::test_auto_trim_to_risk_cap_converts_excess_exposure -vv --tb=long -s

# Quick overview of all failures
./run_tests.sh tests/test_auto_trim.py --tb=line -q

# Stop at first failure with details
./run_tests.sh tests/ -v --tb=short -x
```

### For CI/CD
```bash
# Concise output for logs
./run_tests.sh tests/ -v --tb=short --maxfail=10

# Just pass/fail counts
./run_tests.sh tests/ --tb=no -q
```

### For Development
```bash
# Re-run only failures with details
./run_tests.sh --lf -vv --tb=short -s

# Run fast tests first, then failures
./run_tests.sh --ff -v --tb=short
```

## Output to File

```bash
# Save output to file
./run_tests.sh tests/test_core.py -v --tb=short > test_results.txt 2>&1

# Or use tee to see and save
./run_tests.sh tests/test_core.py -v --tb=short 2>&1 | tee test_results.txt
```

## HTML Reports

```bash
# Install pytest-html
pip install pytest-html

# Generate HTML report
./run_tests.sh tests/ -v --html=report.html --self-contained-html
```

## Common Use Cases

**"What's failing?"**
```bash
./run_tests.sh tests/ --tb=line -q
```

**"Why is test X failing?"**
```bash
./run_tests.sh tests/test_file.py::test_name -vv --tb=long -s
```

**"Run only broken tests"**
```bash
./run_tests.sh --lf -v --tb=short
```

**"Which tests are slow?"**
```bash
./run_tests.sh tests/ --durations=20
```

**"Save detailed results"**
```bash
./run_tests.sh tests/ -v --tb=short 2>&1 | tee test_output.log
```
