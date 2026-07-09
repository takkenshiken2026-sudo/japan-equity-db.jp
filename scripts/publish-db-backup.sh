#!/bin/bash
# ローカル DB を GitHub Release にアップロードし、Render の DB_BACKUP_URL に使います。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/backend/data/edinet.db"
TAG="db-backup"
ASSET="edinet.db.gz"

if [[ ! -f "$DB" ]]; then
  echo "Database not found: $DB" >&2
  exit 1
fi

echo "Compressing $(du -h "$DB" | cut -f1) database..."
gzip -c "$DB" > "/tmp/$ASSET"
echo "Compressed size: $(du -h "/tmp/$ASSET" | cut -f1)"

if ! gh release view "$TAG" &>/dev/null; then
  gh release create "$TAG" --title "Production DB backup" --notes "SQLite backup for Render seed (DB_BACKUP_URL). Re-upload with scripts/publish-db-backup.sh when refreshing."
fi

gh release upload "$TAG" "/tmp/$ASSET" --clobber

URL=$(gh release view "$TAG" --json assets -q ".assets[] | select(.name==\"$ASSET\") | .url")
echo ""
echo "Uploaded. Set Render env DB_BACKUP_URL to the download URL:"
echo "  https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/download/$TAG/$ASSET"
echo ""
echo "Then redeploy or restart the Render service."
