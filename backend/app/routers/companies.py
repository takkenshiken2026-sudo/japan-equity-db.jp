from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import (
    Company,
    CompanyProfile,
    Filing,
    Financial,
    QuarterlyFinancial,
    RealEstateProperty,
    RealEstateSync,
    StockQuote,
    get_db,
)
from app.company_verdict import build_company_verdict
from app.edinet.client import CURRENT_PARSE_VERSION, EdinetClient
from app.edinet.urls import edinet_download_url, edinet_viewer_url
from app.edinet.xbrl_profile import affiliate_summary, parse_profile_xbrl_zip
from app.market.yahoo import fetch_price_history, sec_code_to_ticker
from app.external_media.background import (
    needs_background_news_refresh,
    needs_background_trend_refresh,
    run_news_refresh,
    run_trend_refresh,
)
from app.external_media.collector import refresh_company_news, refresh_company_trend
from app.external_media.store import (
    build_news_payload_from_db,
    build_trend_payload_from_db,
)
from app.db import ExternalMediaSync
from app.queries import latest_financial_subquery

router = APIRouter(prefix="/companies", tags=["companies"])

YUHO_DOC_TYPES = ("120", "130")

_SECTION_LABELS = {
    "major_facilities": "主要設備",
    "store": "店舗",
    "office": "事務所",
}


def _latest_real_estate_fiscal_year(db: Session, edinet_code: str) -> str | None:
    return db.scalar(
        select(func.max(RealEstateProperty.fiscal_year_end)).where(
            RealEstateProperty.edinet_code == edinet_code
        )
    )


def _property_book_total(p: RealEstateProperty) -> float:
    if p.total_book_value is not None:
        return float(p.total_book_value)
    return float(
        (p.land_book_value or 0)
        + (p.building_book_value or 0)
        + (p.other_book_value or 0)
        + (p.machinery_book_value or 0)
        + (p.lease_book_value or 0)
    )


def _build_real_estate_payload(
    db: Session,
    edinet_code: str,
    *,
    limit: int = 500,
    include_items: bool = True,
) -> dict:
    sync = db.scalars(
        select(RealEstateSync)
        .where(RealEstateSync.edinet_code == edinet_code)
        .order_by(RealEstateSync.fiscal_year_end.desc())
        .limit(1)
    ).first()

    latest_fye = _latest_real_estate_fiscal_year(db, edinet_code)
    stmt = select(RealEstateProperty).where(RealEstateProperty.edinet_code == edinet_code)
    if latest_fye:
        stmt = stmt.where(RealEstateProperty.fiscal_year_end == latest_fye)
    stmt = stmt.order_by(RealEstateProperty.total_book_value.desc().nullslast())
    properties = db.scalars(stmt.limit(limit if include_items else 1000)).all()
    properties.sort(key=_property_book_total, reverse=True)

    total_book = sum(_property_book_total(p) for p in properties)
    total_land = sum(p.land_book_value or 0 for p in properties)
    total_building = sum(p.building_book_value or 0 for p in properties)
    total_other = sum(p.other_book_value or 0 for p in properties)
    total_machinery = sum(p.machinery_book_value or 0 for p in properties)
    total_lease = sum(p.lease_book_value or 0 for p in properties)
    total_land_area = sum(p.land_area_sqm or 0 for p in properties)
    total_building_area = sum(p.building_area_sqm or 0 for p in properties)

    by_prefecture: dict[str, dict] = {}
    by_section: dict[str, dict] = {}
    for p in properties:
        book = _property_book_total(p)
        pref = p.prefecture or "不明"
        bucket = by_prefecture.setdefault(
            pref, {"prefecture": pref, "count": 0, "total_book_value_m": 0.0, "land_area_sqm": 0.0}
        )
        bucket["count"] += 1
        bucket["total_book_value_m"] += book
        bucket["land_area_sqm"] += p.land_area_sqm or 0

        sec_key = p.section or "other"
        sec_label = _SECTION_LABELS.get(sec_key, sec_key)
        sec = by_section.setdefault(
            sec_key,
            {"section": sec_key, "label": sec_label, "count": 0, "total_book_value_m": 0.0},
        )
        sec["count"] += 1
        sec["total_book_value_m"] += book

    prefecture_rows = sorted(
        by_prefecture.values(), key=lambda x: x["total_book_value_m"], reverse=True
    )
    section_rows = sorted(
        by_section.values(), key=lambda x: x["total_book_value_m"], reverse=True
    )

    def _serialize_property(p: RealEstateProperty) -> dict:
        book = _property_book_total(p)
        return {
            "facility_name": p.facility_name,
            "location": p.location,
            "prefecture": p.prefecture,
            "section": p.section,
            "section_label": _SECTION_LABELS.get(p.section or "", p.section),
            "category": p.category,
            "building_scale": p.building_scale,
            "building_area_sqm": p.building_area_sqm,
            "building_book_value": p.building_book_value,
            "land_area_sqm": p.land_area_sqm,
            "land_book_value": p.land_book_value,
            "other_book_value": p.other_book_value,
            "total_book_value": book if book else p.total_book_value,
            "machinery_book_value": p.machinery_book_value,
            "lease_book_value": p.lease_book_value,
            "completion_year": p.completion_year,
            "employees": p.employees,
            "fiscal_year_end": p.fiscal_year_end,
            "doc_id": p.doc_id,
        }

    top = [_serialize_property(p) for p in properties[:5]]

    payload: dict = {
        "edinet_code": edinet_code,
        "fiscal_year_end": latest_fye,
        "sync": {
            "doc_id": sync.doc_id if sync else None,
            "fiscal_year_end": sync.fiscal_year_end if sync else None,
            "property_count": sync.property_count if sync else 0,
            "parse_status": sync.parse_status if sync else None,
            "synced_at": sync.synced_at.isoformat() if sync and sync.synced_at else None,
        }
        if sync
        else None,
        "summary": {
            "count": len(properties),
            "total_book_value_m": total_book,
            "total_land_book_value_m": total_land,
            "total_building_book_value_m": total_building,
            "total_other_book_value_m": total_other,
            "total_machinery_book_value_m": total_machinery,
            "total_lease_book_value_m": total_lease,
            "total_land_area_sqm": total_land_area,
            "total_building_area_sqm": total_building_area,
            "prefecture_count": len(prefecture_rows),
        },
        "composition": {
            "land_m": total_land,
            "building_m": total_building,
            "other_m": total_other,
            "machinery_m": total_machinery,
            "lease_m": total_lease,
        },
        "by_prefecture": prefecture_rows,
        "by_section": section_rows,
        "top_properties": top,
    }
    if include_items:
        payload["items"] = [_serialize_property(p) for p in properties]
    return payload


def _property_book_value_expr():
    return func.coalesce(
        RealEstateProperty.total_book_value,
        func.coalesce(RealEstateProperty.land_book_value, 0.0)
        + func.coalesce(RealEstateProperty.building_book_value, 0.0)
        + func.coalesce(RealEstateProperty.other_book_value, 0.0)
        + func.coalesce(RealEstateProperty.machinery_book_value, 0.0)
        + func.coalesce(RealEstateProperty.lease_book_value, 0.0),
    )


def _real_estate_briefs_batch(db: Session, edinet_codes: list[str]) -> dict[str, dict]:
    if not edinet_codes:
        return {}
    latest_rows = db.execute(
        select(
            RealEstateProperty.edinet_code,
            func.max(RealEstateProperty.fiscal_year_end).label("fye"),
        )
        .where(RealEstateProperty.edinet_code.in_(edinet_codes))
        .group_by(RealEstateProperty.edinet_code)
    ).all()
    if not latest_rows:
        return {}
    fye_map = {row.edinet_code: row.fye for row in latest_rows}
    book_value = _property_book_value_expr()
    stats_rows = db.execute(
        select(
            RealEstateProperty.edinet_code,
            RealEstateProperty.fiscal_year_end,
            func.count().label("cnt"),
            func.sum(book_value).label("total"),
        )
        .where(RealEstateProperty.edinet_code.in_(edinet_codes))
        .group_by(RealEstateProperty.edinet_code, RealEstateProperty.fiscal_year_end)
    ).all()
    briefs: dict[str, dict] = {}
    for row in stats_rows:
        if fye_map.get(row.edinet_code) != row.fiscal_year_end:
            continue
        briefs[row.edinet_code] = {
            "fiscal_year_end": row.fiscal_year_end,
            "count": int(row.cnt or 0),
            "total_book_value_m": float(row.total or 0),
        }
    return briefs


def _real_estate_brief(db: Session, edinet_code: str) -> dict | None:
    latest_fye = _latest_real_estate_fiscal_year(db, edinet_code)
    if not latest_fye:
        return None
    rows = db.scalars(
        select(RealEstateProperty)
        .where(
            RealEstateProperty.edinet_code == edinet_code,
            RealEstateProperty.fiscal_year_end == latest_fye,
        )
        .order_by(RealEstateProperty.total_book_value.desc().nullslast())
        .limit(1)
    ).all()
    if not rows:
        return None
    count = db.scalar(
        select(func.count())
        .select_from(RealEstateProperty)
        .where(
            RealEstateProperty.edinet_code == edinet_code,
            RealEstateProperty.fiscal_year_end == latest_fye,
        )
    ) or 0
    total = db.scalar(
        select(func.sum(RealEstateProperty.total_book_value))
        .where(
            RealEstateProperty.edinet_code == edinet_code,
            RealEstateProperty.fiscal_year_end == latest_fye,
        )
    )
    if not total:
        props_all = db.scalars(
            select(RealEstateProperty).where(
                RealEstateProperty.edinet_code == edinet_code,
                RealEstateProperty.fiscal_year_end == latest_fye,
            )
        ).all()
        total = sum(_property_book_total(p) for p in props_all)
    else:
        total = float(total)
    top = rows[0]
    return {
        "fiscal_year_end": latest_fye,
        "count": count,
        "total_book_value_m": total,
        "top_property_name": top.facility_name,
        "top_property_prefecture": top.prefecture,
    }


def _companies_with_real_estate_subquery():
    return select(RealEstateProperty.edinet_code).distinct()


def _latest_yuho_filing(db: Session, edinet_code: str) -> Filing | None:
    return db.scalars(
        select(Filing)
        .where(
            Filing.edinet_code == edinet_code,
            Filing.doc_type_code.in_(YUHO_DOC_TYPES),
            Filing.has_xbrl.is_(True),
        )
        .order_by(Filing.period_end.desc(), Filing.submit_date_time.desc())
        .limit(1)
    ).first()


def _format_yen_short(v: float | None) -> str | None:
    if v is None:
        return None
    abs_v = abs(v)
    if abs_v >= 1e12:
        return f"{v / 1e12:.1f}兆円"
    if abs_v >= 1e8:
        return f"{v / 1e8:.0f}億円"
    return f"{v:,.0f}円"


def _build_basic_intro(
    company: Company,
    latest_fin: Financial | None,
    quote: StockQuote | None,
    re_brief: dict | None,
) -> str:
    parts: list[str] = []
    short_name = company.name.replace("株式会社", "").strip()
    if company.industry:
        parts.append(f"{company.industry}の")
    parts.append(short_name)
    if company.listing_status == "上場":
        parts.append("は上場企業")
    elif company.listing_status:
        parts.append(f"は{company.listing_status}企業")
    else:
        parts.append("は")
    details: list[str] = []
    if company.location:
        details.append(f"本社は{company.location}")
    if latest_fin and latest_fin.revenue:
        details.append(f"直近期売上は{_format_yen_short(latest_fin.revenue)}")
    if latest_fin and latest_fin.operating_margin is not None:
        details.append(f"営業利益率{latest_fin.operating_margin * 100:.1f}%")
    if quote and quote.ticker:
        details.append(f"証券コード {quote.ticker}")
    if re_brief and re_brief.get("count"):
        details.append(f"保有不動産{re_brief['count']}件")
    if details:
        parts.append("。" + "、".join(details))
    else:
        parts.append("。")
    parts.append("有価証券報告書のデータに基づき財務・不動産情報を確認できます。")
    return "".join(parts)


def _serialize_profile(
    profile: CompanyProfile,
    *,
    intro: str | None = None,
) -> dict:
    affiliates = affiliate_summary(profile.affiliated_entities)
    return {
        "source": "edinet",
        "intro": intro,
        "business_description": profile.business_description,
        "company_history": profile.company_history,
        "employee_count": profile.employee_count,
        "employees_text": profile.employees_text,
        "capital_stock_m": profile.capital_stock_m,
        "issued_shares": profile.issued_shares,
        "affiliates_summary": affiliates,
        "fiscal_year_end": profile.fiscal_year_end,
        "doc_id": profile.doc_id,
        "synced_at": profile.synced_at.isoformat() if profile.synced_at else None,
    }


def _basic_profile_payload(
    company: Company,
    latest_fin: Financial | None,
    quote: StockQuote | None,
    re_brief: dict | None,
) -> dict:
    return {
        "source": "basic",
        "intro": _build_basic_intro(company, latest_fin, quote, re_brief),
        "business_description": None,
        "company_history": None,
        "employee_count": None,
        "capital_stock_m": None,
        "issued_shares": None,
        "affiliates_summary": None,
        "fiscal_year_end": company.fiscal_year_end,
        "doc_id": None,
        "synced_at": None,
    }


def _load_or_fetch_profile(
    db: Session,
    company: Company,
    *,
    latest_fin: Financial | None = None,
    quote: StockQuote | None = None,
    re_brief: dict | None = None,
    refresh: bool = False,
) -> dict:
    profile = db.get(CompanyProfile, company.edinet_code)
    if profile and profile.parse_status == "ok" and not refresh:
        intro = _build_basic_intro(company, latest_fin, quote, re_brief)
        return _serialize_profile(profile, intro=intro)

    filing = _latest_yuho_filing(db, company.edinet_code)
    if not filing:
        return _basic_profile_payload(company, latest_fin, quote, re_brief)

    try:
        client = EdinetClient(sleep_seconds=0.35)
        content = client.download_document(filing.doc_id, doc_type="1")
        parsed = parse_profile_xbrl_zip(content)
        if parsed.parse_status != "ok":
            if profile and profile.parse_status == "ok":
                return _serialize_profile(
                    profile,
                    intro=_build_basic_intro(company, latest_fin, quote, re_brief),
                )
            return _basic_profile_payload(company, latest_fin, quote, re_brief)

        profile = profile or CompanyProfile(edinet_code=company.edinet_code)
        profile.doc_id = filing.doc_id
        profile.fiscal_year_end = filing.period_end
        profile.business_description = parsed.business_description
        profile.company_history = parsed.company_history
        profile.employees_text = parsed.employees_text
        profile.affiliated_entities = parsed.affiliated_entities
        profile.employee_count = parsed.employee_count
        profile.capital_stock_m = parsed.capital_stock_m
        profile.issued_shares = parsed.issued_shares
        profile.parse_status = parsed.parse_status
        profile.synced_at = datetime.utcnow()
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return _serialize_profile(
            profile,
            intro=_build_basic_intro(company, latest_fin, quote, re_brief),
        )
    except Exception:
        if profile and profile.parse_status == "ok":
            return _serialize_profile(
                profile,
                intro=_build_basic_intro(company, latest_fin, quote, re_brief),
            )
        return _basic_profile_payload(company, latest_fin, quote, re_brief)


@router.get("")
def search_companies(
    q: Optional[str] = Query(None, description="企業名・証券コード・EDINETコード"),
    listing: Optional[str] = Query(None, description="上場区分"),
    industry: Optional[str] = Query(None, description="業種"),
    has_real_estate: Optional[bool] = Query(None, description="不動産明細あり"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    latest = latest_financial_subquery()
    stmt = (
        select(Company, Financial, StockQuote)
        .outerjoin(latest, Company.edinet_code == latest.c.edinet_code)
        .outerjoin(
            Financial,
            and_(
                Financial.edinet_code == latest.c.edinet_code,
                Financial.fiscal_year_end == latest.c.fiscal_year_end,
            ),
        )
        .outerjoin(StockQuote, Company.edinet_code == StockQuote.edinet_code)
    )
    if q:
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Company.name.like(keyword),
                Company.name_en.like(keyword),
                Company.sec_code.like(keyword),
                Company.edinet_code.like(keyword),
            )
        )
    if listing:
        stmt = stmt.where(Company.listing_status == listing)
    if industry:
        stmt = stmt.where(Company.industry.like(f"%{industry}%"))
    if has_real_estate:
        stmt = stmt.where(Company.edinet_code.in_(_companies_with_real_estate_subquery()))

    count_stmt = select(func.count()).select_from(Company)
    if q:
        keyword = f"%{q.strip()}%"
        count_stmt = count_stmt.where(
            or_(
                Company.name.like(keyword),
                Company.name_en.like(keyword),
                Company.sec_code.like(keyword),
                Company.edinet_code.like(keyword),
            )
        )
    if listing:
        count_stmt = count_stmt.where(Company.listing_status == listing)
    if industry:
        count_stmt = count_stmt.where(Company.industry.like(f"%{industry}%"))
    if has_real_estate:
        count_stmt = count_stmt.where(Company.edinet_code.in_(_companies_with_real_estate_subquery()))
    total = db.scalar(count_stmt)
    rows = db.execute(stmt.order_by(Company.name).offset(offset).limit(limit)).all()
    codes = [r[0].edinet_code for r in rows]
    re_map = _real_estate_briefs_batch(db, codes) if codes else {}
    items = []
    for company, financial, quote in rows:
        row = {
            "edinet_code": company.edinet_code,
            "name": company.name,
            "sec_code": company.sec_code,
            "listing_status": company.listing_status,
            "industry": company.industry,
            "location": company.location,
            "fiscal_year_end": (financial.fiscal_year_end if financial else None) or company.fiscal_year_end,
        }
        if financial:
            row.update({
                "revenue": financial.revenue,
                "roe": financial.roe,
                "operating_margin": financial.operating_margin,
            })
        if quote:
            row.update({
                "price": quote.price,
                "per": quote.per,
                "per_edinet": quote.per_edinet,
                "pbr": quote.pbr,
                "pbr_edinet": quote.pbr_edinet,
                "market_cap": quote.market_cap,
            })
        brief = re_map.get(company.edinet_code)
        if brief:
            row["real_estate"] = brief
        items.append(row)
    return {"total": total or 0, "items": items}


def _industry_benchmark(db: Session, industry: str | None) -> dict | None:
    if not industry:
        return None
    latest = latest_financial_subquery()
    row = db.execute(
        select(
            func.count(Financial.edinet_code),
            func.avg(Financial.operating_margin),
            func.avg(Financial.roe),
            func.avg(Financial.revenue_growth),
            func.avg(Financial.equity_ratio),
        )
        .select_from(Financial)
        .join(
            latest,
            (Financial.edinet_code == latest.c.edinet_code)
            & (Financial.fiscal_year_end == latest.c.fiscal_year_end),
        )
        .join(Company, Financial.edinet_code == Company.edinet_code)
        .where(Company.listing_status == "上場", Company.industry == industry)
    ).one()
    count = row[0] or 0
    if not count:
        return None
    return {
        "industry": industry,
        "company_count": count,
        "avg_operating_margin": row[1],
        "avg_roe": row[2],
        "avg_revenue_growth": row[3],
        "avg_equity_ratio": row[4],
    }


def _company_snapshot(db: Session, edinet_code: str) -> dict | None:
    company = db.get(Company, edinet_code)
    if not company:
        return None
    financials = db.scalars(
        select(Financial)
        .where(Financial.edinet_code == edinet_code)
        .order_by(Financial.fiscal_year_end.desc())
        .limit(1)
    ).all()
    latest = financials[0] if financials else None
    quote = db.get(StockQuote, edinet_code)
    re_brief = _real_estate_brief(db, edinet_code)
    return {
        "edinet_code": company.edinet_code,
        "name": company.name,
        "sec_code": company.sec_code,
        "industry": company.industry,
        "revenue": latest.revenue if latest else None,
        "operating_income": latest.operating_income if latest else None,
        "operating_margin": latest.operating_margin if latest else None,
        "roe": latest.roe if latest else None,
        "revenue_growth": latest.revenue_growth if latest else None,
        "per": (quote.per_edinet or quote.per) if quote else None,
        "pbr": (quote.pbr_edinet or quote.pbr) if quote else None,
        "market_cap": quote.market_cap if quote else None,
        "price": quote.price if quote else None,
        "real_estate": re_brief,
    }


def _filing_submit_map(db: Session, doc_ids: list[str]) -> dict[str, str | None]:
    if not doc_ids:
        return {}
    rows = db.scalars(select(Filing).where(Filing.doc_id.in_(doc_ids))).all()
    return {f.doc_id: f.submit_date_time for f in rows}


def _build_data_freshness(
    db: Session,
    edinet_code: str,
    financials: list[Financial],
    quote: StockQuote | None,
    re_sync: dict | None,
) -> dict:
    latest_fin = financials[0] if financials else None
    latest_filing = (
        db.get(Filing, latest_fin.doc_id) if latest_fin and latest_fin.doc_id else None
    )
    latest_q = db.scalars(
        select(QuarterlyFinancial)
        .where(QuarterlyFinancial.edinet_code == edinet_code)
        .order_by(QuarterlyFinancial.period_end.desc())
        .limit(1)
    ).first()
    profile = db.get(CompanyProfile, edinet_code)
    q_count = db.scalar(
        select(func.count())
        .select_from(QuarterlyFinancial)
        .where(QuarterlyFinancial.edinet_code == edinet_code)
    ) or 0

    return {
        "financial": {
            "fiscal_year_end": latest_fin.fiscal_year_end if latest_fin else None,
            "submit_date_time": latest_filing.submit_date_time if latest_filing else None,
            "updated_at": latest_fin.updated_at.isoformat() if latest_fin and latest_fin.updated_at else None,
            "has_operating_cf": bool(latest_fin and latest_fin.operating_cf is not None),
            "parse_version": latest_fin.parse_version if latest_fin else None,
            "parse_version_current": CURRENT_PARSE_VERSION,
        },
        "quarterly": {
            "latest_period_end": latest_q.period_end if latest_q else None,
            "row_count": q_count,
        },
        "stock": {
            "updated_at": quote.updated_at.isoformat() if quote and quote.updated_at else None,
        },
        "real_estate": {
            "fiscal_year_end": re_sync.get("fiscal_year_end") if re_sync else None,
            "synced_at": re_sync.get("synced_at") if re_sync else None,
            "parse_status": re_sync.get("parse_status") if re_sync else None,
        },
        "profile": {
            "synced_at": profile.synced_at.isoformat() if profile and profile.synced_at else None,
            "parse_status": profile.parse_status if profile else None,
        },
    }


def _price_on_or_before(
    sorted_dates: list[str], price_map: dict[str, float], target: str
) -> float | None:
    candidates = [d for d in sorted_dates if d <= target]
    if not candidates:
        return None
    return price_map.get(candidates[-1])


def _build_valuation_history(
    financials: list[Financial],
    price_points: list[dict],
) -> list[dict]:
    if not financials or not price_points:
        return []
    price_map = {p["date"]: p["close"] for p in price_points}
    sorted_dates = sorted(price_map.keys())
    rows: list[dict] = []
    for fin in reversed(financials):
        fye = (fin.fiscal_year_end or "").strip()
        if not fye:
            continue
        price = _price_on_or_before(sorted_dates, price_map, fye)
        per = price / fin.eps if price and fin.eps else None
        pbr = price / fin.bps if price and fin.bps else None
        div_yield = (
            fin.dividend_per_share / price if price and fin.dividend_per_share else None
        )
        rows.append(
            {
                "fiscal_year_end": fye,
                "price": price,
                "eps": fin.eps,
                "bps": fin.bps,
                "per": round(per, 2) if per is not None else None,
                "pbr": round(pbr, 2) if pbr is not None else None,
                "dividend_per_share": fin.dividend_per_share,
                "dividend_yield": round(div_yield, 4) if div_yield is not None else None,
            }
        )
    return rows


@router.get("/compare/batch")
def compare_companies_batch(
    codes: str = Query(..., description="カンマ区切り EDINETコード（最大4件）"),
    db: Session = Depends(get_db),
):
    code_list = [c.strip() for c in codes.split(",") if c.strip()][:4]
    items = []
    for code in code_list:
        snap = _company_snapshot(db, code)
        if snap:
            items.append(snap)
    return {"count": len(items), "items": items}


@router.get("/compare/detail")
def compare_companies_detail(
    codes: str = Query(..., description="カンマ区切り EDINETコード（最大4件）"),
    financial_limit: int = Query(8, ge=1, le=12),
    db: Session = Depends(get_db),
):
    code_list = [c.strip() for c in codes.split(",") if c.strip()][:4]
    items = []
    for code in code_list:
        company = db.get(Company, code)
        if not company:
            continue
        financials = db.scalars(
            select(Financial)
            .where(Financial.edinet_code == code)
            .order_by(Financial.fiscal_year_end.desc())
            .limit(financial_limit)
        ).all()
        submit_map = _filing_submit_map(db, [f.doc_id for f in financials if f.doc_id])
        latest = financials[0] if financials else None
        quote = db.get(StockQuote, code)
        re_brief = _real_estate_brief(db, code)
        snap = _company_snapshot(db, code) or {}
        items.append(
            {
                **snap,
                "financials": [
                    {
                        "fiscal_year_end": f.fiscal_year_end,
                        "revenue": f.revenue,
                        "operating_income": f.operating_income,
                        "net_income": f.net_income,
                        "operating_margin": f.operating_margin,
                        "roe": f.roe,
                        "roa": f.roa,
                        "revenue_growth": f.revenue_growth,
                        "operating_cf": f.operating_cf,
                        "equity_ratio": f.equity_ratio,
                        "eps": f.eps,
                        "bps": f.bps,
                        "submit_date_time": submit_map.get(f.doc_id),
                    }
                    for f in financials
                ],
                "fiscal_year_end": latest.fiscal_year_end if latest else None,
                "real_estate": re_brief,
                "stock": {
                    "price": quote.price if quote else None,
                    "market_cap": quote.market_cap if quote else None,
                    "per": (quote.per_edinet or quote.per) if quote else None,
                    "pbr": (quote.pbr_edinet or quote.pbr) if quote else None,
                    "dividend_yield": quote.dividend_yield if quote else None,
                    "updated_at": quote.updated_at.isoformat() if quote and quote.updated_at else None,
                }
                if quote
                else None,
            }
        )
    return {"count": len(items), "items": items}


@router.get("/{edinet_code}")
def get_company(
    edinet_code: str,
    financial_limit: int = Query(12, ge=1, le=20),
    db: Session = Depends(get_db),
):
    company = db.get(Company, edinet_code)
    if not company:
        raise HTTPException(status_code=404, detail="企業が見つかりません")

    financials = db.scalars(
        select(Financial)
        .where(Financial.edinet_code == edinet_code)
        .order_by(Financial.fiscal_year_end.desc())
        .limit(financial_limit)
    ).all()

    filings = db.scalars(
        select(Filing)
        .where(Filing.edinet_code == edinet_code)
        .order_by(Filing.submit_date_time.desc())
        .limit(20)
    ).all()

    quote = db.get(StockQuote, edinet_code)
    re_brief = _real_estate_brief(db, edinet_code)
    latest_fin = financials[0] if financials else None
    if re_brief and latest_fin and latest_fin.total_assets:
        re_brief["assets_ratio"] = re_brief["total_book_value_m"] * 1e6 / latest_fin.total_assets

    re_sync_row = db.scalars(
        select(RealEstateSync)
        .where(RealEstateSync.edinet_code == edinet_code)
        .order_by(RealEstateSync.fiscal_year_end.desc())
        .limit(1)
    ).first()
    re_sync = {
        "fiscal_year_end": re_sync_row.fiscal_year_end if re_sync_row else None,
        "synced_at": re_sync_row.synced_at.isoformat() if re_sync_row and re_sync_row.synced_at else None,
        "parse_status": re_sync_row.parse_status if re_sync_row else None,
    }

    submit_map = _filing_submit_map(db, [f.doc_id for f in financials if f.doc_id])
    benchmark = _industry_benchmark(db, company.industry)
    data_freshness = _build_data_freshness(db, edinet_code, financials, quote, re_sync)
    qtr_latest = db.scalars(
        select(QuarterlyFinancial)
        .where(QuarterlyFinancial.edinet_code == edinet_code)
        .order_by(QuarterlyFinancial.period_end.desc())
        .limit(1)
    ).first()
    qtr_dict = None
    if qtr_latest:
        qtr_dict = {"revenue_yoy": qtr_latest.revenue_yoy, "period_end": qtr_latest.period_end}
    re_for_verdict = re_brief
    verdict = build_company_verdict(
        financials=financials,
        stock={
            "per": quote.per if quote else None,
            "pbr": quote.pbr if quote else None,
            "per_edinet": quote.per_edinet if quote else None,
            "pbr_edinet": quote.pbr_edinet if quote else None,
            "market_cap": quote.market_cap if quote else None,
            "price": quote.price if quote else None,
        }
        if quote
        else None,
        benchmark=benchmark,
        quarterly_latest=qtr_dict,
        real_estate=re_for_verdict,
        data_freshness=data_freshness,
    )

    return {
        "company": {
            "edinet_code": company.edinet_code,
            "name": company.name,
            "name_en": company.name_en,
            "sec_code": company.sec_code,
            "jcn": company.jcn,
            "listing_status": company.listing_status,
            "industry": company.industry,
            "submitter_type": company.submitter_type,
            "location": company.location,
            "fiscal_year_end": company.fiscal_year_end,
        },
        "stock": {
            "ticker": quote.ticker,
            "price": quote.price,
            "market_cap": quote.market_cap,
            "per": quote.per,
            "pbr": quote.pbr,
            "per_edinet": quote.per_edinet,
            "pbr_edinet": quote.pbr_edinet,
            "dividend_yield": quote.dividend_yield,
            "fifty_two_week_high": quote.fifty_two_week_high,
            "fifty_two_week_low": quote.fifty_two_week_low,
            "updated_at": quote.updated_at.isoformat() if quote and quote.updated_at else None,
        }
        if quote
        else None,
        "financials": [
            {
                "fiscal_year_end": f.fiscal_year_end,
                "revenue": f.revenue,
                "operating_income": f.operating_income,
                "ordinary_income": f.ordinary_income,
                "net_income": f.net_income,
                "total_assets": f.total_assets,
                "net_assets": f.net_assets,
                "total_liabilities": f.total_liabilities,
                "eps": f.eps,
                "bps": f.bps,
                "operating_cf": f.operating_cf,
                "investing_cf": f.investing_cf,
                "financing_cf": f.financing_cf,
                "cash_and_deposits": f.cash_and_deposits,
                "interest_bearing_debt": f.interest_bearing_debt,
                "dividend_per_share": f.dividend_per_share,
                "operating_margin": f.operating_margin,
                "roe": f.roe,
                "roa": f.roa,
                "equity_ratio": f.equity_ratio,
                "debt_equity_ratio": f.debt_equity_ratio,
                "revenue_growth": f.revenue_growth,
                "doc_id": f.doc_id,
                "submit_date_time": submit_map.get(f.doc_id),
            }
            for f in financials
        ],
        "filings": [
            {
                "doc_id": f.doc_id,
                "doc_type_code": f.doc_type_code,
                "doc_description": f.doc_description,
                "period_start": f.period_start,
                "period_end": f.period_end,
                "submit_date_time": f.submit_date_time,
                "has_pdf": f.has_pdf,
                "has_xbrl": f.has_xbrl,
                "viewer_url": edinet_viewer_url(f.doc_id),
                "pdf_url": edinet_download_url(f.doc_id, "pdf") if f.has_pdf else None,
                "xbrl_url": edinet_download_url(f.doc_id, "xbrl") if f.has_xbrl else None,
            }
            for f in filings
        ],
        "real_estate": re_brief,
        "industry_benchmark": benchmark,
        "data_freshness": data_freshness,
        "verdict": verdict,
        "profile": _basic_profile_payload(company, latest_fin, quote, re_brief),
    }


@router.get("/{edinet_code}/valuation-history")
def get_valuation_history(
    edinet_code: str,
    db: Session = Depends(get_db),
):
    company = db.get(Company, edinet_code)
    if not company:
        raise HTTPException(status_code=404, detail="企業が見つかりません")

    financials = db.scalars(
        select(Financial)
        .where(Financial.edinet_code == edinet_code)
        .order_by(Financial.fiscal_year_end.desc())
        .limit(12)
    ).all()
    quote = db.get(StockQuote, edinet_code)
    ticker = (quote.ticker if quote and quote.ticker else None) or sec_code_to_ticker(
        company.sec_code
    )
    if not ticker:
        raise HTTPException(status_code=404, detail="ティッカーが取得できません")

    price_points = fetch_price_history(ticker, "5y")
    history = _build_valuation_history(financials, price_points)
    return {
        "edinet_code": edinet_code,
        "ticker": ticker,
        "count": len(history),
        "current": {
            "price": quote.price if quote else None,
            "per": quote.per_edinet or quote.per if quote else None,
            "pbr": quote.pbr_edinet or quote.pbr if quote else None,
            "updated_at": quote.updated_at.isoformat() if quote and quote.updated_at else None,
        },
        "items": history,
    }


@router.get("/{edinet_code}/price-history")
def get_price_history(
    edinet_code: str,
    range: str = Query("1y", pattern="^(1mo|3mo|6mo|1y|2y|5y)$"),
    db: Session = Depends(get_db),
):
    company = db.get(Company, edinet_code)
    if not company:
        raise HTTPException(status_code=404, detail="企業が見つかりません")
    quote = db.get(StockQuote, edinet_code)
    ticker = (quote.ticker if quote and quote.ticker else None) or sec_code_to_ticker(company.sec_code)
    if not ticker:
        raise HTTPException(status_code=404, detail="ティッカーが取得できません")
    points = fetch_price_history(ticker, range)
    return {"edinet_code": edinet_code, "ticker": ticker, "range": range, "points": points}


@router.get("/{edinet_code}/profile")
def get_company_profile(
    edinet_code: str,
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    company = db.get(Company, edinet_code)
    if not company:
        raise HTTPException(status_code=404, detail="企業が見つかりません")

    latest_fin = db.scalars(
        select(Financial)
        .where(Financial.edinet_code == edinet_code)
        .order_by(Financial.fiscal_year_end.desc())
        .limit(1)
    ).first()
    quote = db.get(StockQuote, edinet_code)
    re_brief = _real_estate_brief(db, edinet_code)
    return _load_or_fetch_profile(
        db,
        company,
        latest_fin=latest_fin,
        quote=quote,
        re_brief=re_brief,
        refresh=refresh,
    )


@router.get("/{edinet_code}/real-estate")
def get_company_real_estate(
    edinet_code: str,
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    company = db.get(Company, edinet_code)
    if not company:
        raise HTTPException(status_code=404, detail="企業が見つかりません")

    payload = _build_real_estate_payload(db, edinet_code, limit=limit)
    latest_fin = db.scalar(
        select(Financial.total_assets)
        .where(Financial.edinet_code == edinet_code)
        .order_by(Financial.fiscal_year_end.desc())
        .limit(1)
    )
    if latest_fin and payload["summary"]["total_book_value_m"]:
        payload["summary"]["assets_ratio"] = (
            payload["summary"]["total_book_value_m"] * 1e6 / latest_fin
        )
    return payload


@router.get("/{edinet_code}/quarterly")
def get_company_quarterly(
    edinet_code: str,
    limit: int = Query(12, ge=1, le=40),
    db: Session = Depends(get_db),
):
    company = db.get(Company, edinet_code)
    if not company:
        raise HTTPException(status_code=404, detail="企業が見つかりません")

    rows = db.scalars(
        select(QuarterlyFinancial)
        .where(QuarterlyFinancial.edinet_code == edinet_code)
        .order_by(QuarterlyFinancial.period_end.desc())
        .limit(limit)
    ).all()

    return {
        "edinet_code": edinet_code,
        "count": len(rows),
        "items": [
            {
                "doc_id": r.doc_id,
                "period_start": r.period_start,
                "period_end": r.period_end,
                "quarter_number": r.quarter_number,
                "revenue_cumulative": r.revenue_cumulative,
                "revenue_prior_year_cum": r.revenue_prior_year_cum,
                "revenue_single": r.revenue_single,
                "operating_income_cumulative": r.operating_income_cumulative,
                "operating_income_prior_year_cum": r.operating_income_prior_year_cum,
                "operating_income_single": r.operating_income_single,
                "net_income_cumulative": r.net_income_cumulative,
                "net_income_single": r.net_income_single,
                "revenue_yoy": r.revenue_yoy,
                "revenue_qoq": r.revenue_qoq,
                "operating_income_yoy": r.operating_income_yoy,
                "operating_income_qoq": r.operating_income_qoq,
                "net_income_yoy": r.net_income_yoy,
                "net_income_qoq": r.net_income_qoq,
                "eps_cumulative": r.eps_cumulative,
                "eps_yoy": r.eps_yoy,
            }
            for r in rows
        ],
    }


@router.get("/{edinet_code}/news")
def get_company_news(
    edinet_code: str,
    background_tasks: BackgroundTasks,
    limit: int = Query(10, ge=1, le=20),
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    company = db.get(Company, edinet_code)
    if not company:
        raise HTTPException(status_code=404, detail="企業が見つかりません")
    if refresh:
        refresh_company_news(db, company, log_delta=True)
    payload = build_news_payload_from_db(db, company, limit=limit)
    sync_row = db.get(ExternalMediaSync, edinet_code)
    if not refresh and needs_background_news_refresh(
        sync_row, has_data=payload["rss_total"] > 0
    ):
        background_tasks.add_task(run_news_refresh, edinet_code)
        payload["refreshing"] = True
    return payload


@router.get("/{edinet_code}/search-trend")
def get_company_search_trend(
    edinet_code: str,
    background_tasks: BackgroundTasks,
    days: int = Query(90, ge=7, le=90),
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    company = db.get(Company, edinet_code)
    if not company:
        raise HTTPException(status_code=404, detail="企業が見つかりません")
    if refresh:
        refresh_company_trend(db, company, days=days, log_delta=True)
    payload = build_trend_payload_from_db(db, company, days=days)
    sync_row = db.get(ExternalMediaSync, edinet_code)
    if not refresh and needs_background_trend_refresh(
        sync_row, has_data=payload["count"] > 0
    ):
        background_tasks.add_task(run_trend_refresh, edinet_code, days=days)
        payload["refreshing"] = True
    return payload
