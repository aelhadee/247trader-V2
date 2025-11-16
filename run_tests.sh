#!/bin/bash
#
# Test runner with proper TMPDIR configuration
# 
# Usage:
#   ./run_tests.sh                    # Run all tests
#   ./run_tests.sh tests/test_core.py # Run specific test file
#   ./run_tests.sh -k test_config     # Run tests matching pattern
#   ./run_tests.sh --durations=20     # Show 20 slowest tests
#

set -e

# Ensure pytest temp directory exists
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTEST_TMP="${SCRIPT_DIR}/.pytest_tmp"
mkdir -p "${PYTEST_TMP}"

# Export TMPDIR for pytest
export TMPDIR="${PYTEST_TMP}"

# Default to running all tests if no args provided
if [ $# -eq 0 ]; then
    ARGS="tests/"
else
    ARGS="$@"
fi

echo "Running: pytest ${ARGS}"
echo "TMPDIR: ${TMPDIR}"
echo ""

# Run pytest with proper temp directory
python -m pytest ${ARGS}
