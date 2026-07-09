#!/usr/bin/env python3
"""GitHub Pages 用に mock 静的ファイルを public_site/ に出力する。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public_site"
MOCK = ROOT / "mock"
SITE_URL = "https://japan-equity-db.jp"
META_DESCRIPTION = (
    "有報・四半期の財務データとニュース・検索トレンドで上場企業を分析できる株チェック。"
)


def _replace_placeholders(text: str) -> str:
    return (
        text.replace("__SITE_URL__", SITE_URL)
        .replace("__META_DESCRIPTION__", META_DESCRIPTION)
        .replace("__GOOGLE_VERIFICATION__", "")
        .replace("__SITE_JSON_LD__", json.dumps({"@context": "https://schema.org", "@type": "WebSite", "name": "株チェック", "url": SITE_URL}, ensure_ascii=False))
    )


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    for name in ("index.html", "disclaimer.html", "charts.js"):
        target = OUT / name
        target.write_text(_replace_placeholders((MOCK / name).read_text(encoding="utf-8")), encoding="utf-8")

    for asset in ("favicon.svg", "og-image.svg"):
        src = ROOT / "backend" / "app" / "static" / asset
        if not src.exists():
            src = MOCK / asset
        if src.exists():
            shutil.copy2(src, OUT / asset)

    # GitHub Pages SPA: /companies/* 等はアプリ側ルートへ
    (OUT / "404.html").write_text((OUT / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Built {OUT} ({len(list(OUT.iterdir()))} files)")


if __name__ == "__main__":
    main()
