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

# view が一時失敗しても create の 422（既存タグ）で止まらないようにする
if ! gh release view "$TAG" &>/dev/null; then
  gh release create "$TAG" \
    --title "Production DB backup" \
    --notes "SQLite backup for Render seed (DB_BACKUP_URL). Re-upload with scripts/publish-db-backup.sh when refreshing." \
    || echo "gh release create failed (tag may already exist); continuing to upload" >&2
fi

uploaded=0
for attempt in 1 2 3 4; do
  if gh release upload "$TAG" "/tmp/$ASSET" --clobber; then
    uploaded=1
    break
  fi
  echo "release upload attempt ${attempt} failed; retrying..." >&2
  sleep $((attempt * 8))
done
if [[ "$uploaded" != "1" ]]; then
  echo "Failed to upload ${TAG} after retries" >&2
  exit 1
fi

rm -f "/tmp/$ASSET"
python3 "$ROOT/tools/cleanup_local_data.py"

echo ""
echo "Uploaded. Set Render env DB_BACKUP_URL to the download URL:"
echo "  https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/download/$TAG/$ASSET"
echo ""
echo "Then redeploy or restart the Render service."
