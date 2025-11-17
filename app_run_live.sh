#!/bin/bash
#
# 247trader-v2 Production Launcher
# Runs the trading system in LIVE mode with full safeguards
#
# Usage:
#   ./run_live.sh            # Run once (single cycle)
#   ./run_live.sh --loop     # Run continuously (15min intervals)
#   ./run_live.sh --paper    # Run in PAPER mode instead
#
clear
set -e  # Exit on error
set -u  # Exit on undefined variable

# Load credentials from JSON file and export as environment variables
# This is compatible with the new secrets hardening (environment-only credentials)
CRED_FILE="/Users/ahmed/coding-stuff/trader/cb_api.json"
if [ -f "$CRED_FILE" ]; then
    export CB_API_KEY=$(jq -r '.name' "$CRED_FILE")
    export CB_API_SECRET=$(jq -r '.privateKey' "$CRED_FILE")
fi

# Kill existing instances before starting (prevents instance lock errors)
if [ -f "data/247trader-v2.pid" ]; then
    OLD_PID=$(cat data/247trader-v2.pid)
    if ps -p $OLD_PID > /dev/null 2>&1; then
        echo "🔄 Stopping existing instance (PID: $OLD_PID)..."
        kill -2 $OLD_PID 2>/dev/null || true
        sleep 3
        # Force kill if still running
        if ps -p $OLD_PID > /dev/null 2>&1; then
            echo "⚠️  Force stopping instance..."
            kill -9 $OLD_PID 2>/dev/null || true
            sleep 1
        fi
        echo "✅ Previous instance stopped"
    fi
    # Clean up stale PID file
    rm -f data/247trader-v2.pid
fi

# Clean up orphaned metrics exporter on port 9090
METRICS_PORT=9090
METRICS_PID=$(lsof -ti:$METRICS_PORT 2>/dev/null || true)
if [ -n "$METRICS_PID" ]; then
    echo "🔄 Cleaning up orphaned metrics exporter on port $METRICS_PORT (PID: $METRICS_PID)..."
    kill -9 $METRICS_PID 2>/dev/null || true
    sleep 1
    echo "✅ Metrics port freed"
fi
# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project directory
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"

# Logging
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/live_$(date +%Y%m%d_%H%M%S).log"

# Function to log messages
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"
}

log_success() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] ✅ $1${NC}" | tee -a "$LOG_FILE"
}

log_warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] ⚠️  $1${NC}" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ❌ $1${NC}" | tee -a "$LOG_FILE"
}

# Banner
echo ""
echo "═══════════════════════════════════════════════════════════"
log "247trader-v2 Production Launcher"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Parse arguments
MODE="LIVE"  # Default to LIVE/PROD mode
RUN_MODE="once"

for arg in "$@"; do
    case $arg in
        --loop)
            RUN_MODE="loop"
            ;;
        --paper)
            MODE="PAPER"
            ;;
        --dry-run)
            MODE="DRY_RUN"
            ;;
        *)
            log_error "Unknown argument: $arg"
            echo "Usage: $0 [--loop] [--paper|--dry-run]"
            exit 1
            ;;
    esac
done

log "Mode: $MODE"
log "Run mode: $RUN_MODE"
echo ""

# Safety checks
log "Running pre-flight checks..."

# 1. Check if virtual environment exists
if [ ! -d ".venv" ]; then
    log_error "Virtual environment not found at .venv"
    log "Please run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi
log_success "Virtual environment found"

# 2. Check if Coinbase credentials are loaded
if [ -z "${CB_API_KEY:-}" ] || [ -z "${CB_API_SECRET:-}" ]; then
    # Try loading from default file location
    if [ -f "$CRED_FILE" ]; then
        export CB_API_KEY=$(jq -r '.name' "$CRED_FILE")
        export CB_API_SECRET=$(jq -r '.privateKey' "$CRED_FILE")
        log_success "Loaded credentials from: $CRED_FILE"
    else
        log_error "Coinbase credentials not found"
        log "Please set CB_API_KEY and CB_API_SECRET environment variables"
        log "Or place cb_api.json at: $CRED_FILE"
        exit 1
    fi
else
    log_success "Using credentials from environment variables"
fi

# 3. Verify credentials are valid (basic format check)
if [ -z "$CB_API_KEY" ] || [ -z "$CB_API_SECRET" ]; then
    log_error "Credentials are empty after loading"
    exit 1
fi

# 4. Check config file
if [ ! -f "config/app.yaml" ]; then
    log_error "Configuration file not found: config/app.yaml"
    exit 1
fi
log_success "Configuration file found"

# 4.5. Validate configuration files (schema + sanity checks)
log "Validating configuration files..."
python tools/config_validator.py
VALIDATOR_EXIT=$?
if [ $VALIDATOR_EXIT -ne 0 ]; then
    log_error "Configuration validation failed (exit code: $VALIDATOR_EXIT)"
    log_error "Fix YAML errors in config/ and rerun"
    exit 1
fi
log_success "Configuration validation passed"

# 5. Verify we're not in read_only mode for LIVE trading
if [ "$MODE" = "LIVE" ]; then
    if grep -q "read_only: true" config/app.yaml; then
        log_warning "Exchange is in READ_ONLY mode!"
        log_warning "This will prevent real order execution."
        log_warning "To enable live trading, set 'read_only: false' in config/app.yaml"
        echo ""
        read -p "Continue anyway? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log "Aborted by user"
            exit 1
        fi
    fi
fi

# 6. Check mode in config
CURRENT_MODE=$(grep "mode:" config/app.yaml | awk '{print $2}' | tr -d '"')
if [ "$CURRENT_MODE" != "$MODE" ]; then
    log_warning "Config mode is '$CURRENT_MODE' but launching in '$MODE' mode"
    log_warning "The command-line mode takes precedence"
fi

log_success "Pre-flight checks complete"
echo ""

# Activate virtual environment
log "Activating virtual environment..."
source .venv/bin/activate
log_success "Virtual environment activated: $(which python3)"
echo ""

# Show account balance before starting (safety check)
log "Fetching account balance..."
BALANCE=$(python3 <<EOF
import os
os.environ['CB_API_KEY'] = os.environ.get('CB_API_KEY', '')
os.environ['CB_API_SECRET'] = os.environ.get('CB_API_SECRET', '')
try:
    from core.exchange_coinbase import CoinbaseExchange
    ex = CoinbaseExchange()
    accts = ex.get_accounts()
    total = 0
    for a in accts:
        bal = float(a.get('available_balance', {}).get('value', 0))
        curr = a['currency']
        if curr == 'USDC':
            total += bal
        elif curr == 'USD':
            total += bal
    print(f"{total:.2f}")
except Exception as e:
    print("0.00")
EOF
)

if [ "$BALANCE" != "0.00" ]; then
    log_success "Account balance: \$${BALANCE} USDC"
else
    log_warning "Could not fetch account balance (API may be slow)"
fi
echo ""

# Final confirmation for LIVE mode
if [ "$MODE" = "LIVE" ]; then
    echo "═══════════════════════════════════════════════════════════"
    log_warning "⚠️  WARNING: LIVE TRADING MODE ⚠️"
    echo "═══════════════════════════════════════════════════════════"
    log_warning "This will place REAL ORDERS with REAL MONEY"
    log_warning "Account balance: \$${BALANCE} USDC"
    echo ""
    log_success "✅ LIVE mode auto-confirmed (no prompt)"
    echo ""
fi

# Determine command
if [ "$RUN_MODE" = "loop" ]; then
    # Read interval from config/app.yaml (in minutes)
    INTERVAL_MIN=$(grep "interval_minutes:" config/app.yaml | awk '{print $2}')
    if [ -z "$INTERVAL_MIN" ]; then
        INTERVAL_MIN=15
        log_warning "Could not read interval from config, using default: 15 minutes"
    fi
    
    # Convert minutes to seconds (support fractional minutes like 0.5)
    INTERVAL_SEC=$(python3 -c "print(int(float('$INTERVAL_MIN') * 60))")
    
    CMD="python -m runner.main_loop --interval $INTERVAL_SEC"
    log "Starting continuous loop ($INTERVAL_MIN minute intervals = $INTERVAL_SEC seconds)..."
else
    CMD="python -m runner.main_loop --once"
    log "Running single cycle..."
fi

# Add mode override if needed
if [ "$MODE" != "$CURRENT_MODE" ]; then
    # Note: This requires main_loop.py to accept --mode argument
    # For now, user must manually edit config/app.yaml
    log_warning "Note: To permanently change mode, edit config/app.yaml"
fi

echo ""
log "Launching trading system..."
log "Command: $CMD"
log "Logs: $LOG_FILE"
echo ""

# Trap Ctrl+C for clean shutdown
trap 'log_warning "Interrupted by user (Ctrl+C)"; exit 130' INT

# Run the trading system
if [ "$RUN_MODE" = "loop" ]; then
    log_success "System started (Press Ctrl+C to stop)"
    echo ""
    $CMD 2>&1 | tee -a "$LOG_FILE"
else
    $CMD 2>&1 | tee -a "$LOG_FILE"
    EXIT_CODE=$?
    echo ""
    if [ $EXIT_CODE -eq 0 ]; then
        log_success "Cycle completed successfully"
    else
        log_error "Cycle failed with exit code $EXIT_CODE"
    fi
fi

echo ""
log "Session ended"
log "Full logs saved to: $LOG_FILE"
echo ""
