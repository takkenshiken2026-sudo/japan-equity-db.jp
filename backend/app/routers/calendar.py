from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import Company, Filing, get_db
from app.edinet.urls import edinet_download_url, edinet_viewer_url

router = APIRouter(prefix="/calendar", tags=["calendar"])

EARNINGS_DOC_TYPES = ("120", "130", "140", "150")
DISCLOSURE_DOC_TYPES = ("120", "130", "140", "150", "160", "170", "180", "190", "200")


def _serialize_filing(filing: Filing, company: Company | None = None) -> dict:
    return {
        "doc_id": filing.doc_id,
        "edinet_code": filing.edinet_code,
        "company_name": company.name if company else None,
        "sec_code": company.sec_code if company else None,
        "industry": company.industry if company else None,
        "doc_type_code": filing.doc_type_code,
        "doc_description": filing.doc_description,
        "period_start": filing.period_start,
        "period_end": filing.period_end,
        "submit_date_time": filing.submit_date_time,
        "file_date": filing.file_date,
        "has_pdf": filing.has_pdf,
        "has_xbrl": filing.has_xbrl,
        "viewer_url": edinet_viewer_url(filing.doc_id),
        "pdf_url": edinet_download_url(filing.doc_id, "pdf") if filing.has_pdf else None,
        "xbrl_url": edinet_download_url(filing.doc_id, "xbrl") if filing.has_xbrl else None,
    }


@router.get("/earnings")
def earnings_calendar(
    days: int = Query(30, ge=0, le=90, description="今日から先の日数"),
    past_days: int = Query(365, ge=0, le=730, description="過去に遡る日数"),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    today = date.today()
    start = (today - timedelta(days=past_days)).isoformat()
    end = (today + timedelta(days=days)).isoformat()

    submit_date = func.substr(Filing.submit_date_time, 1, 10)
    stmt = (
        select(Filing, Company)
        .join(Company, Filing.edinet_code == Company.edinet_code)
        .where(
            Filing.doc_type_code.in_(EARNINGS_DOC_TYPES),
            Filing.submit_date_time.is_not(None),
            submit_date >= start,
            submit_date <= end,
            Company.listing_status == "上場",
        )
        .order_by(Filing.submit_date_time.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    items = [_serialize_filing(f, c) for f, c in rows]

    by_date: dict[str, list] = {}
    for item in items:
        day = (item.get("submit_date_time") or "")[:10]
        if day:
            by_date.setdefault(day, []).append(item)

    return {
        "range": {"start": start, "end": end},
        "count": len(items),
        "items": items,
        "by_date": by_date,
    }


@router.get("/disclosures")
def recent_disclosures(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    since: Optional[str] = Query(None, description="この日付以降の提出のみ（YYYY-MM-DD）"),
    codes: Optional[str] = Query(None, description="カンマ区切り EDINETコードで絞り込み"),
    doc_type: Optional[str] = Query(None, description="書類種別コード"),
    q: Optional[str] = Query(None, description="企業名・説明文の部分一致"),
    db: Session = Depends(get_db),
):
    submit_date = func.substr(Filing.submit_date_time, 1, 10)
    stmt = (
        select(Filing, Company)
        .join(Company, Filing.edinet_code == Company.edinet_code)
        .where(
            Filing.doc_type_code.in_(DISCLOSURE_DOC_TYPES),
            Company.listing_status == "上場",
        )
        .order_by(Filing.submit_date_time.desc())
    )
    if since:
        stmt = stmt.where(submit_date >= since[:10])
    if codes:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        if code_list:
            stmt = stmt.where(Filing.edinet_code.in_(code_list))
    if doc_type:
        stmt = stmt.where(Filing.doc_type_code == doc_type)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Company.name.like(pattern),
                Filing.doc_description.like(pattern),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.offset(offset).limit(limit)).all()
    items = [_serialize_filing(f, c) for f, c in rows]
    return {"total": total, "count": len(items), "offset": offset, "items": items}
