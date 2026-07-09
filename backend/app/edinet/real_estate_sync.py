from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.db import Company, Filing, RealEstateProperty, RealEstateSync
from app.edinet.client import EdinetClient
from app.edinet.xbrl_facilities import ParseResult, parse_facilities_xbrl_zip, property_to_dict

YUHO_DOC_TYPES = ("120", "130")


def _latest_yuho_subquery(listing: str | None = None):
    stmt = (
        select(
            Filing.edinet_code.label("edinet_code"),
            func.max(Filing.period_end).label("period_end"),
        )
        .where(
            Filing.doc_type_code.in_(YUHO_DOC_TYPES),
            Filing.has_xbrl.is_(True),
        )
        .group_by(Filing.edinet_code)
    )
    if listing:
        stmt = stmt.join(Company, Filing.edinet_code == Company.edinet_code).where(
            Company.listing_status == listing
        )
    return stmt.subquery()


def _pending_filings(
    db: Session,
    *,
    limit: int,
    only_missing: bool,
    listing: str | None,
) -> list[Filing]:
    latest = _latest_yuho_subquery(listing)
    stmt = (
        select(Filing)
        .join(
            latest,
            and_(
                Filing.edinet_code == latest.c.edinet_code,
                Filing.period_end == latest.c.period_end,
            ),
        )
        .where(
            Filing.doc_type_code.in_(YUHO_DOC_TYPES),
            Filing.has_xbrl.is_(True),
        )
        .order_by(Filing.edinet_code)
        .limit(limit)
    )
    if only_missing:
        stmt = stmt.outerjoin(RealEstateSync, Filing.doc_id == RealEstateSync.doc_id).where(
            RealEstateSync.doc_id.is_(None)
        )
    if listing:
        stmt = stmt.join(Company, Filing.edinet_code == Company.edinet_code).where(
            Company.listing_status == listing
        )
    return db.scalars(stmt).all()


def _download_and_parse(client: EdinetClient, doc_id: str) -> tuple[str, ParseResult]:
    content = client.download_document(doc_id, doc_type="1")
    return doc_id, parse_facilities_xbrl_zip(content)


def _save_parse_result(
    db: Session,
    filing: Filing,
    result: ParseResult,
) -> int:
    db.execute(delete(RealEstateProperty).where(RealEstateProperty.doc_id == filing.doc_id))

    count = 0
    for prop in result.properties:
        data = property_to_dict(prop)
        db.add(
            RealEstateProperty(
                edinet_code=filing.edinet_code,
                doc_id=filing.doc_id,
                fiscal_year_end=filing.period_end,
                **data,
            )
        )
        count += 1

    sync = db.get(RealEstateSync, filing.doc_id) or RealEstateSync(doc_id=filing.doc_id)
    sync.edinet_code = filing.edinet_code
    sync.fiscal_year_end = filing.period_end
    sync.property_count = count
    sync.parse_status = result.parse_status
    sync.error_message = result.error
    sync.synced_at = datetime.utcnow()
    db.add(sync)
    return count


def sync_real_estate(
    db: Session,
    client: EdinetClient,
    *,
    limit: int = 100,
    only_missing: bool = True,
    listing: str = "上場",
    workers: int = 8,
) -> dict[str, int]:
    filings = _pending_filings(
        db,
        limit=limit,
        only_missing=only_missing,
        listing=listing or None,
    )
    stats = {
        "processed": len(filings),
        "properties": 0,
        "with_data": 0,
        "empty": 0,
        "errors": 0,
    }
    if not filings:
        return stats

    filing_by_doc = {f.doc_id: f for f in filings}
    parsed: dict[str, ParseResult] = {}

    if workers <= 1:
        for filing in filings:
            try:
                _, result = _download_and_parse(client, filing.doc_id)
                parsed[filing.doc_id] = result
            except Exception as exc:
                parsed[filing.doc_id] = ParseResult(parse_status="error", error=str(exc))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_download_and_parse, client, filing.doc_id): filing.doc_id
                for filing in filings
            }
            for future in as_completed(futures):
                doc_id = futures[future]
                try:
                    _, result = future.result()
                    parsed[doc_id] = result
                except Exception as exc:
                    parsed[doc_id] = ParseResult(parse_status="error", error=str(exc))

    for doc_id, result in parsed.items():
        filing = filing_by_doc[doc_id]
        try:
            count = _save_parse_result(db, filing, result)
            stats["properties"] += count
            if result.parse_status == "ok":
                stats["with_data"] += 1
            elif result.parse_status == "empty":
                stats["empty"] += 1
            else:
                stats["errors"] += 1
            db.commit()
        except Exception:
            db.rollback()
            stats["errors"] += 1

    return stats
