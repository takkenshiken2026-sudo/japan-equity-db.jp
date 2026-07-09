#!/usr/bin/env bash
# 安全かつ高速なフル再収集（単一ライター・ロック付き）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/data/.rebuild.lock"
LOG="$ROOT/data/rebuild-all.log"
cd "$ROOT/backend"
source .venv/bin/activate

cleanup() {
  rm -f "$LOCK"
  sqlite3 data/edinet.db "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== $(date '+%Y-%m-%d %H:%M:%S') rebuild start ===" | tee -a "$LOG"
touch "$LOCK"

db_check() {
  sqlite3 data/edinet.db "PRAGMA integrity_check;" | head -1 | tee -a "$LOG"
}

db_check

echo "=== peaks metadata 2023-2025 (no financials) ===" | tee -a "$LOG"
python -m app.sync_cli sync-peaks --years 2023,2024,2025 --no-financials --sleep 0.4 | tee -a "$LOG"
db_check

echo "=== backfill-fast loop ===" | tee -a "$LOG"
for round in $(seq 1 80); do
  OUT=$(python -m app.sync_cli backfill-fast --limit 500 --workers 12 --sleep 0.22 2>&1 | tee -a "$LOG" | tail -1)
  echo "round $round $OUT" | tee -a "$LOG"
  echo "$OUT" | grep -q "'processed': 0" && break
  if (( round % 10 == 0 )); then db_check; fi
done
db_check

echo "=== collect quarterly ===" | tee -a "$LOG"
python -m app.sync_cli collect-quarterly \
  --years 2023,2024,2025 \
  --sleep 0.45 \
  --parse-limit 500 \
  --parse-workers 12 \
  --parse-sleep 0.22 | tee -a "$LOG"
db_check

echo "=== real estate ===" | tee -a "$LOG"
for round in $(seq 1 12); do
  OUT=$(python -m app.sync_cli sync-real-estate --limit 500 --workers 12 --sleep 0.22 2>&1 | tee -a "$LOG" | tail -1)
  echo "re round $round $OUT" | tee -a "$LOG"
  echo "$OUT" | grep -q "'pending_listed': 0" && break
done
db_check

echo "=== stock prices ===" | tee -a "$LOG"
python -m app.sync_cli sync-prices --limit 4000 --workers 16 --fast | tee -a "$LOG"

python << 'PY' | tee -a "$LOG"
from sqlalchemy import select, func
from app.db import SessionLocal, Company, Filing, Financial, QuarterlyFinancial, RealEstateProperty, StockQuote
db = SessionLocal()
print({
    "companies": db.scalar(select(func.count()).select_from(Company)),
    "filings": db.scalar(select(func.count()).select_from(Filing)),
    "financials": db.scalar(select(func.count()).select_from(Financial)),
    "quarterly": db.scalar(select(func.count()).select_from(QuarterlyFinancial)),
    "re_properties": db.scalar(select(func.count()).select_from(RealEstateProperty)),
    "stock_quotes": db.scalar(select(func.count()).select_from(StockQuote)),
})
db.close()
PY

echo "=== $(date '+%Y-%m-%d %H:%M:%S') rebuild done ===" | tee -a "$LOG"
