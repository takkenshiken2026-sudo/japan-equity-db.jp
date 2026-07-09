from __future__ import annotations

import time
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collection_log import record_external_media_delta
from app.config import settings
from app.db import Company
from app.external_media.store import (
    get_batch_offset,
    purge_irrelevant_news_articles,
    persist_news_articles,
    persist_trend_points,
    set_batch_offset,
)
from app.news.relevance import build_trend_keyword
from app.news.google import RSS_MAX_ITEMS, USER_AGENT, _parse_rss
from app.news.relevance import build_news_query
from app.trends.google import _fetch_timeline, _resolve_days


def refresh_company_news(
    db: Session,
    company: Company,
    *,
    log_delta: bool = True,
) -> dict[str, Any]:
    query = build_news_query(
        name=company.name,
        sec_code=company.sec_code,
        name_en=company.name_en,
    )
    from urllib.parse import quote

    from app.news.google import RSS_BASE

    error: Optional[str] = None
    items: list[dict[str, Any]] = []
    removed = purge_irrelevant_news_articles(db, company)
    try:
        url = f"{RSS_BASE}?q={quote(query)}&hl=ja&gl=JP&ceid=JP:ja"
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
        items = _parse_rss(response.content, limit=RSS_MAX_ITEMS)
        items = filter_relevant_articles(
            items,
            name=company.name,
            sec_code=company.sec_code,
            name_en=company.name_en,
        )
    except Exception:
        error = "fetch_failed"

    stats = persist_news_articles(db, edinet_code=company.edinet_code, items=items, error=error)
    if log_delta and (stats["inserted"] > 0 or error):
        record_external_media_delta(
            source="google_news",
            edinet_code=company.edinet_code,
            stats=stats,
            error=error,
        )
    return {"query": query, "fetched": len(items), "error": error, "purged": removed, **stats}


def refresh_company_trend(
    db: Session,
    company: Company,
    *,
    days: int = 90,
    log_delta: bool = True,
) -> dict[str, Any]:
    resolved_days, timeframe = _resolve_days(days)
    keyword = build_trend_keyword(company.name)
    error: Optional[str] = None
    points: list[dict[str, Any]] = []
    try:
        points = _fetch_timeline(keyword, timeframe)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            error = "rate_limited"
        else:
            error = "fetch_failed"
    except Exception:
        error = "fetch_failed"

    stats = persist_trend_points(
        db,
        edinet_code=company.edinet_code,
        keyword=keyword,
        points=points,
        error=error,
    )
    if log_delta and (stats["inserted"] > 0 or stats["updated"] > 0 or error):
        record_external_media_delta(
            source="google_trends",
            edinet_code=company.edinet_code,
            stats=stats,
            error=error,
        )
    return {
        "keyword": keyword,
        "days": resolved_days,
        "fetched": len(points),
        "error": error,
        **stats,
    }


def collect_external_media_batch(
    db: Session,
    *,
    limit: Optional[int] = None,
    sleep_news: Optional[float] = None,
    sleep_trends: Optional[float] = None,
    trend_days: int = 90,
    trends_enabled: bool = True,
) -> dict[str, Any]:
    limit = limit or settings.external_media_batch_limit
    sleep_news = settings.external_media_sleep_news if sleep_news is None else sleep_news
    sleep_trends = settings.external_media_sleep_trends if sleep_trends is None else sleep_trends

    offset = get_batch_offset(db)
    companies = db.scalars(
        select(Company)
        .where(Company.listing_status == "上場")
        .order_by(Company.edinet_code)
        .offset(offset)
        .limit(limit)
    ).all()
    if not companies:
        offset = 0
        companies = db.scalars(
            select(Company)
            .where(Company.listing_status == "上場")
            .order_by(Company.edinet_code)
            .limit(limit)
        ).all()

    summary: dict[str, Any] = {
        "offset_start": offset,
        "processed": 0,
        "news": {"inserted": 0, "skipped": 0, "errors": 0},
        "trends": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0, "rate_limited": False},
    }

    for company in companies:
        news_result = refresh_company_news(db, company, log_delta=True)
        summary["news"]["inserted"] += news_result.get("inserted", 0)
        summary["news"]["skipped"] += news_result.get("skipped", 0)
        if news_result.get("error"):
            summary["news"]["errors"] += 1
        time.sleep(sleep_news)

        if trends_enabled and not summary["trends"]["rate_limited"]:
            trend_result = refresh_company_trend(
                db,
                company,
                days=trend_days,
                log_delta=True,
            )
            summary["trends"]["inserted"] += trend_result.get("inserted", 0)
            summary["trends"]["updated"] += trend_result.get("updated", 0)
            summary["trends"]["skipped"] += trend_result.get("skipped", 0)
            if trend_result.get("error"):
                summary["trends"]["errors"] += 1
                if trend_result["error"] == "rate_limited":
                    summary["trends"]["rate_limited"] = True
            time.sleep(sleep_trends)

        summary["processed"] += 1

    set_batch_offset(db, offset + summary["processed"])
    db.commit()
    summary["offset_next"] = offset + summary["processed"]
    return summary
