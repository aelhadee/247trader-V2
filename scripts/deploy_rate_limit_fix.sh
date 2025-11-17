#!/bin/bash
#
# Rate Limit Optimization - Deployment Script
# Optimizes Coinbase API calls to reduce cycle latency
#

set -e

echo "========================================="
echo "Rate Limit Optimization Deployment"
echo "========================================="
echo ""

# Step 1: Verify changes
echo "✅ Step 1: Verifying code changes..."
if grep -q "Fetching products from cache" core/exchange_coinbase.py && \
   grep -q "Fetching available symbols from cache" core/exchange_coinbase.py; then
    echo "   ✓ Both optimizations detected"
else
    echo "   ✗ Code changes not found!"
    exit 1
fi

# Step 2: Syntax check
echo ""
echo "✅ Step 2: Running syntax validation..."
python -m py_compile core/exchange_coinbase.py
echo "   ✓ Syntax OK"

# Step 3: Check for running bot
echo ""
echo "✅ Step 3: Checking for running bot..."
if pgrep -f "python -m runner.main_loop" > /dev/null; then
    echo "   ⚠️  Bot is currently running"
    echo ""
    read -p "   Stop and restart bot? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "   Stopping bot..."
        pkill -f "python -m runner.main_loop" || true
        sleep 2
        echo "   ✓ Bot stopped"
    else
        echo "   Skipping restart - manual restart required"
        exit 0
    fi
else
    echo "   ✓ No bot running"
fi

# Step 4: Start bot (optional)
echo ""
echo "✅ Step 4: Ready to start bot"
echo ""
read -p "   Start bot now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "   Starting bot with optimizations..."
    ./app_run_live.sh --loop &
    sleep 3
    echo "   ✓ Bot started (PID: $(pgrep -f 'python -m runner.main_loop'))"
    echo ""
    echo "========================================="
    echo "Monitoring (Ctrl+C to stop watching logs)"
    echo "========================================="
    echo ""
    echo "Expected improvements:"
    echo "  - Cycle time: 13s → 8s (40% faster)"
    echo "  - Rate warnings: 15+/cycle → 0-2/cycle"
    echo ""
    sleep 2
    tail -f logs/247trader-v2.log | grep --line-buffered -E "(Cycle took|rate_limiter|portfolio_snapshot|state_reconcile)"
else
    echo "   Skipped. Start manually with: ./app_run_live.sh --loop"
fi

echo ""
echo "========================================="
echo "Deployment Complete"
echo "========================================="
