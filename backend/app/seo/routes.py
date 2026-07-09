from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.seo.formatters import format_chg, format_million_yen, format_num, format_pct, format_sec_code, format_yen
from app.seo.helpers import SITE_NAME, SITE_TITLE_TAGLINE, format_listed_count_label, industry_from_slug, og_image_url, site_base, template_globals
from app.seo.service import (
    build_company_page_context,
    build_industries_page_context,
    build_industry_page_context,
    count_sitemap_companies,
    list_industries,
    list_sitemap_entries,
    resolve_company,
)

router = APIRouter(tags=["seo"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

templates.env.filters["yen"] = format_yen
templates.env.filters["pct"] = format_pct
templates.env.filters["pct_signed"] = lambda v: format_pct(v, signed=True)
templates.env.filters["sec_code"] = format_sec_code
templates.env.filters["million_yen"] = format_million_yen
templates.env.filters["num"] = format_num
templates.env.filters["chg"] = format_chg


def _with_seo_ctx(ctx: dict, request: Request) -> dict:
    base = site_base(request)
    ctx["og_image_url"] = og_image_url(base)
    ctx.update(template_globals())
    return ctx


def render_404(request: Request, message: str = "") -> HTMLResponse:
    base = site_base(request)
    canonical = f"{base}{request.url.path}"
    return templates.TemplateResponse(
        request,
        "404.html",
        _with_seo_ctx(
            {
                "canonical_url": canonical,
                "message": message,
            },
            request,
        ),
        status_code=404,
    )


@router.get("/companies/{code}", response_class=HTMLResponse)
def company_page(code: str, request: Request, db: Session = Depends(get_db)):
    company = resolve_company(db, code)
    if not company:
        return render_404(request, "企業が見つかりません。")

    if code.strip().upper() != company.edinet_code:
        canonical = f"{site_base(request)}/companies/{company.edinet_code}"
        return RedirectResponse(url=canonical, status_code=301)

    ctx = build_company_page_context(db, company, site_base(request))
    return templates.TemplateResponse(request, "company.html", _with_seo_ctx(ctx, request))


@router.get("/industries", response_class=HTMLResponse)
def industries_page(request: Request, db: Session = Depends(get_db)):
    ctx = build_industries_page_context(db, site_base(request))
    return templates.TemplateResponse(request, "industries.html", _with_seo_ctx(ctx, request))


@router.get("/industries/{slug}", response_class=HTMLResponse)
def industry_page(slug: str, request: Request, db: Session = Depends(get_db)):
    industry_name = industry_from_slug(slug)
    ctx = build_industry_page_context(db, industry_name, site_base(request))
    if not ctx:
        return render_404(request, f"業種「{industry_name}」の企業が見つかりません。")
    return templates.TemplateResponse(request, "industry.html", _with_seo_ctx(ctx, request))


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt(request: Request):
    base = site_base(request)
    return f"""User-agent: *
Allow: /
Allow: /companies/
Allow: /industries/
Allow: /disclaimer
Disallow: /api/

Sitemap: {base}/sitemap.xml
"""


@router.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt(request: Request, db: Session = Depends(get_db)):
    base = site_base(request)
    listed_label = format_listed_count_label(count_sitemap_companies(db))
    return f"""# {SITE_NAME}
> 有価証券報告書から上場企業の財務・四半期・不動産データを分析

## 主要ページ
- トップ: {base}/
- 業種一覧: {base}/industries
- 企業ページ: {base}/companies/{{edinet_code}}
- 免責事項: {base}/disclaimer
- サイトマップ: {base}/sitemap.xml

## データ
- 出典: EDINET（金融庁）/ Yahoo Finance（株価）
- 対象: 上場企業 約{listed_label}社（財務データあり）
- 内容: 年次財務、四半期業績、保有不動産明細、企業概要
"""


@router.api_route("/og-image.svg", methods=["GET", "HEAD"])
def og_image_svg(db: Session = Depends(get_db)):
    listed_label = format_listed_count_label(count_sitemap_companies(db))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#f4f6f9"/>
  <rect x="60" y="60" width="1080" height="510" rx="24" fill="#ffffff" stroke="#e2e8f0"/>
  <text x="100" y="220" fill="#2563eb" font-size="56" font-family="sans-serif" font-weight="700">{SITE_NAME}</text>
  <text x="100" y="300" fill="#0f172a" font-size="36" font-family="sans-serif">{SITE_TITLE_TAGLINE}</text>
  <text x="100" y="380" fill="#64748b" font-size="28" font-family="sans-serif">有価証券報告書ベースで約{listed_label}社を分析</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=604800"})


@router.api_route("/favicon.svg", methods=["GET", "HEAD"])
def favicon_svg():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#2563eb"/>
  <text x="16" y="22" text-anchor="middle" fill="white" font-size="13" font-family="sans-serif" font-weight="700">株</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/sitemap.xml")
def sitemap_xml(request: Request, db: Session = Depends(get_db)):
    base = site_base(request)
    fallback = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries = list_sitemap_entries(db)

    urls = [
        f"  <url><loc>{base}/</loc><lastmod>{fallback}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>",
        f"  <url><loc>{base}/industries</loc><lastmod>{fallback}</lastmod><changefreq>weekly</changefreq><priority>0.7</priority></url>",
        f"  <url><loc>{base}/disclaimer</loc><lastmod>{fallback}</lastmod><changefreq>yearly</changefreq><priority>0.3</priority></url>",
    ]
    for path, lastmod, priority in entries:
        loc = f"{base}{path}"
        lm = lastmod or fallback
        urls.append(
            f"  <url><loc>{loc}</loc><lastmod>{lm}</lastmod><changefreq>weekly</changefreq><priority>{priority}</priority></url>"
        )

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>"
    )
    return Response(content=body, media_type="application/xml")


@router.get("/api/seo/stats")
def seo_stats(db: Session = Depends(get_db)):
    return {
        "sitemap_companies": count_sitemap_companies(db),
        "sitemap_industries": len(list_industries(db)),
        "sitemap_total_urls": 2 + len(list_sitemap_entries(db)),
    }
