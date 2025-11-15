#!/bin/bash
#
# Rehearsal Timeline - Visual progress tracker
# Shows where we are in the 24-hour journey
#

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

AUDIT_LOG="logs/247trader-v2_audit.jsonl"

if [ ! -f "$AUDIT_LOG" ]; then
    echo "❌ Audit log not found"
    exit 1
fi

CYCLES=$(wc -l < "$AUDIT_LOG" | xargs)
PROGRESS=$(echo "scale=1; $CYCLES * 100 / 1440" | bc)
REMAINING=$((1440 - CYCLES))

# Visual progress bar
FILLED=$(echo "scale=0; $CYCLES / 28.8" | bc)  # 50 chars = 1440 cycles
EMPTY=$((50 - FILLED))

BAR="["
for i in $(seq 1 $FILLED); do BAR="${BAR}█"; done
for i in $(seq 1 $EMPTY); do BAR="${BAR}░"; done
BAR="${BAR}]"

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║          24-HOUR PAPER REHEARSAL TIMELINE                      ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "  $BAR $PROGRESS%"
echo ""
echo "  ├─ Started:      2025-11-15 13:35 PST"
echo "  ├─ Current:      $(date '+%Y-%m-%d %H:%M PST')"
echo "  └─ Completes:    2025-11-16 13:35 PST"
echo ""
echo "  Progress:  $CYCLES / 1440 cycles"
echo "  Remaining: $REMAINING cycles (~$(echo "scale=1; $REMAINING / 60" | bc) hours)"
echo ""

# Milestone markers
MILESTONES=(
    "360:6h:25%"
    "720:12h:50%"
    "1080:18h:75%"
    "1368:23h:95%"
    "1440:24h:100%"
)

echo "  Milestones:"
for milestone in "${MILESTONES[@]}"; do
    IFS=':' read -r cycles time pct <<< "$milestone"
    if [ "$CYCLES" -ge "$cycles" ]; then
        echo "    ✅ $time ($pct) - Passed"
    else
        echo "    ⏸️  $time ($pct) - Upcoming"
    fi
done

echo ""

# Time estimates
if [ "$REMAINING" -gt 0 ]; then
    ETA_SECONDS=$((REMAINING * 60))
    ETA_HOURS=$((ETA_SECONDS / 3600))
    ETA_MINS=$(((ETA_SECONDS % 3600) / 60))
    echo "  Estimated completion: ${ETA_HOURS}h ${ETA_MINS}m from now"
else
    echo "  🎉 Rehearsal complete!"
fi

echo ""
