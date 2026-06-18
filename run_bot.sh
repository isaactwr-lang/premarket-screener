#!/bin/bash
# Premarket Screener daily runner
# Scheduled via launchd at 20:30 GMT+8 (8:30pm) daily
# Script self-exits on weekends — only runs Monday–Friday

BOT_DIR="/Users/isaacteo/Library/Mobile Documents/com~apple~CloudDocs/Resume Stuff/Coding Portfolio/Trading Bot"
LOG_DIR="$BOT_DIR/logs"
PYTHON="/opt/anaconda3/bin/python"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run_$(date +%Y-%m-%d).log"

# Exit immediately on Saturday (6) or Sunday (7)
DAY=$(date +%u)
if [ "$DAY" -ge 6 ]; then
    echo "$(date): Weekend — skipping run." >> "$LOG_FILE"
    exit 0
fi

echo "=== $(date) ===" >> "$LOG_FILE"

source /opt/anaconda3/etc/profile.d/conda.sh
conda activate base

cd "$BOT_DIR"
"$PYTHON" main.py >> "$LOG_FILE" 2>&1

echo "Exit code: $?" >> "$LOG_FILE"
