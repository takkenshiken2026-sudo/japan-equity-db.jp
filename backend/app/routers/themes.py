from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import Company, Financial, RealEstateProperty, StockQuote, get_db
from app.company_verdict import DISCLAIMER, GENERAL_RISKS
from app.real_estate_nav import MAX_SANE_NAV_RATIO, MIN_SANE_MARKET_CAP, compute_nav_ratio
from app.routers.companies import _property_book_value_expr, _real_estate_briefs_batch
from app.queries import latest_financial_subquery
from app.routers.screening import _pbr_expr, _serialize_row, _valuation_expr

router = APIRouter(prefix="/themes", tags=["themes"])


def _theme_query_base(db: Session):
    latest = latest_financial_subquery()
    return (
        select(Company, Financial, StockQuote)
        .join(latest, Company.edinet_code == latest.c.edinet_code)
        .join(
            Financial,
            and_(
                Financial.edinet_code == latest.c.edinet_code,
                Financial.fiscal_year_end == latest.c.fiscal_year_end,
            ),
        )
        .outerjoin(StockQuote, Company.edinet_code == StockQuote.edinet_code)
        .where(Company.listing_status == "上場")
    )


def _rows_to_items(db: Session, rows: list) -> list[dict[str, Any]]:
    codes = [company.edinet_code for company, _, _ in rows]
    re_briefs = _real_estate_briefs_batch(db, codes)
    items = []
    for company, financial, quote in rows:
        brief = re_briefs.get(company.edinet_code)
        items.append(_serialize_row(company, financial, quote, brief))
    return items


@router.get("/weekly")
def weekly_themes(
    limit: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
):
    book_value = _property_book_value_expr()
    latest_re = (
        select(
            RealEstateProperty.edinet_code,
            func.max(RealEstateProperty.fiscal_year_end).label("fye"),
        )
        .group_by(RealEstateProperty.edinet_code)
        .subquery()
    )
    re_totals = (
        select(
            RealEstateProperty.edinet_code,
            func.sum(book_value).label("total_book_m"),
        )
        .join(
            latest_re,
            and_(
                RealEstateProperty.edinet_code == latest_re.c.edinet_code,
                RealEstateProperty.fiscal_year_end == latest_re.c.fye,
            ),
        )
        .group_by(RealEstateProperty.edinet_code)
        .subquery()
    )
    nav_expr = (re_totals.c.total_book_m * 1_000_000) / StockQuote.market_cap

    nav_candidates = db.execute(
        _theme_query_base(db)
        .join(re_totals, Company.edinet_code == re_totals.c.edinet_code)
        .where(
            StockQuote.market_cap.is_not(None),
            StockQuote.market_cap >= MIN_SANE_MARKET_CAP,
            nav_expr <= MAX_SANE_NAV_RATIO,
        )
        .order_by(re_totals.c.total_book_m.desc())
        .limit(limit * 20)
    ).all()
    nav_items = sorted(
        (
            i
            for i in _rows_to_items(db, nav_candidates)
            if (i.get("real_estate_nav_ratio") or 0) >= 0.3
        ),
        key=lambda i: i.get("real_estate_nav_ratio") or 0,
        reverse=True,
    )[:limit]

    # 割安成長（PER≤15 & 成長≥10%）
    growth_stmt = (
        _theme_query_base(db)
        .where(
            Financial.revenue_growth >= 0.1,
            _valuation_expr() <= 15,
            _valuation_expr().is_not(None),
        )
        .order_by(Financial.revenue_growth.desc())
        .limit(limit)
    )
    growth_rows = db.execute(growth_stmt).all()

    # 営業CF黒字（CF降順）
    cf_stmt = (
        _theme_query_base(db)
        .where(Financial.operating_cf.is_not(None), Financial.operating_cf > 0)
        .order_by(Financial.operating_cf.desc())
        .limit(limit)
    )
    cf_rows = db.execute(cf_stmt).all()

    # 高品質（ROE≥10% & 利益率≥8% & CF黒字）
    quality_stmt = (
        _theme_query_base(db)
        .where(
            Financial.roe >= 0.1,
            Financial.operating_margin >= 0.08,
            Financial.operating_cf.is_not(None),
            Financial.operating_cf > 0,
        )
        .order_by(Financial.roe.desc())
        .limit(limit)
    )
    quality_rows = db.execute(quality_stmt).all()

    return {
        "disclaimer": DISCLAIMER,
        "general_risks": list(GENERAL_RISKS),
        "themes": [
            {
                "id": "re_nav",
                "title": "不動産NAV割安",
                "description": "不動産帳簿価額が時価総額の30%以上（簡易NAV割安）",
                "preset": "re-nav",
                "count": len(nav_items),
                "items": nav_items,
            },
            {
                "id": "value_growth",
                "title": "割安成長株",
                "description": "PER15倍以下かつ売上成長10%以上",
                "preset": "value-growth",
                "count": len(growth_rows),
                "items": _rows_to_items(db, growth_rows),
            },
            {
                "id": "cf_positive",
                "title": "営業CF黒字",
                "description": "営業キャッシュフローがプラスの銘柄",
                "preset": "cf-positive",
                "count": len(cf_rows),
                "items": _rows_to_items(db, cf_rows),
            },
            {
                "id": "quality",
                "title": "高品質株",
                "description": "ROE10%以上・営業利益率8%以上・営業CF黒字",
                "preset": "quality",
                "count": len(quality_rows),
                "items": _rows_to_items(db, quality_rows),
            },
        ]
    }
