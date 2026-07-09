from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import Company, Filing, QuarterlyFinancial
from app.edinet.client import EdinetClient
from app.edinet.quarterly_parser import (
    CURRENT_PARSE_VERSION,
    calc_quarterly_qoq,
    calc_quarterly_yoy,
    derive_single_quarter_from_cumulative,
    parse_quarter_number,
    parse_quarterly_csv_zip,
)

QUARTERLY_DOC_TYPES = ("140", "150")


def _pending_quarterly_filings(
    db: Session,
    *,
    limit: int,
    only_missing: bool,
    listing: str | None,
    reparse_stale: bool = False,
) -> list[Filing]:
    if reparse_stale:
        stmt = (
            select(Filing)
            .join(QuarterlyFinancial, Filing.doc_id == QuarterlyFinancial.doc_id)
            .where(
                Filing.doc_type_code.in_(QUARTERLY_DOC_TYPES),
                Filing.has_csv.is_(True),
                or_(
                    QuarterlyFinancial.parse_status == "empty",
                    QuarterlyFinancial.parse_version < CURRENT_PARSE_VERSION,
                ),
            )
            .order_by(Filing.period_end.desc())
            .limit(limit)
        )
    else:
        stmt = (
            select(Filing)
            .where(
                Filing.doc_type_code.in_(QUARTERLY_DOC_TYPES),
                Filing.has_csv.is_(True),
            )
            .order_by(Filing.period_end.desc())
            .limit(limit)
        )
        if only_missing:
            stmt = stmt.outerjoin(
                QuarterlyFinancial, Filing.doc_id == QuarterlyFinancial.doc_id
            ).where(QuarterlyFinancial.id.is_(None))
    if listing:
        stmt = stmt.join(Company, Filing.edinet_code == Company.edinet_code).where(
            Company.listing_status == listing
        )
    return db.scalars(stmt).all()


def _download_and_parse(client: EdinetClient, doc_id: str) -> tuple[str, dict]:
    content = client.download_document(doc_id, doc_type="5")
    return doc_id, parse_quarterly_csv_zip(content)


def _mark_quarterly_empty(db: Session, filing: Filing) -> None:
    existing = db.scalar(select(QuarterlyFinancial).where(QuarterlyFinancial.doc_id == filing.doc_id))
    row = existing or QuarterlyFinancial(edinet_code=filing.edinet_code, doc_id=filing.doc_id)
    row.period_start = filing.period_start
    row.period_end = filing.period_end
    row.quarter_number = parse_quarter_number(
        filing.doc_description, filing.period_start, filing.period_end
    )
    row.parse_status = "empty"
    row.parse_version = CURRENT_PARSE_VERSION
    row.updated_at = datetime.utcnow()
    db.add(row)


def _upsert_quarterly(db: Session, filing: Filing, metrics: dict[str, float | None]) -> bool:
    if metrics.get("revenue_current_cumulative") is None and metrics.get(
        "revenue_current_quarter"
    ) is None:
        _mark_quarterly_empty(db, filing)
        return False

    yoy = calc_quarterly_yoy(metrics)
    existing = db.scalar(select(QuarterlyFinancial).where(QuarterlyFinancial.doc_id == filing.doc_id))
    row = existing or QuarterlyFinancial(edinet_code=filing.edinet_code, doc_id=filing.doc_id)

    row.period_start = filing.period_start
    row.period_end = filing.period_end
    row.quarter_number = parse_quarter_number(
        filing.doc_description, filing.period_start, filing.period_end
    )
    row.revenue_cumulative = metrics.get("revenue_current_cumulative")
    row.revenue_prior_year_cum = metrics.get("revenue_prior_year_cumulative")
    row.revenue_single = metrics.get("revenue_current_quarter")
    row.operating_income_cumulative = metrics.get("operating_income_current_cumulative")
    row.operating_income_prior_year_cum = metrics.get("operating_income_prior_year_cumulative")
    row.operating_income_single = metrics.get("operating_income_current_quarter")
    row.net_income_cumulative = metrics.get("net_income_current_cumulative")
    row.net_income_prior_year_cum = metrics.get("net_income_prior_year_cumulative")
    row.net_income_single = metrics.get("net_income_current_quarter")
    row.eps_cumulative = metrics.get("eps_current_cumulative")
    row.eps_prior_year_cum = metrics.get("eps_prior_year_cumulative")
    row.revenue_yoy = yoy.get("revenue_yoy")
    row.operating_income_yoy = yoy.get("operating_income_yoy")
    row.net_income_yoy = yoy.get("net_income_yoy")
    row.eps_yoy = yoy.get("eps_yoy")
    row.parse_status = "ok"
    row.parse_version = CURRENT_PARSE_VERSION
    row.updated_at = datetime.utcnow()
    db.add(row)
    return True


def _same_fiscal_chain(prev: QuarterlyFinancial, current: QuarterlyFinancial) -> bool:
    if not prev.period_end or not current.period_start:
        return bool(prev.period_end and current.period_end and prev.period_end < current.period_end)
    return prev.period_end < current.period_end


def update_qoq_for_edinet_codes(db: Session, edinet_codes: set[str]) -> int:
    updated = 0
    for edinet_code in edinet_codes:
        rows = db.scalars(
            select(QuarterlyFinancial)
            .where(QuarterlyFinancial.edinet_code == edinet_code)
            .order_by(QuarterlyFinancial.period_end.asc())
        ).all()
        prev_row: QuarterlyFinancial | None = None
        for row in rows:
            if prev_row and _same_fiscal_chain(prev_row, row):
                curr_single_rev = row.revenue_single
                if curr_single_rev is None:
                    curr_single_rev = derive_single_quarter_from_cumulative(
                        row.revenue_cumulative,
                        prev_row.revenue_cumulative,
                    )
                prev_single_rev = prev_row.revenue_single
                if prev_single_rev is None:
                    prev_single_rev = prev_row.revenue_cumulative

                curr_single_oi = row.operating_income_single
                if curr_single_oi is None:
                    curr_single_oi = derive_single_quarter_from_cumulative(
                        row.operating_income_cumulative,
                        prev_row.operating_income_cumulative,
                    )
                prev_single_oi = prev_row.operating_income_single
                if prev_single_oi is None:
                    prev_single_oi = prev_row.operating_income_cumulative

                curr_single_ni = row.net_income_single
                if curr_single_ni is None:
                    curr_single_ni = derive_single_quarter_from_cumulative(
                        row.net_income_cumulative,
                        prev_row.net_income_cumulative,
                    )
                prev_single_ni = prev_row.net_income_single
                if prev_single_ni is None:
                    prev_single_ni = prev_row.net_income_cumulative

                row.revenue_qoq = calc_quarterly_qoq(curr_single_rev, prev_single_rev)
                row.operating_income_qoq = calc_quarterly_qoq(curr_single_oi, prev_single_oi)
                row.net_income_qoq = calc_quarterly_qoq(curr_single_ni, prev_single_ni)
                if row.revenue_qoq is not None or row.operating_income_qoq is not None:
                    updated += 1
            prev_row = row
    return updated


def recompute_all_qoq(db: Session) -> int:
    codes = set(
        db.scalars(select(QuarterlyFinancial.edinet_code).distinct()).all()
    )
    count = update_qoq_for_edinet_codes(db, codes)
    db.commit()
    return count


def sync_quarterly_financials(
    db: Session,
    client: EdinetClient,
    *,
    limit: int = 200,
    only_missing: bool = True,
    listing: str | None = "上場",
    workers: int = 12,
    reparse_stale: bool = False,
) -> dict[str, int]:
    filings = _pending_quarterly_filings(
        db, limit=limit, only_missing=only_missing, listing=listing, reparse_stale=reparse_stale
    )
    stats = {
        "processed": 0,
        "parsed": 0,
        "skipped": 0,
        "errors": 0,
        "qoq_updated": 0,
    }
    if not filings:
        return stats

    stats["processed"] = len(filings)
    filing_by_doc = {f.doc_id: f for f in filings}
    parsed_metrics: dict[str, dict] = {}
    touched_codes: set[str] = set()

    if workers <= 1:
        for filing in filings:
            try:
                _, metrics = _download_and_parse(client, filing.doc_id)
                parsed_metrics[filing.doc_id] = metrics
            except Exception:
                stats["errors"] += 1
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_download_and_parse, client, filing.doc_id): filing.doc_id
                for filing in filings
            }
            for future in as_completed(futures):
                doc_id = futures[future]
                try:
                    _, metrics = future.result()
                    parsed_metrics[doc_id] = metrics
                except Exception:
                    stats["errors"] += 1

    for doc_id, metrics in parsed_metrics.items():
        filing = filing_by_doc[doc_id]
        try:
            if _upsert_quarterly(db, filing, metrics):
                stats["parsed"] += 1
                touched_codes.add(filing.edinet_code)
            else:
                stats["skipped"] += 1
        except Exception:
            stats["errors"] += 1

    failed_docs = set(filing_by_doc) - set(parsed_metrics)
    for doc_id in failed_docs:
        try:
            _mark_quarterly_empty(db, filing_by_doc[doc_id])
            stats["skipped"] += 1
        except Exception:
            stats["errors"] += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        stats["errors"] += len(parsed_metrics)

    if touched_codes:
        stats["qoq_updated"] = update_qoq_for_edinet_codes(db, touched_codes)
        db.commit()

    return stats
