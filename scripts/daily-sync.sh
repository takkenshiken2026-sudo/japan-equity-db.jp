#!/bin/bash
# 毎日同期: 書類・財務・reparse・株価・四半期・不動産・プロフィール
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/data"
LOG="$LOG_DIR/daily-sync.log"
VENV="$ROOT/backend/.venv/bin/activate"

mkdir -p "$LOG_DIR"
LOCK="$ROOT/data/.rebuild.lock"
if [[ -f "$LOCK" ]]; then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') daily-sync skipped (rebuild lock) ===" >> "$LOG"
  exit 0
fi

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') daily-sync start ==="
  cd "$ROOT/backend"
  # shellcheck disable=SC1090
  source "$VENV"
  PYTHONUNBUFFERED=1 python -m app.sync_cli daily-sync
  PYTHONUNBUFFERED=1 python -m app.sync_cli sync-profiles --limit 50 --workers 4 || true
  PYTHONUNBUFFERED=1 python -m app.sync_cli seed-no-xbrl-profiles || true
  PYTHONUNBUFFERED=1 python -m app.sync_cli collect-external-media --limit 120 || true
  # 日曜: 四半期メタ収集 + 旧版再解析
  DOW=$(date +%u)
  if [[ "$DOW" == "7" ]]; then
    echo "--- weekly collect-quarterly ---" 
    PYTHONUNBUFFERED=1 python -m app.sync_cli collect-quarterly --years 2024,2025,2026 --parse-limit 1000 --parse-workers 8 || true
    PYTHONUNBUFFERED=1 python -m app.sync_cli sync-quarterly --limit 1000 --reparse-stale --workers 8 || true
  fi
  sqlite3 "$ROOT/backend/data/edinet.db" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') daily-sync done ==="
} >> "$LOG" 2>&1
