from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import Company, CompanyProfile, Filing, Financial, QuarterlyFinancial, StockQuote
from app.edinet.xbrl_profile import affiliate_summary
from app.routers.companies import _basic_profile_payload, _real_estate_brief
from app.seo.formatters import (
    business_excerpt,
    format_chg,
    format_num,
    format_pct,
    format_sec_code,
    format_yen,
    truncate_text,
)
from app.seo.charts import build_company_chart_data, build_industry_chart_data
from app.seo.helpers import SITE_NAME, industry_slug

INDUSTRY_PAGE_LIMIT = 80


def resolve_company(db: Session, code: str) -> Company | None:
    raw = code.strip().upper()
    company = db.get(Company, raw)
    if company:
        return company

    sec = raw.replace(".T", "")
    if sec.isdigit():
        candidates = [sec]
        if len(sec) == 4:
            candidates.append(sec + "0")
        if len(sec) == 5 and sec.endswith("0"):
            candidates.append(sec[:-1] + "0")
        for c in candidates:
            found = db.scalars(select(Company).where(Company.sec_code == c).limit(1)).first()
            if found:
                return found
    return None


def _load_profile(db: Session, company: Company) -> dict:
    profile = db.get(CompanyProfile, company.edinet_code)
    if profile and profile.parse_status == "ok":
        return {
            "source": "edinet",
            "business_description": profile.business_description,
            "company_history": profile.company_history,
            "employee_count": profile.employee_count,
            "capital_stock_m": profile.capital_stock_m,
            "issued_shares": profile.issued_shares,
            "affiliates_summary": affiliate_summary(profile.affiliated_entities),
            "fiscal_year_end": profile.fiscal_year_end,
        }
    return {}


def _quarter_label(row: QuarterlyFinancial) -> str:
    if row.quarter_number:
        return f"第{row.quarter_number}四半期"
    if row.period_end:
        return row.period_end[:7]
    return "四半期"


def _build_faq_json_ld(company: Company, latest: Financial | None, sec_display: str) -> dict | None:
    if not latest or not latest.revenue:
        return None
    questions = [
        {
            "q": f"{company.name}の売上高はいくらですか？",
            "a": f"{company.name}の直近期（{latest.fiscal_year_end}）の売上高は{format_yen(latest.revenue)}です。",
        },
    ]
    if latest.operating_margin is not None:
        questions.append({
            "q": f"{company.name}の営業利益率は？",
            "a": f"直近期の営業利益率は{format_pct(latest.operating_margin)}です。",
        })
    if latest.roe is not None:
        questions.append({
            "q": f"{company.name}のROEは？",
            "a": f"直近期のROE（自己資本利益率）は{format_pct(latest.roe)}です。",
        })
    questions.append({
        "q": f"{company.name}の証券コードは？",
        "a": f"証券コードは{sec_display}、EDINETコードは{company.edinet_code}です。",
    })
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": item["q"],
                "acceptedAnswer": {"@type": "Answer", "text": item["a"]},
            }
            for item in questions
        ],
    }


def _build_company_json_ld(
    company: Company,
    *,
    base_url: str,
    canonical: str,
    page_title: str,
    meta_description: str,
    sec_display: str,
    latest: Financial | None,
    industry_url: str | None,
) -> dict:
    graph: list[dict] = [
        {
            "@type": "WebPage",
            "@id": canonical,
            "url": canonical,
            "name": page_title,
            "description": meta_description,
            "inLanguage": "ja",
            "isPartOf": {"@type": "WebSite", "name": SITE_NAME, "url": base_url.rstrip("/")},
        },
    ]

    crumbs = [
        {"@type": "ListItem", "position": 1, "name": "トップ", "item": base_url.rstrip("/")},
    ]
    pos = 2
    if company.industry and industry_url:
        crumbs.append({"@type": "ListItem", "position": pos, "name": company.industry, "item": industry_url})
        pos += 1
    crumbs.append({"@type": "ListItem", "position": pos, "name": company.name, "item": canonical})
    graph.append({"@type": "BreadcrumbList", "itemListElement": crumbs})

    corp: dict = {
        "@type": "Corporation",
        "name": company.name,
        "url": canonical,
        "identifier": company.edinet_code,
        "tickerSymbol": sec_display,
    }
    if company.location:
        corp["address"] = {"@type": "PostalAddress", "streetAddress": company.location}
    if company.industry:
        corp["industry"] = company.industry
    graph.append(corp)

    if latest and latest.revenue:
        graph.append({
            "@type": "Dataset",
            "name": f"{company.name} 財務データ",
            "description": f"{company.name}の有価証券報告書ベース財務指標",
            "url": canonical,
            "inLanguage": "ja",
            "creator": {"@type": "Organization", "name": company.name},
        })

    faq = _build_faq_json_ld(company, latest, sec_display)
    if faq:
        graph.append(faq)

    return {"@context": "https://schema.org", "@graph": graph}


def build_company_page_context(db: Session, company: Company, base_url: str) -> dict:
    financials = db.scalars(
        select(Financial)
        .where(Financial.edinet_code == company.edinet_code)
        .order_by(Financial.fiscal_year_end.desc())
        .limit(8)
    ).all()

    quarterly = db.scalars(
        select(QuarterlyFinancial)
        .where(QuarterlyFinancial.edinet_code == company.edinet_code)
        .order_by(QuarterlyFinancial.period_end.desc())
        .limit(8)
    ).all()

    filings = db.scalars(
        select(Filing)
        .where(Filing.edinet_code == company.edinet_code)
        .order_by(Filing.submit_date_time.desc())
        .limit(10)
    ).all()

    quote = db.get(StockQuote, company.edinet_code)
    re_brief = _real_estate_brief(db, company.edinet_code)
    latest = financials[0] if financials else None
    latest_quarter = quarterly[0] if quarterly else None

    if re_brief and latest and latest.total_assets:
        re_brief["assets_ratio"] = re_brief["total_book_value_m"] * 1e6 / latest.total_assets

    profile_row = _load_profile(db, company)
    basic = _basic_profile_payload(company, latest, quote, re_brief)
    intro = basic.get("intro") or ""
    business = profile_row.get("business_description")

    sec_display = format_sec_code(company.sec_code)
    page_title = f"{company.name}（{sec_display}）財務・不動産データ | {SITE_NAME}"

    desc_parts = [f"{company.name}（証券コード {sec_display}）の"]
    if latest and latest.revenue:
        desc_parts.append(f"売上高{format_yen(latest.revenue)}")
        if latest.operating_margin is not None:
            desc_parts.append(f"営業利益率{format_pct(latest.operating_margin)}")
        if latest.roe is not None:
            desc_parts.append(f"ROE{format_pct(latest.roe)}")
    if latest_quarter:
        q_rev = latest_quarter.revenue_single or latest_quarter.revenue_cumulative
        if q_rev:
            q_label = "直近四半期売上" if latest_quarter.revenue_single else "直近四半期累計売上"
            desc_parts.append(f"{q_label}{format_yen(q_rev)}")
    if re_brief:
        desc_parts.append(f"保有不動産{re_brief['count']}件")
    desc_parts.append("有価証券報告書・四半期・株価データを掲載。")
    meta_description = truncate_text("、".join(desc_parts), 160)

    canonical = f"{base_url.rstrip('/')}/companies/{company.edinet_code}"
    industry_url = (
        f"{base_url.rstrip('/')}/industries/{industry_slug(company.industry)}"
        if company.industry
        else None
    )

    json_ld = _build_company_json_ld(
        company,
        base_url=base_url,
        canonical=canonical,
        page_title=page_title,
        meta_description=meta_description,
        sec_display=sec_display,
        latest=latest,
        industry_url=industry_url,
    )

    return {
        "company": company,
        "sec_display": sec_display,
        "financials": financials,
        "quarterly": quarterly,
        "chart_data": build_company_chart_data(financials, quarterly),
        "filings": filings,
        "quote": quote,
        "real_estate": re_brief,
        "latest": latest,
        "latest_quarter": latest_quarter,
        "quarter_label": _quarter_label(latest_quarter) if latest_quarter else None,
        "intro": intro,
        "business_excerpt": business_excerpt(business),
        "profile": profile_row,
        "page_title": page_title,
        "meta_description": meta_description,
        "canonical_url": canonical,
        "industry_url": industry_url,
        "analyzer_url": f"{base_url.rstrip('/')}/#/company/{company.edinet_code}",
        "json_ld": json_ld,
    }


def list_industries(db: Session) -> list[dict]:
    fin_sub = (
        select(
            Financial.edinet_code.label("edinet_code"),
            func.max(Financial.fiscal_year_end).label("fye"),
        )
        .group_by(Financial.edinet_code)
        .subquery()
    )
    rows = db.execute(
        select(Company.industry, func.count(func.distinct(Company.edinet_code)))
        .join(fin_sub, Company.edinet_code == fin_sub.c.edinet_code)
        .where(Company.listing_status == "上場", Company.industry.is_not(None))
        .group_by(Company.industry)
        .order_by(func.count(func.distinct(Company.edinet_code)).desc())
    ).all()
    return [
        {
            "name": name,
            "slug": industry_slug(name),
            "count": count,
            "url": f"/industries/{industry_slug(name)}",
        }
        for name, count in rows
    ]


def build_industries_page_context(db: Session, base_url: str) -> dict:
    industries = list_industries(db)
    title = f"業種別 上場企業一覧 | {SITE_NAME}"
    desc = f"上場企業を{len(industries)}業種で分類。各業種の財務データ・企業一覧へリンクします。"
    canonical = f"{base_url.rstrip('/')}/industries"
    json_ld = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "CollectionPage",
                "name": title,
                "description": desc,
                "url": canonical,
                "inLanguage": "ja",
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "トップ", "item": base_url.rstrip("/")},
                    {"@type": "ListItem", "position": 2, "name": "業種一覧", "item": canonical},
                ],
            },
        ],
    }
    return {
        "page_title": title,
        "meta_description": desc,
        "canonical_url": canonical,
        "industries": industries,
        "json_ld": json_ld,
    }


def build_industry_page_context(db: Session, industry_name: str, base_url: str) -> dict | None:
    fin_sub = (
        select(
            Financial.edinet_code.label("edinet_code"),
            func.max(Financial.fiscal_year_end).label("fye"),
        )
        .group_by(Financial.edinet_code)
        .subquery()
    )
    rows = db.execute(
        select(Company, Financial, StockQuote)
        .join(fin_sub, Company.edinet_code == fin_sub.c.edinet_code)
        .join(
            Financial,
            and_(
                Financial.edinet_code == fin_sub.c.edinet_code,
                Financial.fiscal_year_end == fin_sub.c.fye,
            ),
        )
        .outerjoin(StockQuote, Company.edinet_code == StockQuote.edinet_code)
        .where(Company.listing_status == "上場", Company.industry == industry_name)
        .order_by(Financial.revenue.desc().nullslast())
        .limit(INDUSTRY_PAGE_LIMIT)
    ).all()

    if not rows:
        return None

    margins = [fin.operating_margin for _, fin, _ in rows if fin.operating_margin is not None]
    roes = [fin.roe for _, fin, _ in rows if fin.roe is not None]
    growths = [fin.revenue_growth for _, fin, _ in rows if fin.revenue_growth is not None]
    revenues = [fin.revenue for _, fin, _ in rows if fin.revenue is not None]

    def _avg(vals: list[float]) -> float | None:
        return sum(vals) / len(vals) if vals else None

    companies = [
        {
            "edinet_code": c.edinet_code,
            "name": c.name,
            "sec_code": format_sec_code(c.sec_code),
            "revenue": format_yen(fin.revenue),
            "revenue_raw": fin.revenue,
            "operating_margin": format_pct(fin.operating_margin),
            "roe": format_pct(fin.roe),
            "revenue_growth": format_chg(fin.revenue_growth),
            "operating_income": format_yen(fin.operating_income),
            "net_income": format_yen(fin.net_income),
            "per": (
                f"{format_num(quote.per_edinet or quote.per, 1)}倍"
                if quote and (quote.per_edinet or quote.per)
                else "-"
            ),
            "url": f"/companies/{c.edinet_code}",
        }
        for c, fin, quote in rows
    ]
    slug = industry_slug(industry_name)
    title = f"{industry_name} 上場企業一覧（売上順） | {SITE_NAME}"
    desc = truncate_text(
        f"{industry_name}の上場企業{len(companies)}社を売上高順に掲載（業種内上位{INDUSTRY_PAGE_LIMIT}社まで）。有価証券報告書ベースの財務・不動産データへリンク。",
        160,
    )
    canonical = f"{base_url.rstrip('/')}/industries/{slug}"
    json_ld = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "CollectionPage",
                "name": title,
                "description": desc,
                "url": canonical,
                "inLanguage": "ja",
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "トップ", "item": base_url.rstrip("/")},
                    {"@type": "ListItem", "position": 2, "name": "業種一覧", "item": f"{base_url.rstrip('/')}/industries"},
                    {"@type": "ListItem", "position": 3, "name": industry_name, "item": canonical},
                ],
            },
        ],
    }
    return {
        "industry_name": industry_name,
        "companies": companies,
        "industry_stats": {
            "count": len(companies),
            "avg_margin": format_pct(_avg(margins)),
            "avg_roe": format_pct(_avg(roes)),
            "avg_growth": format_chg(_avg(growths)),
            "top_revenue": format_yen(max(revenues)) if revenues else "-",
        },
        "chart_data": build_industry_chart_data(companies),
        "page_title": title,
        "meta_description": desc,
        "canonical_url": canonical,
        "json_ld": json_ld,
    }


def list_sitemap_entries(db: Session) -> list[tuple[str, str, str]]:
    """(loc_path, lastmod, priority)"""
    fin_sub = (
        select(
            Financial.edinet_code.label("edinet_code"),
            func.max(Financial.fiscal_year_end).label("fye"),
        )
        .group_by(Financial.edinet_code)
        .subquery()
    )
    profile_lm = (
        select(
            CompanyProfile.edinet_code.label("edinet_code"),
            func.max(CompanyProfile.synced_at).label("synced"),
        )
        .where(CompanyProfile.parse_status == "ok")
        .group_by(CompanyProfile.edinet_code)
        .subquery()
    )
    rows = db.execute(
        select(Company.edinet_code, fin_sub.c.fye, profile_lm.c.synced)
        .join(fin_sub, Company.edinet_code == fin_sub.c.edinet_code)
        .outerjoin(profile_lm, Company.edinet_code == profile_lm.c.edinet_code)
        .where(Company.listing_status == "上場")
        .order_by(Company.edinet_code)
    ).all()

    entries: list[tuple[str, str, str]] = []
    for code, fye, synced in rows:
        lm = (fye or "")[:10]
        if synced:
            sync_date = synced.strftime("%Y-%m-%d") if hasattr(synced, "strftime") else str(synced)[:10]
            if sync_date > lm:
                lm = sync_date
        entries.append((f"/companies/{code}", lm, "0.8"))

    for ind in list_industries(db):
        entries.append((f"/industries/{ind['slug']}", "", "0.6"))

    return entries


def count_sitemap_companies(db: Session) -> int:
    return sum(1 for path, _, _ in list_sitemap_entries(db) if path.startswith("/companies/"))
