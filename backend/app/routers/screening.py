from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, nullslast, select
from sqlalchemy.orm import Session

from app.db import Company, Financial, RealEstateProperty, StockQuote, get_db
from app.market.yahoo import MAX_SANE_EPS, MAX_SANE_PER, MIN_SANE_EPS, MIN_SANE_PER
from app.queries import latest_financial_subquery
from app.real_estate_nav import MAX_SANE_NAV_RATIO, MIN_SANE_MARKET_CAP, compute_nav_ratio
from app.routers.companies import (
    _companies_with_real_estate_subquery,
    _property_book_value_expr,
    _real_estate_briefs_batch,
)

router = APIRouter(prefix="/screening", tags=["screening"])


def _valuation_expr():
    return func.coalesce(StockQuote.per_edinet, StockQuote.per)


def _pbr_expr():
    return func.coalesce(StockQuote.pbr_edinet, StockQuote.pbr)


def _valuation_sanity_filters():
    return [
        Financial.eps.is_not(None),
        Financial.eps > MIN_SANE_EPS,
        Financial.eps <= MAX_SANE_EPS,
        _valuation_expr() >= MIN_SANE_PER,
        _valuation_expr() <= MAX_SANE_PER,
    ]


def _real_estate_totals_subquery():
    book_value = _property_book_value_expr()
    latest_re = (
        select(
            RealEstateProperty.edinet_code,
            func.max(RealEstateProperty.fiscal_year_end).label("fye"),
        )
        .group_by(RealEstateProperty.edinet_code)
        .subquery()
    )
    return (
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


def _serialize_row(company: Company, financial: Financial, quote: Optional[StockQuote], re_brief: dict | None = None) -> dict[str, Any]:
    row = {
        "edinet_code": company.edinet_code,
        "name": company.name,
        "sec_code": company.sec_code,
        "industry": company.industry,
        "fiscal_year_end": financial.fiscal_year_end,
        "revenue": financial.revenue,
        "operating_income": financial.operating_income,
        "net_income": financial.net_income,
        "operating_margin": financial.operating_margin,
        "roe": financial.roe,
        "roa": financial.roa,
        "equity_ratio": financial.equity_ratio,
        "revenue_growth": financial.revenue_growth,
        "eps": financial.eps,
        "bps": financial.bps,
        "operating_cf": financial.operating_cf,
        "cash_and_deposits": financial.cash_and_deposits,
        "interest_bearing_debt": financial.interest_bearing_debt,
        "dividend_per_share": financial.dividend_per_share,
        "doc_id": financial.doc_id,
        "price": quote.price if quote else None,
        "market_cap": quote.market_cap if quote else None,
        "per": quote.per if quote else None,
        "pbr": quote.pbr if quote else None,
        "per_edinet": quote.per_edinet if quote else None,
        "pbr_edinet": quote.pbr_edinet if quote else None,
        "dividend_yield": quote.dividend_yield if quote else None,
    }
    if re_brief:
        row["real_estate"] = re_brief
        nav = compute_nav_ratio(
            re_brief.get("total_book_value_m"),
            quote.market_cap if quote else None,
            total_assets=financial.total_assets,
        )
        if nav is not None:
            row["real_estate_nav_ratio"] = nav
    return row


@router.get("")
def screen_companies(
    listing: str = Query("上場"),
    industry: Optional[str] = None,
    min_revenue: Optional[float] = None,
    max_revenue: Optional[float] = None,
    min_operating_margin: Optional[float] = None,
    min_roe: Optional[float] = None,
    min_roa: Optional[float] = None,
    min_revenue_growth: Optional[float] = None,
    min_per: Optional[float] = None,
    max_per: Optional[float] = None,
    min_pbr: Optional[float] = None,
    max_pbr: Optional[float] = None,
    has_real_estate: Optional[bool] = None,
    has_operating_cf: Optional[bool] = None,
    min_real_estate_nav_ratio: Optional[float] = None,
    sort_by: str = Query(
        "revenue",
        pattern="^(revenue|operating_margin|roe|roa|revenue_growth|net_income|per|pbr|market_cap|operating_cf|real_estate_nav|real_estate_book)$",
    ),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    latest = latest_financial_subquery()
    stmt = (
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
        .where(Company.listing_status == listing)
    )

    filters = []
    if industry:
        filters.append(Company.industry.like(f"%{industry}%"))
    if min_revenue is not None:
        filters.append(Financial.revenue >= min_revenue)
    if max_revenue is not None:
        filters.append(Financial.revenue <= max_revenue)
    if min_operating_margin is not None:
        filters.append(Financial.operating_margin >= min_operating_margin)
    if min_roe is not None:
        filters.append(Financial.roe >= min_roe)
    if min_roa is not None:
        filters.append(Financial.roa >= min_roa)
    if min_revenue_growth is not None:
        filters.append(Financial.revenue_growth >= min_revenue_growth)
    uses_per_filter = min_per is not None or max_per is not None or sort_by in ("per", "pbr")
    if uses_per_filter:
        filters.extend(_valuation_sanity_filters())
    if min_per is not None:
        filters.append(_valuation_expr() >= min_per)
    if max_per is not None:
        filters.append(_valuation_expr() <= max_per)
    if min_pbr is not None:
        filters.append(_pbr_expr() >= min_pbr)
    if max_pbr is not None:
        filters.append(_pbr_expr() <= max_pbr)
    if has_real_estate:
        filters.append(Company.edinet_code.in_(_companies_with_real_estate_subquery()))
    if has_operating_cf:
        filters.append(Financial.operating_cf.is_not(None))
        filters.append(Financial.operating_cf > 0)

    if sort_by == "market_cap":
        filters.append(StockQuote.market_cap.is_not(None))
        filters.append(StockQuote.market_cap > 0)

    re_totals = None
    nav_expr = None
    if min_real_estate_nav_ratio is not None or sort_by in ("real_estate_nav", "real_estate_book"):
        re_totals = _real_estate_totals_subquery()
        stmt = stmt.join(re_totals, Company.edinet_code == re_totals.c.edinet_code)
        if sort_by == "real_estate_nav" or min_real_estate_nav_ratio is not None:
            nav_expr = (re_totals.c.total_book_m * 1_000_000) / StockQuote.market_cap
        if min_real_estate_nav_ratio is not None:
            filters.append(StockQuote.market_cap.is_not(None))
            filters.append(StockQuote.market_cap >= MIN_SANE_MARKET_CAP)
            filters.append(nav_expr >= min_real_estate_nav_ratio)
            filters.append(nav_expr <= MAX_SANE_NAV_RATIO)
        elif sort_by == "real_estate_nav":
            filters.append(StockQuote.market_cap.is_not(None))
            filters.append(StockQuote.market_cap >= MIN_SANE_MARKET_CAP)
            filters.append(nav_expr <= MAX_SANE_NAV_RATIO)

    if filters:
        stmt = stmt.where(and_(*filters))

    sort_map = {
        "revenue": Financial.revenue,
        "operating_margin": Financial.operating_margin,
        "roe": Financial.roe,
        "roa": Financial.roa,
        "revenue_growth": Financial.revenue_growth,
        "net_income": Financial.net_income,
        "operating_cf": Financial.operating_cf,
        "per": _valuation_expr(),
        "pbr": _pbr_expr(),
        "market_cap": StockQuote.market_cap,
        "real_estate_nav": nav_expr,
        "real_estate_book": re_totals.c.total_book_m if re_totals is not None else None,
    }
    sort_column = sort_map[sort_by]
    if sort_by in ("real_estate_nav", "real_estate_book") and sort_column is None:
        sort_column = Financial.revenue
    order_expr = sort_column.desc() if order == "desc" else sort_column.asc()
    stmt = stmt.order_by(nullslast(order_expr) if order == "desc" else order_expr)

    fetch_limit = limit
    fetch_offset = offset
    if sort_by == "real_estate_nav":
        fetch_limit = min(limit * 25, 500)
        fetch_offset = 0

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.offset(fetch_offset).limit(fetch_limit)).all()

    codes = [company.edinet_code for company, _, _ in rows]
    re_briefs = _real_estate_briefs_batch(db, codes)

    items = []
    for company, financial, quote in rows:
        brief = re_briefs.get(company.edinet_code)
        row = _serialize_row(company, financial, quote, brief)
        items.append(row)

    uses_computed_nav = min_real_estate_nav_ratio is not None or sort_by == "real_estate_nav"
    if uses_computed_nav:
        if min_real_estate_nav_ratio is not None:
            items = [
                i
                for i in items
                if (i.get("real_estate_nav_ratio") or 0) >= min_real_estate_nav_ratio
            ]
        if sort_by == "real_estate_nav":
            items.sort(
                key=lambda i: i.get("real_estate_nav_ratio") or 0,
                reverse=order == "desc",
            )
            items = items[offset : offset + limit]

    return {
        "total": total or 0,
        "items": items,
    }


@router.get("/industries")
def list_industries(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Company.industry)
        .where(Company.listing_status == "上場", Company.industry.is_not(None))
        .distinct()
        .order_by(Company.industry)
    ).all()
    return {"items": rows}
