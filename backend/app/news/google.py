from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree as ET

USER_AGENT = "Mozilla/5.0 (compatible; KabuCheck/1.0)"
RSS_BASE = "https://news.google.com/rss/search"
RSS_MAX_ITEMS = 100
TIMELINE_MONTHS = 12

def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _parse_pub_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_rss(xml_bytes: bytes, *, limit: int) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        return []

    items: list[dict[str, Any]] = []
    for node in channel.findall("item")[:limit]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title or not link:
            continue
        source_el = node.find("source")
        source_name = (source_el.text or "").strip() if source_el is not None else None
        if " - " in title:
            main, tail = title.rsplit(" - ", 1)
            title = main.strip()
            if not source_name:
                source_name = tail.strip()
        items.append(
            {
                "title": title,
                "link": link,
                "published_at": _parse_pub_date(node.findtext("pubDate")),
                "source_name": source_name,
                "summary": _strip_html(node.findtext("description")),
            }
        )
    return items


def _month_key(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m")
    except (TypeError, ValueError, OverflowError):
        return None


def _month_label(month_key: str) -> str:
    year, month = month_key.split("-", 1)
    return f"{year}年{int(month)}月"


def _recent_month_keys(*, months: int, anchor: datetime | None = None) -> list[str]:
    now = anchor or datetime.now(timezone.utc)
    year = now.year
    month = now.month
    keys: list[str] = []
    for _ in range(months):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(keys))


def build_monthly_timeline(items: list[dict[str, Any]], *, months: int = TIMELINE_MONTHS) -> list[dict[str, Any]]:
    counter = Counter(_month_key(item.get("published_at")) for item in items)
    counter.pop(None, None)
    timeline: list[dict[str, Any]] = []
    for key in _recent_month_keys(months=months):
        timeline.append({"period": key, "label": _month_label(key), "count": counter.get(key, 0)})
    return timeline
