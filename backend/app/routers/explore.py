"""既存データから横断探索用の集計 API。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, nullslast, select
from sqlalchemy.orm import Session

from app.db import Company, Financial, QuarterlyFinancial, RealEstateProperty, StockQuote, get_db
from app.queries import latest_financial_subquery
from app.routers.companies import _property_book_value_expr, _real_estate_briefs_batch
from app.routers.screening import _serialize_row

router = APIRouter(prefix="/explore", tags=["explore"])


def _latest_quarterly_subquery():
    return (
        select(
            QuarterlyFinancial.edinet_code,
            func.max(QuarterlyFinancial.period_end).label("period_end"),
        )
        .where(QuarterlyFinancial.parse_status == "ok")
        .group_by(QuarterlyFinancial.edinet_code)
        .subquery()
    )


@router.get("/quarterly-momentum")
def quarterly_momentum(
    min_revenue_yoy: Optional[float] = Query(0.1, description="売上YoY下限（小数）"),
    sort_by: str = Query("revenue_yoy", pattern="^(revenue_yoy|operating_income_yoy|revenue_qoq)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    industry: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """直近四半期の YoY/QoQ で横断ランキング。"""
    latest_q = _latest_quarterly_subquery()
    latest_fin = latest_financial_subquery()
    stmt = (
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
        .where(Company.listing_status == "上場")
    )
    if industry:
        stmt = stmt.where(Company.industry.like(f"%{industry}%"))
    if min_revenue_yoy is not None and sort_by == "revenue_yoy":
        stmt = stmt.where(QuarterlyFinancial.revenue_yoy.is_not(None))
        stmt = stmt.where(QuarterlyFinancial.revenue_yoy >= min_revenue_yoy)
    elif sort_by == "operating_income_yoy":
        stmt = stmt.where(QuarterlyFinancial.operating_income_yoy.is_not(None))
    elif sort_by == "revenue_qoq":
        stmt = stmt.where(QuarterlyFinancial.revenue_qoq.is_not(None))

    sort_map = {
        "revenue_yoy": QuarterlyFinancial.revenue_yoy,
        "operating_income_yoy": QuarterlyFinancial.operating_income_yoy,
        "revenue_qoq": QuarterlyFinancial.revenue_qoq,
    }
    col = sort_map[sort_by]
    order_expr = col.desc() if order == "desc" else col.asc()
    stmt = stmt.order_by(nullslast(order_expr) if order == "desc" else order_expr)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.offset(offset).limit(limit)).all()
    codes = [c.edinet_code for c, _, _, _ in rows]
    re_briefs = _real_estate_briefs_batch(db, codes)

    items: list[dict[str, Any]] = []
    for company, qtr, financial, quote in rows:
        base = (
            _serialize_row(company, financial, quote, re_briefs.get(company.edinet_code))
            if financial is not None
            else {
                "edinet_code": company.edinet_code,
                "name": company.name,
                "sec_code": company.sec_code,
                "industry": company.industry,
            }
        )
        base.update(
            {
                "period_end": qtr.period_end,
                "quarter_number": qtr.quarter_number,
                "revenue_yoy": qtr.revenue_yoy,
                "operating_income_yoy": qtr.operating_income_yoy,
                "revenue_qoq": qtr.revenue_qoq,
                "revenue_single": qtr.revenue_single,
                "revenue_cumulative": qtr.revenue_cumulative,
            }
        )
        items.append(base)

    return {"total": total, "count": len(items), "offset": offset, "items": items}


@router.get("/prefectures")
def real_estate_by_prefecture(
    limit: int = Query(20, ge=1, le=50),
    top_companies: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """都道府県別の不動産帳簿価額ランキングと代表企業。"""
    book = _property_book_value_expr()
    latest_re = (
        select(
            RealEstateProperty.edinet_code,
            func.max(RealEstateProperty.fiscal_year_end).label("fye"),
        )
        .group_by(RealEstateProperty.edinet_code)
        .subquery()
    )
    company_totals = (
        select(
            RealEstateProperty.edinet_code,
            RealEstateProperty.prefecture,
            func.sum(book).label("book_m"),
            func.count().label("prop_count"),
        )
        .join(
            latest_re,
            and_(
                RealEstateProperty.edinet_code == latest_re.c.edinet_code,
                RealEstateProperty.fiscal_year_end == latest_re.c.fye,
            ),
        )
        .join(Company, RealEstateProperty.edinet_code == Company.edinet_code)
        .where(
            Company.listing_status == "上場",
            RealEstateProperty.prefecture.is_not(None),
            RealEstateProperty.prefecture != "",
        )
        .group_by(RealEstateProperty.edinet_code, RealEstateProperty.prefecture)
        .subquery()
    )

    pref_rows = db.execute(
        select(
            company_totals.c.prefecture,
            func.sum(company_totals.c.book_m).label("total_book_m"),
            func.sum(company_totals.c.prop_count).label("property_count"),
            func.count(func.distinct(company_totals.c.edinet_code)).label("company_count"),
        )
        .group_by(company_totals.c.prefecture)
        .order_by(func.sum(company_totals.c.book_m).desc())
        .limit(limit)
    ).all()

    items: list[dict[str, Any]] = []
    for pref, total_book_m, property_count, company_count in pref_rows:
        top = db.execute(
            select(
                company_totals.c.edinet_code,
                Company.name,
                Company.sec_code,
                company_totals.c.book_m,
                company_totals.c.prop_count,
            )
            .join(Company, company_totals.c.edinet_code == Company.edinet_code)
            .where(company_totals.c.prefecture == pref)
            .order_by(company_totals.c.book_m.desc())
            .limit(top_companies)
        ).all()
        items.append(
            {
                "prefecture": pref,
                "total_book_value_m": float(total_book_m or 0),
                "property_count": int(property_count or 0),
                "company_count": int(company_count or 0),
                "top_companies": [
                    {
                        "edinet_code": code,
                        "name": name,
                        "sec_code": sec,
                        "total_book_value_m": float(book_m or 0),
                        "property_count": int(pc or 0),
                    }
                    for code, name, sec, book_m, pc in top
                ],
            }
        )

    return {"count": len(items), "items": items}
