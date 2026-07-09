from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Company, CompanyNewsArticle, CompanyTrendPoint
from app.external_media.store import _is_relevant_article_row
from app.seo.formatters import format_sec_code

JST = ZoneInfo("Asia/Tokyo")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _jst_today() -> date:
    return datetime.now(JST).date()


def _article_jst_date(article: CompanyNewsArticle) -> Optional[date]:
    raw = (article.published_at or "").strip()
    if raw:
        try:
            normalized = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(JST).date()
        except ValueError:
            pass
    seen = article.first_seen_at
    if seen is None:
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return seen.astimezone(JST).date()


def _compute_search_spike(
    rows: list[CompanyTrendPoint],
    *,
    recent_n: int = 2,
    prior_n: int = 7,
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[int]]:
    if len(rows) < recent_n + prior_n:
        return None, None, None, None
    values = [(row.point_date, row.value) for row in rows]
    recent = values[-recent_n:]
    prior = values[-(recent_n + prior_n) : -recent_n]
    if not prior:
        return None, None, None, None
    recent_avg = sum(value for _, value in recent) / len(recent)
    prior_avg = sum(value for _, value in prior) / len(prior)
    if prior_avg <= 0 and recent_avg <= 0:
        return None, None, None, None
    spike = recent_avg - prior_avg
    latest = values[-1][1]
    return round(spike, 1), round(recent_avg, 1), round(prior_avg, 1), latest


def list_news_trending(
    db: Session,
    *,
    limit: int = 8,
    days: int = 7,
) -> list[dict[str, Any]]:
    today = _jst_today()
    yesterday = today - timedelta(days=1)
    lookback_start = today - timedelta(days=max(days, 14))
    cutoff_naive = (
        datetime.combine(lookback_start, datetime.min.time(), tzinfo=JST)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )

    articles = db.scalars(
        select(CompanyNewsArticle).where(CompanyNewsArticle.first_seen_at >= cutoff_naive)
    ).all()
    grouped: dict[str, list[CompanyNewsArticle]] = defaultdict(list)
    for article in articles:
        grouped[article.edinet_code].append(article)

    items: list[dict[str, Any]] = []
    for edinet_code, company_articles in grouped.items():
        company = db.get(Company, edinet_code)
        if not company or company.listing_status != "上場":
            continue
        relevant = [row for row in company_articles if _is_relevant_article_row(row, company)]
        if not relevant:
            continue

        today_count = sum(1 for row in relevant if _article_jst_date(row) == today)
        prior_count = sum(1 for row in relevant if _article_jst_date(row) == yesterday)
        delta = today_count - prior_count
        if today_count <= 0 and delta <= 0:
            continue

        today_articles = [row for row in relevant if _article_jst_date(row) == today]
        latest = max(
            today_articles or relevant,
            key=lambda row: (
                row.published_at or "",
                row.first_seen_at.isoformat() if row.first_seen_at else "",
            ),
        )
        items.append(
            {
                "edinet_code": edinet_code,
                "name": company.name,
                "sec_code": format_sec_code(company.sec_code),
                "today_count": today_count,
                "prior_count": prior_count,
                "delta": delta,
                "article_count": today_count,
                "latest_title": latest.title,
                "latest_link": latest.link,
                "latest_at": latest.published_at
                or latest.first_seen_at.replace(tzinfo=timezone.utc).isoformat(),
            }
        )

    items.sort(key=lambda item: (item["delta"], item["today_count"]), reverse=True)
    return items[:limit]


def list_search_trending(
    db: Session,
    *,
    limit: int = 8,
    days: int = 7,
) -> list[dict[str, Any]]:
    lookback_days = max(days * 2, 14)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()
    codes = db.scalars(
        select(CompanyTrendPoint.edinet_code)
        .where(CompanyTrendPoint.point_date >= cutoff)
        .distinct()
    ).all()

    scored: list[dict[str, Any]] = []
    for edinet_code in codes:
        company = db.get(Company, edinet_code)
        if not company or company.listing_status != "上場":
            continue
        rows = db.scalars(
            select(CompanyTrendPoint)
            .where(
                CompanyTrendPoint.edinet_code == edinet_code,
                CompanyTrendPoint.point_date >= cutoff,
            )
            .order_by(CompanyTrendPoint.point_date.asc())
        ).all()
        spike, recent_avg, prior_avg, latest = _compute_search_spike(rows)
        if spike is None or recent_avg is None or recent_avg <= 0:
            continue
        keyword = rows[-1].keyword if rows else None
        scored.append(
            {
                "edinet_code": edinet_code,
                "name": company.name,
                "sec_code": format_sec_code(company.sec_code),
                "keyword": keyword,
                "spike": spike,
                "recent_avg": recent_avg,
                "prior_avg": prior_avg,
                "latest_value": latest,
                "source": "google_trends",
            }
        )

    scored.sort(key=lambda item: (item["spike"], item["recent_avg"]), reverse=True)
    positive = [item for item in scored if item["spike"] > 0]
    if positive or scored:
        return (positive or scored)[:limit]
    return _list_search_trending_news_fallback(db, limit=limit, days=days)


def _list_search_trending_news_fallback(
    db: Session,
    *,
    limit: int,
    days: int,
) -> list[dict[str, Any]]:
    """Google Trends が未収集・レート制限時の暫定表示（ニュース前日比の伸び率）。"""
    news_items = list_news_trending(db, limit=max(limit * 3, limit), days=days)
    scored: list[dict[str, Any]] = []
    for item in news_items:
        today = item.get("today_count") or 0
        prior = item.get("prior_count") or 0
        if today <= 0:
            continue
        if prior > 0:
            growth = round(((today - prior) / prior) * 100, 1)
        else:
            growth = float(today * 10)
        scored.append(
            {
                "edinet_code": item["edinet_code"],
                "name": item["name"],
                "sec_code": item["sec_code"],
                "keyword": None,
                "spike": growth,
                "recent_avg": float(today),
                "prior_avg": float(prior),
                "latest_value": today,
                "source": "news_momentum",
            }
        )
    scored.sort(key=lambda row: (row["spike"], row["recent_avg"]), reverse=True)
    return scored[:limit]
