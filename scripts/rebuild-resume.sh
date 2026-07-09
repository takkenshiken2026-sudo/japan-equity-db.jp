#!/usr/bin/env bash
# 高速残タスク収集（429適応バックオフ付き）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/data/.rebuild.lock"
LOG="$ROOT/data/rebuild-resume.log"
cd "$ROOT/backend"
source .venv/bin/activate

WORKERS=12
LIMIT=500
SLEEP=0.22

cleanup() {
  rm -f "$LOCK"
  sqlite3 data/edinet.db "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== $(date '+%Y-%m-%d %H:%M:%S') fast resume ===" | tee -a "$LOG"
touch "$LOCK"

run_batch() {
  local cmd=$1 label=$2
  local round=0 max_rounds=${3:-100}
  while (( round < max_rounds )); do
    round=$((round + 1))
    OUT=$(eval "$cmd" 2>&1 | tee -a "$LOG" | tail -1)
    echo "$label round $round $OUT" | tee -a "$LOG"
    echo "$OUT" | grep -q "'processed': 0" && break
    echo "$OUT" | grep -q "pending.*: 0" && break
    ERR=$(echo "$OUT" | sed -n "s/.*'errors': \([0-9]*\).*/\1/p")
    PROC=$(echo "$OUT" | sed -n "s/.*'processed': \([0-9]*\).*/\1/p")
    if [[ -n "${ERR:-}" && -n "${PROC:-}" && "$PROC" -gt 0 && "$ERR" -gt $((PROC / 2)) ]]; then
      echo "429 backoff 45s..." | tee -a "$LOG"
      sleep 45
    fi
    if (( round % 15 == 0 )); then sqlite3 data/edinet.db "PRAGMA integrity_check;" | head -1 | tee -a "$LOG"; fi
  done
}

echo "=== backfill-fast ===" | tee -a "$LOG"
run_batch "python -m app.sync_cli backfill-fast --limit $LIMIT --workers $WORKERS --sleep $SLEEP" "fin" 90

echo "=== sync-quarterly ===" | tee -a "$LOG"
run_batch "python -m app.sync_cli sync-quarterly --limit $LIMIT --workers 10 --sleep 0.28" "q" 40
python -c "from app.db import SessionLocal; from app.edinet.quarterly_sync import recompute_all_qoq; db=SessionLocal(); print('qoq', recompute_all_qoq(db)); db.close()" | tee -a "$LOG"

echo "=== real estate ===" | tee -a "$LOG"
run_batch "python -m app.sync_cli sync-real-estate --limit $LIMIT --workers $WORKERS --sleep $SLEEP" "re" 30

echo "=== stock prices ===" | tee -a "$LOG"
python -m app.sync_cli sync-prices --limit 4000 --workers 16 --fast | tee -a "$LOG"

python << 'PY' | tee -a "$LOG"
from sqlalchemy import select, func
from app.db import SessionLocal, Filing, Financial, QuarterlyFinancial, RealEstateProperty, StockQuote
db = SessionLocal()
print({
    "filings": db.scalar(select(func.count()).select_from(Filing)),
    "financials": db.scalar(select(func.count()).select_from(Financial)),
    "quarterly": db.scalar(select(func.count()).select_from(QuarterlyFinancial)),
    "re_properties": db.scalar(select(func.count()).select_from(RealEstateProperty)),
    "stock_quotes": db.scalar(select(func.count()).select_from(StockQuote)),
})
db.close()
PY

echo "=== $(date '+%Y-%m-%d %H:%M:%S') fast resume done ===" | tee -a "$LOG"
