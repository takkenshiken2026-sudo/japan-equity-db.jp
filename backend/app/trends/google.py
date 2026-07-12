from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.news.relevance import build_trend_keyword

BASE_URL = "https://trends.google.com/trends"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HL = "ja-JP"
ACCEPT_LANGUAGE = "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"
TZ = 540
GEO = "JP"
# CONSENT/SOCS を先に持たせると同意ウォールでの 302→429 を回避しやすい
_PRIMING_COOKIES = {"CONSENT": "YES+", "SOCS": "CAI"}
_MAX_RETRIES = 2
_RETRY_BACKOFF = 3.0


def _new_client() -> httpx.Client:
    headers = {
        "User-Agent": USER_AGENT,
        "accept": "application/json, text/plain, */*",
        "accept-language": ACCEPT_LANGUAGE,
        "referer": f"{BASE_URL}/explore?geo={GEO}",
        "x-requested-with": "XMLHttpRequest",
    }
    client = httpx.Client(
        timeout=20.0, follow_redirects=True, headers=headers, cookies=_PRIMING_COOKIES
    )
    # トップページを1度踏んで NID 等の本物の Cookie を取得（失敗は無視）
    try:
        client.get("https://trends.google.com/", params={"geo": GEO})
    except httpx.HTTPError:
        pass
    return client


def _request_with_retry(
    client: httpx.Client, method: str, url: str, *, params: dict
) -> httpx.Response:
    last: httpx.Response | None = None
    for attempt in range(_MAX_RETRIES + 1):
        resp = client.request(method, url, params=params)
        if resp.status_code in (429, 302, 403):
            last = resp
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            raise httpx.HTTPStatusError(
                "rate_limited", request=resp.request, response=resp
            )
        resp.raise_for_status()
        return resp
    assert last is not None
    raise httpx.HTTPStatusError("rate_limited", request=last.request, response=last)

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
    with _new_client() as client:
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
        widget_resp = _request_with_retry(
            client, "GET", f"{BASE_URL}/api/explore", params=explore_params
        )
        widgets = _parse_json_response(widget_resp.text, 4)["widgets"]
        timeseries = next(widget for widget in widgets if widget.get("id") == "TIMESERIES")

        timeline_params = {
            "req": json.dumps(timeseries["request"], ensure_ascii=False),
            "token": timeseries["token"],
            "tz": TZ,
        }
        timeline_resp = _request_with_retry(
            client, "GET", f"{BASE_URL}/api/widgetdata/multiline", params=timeline_params
        )
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
