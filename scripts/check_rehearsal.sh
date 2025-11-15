#!/bin/bash
# Simple PAPER Rehearsal Status Check
# Run anytime to get current status

cd "$(dirname "$0")/.."

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║       24-HOUR PAPER MODE REHEARSAL STATUS                 ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check if running
if [ -f data/247trader-v2.pid ]; then
    PID=$(cat data/247trader-v2.pid)
    if ps -p $PID > /dev/null 2>&1; then
        echo "✅ Bot Status: RUNNING (PID: $PID)"
        UPTIME=$(ps -o etime= -p $PID | xargs)
        echo "   Uptime: $UPTIME"
        MEM=$(ps -o rss= -p $PID | awk '{printf "%.1f MB", $1/1024}')
        echo "   Memory: $MEM"
    else
        echo "❌ Bot Status: STOPPED (stale PID file)"
        exit 1
    fi
else
    echo "❌ Bot Status: NOT RUNNING (no PID file)"
    exit 1
fi

echo ""

# Progress
TOTAL=$(wc -l < logs/247trader-v2_audit.jsonl | xargs)
PERCENT=$(echo "scale=1; $TOTAL * 100 / 1440" | bc 2>/dev/null || echo "N/A")
echo "📊 Progress: $TOTAL / 1440 cycles ($PERCENT%)"

# Time estimates
START_TIME="2025-11-15 13:35:00"
END_TIME="2025-11-16 13:35:00"
CURRENT=$(date "+%Y-%m-%d %H:%M:%S")
echo "   Started: ~13:35 PST (2025-11-15)"
echo "   Current: $(date "+%H:%M:%S PST")"
echo "   ETA: 13:35 PST (2025-11-16)"

echo ""

# Latest cycle
echo "📋 Latest Cycles:"
tail -5 logs/247trader-v2_audit.jsonl | jq -r '[.timestamp[11:19], .status, .config_hash // "⚠️ null", .no_trade_reason // "-"] | @tsv' | awk '{printf "   %s | %-8s | %s | %s\n", $1, $2, $3, $4}'

echo ""

# Config hash check
UNIQUE_HASHES=$(jq -r '.config_hash // "null"' logs/247trader-v2_audit.jsonl | sort | uniq | wc -l | xargs)
if [ "$UNIQUE_HASHES" -le 2 ]; then
    PRIMARY_HASH=$(jq -r 'select(.config_hash != null) | .config_hash' logs/247trader-v2_audit.jsonl | tail -1)
    echo "✅ Config Hash: $PRIMARY_HASH (consistent)"
else
    echo "⚠️  Config Hash: INCONSISTENT ($UNIQUE_HASHES different values)"
fi

echo ""

# Error summary (today's logs only)
TODAY_LOG=$(ls -t logs/live_*.log 2>/dev/null | head -1)
if [ -n "$TODAY_LOG" ]; then
    ERROR_COUNT=$(grep -c "ERROR" "$TODAY_LOG" 2>/dev/null || echo 0)
    EXCEPTION_COUNT=$(grep -c "Traceback" "$TODAY_LOG" 2>/dev/null || echo 0)
    
    if [ "$ERROR_COUNT" -gt 0 ] || [ "$EXCEPTION_COUNT" -gt 0 ]; then
        echo "⚠️  Errors: $ERROR_COUNT | Exceptions: $EXCEPTION_COUNT (in current log)"
    else
        echo "✅ Health: No errors or exceptions"
    fi
fi

echo ""
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Commands:"
echo "  Watch cycles: watch -n 5 'tail -3 logs/247trader-v2_audit.jsonl | jq -c \"{time:.timestamp[11:19], status, hash:.config_hash}\"'"
echo "  Follow logs:  tail -f logs/live_*.log"
echo "  Stop bot:     kill -2 \$(cat data/247trader-v2.pid)"
echo ""
