#!/bin/bash
# 本番コンテナ内の日次同期（launchd / venv 不要）
set -euo pipefail

LOG_DIR="${COLLECTION_LOG_DIR:-/app/data/collection-logs}"
LOG="$LOG_DIR/daily-sync.log"
DB_PATH="/app/data/edinet.db"

mkdir -p "$LOG_DIR"
cd /app

if [[ ! -f "$DB_PATH" ]]; then
  echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') daily-sync skipped (no database) ===" >> "$LOG"
  exit 0
fi

{
  echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') daily-sync start ==="
  export PYTHONUNBUFFERED=1
  python -m app.sync_cli daily-sync
  python -m app.sync_cli sync-profiles --limit 50 --workers 4 || true
  python -m app.sync_cli seed-no-xbrl-profiles || true
  python -m app.sync_cli collect-external-media --limit "${EXTERNAL_MEDIA_BATCH_LIMIT:-120}" || true
  DOW=$(date -u +%u)
  if [[ "$DOW" == "7" ]]; then
    echo "--- weekly collect-quarterly ---"
    python -m app.sync_cli collect-quarterly --years 2024,2025,2026 --parse-limit 1000 --parse-workers 8 || true
    python -m app.sync_cli sync-quarterly --limit 1000 --reparse-stale --workers 8 || true
  fi
  python -c "from app.db_maintenance import checkpoint_sqlite_wal; checkpoint_sqlite_wal('TRUNCATE')"
  echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') daily-sync done ==="
} >> "$LOG" 2>&1
