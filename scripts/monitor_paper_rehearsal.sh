#!/bin/bash
#
# PAPER Mode Rehearsal Monitor
# Monitors 24-hour PAPER trading session for production readiness validation
#
# Usage: ./scripts/monitor_paper_rehearsal.sh
#

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$PROJECT_DIR"

AUDIT_LOG="logs/247trader-v2_audit.jsonl"
LIVE_LOG=$(ls -t logs/live_*.log 2>/dev/null | head -1)

echo ""
echo "═══════════════════════════════════════════════════════════"
echo -e "${CYAN}247trader-v2 PAPER Mode Rehearsal Monitor${NC}"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Function to check if bot is running
check_running() {
    if [ -f "data/247trader-v2.pid" ]; then
        PID=$(cat data/247trader-v2.pid)
        if ps -p $PID > /dev/null 2>&1; then
            echo -e "${GREEN}✅ Bot is RUNNING${NC} (PID: $PID)"
            return 0
        else
            echo -e "${RED}❌ Bot STOPPED${NC} (stale PID file)"
            return 1
        fi
    else
        echo -e "${RED}❌ Bot NOT RUNNING${NC} (no PID file)"
        return 1
    fi
}

# Function to get uptime
get_uptime() {
    if [ -f "data/247trader-v2.pid" ]; then
        PID=$(cat data/247trader-v2.pid)
        if ps -p $PID > /dev/null 2>&1; then
            STARTED=$(ps -o lstart= -p $PID)
            START_TIME=$(date -j -f "%a %b %d %T %Y" "$STARTED" +%s 2>/dev/null || echo "0")
            NOW=$(date +%s)
            UPTIME_SEC=$((NOW - START_TIME))
            
            HOURS=$((UPTIME_SEC / 3600))
            MINUTES=$(((UPTIME_SEC % 3600) / 60))
            
            echo "${HOURS}h ${MINUTES}m"
        else
            echo "N/A"
        fi
    else
        echo "N/A"
    fi
}

# Function to get cycle count
get_cycle_count() {
    if [ -f "$AUDIT_LOG" ]; then
        wc -l < "$AUDIT_LOG" | tr -d ' '
    else
        echo "0"
    fi
}

# Function to get config hash
get_config_hash() {
    if [ -f "$AUDIT_LOG" ]; then
        tail -1 "$AUDIT_LOG" | jq -r '.config_hash // "unknown"' 2>/dev/null || echo "unknown"
    else
        echo "unknown"
    fi
}

# Function to check for errors
check_errors() {
    if [ -f "$LIVE_LOG" ]; then
        ERROR_COUNT=$(grep -c "ERROR" "$LIVE_LOG" 2>/dev/null || echo "0")
        EXCEPTION_COUNT=$(grep -c "Exception" "$LIVE_LOG" 2>/dev/null || echo "0")
        
        if [ "$ERROR_COUNT" -gt 0 ] || [ "$EXCEPTION_COUNT" -gt 0 ]; then
            echo -e "${RED}⚠️  Errors: $ERROR_COUNT | Exceptions: $EXCEPTION_COUNT${NC}"
        else
            echo -e "${GREEN}✅ No errors or exceptions${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  No live log found${NC}"
    fi
}

# Function to get recent cycle status
get_recent_cycles() {
    if [ -f "$AUDIT_LOG" ]; then
        echo -e "\n${BLUE}Last 5 Cycles:${NC}"
        tail -5 "$AUDIT_LOG" | jq -r '[.timestamp, .status, .no_trade_reason // "N/A"] | @tsv' | \
            awk '{printf "  %s | %s | %s\n", $1, $2, $3}'
    fi
}

# Function to get metrics summary
get_metrics() {
    if [ -f "$AUDIT_LOG" ]; then
        echo -e "\n${BLUE}Metrics Summary:${NC}"
        
        # Total cycles
        TOTAL=$(wc -l < "$AUDIT_LOG" | tr -d ' ')
        echo "  Total Cycles: $TOTAL"
        
        # NO_TRADE reasons
        NO_TRADE=$(grep '"status":"NO_TRADE"' "$AUDIT_LOG" | wc -l | tr -d ' ')
        echo "  NO_TRADE: $NO_TRADE"
        
        # Trades
        TRADES=$(grep '"status":"TRADE"' "$AUDIT_LOG" | wc -l | tr -d ' ')
        echo "  TRADES: $TRADES"
        
        # Circuit breaker trips
        CIRCUIT_TRIPS=$(grep '"circuit_breaker_tripped":true' "$AUDIT_LOG" | wc -l | tr -d ' ')
        if [ "$CIRCUIT_TRIPS" -gt 0 ]; then
            echo -e "  ${RED}Circuit Breaker Trips: $CIRCUIT_TRIPS${NC}"
        else
            echo "  Circuit Breaker Trips: 0"
        fi
    fi
}

# Function to check config consistency
check_config_consistency() {
    if [ -f "$AUDIT_LOG" ]; then
        echo -e "\n${BLUE}Config Hash Consistency:${NC}"
        
        UNIQUE_HASHES=$(jq -r '.config_hash // "unknown"' "$AUDIT_LOG" | sort | uniq | wc -l | tr -d ' ')
        
        if [ "$UNIQUE_HASHES" -eq 1 ]; then
            HASH=$(jq -r '.config_hash // "unknown"' "$AUDIT_LOG" | sort | uniq)
            echo -e "  ${GREEN}✅ Consistent: $HASH${NC}"
        else
            echo -e "  ${RED}⚠️  Config changed $UNIQUE_HASHES times during session!${NC}"
            jq -r '.config_hash // "unknown"' "$AUDIT_LOG" | sort | uniq | while read -r hash; do
                COUNT=$(jq -r '.config_hash // "unknown"' "$AUDIT_LOG" | grep -c "^$hash$" || echo "0")
                echo "    $hash: $COUNT cycles"
            done
        fi
    fi
}

# Function to check alerts
check_alerts() {
    if [ -f "$LIVE_LOG" ]; then
        echo -e "\n${BLUE}Alert Summary:${NC}"
        
        ALERT_COUNT=$(grep -c "ALERT" "$LIVE_LOG" 2>/dev/null || echo "0")
        
        if [ "$ALERT_COUNT" -gt 0 ]; then
            echo -e "  ${YELLOW}Total Alerts: $ALERT_COUNT${NC}"
            
            # Show alert types
            grep "ALERT" "$LIVE_LOG" | grep -o 'type=[^ ]*' | sort | uniq -c | \
                awk '{printf "    %s: %d\n", $2, $1}'
        else
            echo "  No alerts fired"
        fi
    fi
}

# Main monitoring loop
echo -e "${CYAN}Starting continuous monitoring (Ctrl+C to stop)...${NC}"
echo ""

while true; do
    clear
    
    echo "═══════════════════════════════════════════════════════════"
    echo -e "${CYAN}247trader-v2 PAPER Mode Rehearsal Monitor${NC}"
    echo "Updated: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
    
    # Status
    echo -e "${BLUE}Status:${NC}"
    check_running
    echo "  Uptime: $(get_uptime)"
    echo "  Cycles: $(get_cycle_count)"
    echo "  Config Hash: $(get_config_hash)"
    echo ""
    
    # Error check
    echo -e "${BLUE}Health:${NC}"
    check_errors
    echo ""
    
    # Recent cycles
    get_recent_cycles
    
    # Metrics
    get_metrics
    
    # Config consistency
    check_config_consistency
    
    # Alerts
    check_alerts
    
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo -e "${CYAN}Refreshing in 60 seconds... (Ctrl+C to stop)${NC}"
    
    sleep 60
done
