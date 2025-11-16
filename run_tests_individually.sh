#!/usr/bin/env bash
# run_tests_individually.sh - Run each test file separately and report results
# Usage: ./run_tests_individually.sh [--verbose] [--stop-on-fail]

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
VERBOSE=false
STOP_ON_FAIL=false
SHOW_OUTPUT=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --stop-on-fail|-x)
            STOP_ON_FAIL=true
            shift
            ;;
        --show-output|-s)
            SHOW_OUTPUT=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--verbose|-v] [--stop-on-fail|-x] [--show-output|-s]"
            exit 1
            ;;
    esac
done

# Setup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure .pytest_tmp exists
mkdir -p .pytest_tmp

# Export TMPDIR
export TMPDIR="$PWD/.pytest_tmp"

# Find all test files
TEST_FILES=($(find tests -name "test_*.py" -type f | sort))

if [ ${#TEST_FILES[@]} -eq 0 ]; then
    echo -e "${RED}No test files found in tests/ directory${NC}"
    exit 1
fi

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Running ${#TEST_FILES[@]} test files individually${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Track results
declare -a PASSED_FILES
declare -a FAILED_FILES
declare -a ERROR_FILES
TOTAL_TESTS=0
TOTAL_PASSED=0
TOTAL_FAILED=0
TOTAL_ERRORS=0

# Run each test file
for test_file in "${TEST_FILES[@]}"; do
    echo -e "${YELLOW}┌─────────────────────────────────────────────────────────┐${NC}"
    echo -e "${YELLOW}│ Running: $(basename "$test_file")${NC}"
    echo -e "${YELLOW}└─────────────────────────────────────────────────────────┘${NC}"
    
    # Build pytest command
    if [ "$VERBOSE" = true ]; then
        PYTEST_ARGS="-v --tb=short"
    else
        PYTEST_ARGS="-v --tb=line"
    fi
    
    if [ "$SHOW_OUTPUT" = true ]; then
        PYTEST_ARGS="$PYTEST_ARGS -s"
    fi
    
    # Run test and capture output
    OUTPUT_FILE=$(mktemp)
    if python -m pytest "$test_file" $PYTEST_ARGS > "$OUTPUT_FILE" 2>&1; then
        # Success
        echo -e "${GREEN}✓ PASSED${NC}"
        PASSED_FILES+=("$test_file")
        
        # Extract stats
        if grep -q "passed" "$OUTPUT_FILE"; then
            STATS=$(grep -E "[0-9]+ passed" "$OUTPUT_FILE" | tail -1)
            echo "  $STATS"
            PASSED_COUNT=$(echo "$STATS" | grep -oE "[0-9]+ passed" | grep -oE "[0-9]+")
            TOTAL_TESTS=$((TOTAL_TESTS + PASSED_COUNT))
            TOTAL_PASSED=$((TOTAL_PASSED + PASSED_COUNT))
        fi
    else
        # Failure or error
        EXIT_CODE=$?
        echo -e "${RED}✗ FAILED${NC}"
        
        # Extract stats
        if grep -q "passed\|failed\|error" "$OUTPUT_FILE"; then
            STATS=$(grep -E "[0-9]+ (passed|failed|error)" "$OUTPUT_FILE" | tail -1)
            echo "  $STATS"
            
            # Parse counts
            if echo "$STATS" | grep -q "passed"; then
                PASSED_COUNT=$(echo "$STATS" | grep -oE "[0-9]+ passed" | grep -oE "[0-9]+" || echo "0")
                TOTAL_PASSED=$((TOTAL_PASSED + PASSED_COUNT))
                TOTAL_TESTS=$((TOTAL_TESTS + PASSED_COUNT))
            fi
            if echo "$STATS" | grep -q "failed"; then
                FAILED_COUNT=$(echo "$STATS" | grep -oE "[0-9]+ failed" | grep -oE "[0-9]+" || echo "0")
                TOTAL_FAILED=$((TOTAL_FAILED + FAILED_COUNT))
                TOTAL_TESTS=$((TOTAL_TESTS + FAILED_COUNT))
                FAILED_FILES+=("$test_file")
            fi
            if echo "$STATS" | grep -q "error"; then
                ERROR_COUNT=$(echo "$STATS" | grep -oE "[0-9]+ error" | grep -oE "[0-9]+" || echo "0")
                TOTAL_ERRORS=$((TOTAL_ERRORS + ERROR_COUNT))
                TOTAL_TESTS=$((TOTAL_TESTS + ERROR_COUNT))
                ERROR_FILES+=("$test_file")
            fi
        fi
        
        # Show failure details if verbose or if requested
        if [ "$VERBOSE" = true ] || [ "$SHOW_OUTPUT" = true ]; then
            echo ""
            echo -e "${RED}Output:${NC}"
            cat "$OUTPUT_FILE"
        fi
        
        # Stop on fail if requested
        if [ "$STOP_ON_FAIL" = true ]; then
            echo ""
            echo -e "${RED}Stopping due to --stop-on-fail flag${NC}"
            rm -f "$OUTPUT_FILE"
            exit 1
        fi
    fi
    
    rm -f "$OUTPUT_FILE"
    echo ""
done

# Print summary
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  SUMMARY${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${BLUE}Total test files:${NC} ${#TEST_FILES[@]}"
echo -e "${GREEN}Passed files:${NC}    ${#PASSED_FILES[@]}"
echo -e "${RED}Failed files:${NC}    ${#FAILED_FILES[@]}"
if [ ${#ERROR_FILES[@]} -gt 0 ]; then
    echo -e "${RED}Error files:${NC}     ${#ERROR_FILES[@]}"
fi
echo ""
echo -e "${BLUE}Total tests:${NC}     $TOTAL_TESTS"
echo -e "${GREEN}Passed:${NC}          $TOTAL_PASSED"
if [ $TOTAL_FAILED -gt 0 ]; then
    echo -e "${RED}Failed:${NC}          $TOTAL_FAILED"
fi
if [ $TOTAL_ERRORS -gt 0 ]; then
    echo -e "${RED}Errors:${NC}          $TOTAL_ERRORS"
fi
echo ""

# List failed files
if [ ${#FAILED_FILES[@]} -gt 0 ]; then
    echo -e "${RED}Failed test files:${NC}"
    for file in "${FAILED_FILES[@]}"; do
        echo "  • $file"
    done
    echo ""
fi

if [ ${#ERROR_FILES[@]} -gt 0 ] && [ ${#ERROR_FILES[@]} -ne ${#FAILED_FILES[@]} ]; then
    echo -e "${RED}Error test files:${NC}"
    for file in "${ERROR_FILES[@]}"; do
        # Only show if not already in failed list
        if [[ ! " ${FAILED_FILES[@]} " =~ " ${file} " ]]; then
            echo "  • $file"
        fi
    done
    echo ""
fi

# Exit with appropriate code
if [ ${#FAILED_FILES[@]} -gt 0 ] || [ ${#ERROR_FILES[@]} -gt 0 ]; then
    echo -e "${RED}Some tests failed or had errors${NC}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
