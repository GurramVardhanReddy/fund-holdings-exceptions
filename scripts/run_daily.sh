#!/usr/bin/env bash
# Daily job: download today's holdings, load them, run the checks, keep a log.
# Example crontab line (weekdays at 07:30, after providers publish the prior day):
#   30 7 * * 1-5  /path/to/fund-holdings-exceptions/scripts/run_daily.sh >> /path/to/logs/cron.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
DAY="$(date +%F)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run-$DAY.log"

{
  echo "== $(date -Is) fetch"
  # A failed download is logged but does not stop the run: the files that did arrive
  # are still checked, and the summary lists what could not be read.
  bash "$ROOT/scripts/fetch.sh" "$DAY" || echo "WARN some downloads failed"
  echo "== $(date -Is) check"
  cd "$ROOT"
  "$PY" -m fundx.pipeline "$ROOT/data/raw/$DAY"
  echo "== $(date -Is) done"
} 2>&1 | tee -a "$LOG"
