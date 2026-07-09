from __future__ import annotations

import re
from typing import Any, Sequence


def _year_label(value: str | None) -> str:
    if not value:
        return ""
    match = re.search(r"(\d{4})", str(value))
    return match.group(1) if match else str(value)[:7]


def _growth_rate(current: float | None, prior: float | None) -> float | None:
    if current is None or prior in (None, 0):
        return None
    return (current - prior) / prior


def _has_annual_metrics(row: Any) -> bool:
    return any(
        getattr(row, attr, None) is not None
        for attr in ("revenue", "operating_income", "net_income", "total_assets")
    )


def _fiscal_month(value: str | None) -> str:
    if not value or len(value) < 7:
        return ""
    return value[5:7]


def _sanitize_annual_financials(financials: Sequence[Any]) -> list[Any]:
    """年次グラフ用に1年度1行へ集約（四半期・空行を除外）。"""
    buckets: dict[str, list[Any]] = {}
    for row in financials:
        fiscal_year_end = (getattr(row, "fiscal_year_end", None) or "").strip()
        if not fiscal_year_end or not _has_annual_metrics(row):
            continue
        year = _year_label(fiscal_year_end)
        if not year:
            continue
        buckets.setdefault(year, []).append(row)

    chosen: list[Any] = []
    for year in sorted(buckets):
        rows = buckets[year]
        march_rows = [row for row in rows if _fiscal_month(row.fiscal_year_end) == "03"]
        pool = march_rows or rows
        pool.sort(
            key=lambda row: (
                getattr(row, "revenue", None) or 0,
                getattr(row, "fiscal_year_end", "") or "",
            ),
            reverse=True,
        )
        chosen.append(pool[0])
    return chosen


def _quarter_label(row: Any) -> str:
    period_end = (getattr(row, "period_end", None) or "").strip()
    quarter_number = getattr(row, "quarter_number", None)
    if period_end and quarter_number:
        return f"{period_end[2:4]}Q{quarter_number}"
    if quarter_number:
        return f"Q{quarter_number}"
    return _year_label(period_end) or period_end[:7]


def _resolve_revenue_yoy(row: Any) -> float | None:
    if getattr(row, "revenue_yoy", None) is not None:
        return row.revenue_yoy
    return _growth_rate(
        getattr(row, "revenue_cumulative", None),
        getattr(row, "revenue_prior_year_cum", None),
    )


def _resolve_operating_income_yoy(row: Any) -> float | None:
    if getattr(row, "operating_income_yoy", None) is not None:
        return row.operating_income_yoy
    return _growth_rate(
        getattr(row, "operating_income_cumulative", None),
        getattr(row, "operating_income_prior_year_cum", None),
    )


def _sanitize_quarterly_rows(quarterly: Sequence[Any]) -> list[Any]:
    rows = sorted(
        quarterly,
        key=lambda row: (getattr(row, "period_end", None) or "", getattr(row, "doc_id", "") or ""),
    )
    chosen: list[Any] = []
    seen: set[str] = set()
    for row in rows:
        label = _quarter_label(row)
        if label in seen:
            continue
        revenue_yoy = _resolve_revenue_yoy(row)
        operating_income_yoy = _resolve_operating_income_yoy(row)
        if revenue_yoy is None and operating_income_yoy is None:
            continue
        seen.add(label)
        chosen.append(row)
    return chosen


def _fin_rows(financials: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "label": _year_label(f.fiscal_year_end),
            "revenue": f.revenue,
            "operating_income": f.operating_income,
            "net_income": f.net_income,
            "operating_cf": f.operating_cf,
            "total_assets": f.total_assets,
            "roe": f.roe,
            "operating_margin": f.operating_margin,
        }
        for f in _sanitize_annual_financials(financials)
    ]


def _quarter_rows(quarterly: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "label": _quarter_label(q),
            "revenue_yoy": _resolve_revenue_yoy(q),
            "operating_income_yoy": _resolve_operating_income_yoy(q),
        }
        for q in _sanitize_quarterly_rows(quarterly)
    ]


def build_company_chart_data(financials: Sequence[Any], quarterly: Sequence[Any]) -> dict[str, Any]:
    return {
        "annual": _fin_rows(financials),
        "quarterly": _quarter_rows(quarterly),
    }


def build_industry_chart_data(companies: Sequence[dict[str, Any]]) -> dict[str, Any]:
    top = [c for c in companies if c.get("revenue_raw")][:12]
    return {
        "labels": [c["name"].replace("株式会社", "").strip()[:12] for c in top],
        "revenues": [c["revenue_raw"] for c in top],
    }
