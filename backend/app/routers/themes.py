from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import Company, Financial, QuarterlyFinancial, RealEstateProperty, StockQuote, get_db
from app.company_verdict import DISCLAIMER, GENERAL_RISKS
from app.real_estate_nav import MAX_SANE_NAV_RATIO, MIN_SANE_MARKET_CAP
from app.routers.companies import _property_book_value_expr, _real_estate_briefs_batch
from app.queries import latest_financial_subquery
from app.routers.explore import _latest_quarterly_subquery
from app.routers.screening import _net_cash_expr, _serialize_row, _valuation_expr

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

    net_cash_stmt = (
        _theme_query_base(db)
        .where(Financial.cash_and_deposits.is_not(None), _net_cash_expr() > 0)
        .order_by(_net_cash_expr().desc())
        .limit(limit)
    )
    net_cash_rows = db.execute(net_cash_stmt).all()

    dividend_stmt = (
        _theme_query_base(db)
        .where(
            StockQuote.dividend_yield.is_not(None),
            StockQuote.dividend_yield >= 0.03,
            Financial.operating_cf.is_not(None),
            Financial.operating_cf > 0,
        )
        .order_by(StockQuote.dividend_yield.desc())
        .limit(limit)
    )
    dividend_rows = db.execute(dividend_stmt).all()

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

    latest_q = _latest_quarterly_subquery()
    latest_fin = latest_financial_subquery()
    q_momentum_stmt = (
        select(Company, QuarterlyFinancial, Financial, StockQuote)
        .join(latest_q, Company.edinet_code == latest_q.c.edinet_code)
        .join(
            QuarterlyFinancial,
            and_(
                QuarterlyFinancial.edinet_code == latest_q.c.edinet_code,
                QuarterlyFinancial.period_end == latest_q.c.period_end,
            ),
        )
        .outerjoin(latest_fin, Company.edinet_code == latest_fin.c.edinet_code)
        .outerjoin(
            Financial,
            and_(
                Financial.edinet_code == latest_fin.c.edinet_code,
                Financial.fiscal_year_end == latest_fin.c.fiscal_year_end,
            ),
        )
        .outerjoin(StockQuote, Company.edinet_code == StockQuote.edinet_code)
        .where(
            Company.listing_status == "上場",
            QuarterlyFinancial.revenue_yoy.is_not(None),
            QuarterlyFinancial.revenue_yoy >= 0.1,
        )
        .order_by(QuarterlyFinancial.revenue_yoy.desc())
        .limit(limit)
    )
    q_rows = db.execute(q_momentum_stmt).all()
    q_codes = [c.edinet_code for c, _, _, _ in q_rows]
    q_briefs = _real_estate_briefs_batch(db, q_codes)
    q_items = []
    for company, qtr, financial, quote in q_rows:
        if financial is None:
            continue
        item = _serialize_row(company, financial, quote, q_briefs.get(company.edinet_code))
        item["revenue_yoy"] = qtr.revenue_yoy
        item["period_end"] = qtr.period_end
        q_items.append(item)

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
                "id": "net_cash",
                "title": "ネットキャッシュ",
                "description": "現金預金が有利子負債を上回る企業（ネットキャッシュ降順）",
                "preset": "net-cash",
                "count": len(net_cash_rows),
                "items": _rows_to_items(db, net_cash_rows),
            },
            {
                "id": "high_dividend",
                "title": "高配当＋CF黒字",
                "description": "配当利回り3%以上かつ営業CF黒字",
                "preset": "high-dividend",
                "count": len(dividend_rows),
                "items": _rows_to_items(db, dividend_rows),
            },
            {
                "id": "q_momentum",
                "title": "四半期モメンタム",
                "description": "直近四半期売上YoY 10%以上",
                "preset": "q-momentum",
                "count": len(q_items),
                "items": q_items,
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
                "id": "quality",
                "title": "高品質株",
                "description": "ROE10%以上・営業利益率8%以上・営業CF黒字",
                "preset": "quality",
                "count": len(quality_rows),
                "items": _rows_to_items(db, quality_rows),
            },
        ]
    }
