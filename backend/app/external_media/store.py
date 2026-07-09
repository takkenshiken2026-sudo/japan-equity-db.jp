from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import (
    Company,
    CompanyNewsArticle,
    CompanyTrendPoint,
    ExternalMediaBatchState,
    ExternalMediaSync,
)
from app.news.google import TIMELINE_MONTHS, build_monthly_timeline
from app.news.relevance import build_news_query, build_trend_keyword, filter_relevant_articles, is_relevant_article


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_batch_offset(db: Session) -> int:
    row = db.get(ExternalMediaBatchState, "cursor_offset")
    if not row or not row.value.isdigit():
        return 0
    return int(row.value)


def set_batch_offset(db: Session, offset: int) -> None:
    row = db.get(ExternalMediaBatchState, "cursor_offset")
    if row is None:
        row = ExternalMediaBatchState(key="cursor_offset", value=str(offset))
        db.add(row)
    else:
        row.value = str(offset)
        row.updated_at = _utcnow()


def _touch_sync_row(db: Session, edinet_code: str) -> ExternalMediaSync:
    row = db.get(ExternalMediaSync, edinet_code)
    if row is None:
        row = ExternalMediaSync(edinet_code=edinet_code)
        db.add(row)
    return row


def persist_news_articles(
    db: Session,
    *,
    edinet_code: str,
    items: list[dict[str, Any]],
    error: Optional[str] = None,
) -> dict[str, int]:
    existing_links = set(
        db.scalars(
            select(CompanyNewsArticle.link).where(CompanyNewsArticle.edinet_code == edinet_code)
        ).all()
    )
    inserted = 0
    for item in items:
        link = (item.get("link") or "").strip()
        title = (item.get("title") or "").strip()
        if not link or not title or link in existing_links:
            continue
        db.add(
            CompanyNewsArticle(
                edinet_code=edinet_code,
                link=link,
                title=title,
                published_at=item.get("published_at"),
                source_name=item.get("source_name"),
                summary=item.get("summary"),
                first_seen_at=_utcnow(),
            )
        )
        existing_links.add(link)
        inserted += 1

    sync_row = _touch_sync_row(db, edinet_code)
    sync_row.news_synced_at = _utcnow()
    sync_row.news_error = error
    db.flush()
    sync_row.news_total = db.scalar(
        select(func.count()).select_from(CompanyNewsArticle).where(
            CompanyNewsArticle.edinet_code == edinet_code
        )
    ) or 0
    db.commit()
    return {
        "inserted": inserted,
        "skipped": max(0, len(items) - inserted),
        "total": sync_row.news_total,
    }


def persist_trend_points(
    db: Session,
    *,
    edinet_code: str,
    keyword: str,
    points: list[dict[str, Any]],
    error: Optional[str] = None,
) -> dict[str, int]:
    existing = {
        row.point_date: row
        for row in db.scalars(
            select(CompanyTrendPoint).where(CompanyTrendPoint.edinet_code == edinet_code)
        ).all()
    }
    inserted = 0
    updated = 0
    skipped = 0
    now = _utcnow()
    for point in points:
        point_date = point.get("date")
        value = point.get("value")
        if not point_date or value is None:
            continue
        row = existing.get(point_date)
        if row is None:
            db.add(
                CompanyTrendPoint(
                    edinet_code=edinet_code,
                    point_date=point_date,
                    value=int(value),
                    keyword=keyword,
                    updated_at=now,
                )
            )
            inserted += 1
            continue
        if int(row.value) == int(value):
            skipped += 1
            continue
        row.value = int(value)
        row.keyword = keyword
        row.updated_at = now
        updated += 1

    sync_row = _touch_sync_row(db, edinet_code)
    sync_row.trend_synced_at = _utcnow()
    sync_row.trend_error = error
    db.flush()
    sync_row.trend_total = db.scalar(
        select(func.count()).select_from(CompanyTrendPoint).where(
            CompanyTrendPoint.edinet_code == edinet_code
        )
    ) or 0
    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "total": sync_row.trend_total,
    }


def _article_to_item(row: CompanyNewsArticle) -> dict[str, Any]:
    return {
        "title": row.title,
        "link": row.link,
        "published_at": row.published_at,
        "source_name": row.source_name,
        "summary": row.summary,
    }


def _is_relevant_article_row(row: CompanyNewsArticle, company: Company) -> bool:
    return is_relevant_article(
        _article_to_item(row),
        name=company.name,
        sec_code=company.sec_code,
        name_en=company.name_en,
    )


def build_timeline_from_articles(
    articles: list[CompanyNewsArticle],
    *,
    months: int = TIMELINE_MONTHS,
) -> list[dict[str, Any]]:
    items = [{"published_at": article.published_at} for article in articles]
    return build_monthly_timeline(items, months=months)


def build_news_payload_from_db(
    db: Session,
    company: Company,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    query = build_news_query(
        name=company.name,
        sec_code=company.sec_code,
        name_en=company.name_en,
    )
    articles = db.scalars(
        select(CompanyNewsArticle)
        .where(CompanyNewsArticle.edinet_code == company.edinet_code)
        .order_by(
            CompanyNewsArticle.published_at.desc().nullslast(),
            CompanyNewsArticle.first_seen_at.desc(),
        )
    ).all()
    relevant_articles = [row for row in articles if _is_relevant_article_row(row, company)]
    display_articles = relevant_articles[:limit]
    sync_row = db.get(ExternalMediaSync, company.edinet_code)
    fetched_at = None
    if sync_row and sync_row.news_synced_at:
        fetched_at = sync_row.news_synced_at.replace(tzinfo=timezone.utc).isoformat()
    total = len(relevant_articles)
    return {
        "edinet_code": company.edinet_code,
        "company_name": company.name,
        "query": query,
        "source": "google_news_rss",
        "storage": "database",
        "fetched_at": fetched_at,
        "cached": True,
        "count": len(display_articles),
        "rss_total": total,
        "timeline": build_timeline_from_articles(relevant_articles),
        "items": [_article_to_item(row) for row in display_articles],
        "error": sync_row.news_error if sync_row else None,
    }


def build_trend_payload_from_db(
    db: Session,
    company: Company,
    *,
    days: int = 90,
    keyword: Optional[str] = None,
) -> dict[str, Any]:
    resolved_keyword = keyword or build_trend_keyword(company.name)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = db.scalars(
        select(CompanyTrendPoint)
        .where(
            CompanyTrendPoint.edinet_code == company.edinet_code,
            CompanyTrendPoint.point_date >= cutoff,
        )
        .order_by(CompanyTrendPoint.point_date.asc())
    ).all()
    points = [{"date": row.point_date, "value": row.value} for row in rows]
    values = [point["value"] for point in points]
    average = round(sum(values) / len(values), 1) if values else None
    peak = max(values) if values else None
    sync_row = db.get(ExternalMediaSync, company.edinet_code)
    fetched_at = None
    if sync_row and sync_row.trend_synced_at:
        fetched_at = sync_row.trend_synced_at.replace(tzinfo=timezone.utc).isoformat()
    return {
        "edinet_code": company.edinet_code,
        "company_name": company.name,
        "keyword": resolved_keyword,
        "geo": "JP",
        "days": days,
        "timeframe": f"stored:{days}d",
        "source": "google_trends",
        "storage": "database",
        "fetched_at": fetched_at,
        "cached": True,
        "count": len(points),
        "average": average,
        "peak": peak,
        "points": points,
        "error": sync_row.trend_error if sync_row else None,
    }


def purge_irrelevant_news_articles(db: Session, company: Company) -> int:
    rows = db.scalars(
        select(CompanyNewsArticle).where(CompanyNewsArticle.edinet_code == company.edinet_code)
    ).all()
    removed = 0
    for row in rows:
        if _is_relevant_article_row(row, company):
            continue
        db.delete(row)
        removed += 1
    if removed:
        sync_row = _touch_sync_row(db, company.edinet_code)
        db.flush()
        sync_row.news_total = db.scalar(
            select(func.count()).select_from(CompanyNewsArticle).where(
                CompanyNewsArticle.edinet_code == company.edinet_code
            )
        ) or 0
        db.commit()
    return removed


def purge_all_irrelevant_news(db: Session, *, listing: str = "上場", limit: Optional[int] = None) -> dict[str, int]:
    query = select(Company).order_by(Company.edinet_code)
    if listing:
        query = query.where(Company.listing_status == listing)
    if limit:
        query = query.limit(limit)
    companies = db.scalars(query).all()
    summary = {"companies": 0, "removed": 0}
    for company in companies:
        removed = purge_irrelevant_news_articles(db, company)
        summary["companies"] += 1
        summary["removed"] += removed
    return summary


def should_refresh_news(sync_row: Optional[ExternalMediaSync], *, hours: int = 6) -> bool:
    if sync_row is None or sync_row.news_synced_at is None:
        return True
    return sync_row.news_synced_at < _utcnow() - timedelta(hours=hours)


def should_refresh_trend(sync_row: Optional[ExternalMediaSync], *, hours: int = 12) -> bool:
    if sync_row is None or sync_row.trend_synced_at is None:
        return True
    return sync_row.trend_synced_at < _utcnow() - timedelta(hours=hours)
