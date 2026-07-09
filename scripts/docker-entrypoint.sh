#!/bin/bash
set -euo pipefail

DB_PATH="/app/data/edinet.db"
mkdir -p /app/data "${COLLECTION_LOG_DIR:-/app/data/collection-logs}"

seed_database() {
  local url="$1"
  echo "[entrypoint] Seeding database from DB_BACKUP_URL..."
  tmp="$(mktemp)"
  if curl -fsSL "$url" -o "$tmp"; then
    if [[ "$url" == *.gz ]]; then
      gunzip -c "$tmp" > "$DB_PATH"
    else
      cp "$tmp" "$DB_PATH"
    fi
    rm -f "$tmp"
    echo "[entrypoint] Database seeded ($(wc -c < "$DB_PATH") bytes)"
  else
    rm -f "$tmp"
    echo "[entrypoint] WARNING: failed to download DB_BACKUP_URL" >&2
  fi
}

if [[ ! -f "$DB_PATH" ]] || [[ "$(wc -c < "$DB_PATH" | tr -d ' ')" -lt 100000 ]]; then
  if [[ -n "${DB_BACKUP_URL:-}" ]]; then
    seed_database "$DB_BACKUP_URL"
  else
    echo "[entrypoint] Empty database — run sync_cli or set DB_BACKUP_URL"
  fi
fi

python -c "from app.db import init_db; init_db()"

if [[ "${ENABLE_DAILY_CRON:-true}" == "true" ]]; then
  chmod +x /app/scripts/production-daily-sync.sh
  echo "0 22 * * * root /app/scripts/production-daily-sync.sh" > /etc/cron.d/edinet-daily-sync
  chmod 0644 /etc/cron.d/edinet-daily-sync
  cron
  echo "[entrypoint] Daily sync cron enabled (22:00 UTC = 07:00 JST)"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
