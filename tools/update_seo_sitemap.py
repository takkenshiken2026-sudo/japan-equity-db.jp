#!/usr/bin/env python3
"""DB から seo/sitemap.xml を再生成する（GitHub Actions 用のコミット済みサイトマップ更新）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("SITE_URL", "https://japan-equity-db.jp")

from build_public_site import SEO, _sitemap_from_db  # noqa: E402


def main() -> None:
    result = _sitemap_from_db()
    if not result:
        raise SystemExit("edinet.db が見つからないか、サイトマップ生成に失敗しました。")
    sitemap_xml, company_count = result
    SEO.mkdir(parents=True, exist_ok=True)
    out = SEO / "sitemap.xml"
    out.write_text(sitemap_xml, encoding="utf-8")
    print(f"Wrote {out} ({company_count} companies)")


if __name__ == "__main__":
    main()
