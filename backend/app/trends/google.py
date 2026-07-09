from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from app.news.relevance import build_trend_keyword

BASE_URL = "https://trends.google.com/trends"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HL = "ja-JP"
TZ = 540
GEO = "JP"

_TIMEFRAMES = {
    7: "now 7-d",
    30: "today 1-m",
    90: "today 3-m",
}


def _resolve_days(days: int) -> tuple[int, str]:
    if days <= 7:
        return 7, _TIMEFRAMES[7]
    if days <= 30:
        return 30, _TIMEFRAMES[30]
    return 90, _TIMEFRAMES[90]


def _parse_json_response(text: str, trim_chars: int) -> dict[str, Any]:
    return json.loads(text[trim_chars:])


def _fetch_timeline(keyword: str, timeframe: str) -> list[dict[str, Any]]:
    headers = {
        "User-Agent": USER_AGENT,
        "accept-language": HL,
    }
    with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
        explore_resp = client.get(f"{BASE_URL}/explore", params={"geo": GEO, "hl": "ja"})
        if explore_resp.status_code == 429:
            raise httpx.HTTPStatusError(
                "rate_limited",
                request=explore_resp.request,
                response=explore_resp,
            )
        explore_resp.raise_for_status()

        explore_params = {
            "hl": HL,
            "tz": TZ,
            "req": json.dumps(
                {
                    "comparisonItem": [{"keyword": keyword, "time": timeframe, "geo": GEO}],
                    "category": 0,
                    "property": "",
                },
                ensure_ascii=False,
            ),
        }
        widget_resp = client.post(f"{BASE_URL}/api/explore", params=explore_params)
        if widget_resp.status_code == 429:
            raise httpx.HTTPStatusError(
                "rate_limited",
                request=widget_resp.request,
                response=widget_resp,
            )
        widget_resp.raise_for_status()
        widgets = _parse_json_response(widget_resp.text, 4)["widgets"]
        timeseries = next(widget for widget in widgets if widget.get("id") == "TIMESERIES")

        timeline_params = {
            "req": json.dumps(timeseries["request"], ensure_ascii=False),
            "token": timeseries["token"],
            "tz": TZ,
        }
        timeline_resp = client.get(f"{BASE_URL}/api/widgetdata/multiline", params=timeline_params)
        if timeline_resp.status_code == 429:
            raise httpx.HTTPStatusError(
                "rate_limited",
                request=timeline_resp.request,
                response=timeline_resp,
            )
        timeline_resp.raise_for_status()
        timeline = _parse_json_response(timeline_resp.text, 5)

    points: list[dict[str, Any]] = []
    for row in timeline.get("default", {}).get("timelineData", []):
        values = row.get("value") or []
        if not values:
            continue
        value = values[0]
        if value is None:
            continue
        ts = row.get("time")
        if ts is None:
            continue
        date = datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
        points.append(
            {
                "date": date,
                "value": int(value),
            }
        )
    return points
