from __future__ import annotations

from urllib.parse import quote, unquote

from fastapi import Request

from app.config import settings

SITE_NAME = "株チェック"
SITE_TITLE_TAGLINE = "有報・四半期データで銘柄分析"
SITE_HOME_TITLE = f"{SITE_NAME} | {SITE_TITLE_TAGLINE}"


def site_base(request: Request) -> str:
    if settings.site_url:
        return settings.site_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def industry_slug(industry: str) -> str:
    return quote(industry.strip(), safe="")


def industry_from_slug(slug: str) -> str:
    return unquote(slug)


def og_image_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/og-image.svg"


def format_listed_count_label(count: int) -> str:
    """Marketing copy: round down to nearest 100 (e.g. 3835 -> 3,800)."""
    if count >= 100:
        return f"{(count // 100) * 100:,}"
    return f"{count:,}"


def build_home_meta_description(listed_count: int) -> str:
    label = format_listed_count_label(listed_count)
    return (
        f"{SITE_NAME}は有価証券報告書から売上高・ROE・保有不動産を分析。"
        f"約{label}社の上場企業をスクリーニング・検索できます。"
    )


def template_globals() -> dict:
    return {
        "google_site_verification": settings.google_site_verification or "",
        "site_name": SITE_NAME,
        "site_title_tagline": SITE_TITLE_TAGLINE,
    }


def build_website_json_ld(base_url: str) -> dict:
    base = base_url.rstrip("/")
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": SITE_NAME,
        "url": base,
        "inLanguage": "ja",
        "description": f"{SITE_NAME} — 有価証券報告書から上場企業の財務・四半期・不動産データを分析できるツール",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{base}/#/search?q={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    }
