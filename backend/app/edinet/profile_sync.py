from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import Company, CompanyProfile, Filing, Financial
from app.db_maintenance import checkpoint_after_write
from app.edinet.client import EdinetClient
from app.edinet.xbrl_profile import parse_profile_xbrl_zip

YUHO_DOC_TYPES = ("120", "130")


def _filing_period_key():
    return func.coalesce(
        func.nullif(Filing.period_end, ""),
        func.substr(Filing.submit_date_time, 1, 10),
        Filing.doc_id,
    )


def _latest_yuho_subquery(listing: str | None = None):
    period_key = _filing_period_key().label("period_key")
    stmt = (
        select(
            Filing.edinet_code.label("edinet_code"),
            func.max(period_key).label("period_key"),
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


def _pending_profile_filings(
    db: Session,
    *,
    limit: int,
    only_missing: bool,
    listing: str | None,
    priority_revenue: bool = True,
) -> list[Filing]:
    latest = _latest_yuho_subquery(listing)
    stmt = (
        select(Filing)
        .join(
            latest,
            and_(
                Filing.edinet_code == latest.c.edinet_code,
                _filing_period_key() == latest.c.period_key,
            ),
        )
        .where(
            Filing.doc_type_code.in_(YUHO_DOC_TYPES),
            Filing.has_xbrl.is_(True),
        )
    )
    if only_missing:
        stmt = stmt.outerjoin(
            CompanyProfile, Filing.edinet_code == CompanyProfile.edinet_code
        ).where(
            or_(
                CompanyProfile.edinet_code.is_(None),
                and_(
                    CompanyProfile.parse_status != "ok",
                    CompanyProfile.parse_status != "no_xbrl",
                    CompanyProfile.parse_status != "no_content",
                ),
            )
        )
    if listing:
        stmt = stmt.join(Company, Filing.edinet_code == Company.edinet_code).where(
            Company.listing_status == listing
        )

    if priority_revenue:
        fin_sub = (
            select(
                Financial.edinet_code.label("edinet_code"),
                func.max(Financial.fiscal_year_end).label("fye"),
            )
            .group_by(Financial.edinet_code)
            .subquery()
        )
        stmt = (
            stmt.outerjoin(fin_sub, Filing.edinet_code == fin_sub.c.edinet_code)
            .outerjoin(
                Financial,
                and_(
                    Financial.edinet_code == fin_sub.c.edinet_code,
                    Financial.fiscal_year_end == fin_sub.c.fye,
                ),
            )
            .order_by(Financial.revenue.desc().nullslast(), Filing.edinet_code)
        )
    else:
        stmt = stmt.order_by(Filing.edinet_code)

    return db.scalars(stmt.limit(limit)).all()


def _download_and_parse(client: EdinetClient, doc_id: str):
    content = client.download_document(doc_id, doc_type="1")
    return doc_id, parse_profile_xbrl_zip(content)


def _save_profile(db: Session, filing: Filing, parsed) -> str:
    profile = db.get(CompanyProfile, filing.edinet_code)
    if parsed.parse_status != "ok":
        if profile and profile.parse_status == "ok":
            return "cached"
        if not profile:
            profile = CompanyProfile(edinet_code=filing.edinet_code)
        profile.doc_id = filing.doc_id
        profile.fiscal_year_end = filing.period_end
        profile.parse_status = (
            "no_content"
            if parsed.parse_status == "empty"
            else (parsed.parse_status or "error")
        )
        profile.synced_at = datetime.utcnow()
        db.add(profile)
        return "error"

    profile = profile or CompanyProfile(edinet_code=filing.edinet_code)
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
    return "ok"


def sync_profiles(
    db: Session,
    client: EdinetClient,
    *,
    limit: int = 100,
    only_missing: bool = True,
    listing: str = "上場",
    workers: int = 4,
    priority_revenue: bool = True,
) -> dict[str, int]:
    filings = _pending_profile_filings(
        db,
        limit=limit,
        only_missing=only_missing,
        listing=listing or None,
        priority_revenue=priority_revenue,
    )
    stats = {"processed": len(filings), "ok": 0, "cached": 0, "error": 0}
    if not filings:
        return stats

    filing_by_doc = {f.doc_id: f for f in filings}
    parsed_results: dict[str, object] = {}

    if workers <= 1:
        for filing in filings:
            try:
                _, parsed = _download_and_parse(client, filing.doc_id)
                parsed_results[filing.doc_id] = parsed
            except Exception as exc:
                parsed_results[filing.doc_id] = type("E", (), {"parse_status": "error", "error": str(exc)})()
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_download_and_parse, client, filing.doc_id): filing.doc_id
                for filing in filings
            }
            for future in as_completed(futures):
                doc_id = futures[future]
                try:
                    _, parsed = future.result()
                    parsed_results[doc_id] = parsed
                except Exception as exc:
                    parsed_results[doc_id] = type("E", (), {"parse_status": "error", "error": str(exc)})()

    for doc_id, parsed in parsed_results.items():
        filing = filing_by_doc[doc_id]
        try:
            status = _save_profile(db, filing, parsed)
            stats[status] = stats.get(status, 0) + 1
            db.commit()
        except Exception:
            db.rollback()
            stats["error"] += 1

    checkpoint_after_write(db)
    return stats


def seed_no_xbrl_profiles(db: Session, listing: str = "上場") -> dict[str, int]:
    """XBRLのない上場企業に no_xbrl 行を付与し、再試行ループを止める。"""
    latest = _latest_yuho_subquery(listing or None)
    stmt = (
        select(Company.edinet_code)
        .outerjoin(CompanyProfile, Company.edinet_code == CompanyProfile.edinet_code)
        .outerjoin(
            latest,
            Company.edinet_code == latest.c.edinet_code,
        )
        .where(
            Company.listing_status == listing,
            CompanyProfile.edinet_code.is_(None),
            latest.c.edinet_code.is_(None),
        )
    )
    codes = db.scalars(stmt).all()
    created = 0
    now = datetime.utcnow()
    for code in codes:
        db.add(
            CompanyProfile(
                edinet_code=code,
                parse_status="no_xbrl",
                synced_at=now,
            )
        )
        created += 1
    if created:
        db.commit()
        checkpoint_after_write(db)
    return {"seeded_no_xbrl": created}


def count_pending_profiles(db: Session, listing: str = "上場") -> int:
    latest = _latest_yuho_subquery(listing or None)
    return (
        db.scalar(
            select(func.count())
            .select_from(Filing)
            .join(
                latest,
                and_(
                    Filing.edinet_code == latest.c.edinet_code,
                    _filing_period_key() == latest.c.period_key,
                ),
            )
            .outerjoin(CompanyProfile, Filing.edinet_code == CompanyProfile.edinet_code)
            .join(Company, Filing.edinet_code == Company.edinet_code)
            .where(
                Filing.doc_type_code.in_(YUHO_DOC_TYPES),
                Filing.has_xbrl.is_(True),
                Company.listing_status == listing,
                or_(
                    CompanyProfile.edinet_code.is_(None),
                    and_(
                        CompanyProfile.parse_status != "ok",
                        CompanyProfile.parse_status != "no_xbrl",
                        CompanyProfile.parse_status != "no_content",
                    ),
                ),
            )
        )
        if listing
        else 0
    ) or 0
