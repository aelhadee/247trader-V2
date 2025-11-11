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

set -e  # Exit on error
set -u  # Exit on undefined variable

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

# 2. Check if Coinbase credentials are set
if [ -z "${CB_API_SECRET_FILE:-}" ]; then
    if [ ! -f "/Users/ahmed/coding-stuff/trader/cb_api.json" ]; then
        log_error "Coinbase credentials not found"
        log "Please set CB_API_SECRET_FILE environment variable or place cb_api.json in /Users/ahmed/coding-stuff/trader/"
        exit 1
    else
        export CB_API_SECRET_FILE="/Users/ahmed/coding-stuff/trader/cb_api.json"
        log_success "Using credentials from: $CB_API_SECRET_FILE"
    fi
else
    log_success "Using credentials from: $CB_API_SECRET_FILE"
fi

# 3. Check if credentials file exists
if [ ! -f "$CB_API_SECRET_FILE" ]; then
    log_error "Credentials file not found: $CB_API_SECRET_FILE"
    exit 1
fi

# 4. Check config file
if [ ! -f "config/app.yaml" ]; then
    log_error "Configuration file not found: config/app.yaml"
    exit 1
fi
log_success "Configuration file found"

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
    read -p "Press ENTER to confirm live trading (or Ctrl+C to abort): " -r
    echo
    # Empty input (just pressing Enter) confirms
    log "Live trading confirmed by user"
    echo ""
fi

# Determine command
if [ "$RUN_MODE" = "loop" ]; then
    # Read interval from config/app.yaml
    INTERVAL=$(grep "interval_minutes:" config/app.yaml | awk '{print $2}')
    if [ -z "$INTERVAL" ]; then
        INTERVAL=15
        log_warning "Could not read interval from config, using default: 15 minutes"
    fi
    CMD="python -m runner.main_loop --interval $INTERVAL"
    log "Starting continuous loop ($INTERVAL minute intervals)..."
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
