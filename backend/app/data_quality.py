from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import Company, Financial, QuarterlyFinancial, RealEstateSync, StockQuote
from app.edinet.client import CURRENT_PARSE_VERSION as FINANCIAL_PARSE_VERSION
from app.edinet.quarterly_parser import CURRENT_PARSE_VERSION as QUARTERLY_PARSE_VERSION
from app.queries import latest_financial_subquery


def build_data_quality_stats(db: Session) -> dict:
    latest = latest_financial_subquery()
    listed_with_fin = (
        select(func.count(func.distinct(Company.edinet_code)))
        .join(latest, Company.edinet_code == latest.c.edinet_code)
        .where(Company.listing_status == "上場")
        .scalar_subquery()
    )

    listed = db.scalar(
        select(func.count()).select_from(Company).where(Company.listing_status == "上場")
    ) or 0

    missing_operating_cf = db.scalar(
        select(func.count())
        .select_from(Financial)
        .join(
            latest,
            and_(
                Financial.edinet_code == latest.c.edinet_code,
                Financial.fiscal_year_end == latest.c.fiscal_year_end,
            ),
        )
        .join(Company, Financial.edinet_code == Company.edinet_code)
        .where(Company.listing_status == "上場", Financial.operating_cf.is_(None))
    ) or 0

    financial_reparse_needed = db.scalar(
        select(func.count())
        .select_from(Financial)
        .where(Financial.parse_version < FINANCIAL_PARSE_VERSION)
    ) or 0

    quarterly_companies = db.scalar(
        select(func.count(func.distinct(QuarterlyFinancial.edinet_code)))
    ) or 0

    quarterly_empty = db.scalar(
        select(func.count())
        .select_from(QuarterlyFinancial)
        .where(QuarterlyFinancial.parse_status == "empty")
    ) or 0

    quarterly_reparse_needed = db.scalar(
        select(func.count())
        .select_from(QuarterlyFinancial)
        .where(
            (QuarterlyFinancial.parse_status == "empty")
            | (QuarterlyFinancial.parse_version < QUARTERLY_PARSE_VERSION)
        )
    ) or 0

    missing_quotes = db.scalar(
        select(func.count())
        .select_from(Company)
        .outerjoin(StockQuote, Company.edinet_code == StockQuote.edinet_code)
        .where(Company.listing_status == "上場", StockQuote.edinet_code.is_(None))
    ) or 0

    real_estate_pending = db.scalar(
        select(func.count())
        .select_from(RealEstateSync)
        .where(RealEstateSync.parse_status != "ok")
    ) or 0

    listed_with_fin_count = db.scalar(select(listed_with_fin)) or 0

    return {
        "listed_companies": listed,
        "listed_with_financials": listed_with_fin_count,
        "missing_operating_cf": missing_operating_cf,
        "financial_reparse_needed": financial_reparse_needed,
        "quarterly_companies": quarterly_companies,
        "quarterly_empty_rows": quarterly_empty,
        "quarterly_reparse_needed": quarterly_reparse_needed,
        "missing_stock_quotes": missing_quotes,
        "real_estate_pending": real_estate_pending,
        "financial_parse_version": FINANCIAL_PARSE_VERSION,
        "quarterly_parse_version": QUARTERLY_PARSE_VERSION,
    }
