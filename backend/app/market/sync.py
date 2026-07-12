from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db import Company, CompanyProfile, Financial, StockQuote
from app.queries import latest_financial_subquery
from app.market.yahoo import (
    calc_market_cap,
    calc_valuation_from_financials,
    estimate_shares_outstanding,
    fetch_quote,
    fetch_quote_fast,
    sec_code_to_ticker,
)


def _derive_quote_fields(
    quote_data: dict,
    financial: Financial | None,
    profile: CompanyProfile | None,
) -> dict[str, float | None]:
    shares = estimate_shares_outstanding(
        eps=financial.eps if financial else None,
        bps=financial.bps if financial else None,
        net_income=financial.net_income if financial else None,
        net_assets=financial.net_assets if financial else None,
        issued_shares_text=profile.issued_shares if profile else None,
    )
    price = quote_data.get("price")
    market_cap = calc_market_cap(
        price,
        shares,
        yahoo_market_cap=quote_data.get("market_cap"),
    )
    valuation = calc_valuation_from_financials(
        price,
        financial.eps if financial else None,
        financial.bps if financial else None,
        financial.net_assets if financial else None,
        shares_outstanding=shares,
    )
    # 配当利回りは Yahoo の高速取得（chart API）では取れないため、
    # EDINET の1株配当と株価から自前で算出する（無料・自己収集）。
    dps = financial.dividend_per_share if financial else None
    dividend_yield_edinet = None
    if dps and price and price > 0 and 0 < dps < price:
        dividend_yield_edinet = dps / price
    return {
        "market_cap": market_cap,
        "per_edinet": valuation.get("per_edinet"),
        "pbr_edinet": valuation.get("pbr_edinet"),
        "dividend_yield_edinet": dividend_yield_edinet,
    }


def sync_stock_prices(
    db: Session,
    *,
    listing: str = "上場",
    limit: int = 500,
    sleep_seconds: float = 0.2,
    only_missing: bool = False,
    workers: int = 1,
    fast: bool = False,
) -> dict[str, int]:
    stmt = select(Company).where(
        Company.listing_status == listing,
        Company.sec_code.is_not(None),
    )
    if only_missing:
        stmt = stmt.outerjoin(StockQuote, Company.edinet_code == StockQuote.edinet_code).where(
            StockQuote.edinet_code.is_(None)
        )
    companies = db.scalars(stmt.order_by(Company.edinet_code).limit(limit)).all()

    latest = latest_financial_subquery()
    financial_map: dict[str, Financial] = {}
    profile_map: dict[str, CompanyProfile] = {}
    if companies:
        edinet_codes = [c.edinet_code for c in companies]
        rows = db.execute(
            select(Financial)
            .join(
                latest,
                and_(
                    Financial.edinet_code == latest.c.edinet_code,
                    Financial.fiscal_year_end == latest.c.fiscal_year_end,
                ),
            )
            .where(Financial.edinet_code.in_(edinet_codes))
        ).scalars().all()
        financial_map = {f.edinet_code: f for f in rows}
        profiles = db.scalars(
            select(CompanyProfile).where(CompanyProfile.edinet_code.in_(edinet_codes))
        ).all()
        profile_map = {p.edinet_code: p for p in profiles}

    stats = {"processed": 0, "updated": 0, "skipped": 0, "errors": 0}
    jobs: list[tuple[Company, str]] = []

    for company in companies:
        stats["processed"] += 1
        ticker = sec_code_to_ticker(company.sec_code)
        if not ticker:
            stats["skipped"] += 1
            continue
        jobs.append((company, ticker))

    use_parallel = fast or workers > 1
    if use_parallel:
        _sync_parallel(
            db,
            jobs,
            financial_map,
            profile_map,
            stats,
            workers=max(workers, 12),
        )
    else:
        _sync_sequential(
            db,
            jobs,
            financial_map,
            profile_map,
            stats,
            sleep_seconds=sleep_seconds,
        )

    db.commit()
    return stats


def backfill_quote_valuations(
    db: Session,
    *,
    listing: str = "上場",
) -> dict[str, int]:
    latest = latest_financial_subquery()
    rows = db.execute(
        select(StockQuote, Financial, CompanyProfile)
        .join(Company, StockQuote.edinet_code == Company.edinet_code)
        .join(latest, StockQuote.edinet_code == latest.c.edinet_code)
        .outerjoin(
            Financial,
            and_(
                Financial.edinet_code == StockQuote.edinet_code,
                Financial.fiscal_year_end == latest.c.fiscal_year_end,
            ),
        )
        .outerjoin(CompanyProfile, CompanyProfile.edinet_code == StockQuote.edinet_code)
        .where(Company.listing_status == listing, StockQuote.price.is_not(None))
    ).all()

    stats = {"processed": 0, "updated": 0, "market_cap_filled": 0}
    for quote, financial, profile in rows:
        stats["processed"] += 1
        derived = _derive_quote_fields(
            {"price": quote.price, "market_cap": quote.market_cap},
            financial,
            profile,
        )
        changed = False
        if derived["market_cap"] and quote.market_cap != derived["market_cap"]:
            quote.market_cap = derived["market_cap"]
            stats["market_cap_filled"] += 1
            changed = True
        if derived["per_edinet"] != quote.per_edinet:
            quote.per_edinet = derived["per_edinet"]
            changed = True
        if derived["pbr_edinet"] != quote.pbr_edinet:
            quote.pbr_edinet = derived["pbr_edinet"]
            changed = True
        if quote.dividend_yield is None and derived.get("dividend_yield_edinet") is not None:
            quote.dividend_yield = derived["dividend_yield_edinet"]
            changed = True
        if changed:
            quote.updated_at = datetime.utcnow()
            db.add(quote)
            stats["updated"] += 1

    db.commit()
    return stats


def _sync_sequential(
    db: Session,
    jobs: list[tuple[Company, str]],
    financial_map: dict[str, Financial],
    profile_map: dict[str, CompanyProfile],
    stats: dict[str, int],
    *,
    sleep_seconds: float,
) -> None:
    for company, ticker in jobs:
        try:
            quote_data = fetch_quote(ticker, sleep_seconds=sleep_seconds)
            _save_quote(
                db,
                company,
                ticker,
                quote_data,
                financial_map.get(company.edinet_code),
                profile_map.get(company.edinet_code),
            )
            stats["updated"] += 1
        except Exception:
            stats["errors"] += 1


def _sync_parallel(
    db: Session,
    jobs: list[tuple[Company, str]],
    financial_map: dict[str, Financial],
    profile_map: dict[str, CompanyProfile],
    stats: dict[str, int],
    *,
    workers: int,
) -> None:
    company_by_ticker = {ticker: company for company, ticker in jobs}
    fetched: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_quote_fast, ticker): ticker for _, ticker in jobs}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                fetched[ticker] = future.result()
            except Exception:
                stats["errors"] += 1

    for ticker, quote_data in fetched.items():
        company = company_by_ticker[ticker]
        _save_quote(
            db,
            company,
            ticker,
            quote_data,
            financial_map.get(company.edinet_code),
            profile_map.get(company.edinet_code),
        )
        stats["updated"] += 1


def _save_quote(
    db: Session,
    company: Company,
    ticker: str,
    quote_data: dict,
    financial: Financial | None,
    profile: CompanyProfile | None = None,
) -> None:
    derived = _derive_quote_fields(quote_data, financial, profile)
    quote = db.get(StockQuote, company.edinet_code) or StockQuote(
        edinet_code=company.edinet_code
    )
    quote.ticker = ticker
    quote.price = quote_data.get("price")
    quote.market_cap = derived["market_cap"]
    quote.per = quote_data.get("per")
    quote.pbr = quote_data.get("pbr")
    quote.per_edinet = derived["per_edinet"]
    quote.pbr_edinet = derived["pbr_edinet"]
    quote.dividend_yield = quote_data.get("dividend_yield") or derived.get("dividend_yield_edinet")
    quote.fifty_two_week_high = quote_data.get("fifty_two_week_high")
    quote.fifty_two_week_low = quote_data.get("fifty_two_week_low")
    quote.updated_at = datetime.utcnow()
    db.add(quote)
