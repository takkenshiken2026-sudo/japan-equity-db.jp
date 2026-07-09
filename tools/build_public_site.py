#!/usr/bin/env python3
"""GitHub Pages 用に mock 静的ファイルと SEO 資産を public_site/ に出力する。"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public_site"
MOCK = ROOT / "mock"
SEO = ROOT / "seo"
SITE_URL = os.environ.get("SITE_URL", "https://japan-equity-db.jp").rstrip("/")
META_DESCRIPTION = (
    "有報・四半期の財務データとニュース・検索トレンドで上場企業を分析できる株チェック。"
)
SITE_NAME = "株チェック"
SITE_TITLE_TAGLINE = "有報・四半期データで銘柄分析"


def _google_verification_tag() -> str:
    code = os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip()
    if not code:
        return ""
    return f'<meta name="google-site-verification" content="{code}" />'


def _replace_placeholders(text: str, *, listed_label: str = "3,800") -> str:
    ld = json.dumps(
        {"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME, "url": SITE_URL},
        ensure_ascii=False,
    )
    return (
        text.replace("__SITE_URL__", SITE_URL)
        .replace("__META_DESCRIPTION__", META_DESCRIPTION)
        .replace("__GOOGLE_VERIFICATION__", _google_verification_tag())
        .replace("__SITE_JSON_LD__", ld)
    )


def _listed_count_label(count: int) -> str:
    if count >= 100:
        return f"{(count // 100) * 100:,}"
    return f"{count:,}"


def _sitemap_from_db() -> tuple[str, int] | None:
    db_candidates = [ROOT / "backend/data/edinet.db", ROOT / "data/edinet.db"]
    db_path = next((p for p in db_candidates if p.exists() and p.stat().st_size > 100_000), None)
    if not db_path:
        return None

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve()}"
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.db import SessionLocal
        from app.seo.service import count_sitemap_companies, list_sitemap_entries
    except Exception as exc:  # noqa: BLE001
        print(f"Skip DB sitemap ({exc})")
        return None

    fallback = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        entries = list_sitemap_entries(db)
        company_count = count_sitemap_companies(db)
        urls = [
            f"  <url><loc>{SITE_URL}/</loc><lastmod>{fallback}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>",
            f"  <url><loc>{SITE_URL}/industries</loc><lastmod>{fallback}</lastmod><changefreq>weekly</changefreq><priority>0.7</priority></url>",
            f"  <url><loc>{SITE_URL}/disclaimer</loc><lastmod>{fallback}</lastmod><changefreq>yearly</changefreq><priority>0.3</priority></url>",
        ]
        for path, lastmod, priority in entries:
            lm = (lastmod or fallback)[:10]
            urls.append(
                f"  <url><loc>{SITE_URL}{path}</loc><lastmod>{lm}</lastmod>"
                f"<changefreq>weekly</changefreq><priority>{priority}</priority></url>"
            )
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls)
            + "\n</urlset>"
        )
        return body, company_count
    finally:
        db.close()


def _sitemap_from_repo() -> str | None:
    path = SEO / "sitemap.xml"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _minimal_sitemap() -> str:
    fallback = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{SITE_URL}/</loc><lastmod>{fallback}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>
  <url><loc>{SITE_URL}/disclaimer</loc><lastmod>{fallback}</lastmod><changefreq>yearly</changefreq><priority>0.3</priority></url>
</urlset>
"""


def _write_robots_txt() -> None:
    (OUT / "robots.txt").write_text(
        f"""User-agent: *
Allow: /
Allow: /companies/
Allow: /industries/
Allow: /disclaimer
Disallow: /api/

Sitemap: {SITE_URL}/sitemap.xml
""",
        encoding="utf-8",
    )


def _write_llms_txt(listed_label: str) -> None:
    (OUT / "llms.txt").write_text(
        f"""# {SITE_NAME}
> 有価証券報告書から上場企業の財務・四半期・不動産データを分析

## 主要ページ
- トップ: {SITE_URL}/
- 業種一覧: {SITE_URL}/industries
- 企業ページ: {SITE_URL}/companies/{{edinet_code}}
- 免責事項: {SITE_URL}/disclaimer
- サイトマップ: {SITE_URL}/sitemap.xml

## データ
- 出典: EDINET（金融庁）/ Yahoo Finance（株価）
- 対象: 上場企業 約{listed_label}社（財務データあり）
- 内容: 年次財務、四半期業績、保有不動産明細、企業概要
""",
        encoding="utf-8",
    )


def _write_brand_assets(listed_label: str) -> None:
    (OUT / "favicon.svg").write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#111827"/>
  <text x="16" y="22" text-anchor="middle" fill="white" font-size="13" font-family="sans-serif" font-weight="700">株</text>
</svg>""",
        encoding="utf-8",
    )
    (OUT / "og-image.svg").write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#f3f4f6"/>
  <rect x="60" y="60" width="1080" height="510" rx="8" fill="#ffffff" stroke="#e5e7eb"/>
  <text x="100" y="220" fill="#111827" font-size="56" font-family="sans-serif" font-weight="700">{SITE_NAME}</text>
  <text x="100" y="300" fill="#374151" font-size="36" font-family="sans-serif">{SITE_TITLE_TAGLINE}</text>
  <text x="100" y="380" fill="#6b7280" font-size="28" font-family="sans-serif">有価証券報告書ベースで約{listed_label}社を分析</text>
</svg>""",
        encoding="utf-8",
    )


def _write_google_verification_html() -> None:
    filename = os.environ.get("GOOGLE_SITE_VERIFICATION_HTML", "").strip()
    if not filename:
        return
    if not filename.endswith(".html") or ".." in filename or "/" in filename:
        raise ValueError("GOOGLE_SITE_VERIFICATION_HTML must be a simple .html filename")
    token = filename.removesuffix(".html")
    (OUT / filename).write_text(f"google-site-verification: {token}.html\n", encoding="utf-8")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    SEO.mkdir(parents=True, exist_ok=True)

    listed_label = "3,800"
    sitemap_result = _sitemap_from_db()
    if sitemap_result:
        sitemap_xml, company_count = sitemap_result
        listed_label = _listed_count_label(company_count)
        (SEO / "sitemap.xml").write_text(sitemap_xml, encoding="utf-8")
        print(f"Sitemap from DB: {company_count} companies")
    else:
        sitemap_xml = _sitemap_from_repo() or _minimal_sitemap()
        print("Sitemap from repo or minimal fallback")

    for name in ("index.html", "disclaimer.html", "charts.js"):
        target = OUT / name
        target.write_text(
            _replace_placeholders((MOCK / name).read_text(encoding="utf-8"), listed_label=listed_label),
            encoding="utf-8",
        )

    _write_brand_assets(listed_label)
    _write_robots_txt()
    _write_llms_txt(listed_label)
    (OUT / "sitemap.xml").write_text(sitemap_xml, encoding="utf-8")
    _write_google_verification_html()

    # GitHub Pages SPA: /companies/* 等はアプリ側ルートへ
    (OUT / "404.html").write_text((OUT / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Built {OUT} ({len(list(OUT.iterdir()))} files)")


if __name__ == "__main__":
    main()
