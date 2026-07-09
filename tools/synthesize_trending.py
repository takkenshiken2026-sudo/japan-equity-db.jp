"""company JSON バンドルから trending/home.json を合成する（DB 集計が空のとき用）。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DISCLAIMER = (
    "Google トレンド・Google News の公開データに基づく参考情報です。"
    "検索関心度や報道件数は実際の投資判断材料として十分ではありません。"
)


def _jst_today() -> date:
    return datetime.now(JST).date()


def _parse_jst_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).date()
    except ValueError:
        return None


def _format_sec_code(sec: str | None) -> str:
    if not sec:
        return "-"
    s = str(sec).strip()
    if len(s) == 4 and s.isdigit():
        return f"{s}.T"
    return s


def _compute_spike(points: list[dict]) -> tuple[float | None, float | None, float | None, int | None]:
    if len(points) < 9:
        return None, None, None, None
    values = [(p.get("date") or p.get("point_date"), p.get("value")) for p in points]
    values = [(d, v) for d, v in values if v is not None]
    if len(values) < 9:
        return None, None, None, None
    recent = values[-2:]
    prior = values[-9:-2]
    if not prior:
        return None, None, None, None
    recent_avg = sum(v for _, v in recent) / len(recent)
    prior_avg = sum(v for _, v in prior) / len(prior)
    if prior_avg <= 0 and recent_avg <= 0:
        return None, None, None, None
    spike = recent_avg - prior_avg
    latest = values[-1][1]
    return round(spike, 1), round(recent_avg, 1), round(prior_avg, 1), int(latest) if latest is not None else None


def synthesize_trending_home(data_dir: Path, *, limit: int = 8) -> dict:
    screening_path = data_dir / "screening/index.json"
    companies_dir = data_dir / "companies"
    if not screening_path.exists() or not companies_dir.exists():
        return {
            "period_days": 7,
            "search_trending": [],
            "search_trending_source": "unavailable",
            "news_trending": [],
            "disclaimer": DISCLAIMER,
            "synthesized": True,
        }

    screening = json.loads(screening_path.read_text(encoding="utf-8"))
    meta_by_code = {
        row["edinet_code"]: row
        for row in screening.get("items", [])
        if row.get("edinet_code")
    }

    today = _jst_today()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)

    news_items: list[dict] = []
    trend_items: list[dict] = []

    for path in companies_dir.glob("*.json"):
        code = path.stem
        meta = meta_by_code.get(code, {})
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        name = meta.get("name") or (bundle.get("news") or {}).get("company_name") or code
        sec_code = _format_sec_code(meta.get("sec_code"))

        news = bundle.get("news") or {}
        articles = news.get("items") or []
        if articles:
            today_count = 0
            prior_count = 0
            for article in articles:
                d = _parse_jst_date(article.get("published_at"))
                if d == today:
                    today_count += 1
                elif d == yesterday:
                    prior_count += 1
            if today_count <= 0 and prior_count <= 0:
                recent = [
                    a for a in articles
                    if (d := _parse_jst_date(a.get("published_at"))) and d >= week_ago
                ]
                if recent:
                    today_count = len(recent)
                    prior_count = max(0, len(articles) - len(recent))
            if today_count > 0 or prior_count > 0:
                delta = today_count - prior_count
                latest = max(
                    articles,
                    key=lambda a: a.get("published_at") or "",
                )
                news_items.append(
                    {
                        "edinet_code": code,
                        "name": name,
                        "sec_code": sec_code,
                        "today_count": today_count,
                        "prior_count": prior_count,
                        "delta": delta,
                        "article_count": today_count,
                        "latest_title": latest.get("title"),
                        "latest_link": latest.get("link"),
                        "latest_at": latest.get("published_at"),
                    }
                )
            elif news.get("timeline"):
                timeline = news["timeline"]
                if len(timeline) >= 2:
                    cur = timeline[-1].get("count") or 0
                    prev = timeline[-2].get("count") or 0
                    if cur > 0:
                        news_items.append(
                            {
                                "edinet_code": code,
                                "name": name,
                                "sec_code": sec_code,
                                "today_count": cur,
                                "prior_count": prev,
                                "delta": cur - prev,
                                "article_count": cur,
                                "latest_title": articles[0]["title"] if articles else None,
                                "latest_link": articles[0]["link"] if articles else None,
                                "latest_at": articles[0].get("published_at") if articles else None,
                            }
                        )

        trend = bundle.get("trend_7") or {}
        points = trend.get("points") or []
        if points and not trend.get("error"):
            spike, recent_avg, prior_avg, latest = _compute_spike(points)
            if spike is not None and recent_avg and recent_avg > 0:
                trend_items.append(
                    {
                        "edinet_code": code,
                        "name": name,
                        "sec_code": sec_code,
                        "keyword": trend.get("keyword"),
                        "spike": spike,
                        "recent_avg": recent_avg,
                        "prior_avg": prior_avg,
                        "latest_value": latest,
                        "source": "google_trends",
                    }
                )

    news_items.sort(key=lambda x: (x["delta"], x["today_count"]), reverse=True)
    news_trending = news_items[:limit]

    trend_items.sort(key=lambda x: (x["spike"], x["recent_avg"]), reverse=True)
    positive = [t for t in trend_items if t["spike"] > 0]
    search_trending = (positive or trend_items)[:limit]

    if not search_trending and news_trending:
        search_trending = []
        for item in news_trending[:limit]:
            today_c = item["today_count"] or 0
            prior_c = item["prior_count"] or 0
            if prior_c > 0:
                growth = round(((today_c - prior_c) / prior_c) * 100, 1)
            else:
                growth = float(today_c * 10)
            search_trending.append(
                {
                    "edinet_code": item["edinet_code"],
                    "name": item["name"],
                    "sec_code": item["sec_code"],
                    "keyword": None,
                    "spike": growth,
                    "recent_avg": float(today_c),
                    "prior_avg": float(prior_c),
                    "latest_value": today_c,
                    "source": "news_momentum",
                }
            )
        search_source = "news_momentum"
    elif search_trending:
        search_source = "google_trends"
    else:
        search_source = "unavailable"

    return {
        "period_days": 7,
        "search_trending": search_trending,
        "search_trending_source": search_source,
        "news_trending": news_trending,
        "disclaimer": DISCLAIMER,
        "synthesized": True,
    }


def write_synthesized_trending(data_dir: Path, *, limit: int = 8) -> dict:
    payload = synthesize_trending_home(data_dir, limit=limit)
    out = data_dir / "trending/home.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    print(
        f"  synthesized trending/home.json: "
        f"search={len(payload['search_trending'])}, news={len(payload['news_trending'])}"
    )
    return payload
