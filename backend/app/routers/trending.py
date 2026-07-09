from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.external_media.trending import list_news_trending, list_search_trending

router = APIRouter(prefix="/trending", tags=["trending"])

MEDIA_DISCLAIMER = (
    "Google トレンド・Google News の公開データに基づく参考情報です。"
    "検索関心度や報道件数は実際の投資判断材料として十分ではありません。"
)


@router.get("/home")
def home_trending(
    limit: int = Query(8, ge=1, le=20),
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
):
    search_items = list_search_trending(db, limit=limit, days=days)
    if search_items:
        search_source = search_items[0].get("source", "google_trends")
    else:
        search_source = "unavailable"
    return {
        "period_days": days,
        "search_trending": search_items,
        "search_trending_source": search_source,
        "news_trending": list_news_trending(db, limit=limit, days=days),
        "disclaimer": MEDIA_DISCLAIMER,
    }
