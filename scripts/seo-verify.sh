#!/bin/bash
# SEOエンドポイントの動作確認
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"

for _ in 1 2 3 4 5; do
  if curl -sf "${BASE}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

check() {
  local path="$1"
  local expect="$2"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}${path}")
  if [[ "$code" != "$expect" ]]; then
    echo "FAIL ${path} expected=${expect} got=${code}"
    exit 1
  fi
  echo "OK   ${path} (${code})"
}

echo "=== SEO verify: ${BASE} ==="
check "/" 200
check "/robots.txt" 200
check "/sitemap.xml" 200
check "/llms.txt" 200
check "/favicon.svg" 200
check "/og-image.svg" 200
check "/industries" 200
check "/companies/E02144" 200
check "/companies/7203" 301
check "/api/seo/stats" 200

grep -m1 -q 'og:image' < <(curl -sf "${BASE}/") && echo "OK   homepage og:image" || { echo "FAIL homepage og:image"; exit 1; }
curl -sf "${BASE}/companies/E02144" | grep -m1 -q 'FAQPage' && echo "OK   company JSON-LD" || { echo "FAIL company JSON-LD"; exit 1; }

echo "=== all checks passed ==="
