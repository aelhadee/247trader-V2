#!/bin/bash
#
# Post-Rehearsal Analysis Script
# Run after 24-hour PAPER rehearsal completes to generate comprehensive report
#
# Usage: ./scripts/analyze_rehearsal.sh
#

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

AUDIT_LOG="logs/247trader-v2_audit.jsonl"
REPORT_FILE="logs/paper_rehearsal_final_report.md"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo ""
echo "═══════════════════════════════════════════════════════════"
echo -e "${BLUE}24-Hour PAPER Rehearsal Analysis${NC}"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Check if rehearsal completed
if [ ! -f "$AUDIT_LOG" ]; then
    echo -e "${RED}❌ Audit log not found: $AUDIT_LOG${NC}"
    exit 1
fi

TOTAL_CYCLES=$(wc -l < "$AUDIT_LOG" | xargs)

if [ "$TOTAL_CYCLES" -lt 1440 ]; then
    echo -e "${YELLOW}⚠️  Warning: Only $TOTAL_CYCLES cycles found (expected 1440)${NC}"
    echo -e "${YELLOW}   Rehearsal may not be complete yet${NC}"
    echo ""
    read -p "Continue with analysis anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo -e "${GREEN}✅ Found $TOTAL_CYCLES cycles${NC}"
echo ""

# Generate report
echo "Generating comprehensive report..."
echo ""

cat > "$REPORT_FILE" << 'REPORT_HEADER'
# 24-Hour PAPER Rehearsal - Final Report 📊

**Generated:** $(date "+%Y-%m-%d %H:%M:%S PST")  
**Duration:** 24 hours  
**Mode:** PAPER  
**Config Hash:** $(jq -r 'select(.config_hash != null) | .config_hash' "$AUDIT_LOG" | head -1)

---

## Executive Summary

REPORT_HEADER

# Add metrics
echo "## Key Metrics" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Total cycles
echo "### Cycle Statistics" >> "$REPORT_FILE"
echo "\`\`\`" >> "$REPORT_FILE"
echo "Total Cycles:     $TOTAL_CYCLES" >> "$REPORT_FILE"
echo "Expected Cycles:  1440 (1 per minute)" >> "$REPORT_FILE"
COMPLETION_RATE=$(echo "scale=1; $TOTAL_CYCLES * 100 / 1440" | bc)
echo "Completion Rate:  ${COMPLETION_RATE}%" >> "$REPORT_FILE"
echo "\`\`\`" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Status breakdown
echo "### Status Distribution" >> "$REPORT_FILE"
echo "\`\`\`" >> "$REPORT_FILE"
jq -r '.status' "$AUDIT_LOG" | sort | uniq -c | awk '{printf "%-12s: %d (%.1f%%)\n", $2, $1, $1*100/'"$TOTAL_CYCLES"'}' >> "$REPORT_FILE"
echo "\`\`\`" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# NO_TRADE reasons
echo "### NO_TRADE Reasons (Top 10)" >> "$REPORT_FILE"
echo "\`\`\`" >> "$REPORT_FILE"
jq -r 'select(.status=="NO_TRADE") | .no_trade_reason // "unknown"' "$AUDIT_LOG" | sort | uniq -c | sort -rn | head -10 | awk '{printf "%-40s: %d\n", $2, $1}' >> "$REPORT_FILE"
echo "\`\`\`" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Config hash consistency
echo "### Config Hash Consistency" >> "$REPORT_FILE"
echo "\`\`\`" >> "$REPORT_FILE"
jq -r '.config_hash // "null"' "$AUDIT_LOG" | sort | uniq -c | awk '{printf "%-20s: %d cycles\n", $2, $1}' >> "$REPORT_FILE"
echo "\`\`\`" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Performance metrics
echo "### Performance" >> "$REPORT_FILE"
echo "\`\`\`" >> "$REPORT_FILE"

# Average cycle latency
AVG_LATENCY=$(jq -r '.stage_latencies.total_cycle // 0' "$AUDIT_LOG" | awk '{sum+=$1; count++} END {printf "%.2f", sum/count}')
echo "Average Cycle Latency: ${AVG_LATENCY}s" >> "$REPORT_FILE"

# P95 latency
P95_LATENCY=$(jq -r '.stage_latencies.total_cycle // 0' "$AUDIT_LOG" | sort -n | awk '{a[NR]=$1} END {print a[int(NR*0.95)]}')
echo "P95 Cycle Latency:     ${P95_LATENCY}s" >> "$REPORT_FILE"

# Max latency
MAX_LATENCY=$(jq -r '.stage_latencies.total_cycle // 0' "$AUDIT_LOG" | sort -n | tail -1)
echo "Max Cycle Latency:     ${MAX_LATENCY}s" >> "$REPORT_FILE"

echo "\`\`\`" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Success criteria check
echo "## Success Criteria Evaluation" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

echo "### Must-Pass Criteria" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Check for exceptions
EXCEPTION_COUNT=$(grep -c "Traceback" logs/live_*.log 2>/dev/null || echo 0)
if [ "$EXCEPTION_COUNT" -eq 0 ]; then
    echo "- [x] **Zero unhandled exceptions** ✅" >> "$REPORT_FILE"
else
    echo "- [ ] **Zero unhandled exceptions** ❌ Found $EXCEPTION_COUNT" >> "$REPORT_FILE"
fi

# Check config hash
UNIQUE_HASHES=$(jq -r 'select(.config_hash != null) | .config_hash' "$AUDIT_LOG" | sort | uniq | wc -l | xargs)
if [ "$UNIQUE_HASHES" -eq 1 ]; then
    echo "- [x] **Config hash constant** ✅" >> "$REPORT_FILE"
else
    echo "- [ ] **Config hash constant** ❌ Found $UNIQUE_HASHES different hashes" >> "$REPORT_FILE"
fi

# Check cycle completion rate
if [ "$TOTAL_CYCLES" -ge 1368 ]; then  # 95% of 1440
    echo "- [x] **Cycle completion rate >95%** ✅ (${COMPLETION_RATE}%)" >> "$REPORT_FILE"
else
    echo "- [ ] **Cycle completion rate >95%** ❌ (${COMPLETION_RATE}%)" >> "$REPORT_FILE"
fi

# Check memory (if bot still running)
if [ -f "data/247trader-v2.pid" ]; then
    PID=$(cat data/247trader-v2.pid)
    if ps -p $PID > /dev/null 2>&1; then
        MEM=$(ps -o rss= -p $PID | awk '{printf "%.1f", $1/1024}')
        if [ "$(echo "$MEM < 500" | bc)" -eq 1 ]; then
            echo "- [x] **Memory <500MB** ✅ (${MEM}MB)" >> "$REPORT_FILE"
        else
            echo "- [ ] **Memory <500MB** ❌ (${MEM}MB)" >> "$REPORT_FILE"
        fi
    else
        echo "- [ ] **Memory <500MB** ⚠️ (Bot not running - cannot check)" >> "$REPORT_FILE"
    fi
else
    echo "- [ ] **Memory <500MB** ⚠️ (Bot not running - cannot check)" >> "$REPORT_FILE"
fi

echo "" >> "$REPORT_FILE"

# GO/NO-GO Decision
echo "## GO/NO-GO Decision" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Count passing criteria
PASS_COUNT=0
[ "$EXCEPTION_COUNT" -eq 0 ] && PASS_COUNT=$((PASS_COUNT + 1))
[ "$UNIQUE_HASHES" -eq 1 ] && PASS_COUNT=$((PASS_COUNT + 1))
[ "$TOTAL_CYCLES" -ge 1368 ] && PASS_COUNT=$((PASS_COUNT + 1))

if [ "$PASS_COUNT" -ge 3 ]; then
    echo "### ✅ **GO for LIVE Deployment**" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "All critical success criteria passed. System is ready for LIVE deployment with minimal capital (\$100-\$500)." >> "$REPORT_FILE"
    
    echo ""
    echo -e "${GREEN}════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✅ GO FOR LIVE DEPLOYMENT  ${NC}"
    echo -e "${GREEN}════════════════════════════════════════${NC}"
    echo ""
else
    echo "### ❌ **NO-GO for LIVE Deployment**" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "One or more critical criteria failed. Review issues and re-run rehearsal." >> "$REPORT_FILE"
    
    echo ""
    echo -e "${RED}════════════════════════════════════════${NC}"
    echo -e "${RED}  ❌ NO-GO - ISSUES DETECTED  ${NC}"
    echo -e "${RED}════════════════════════════════════════${NC}"
    echo ""
fi

echo "" >> "$REPORT_FILE"

# Recommendations
echo "## Recommendations" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Check if any trades happened
TRADE_COUNT=$(jq -r 'select(.status=="TRADE")' "$AUDIT_LOG" | wc -l | xargs)
if [ "$TRADE_COUNT" -eq 0 ]; then
    echo "### ⚠️ No Trades Executed" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "The rehearsal completed with 100% NO_TRADE cycles. This is expected during low volatility but means:" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "- Fill reconciliation not tested" >> "$REPORT_FILE"
    echo "- Order execution flow not validated" >> "$REPORT_FILE"
    echo "- PnL tracking not verified with real fills" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "**Action:** Monitor closely during first LIVE trades." >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
fi

# Check latency
if [ "$(echo "$AVG_LATENCY > 30" | bc)" -eq 1 ]; then
    echo "### ⚠️ High Cycle Latency" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "Average cycle latency (${AVG_LATENCY}s) exceeds 30s target. Consider:" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "- Review slow stages in stage_latencies" >> "$REPORT_FILE"
    echo "- Optimize API calls or caching" >> "$REPORT_FILE"
    echo "- Increase cycle interval if acceptable" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
fi

echo "---" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"
echo "**Report Generated:** $(date)" >> "$REPORT_FILE"
echo "**Full audit log:** $AUDIT_LOG" >> "$REPORT_FILE"
echo "**Analysis script:** $0" >> "$REPORT_FILE"

echo -e "${GREEN}✅ Report generated: $REPORT_FILE${NC}"
echo ""
echo "View report:"
echo "  cat $REPORT_FILE"
echo ""
echo "Or open in editor:"
echo "  code $REPORT_FILE"
echo ""
