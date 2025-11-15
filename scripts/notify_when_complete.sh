#!/bin/bash
#
# Rehearsal Completion Notifier
# Polls audit log and sends notification when 1,440 cycles reached
#
# Usage: ./scripts/notify_when_complete.sh [check_interval_minutes]
#

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

AUDIT_LOG="logs/247trader-v2_audit.jsonl"
CHECK_INTERVAL_MINUTES=${1:-60}  # Default: check every hour
TARGET_CYCLES=1440

echo "🔔 Rehearsal Completion Notifier"
echo "================================"
echo ""
echo "Target cycles: $TARGET_CYCLES"
echo "Check interval: ${CHECK_INTERVAL_MINUTES} minutes"
echo "Started at: $(date)"
echo ""
echo "Press Ctrl+C to stop monitoring"
echo ""

while true; do
    if [ ! -f "$AUDIT_LOG" ]; then
        echo "[$(date '+%H:%M:%S')] ⚠️  Audit log not found"
        sleep $((CHECK_INTERVAL_MINUTES * 60))
        continue
    fi
    
    CURRENT_CYCLES=$(wc -l < "$AUDIT_LOG" | xargs)
    PROGRESS=$(echo "scale=1; $CURRENT_CYCLES * 100 / $TARGET_CYCLES" | bc)
    
    echo "[$(date '+%H:%M:%S')] Progress: $CURRENT_CYCLES / $TARGET_CYCLES cycles ($PROGRESS%)"
    
    if [ "$CURRENT_CYCLES" -ge "$TARGET_CYCLES" ]; then
        echo ""
        echo "🎉 ========================================="
        echo "   REHEARSAL COMPLETE!"
        echo "   Final cycles: $CURRENT_CYCLES"
        echo "   Completed at: $(date)"
        echo "========================================="
        echo ""
        
        # Generate final report
        echo "Generating final analysis report..."
        if [ -x ./scripts/analyze_rehearsal.sh ]; then
            ./scripts/analyze_rehearsal.sh
        else
            echo "⚠️  analyze_rehearsal.sh not found or not executable"
        fi
        
        echo ""
        echo "Next steps:"
        echo "1. Review: cat logs/paper_rehearsal_final_report.md"
        echo "2. If GO decision: Follow docs/LIVE_DEPLOYMENT_CHECKLIST.md"
        echo "3. If NO-GO: Review issues and re-run rehearsal"
        echo ""
        
        # Send alert if configured
        python -c "
from infra.alert_service import create_alert_service
import yaml

try:
    config = yaml.safe_load(open('config/app.yaml'))
    alert_service = create_alert_service(config.get('alerts', {}))
    
    if alert_service:
        alert_service.send_alert(
            alert_type='info',
            message='✅ 24-hour PAPER rehearsal completed successfully',
            severity='info',
            details={
                'cycles': $CURRENT_CYCLES,
                'completed_at': '$(date)',
                'report': 'logs/paper_rehearsal_final_report.md'
            }
        )
        print('✅ Completion alert sent')
except Exception as e:
    print(f'⚠️  Could not send alert: {e}')
" 2>/dev/null || echo "⚠️  Alert service not available"
        
        break
    fi
    
    # Calculate ETA
    if [ "$CURRENT_CYCLES" -gt 0 ]; then
        REMAINING=$((TARGET_CYCLES - CURRENT_CYCLES))
        ETA_MINUTES=$REMAINING  # 1 cycle per minute
        ETA_HOURS=$(echo "scale=1; $ETA_MINUTES / 60" | bc)
        
        echo "           Remaining: $REMAINING cycles (~${ETA_HOURS}h)"
    fi
    
    sleep $((CHECK_INTERVAL_MINUTES * 60))
done
