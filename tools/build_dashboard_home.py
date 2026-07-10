"""screening/index.json からダッシュボード用の軽量 home.json を生成する。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl

MIN_SANE_EPS = 0.01
MAX_SANE_EPS = 100000
MIN_SANE_PER = 0.5
MAX_SANE_PER = 500
MIN_SANE_MARKET_CAP = 1_000_000_000

# mock/index.html の DASH_VIEWS（special 以外）と揃える
DASH_VIEW_QUERIES: dict[str, str] = {
    "revenue": "listing=上場&sort_by=revenue&order=desc",
    "roe": "listing=上場&sort_by=roe&order=desc&min_roe=0.1",
    "margin": "listing=上場&sort_by=operating_margin&order=desc",
    "growth": "listing=上場&sort_by=revenue_growth&order=desc&min_revenue_growth=0.1",
    "net_income": "listing=上場&sort_by=net_income&order=desc",
    "market_cap": "listing=上場&sort_by=market_cap&order=desc",
    "low_per": "listing=上場&sort_by=per&order=asc&max_per=20",
    "realestate": "listing=上場&sort_by=real_estate_book&order=desc&has_real_estate=true",
    "net_cash": "listing=上場&sort_by=net_cash&order=desc&has_net_cash=true",
    "dividend": "listing=上場&sort_by=dividend_yield&order=desc&min_dividend_yield=0.03",
}


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # NaN check


def _per_val(item: dict) -> float | None:
    return _num(item.get("per_edinet") if item.get("per_edinet") is not None else item.get("per"))


def _pbr_val(item: dict) -> float | None:
    return _num(item.get("pbr_edinet") if item.get("pbr_edinet") is not None else item.get("pbr"))


def _net_cash(item: dict) -> float | None:
    if item.get("net_cash") is not None:
        return _num(item.get("net_cash"))
    cash = _num(item.get("cash_and_deposits"))
    if cash is None:
        return None
    debt = _num(item.get("interest_bearing_debt")) or 0.0
    return cash - debt


def _passes_per_sanity(item: dict) -> bool:
    eps = _num(item.get("eps"))
    p = _per_val(item)
    if eps is None or eps <= MIN_SANE_EPS or eps > MAX_SANE_EPS:
        return False
    if p is None or p < MIN_SANE_PER or p > MAX_SANE_PER:
        return False
    return True


def filter_screening(items: list[dict], params: dict[str, str]) -> list[dict]:
    rows = list(items)
    industry = params.get("industry") or ""
    min_revenue = _num(params.get("min_revenue"))
    max_revenue = _num(params.get("max_revenue"))
    min_operating_margin = _num(params.get("min_operating_margin"))
    min_roe = _num(params.get("min_roe"))
    min_roa = _num(params.get("min_roa"))
    min_growth = _num(params.get("min_revenue_growth"))
    min_per = _num(params.get("min_per"))
    max_per = _num(params.get("max_per"))
    min_pbr = _num(params.get("min_pbr"))
    max_pbr = _num(params.get("max_pbr"))
    min_nav = _num(params.get("min_real_estate_nav_ratio"))
    has_re = params.get("has_real_estate") == "true"
    has_cf = params.get("has_operating_cf") == "true"
    has_net_cash = params.get("has_net_cash") == "true"
    min_equity = _num(params.get("min_equity_ratio"))
    max_de = _num(params.get("max_debt_equity_ratio"))
    min_div_yield = _num(params.get("min_dividend_yield"))
    sort_by = params.get("sort_by") or "revenue"
    order = params.get("order") or "desc"

    uses_per = min_per is not None or max_per is not None or sort_by in ("per", "pbr")
    if uses_per:
        rows = [r for r in rows if _passes_per_sanity(r)]

    if industry:
        rows = [r for r in rows if industry in (r.get("industry") or "")]
    if min_revenue is not None:
        rows = [r for r in rows if (_num(r.get("revenue")) or float("-inf")) >= min_revenue]
    if max_revenue is not None:
        rows = [r for r in rows if (_num(r.get("revenue")) or float("inf")) <= max_revenue]
    if min_operating_margin is not None:
        rows = [r for r in rows if (_num(r.get("operating_margin")) or float("-inf")) >= min_operating_margin]
    if min_roe is not None:
        rows = [r for r in rows if (_num(r.get("roe")) or float("-inf")) >= min_roe]
    if min_roa is not None:
        rows = [r for r in rows if (_num(r.get("roa")) or float("-inf")) >= min_roa]
    if min_growth is not None:
        rows = [r for r in rows if (_num(r.get("revenue_growth")) or float("-inf")) >= min_growth]
    if min_per is not None:
        rows = [r for r in rows if (_per_val(r) or float("-inf")) >= min_per]
    if max_per is not None:
        rows = [r for r in rows if (_per_val(r) or float("inf")) <= max_per]
    if min_pbr is not None:
        rows = [r for r in rows if (_pbr_val(r) or float("-inf")) >= min_pbr]
    if max_pbr is not None:
        rows = [r for r in rows if (_pbr_val(r) or float("inf")) <= max_pbr]
    if has_re:
        rows = [r for r in rows if r.get("real_estate")]
    if has_cf:
        rows = [r for r in rows if (_num(r.get("operating_cf")) or 0) > 0]
    if has_net_cash:
        rows = [r for r in rows if (nc := _net_cash(r)) is not None and nc > 0]
    if min_equity is not None:
        rows = [r for r in rows if (_num(r.get("equity_ratio")) or float("-inf")) >= min_equity]
    if max_de is not None:
        rows = [
            r for r in rows
            if (de := _num(r.get("debt_equity_ratio"))) is not None and de >= 0 and de <= max_de
        ]
    if min_div_yield is not None:
        rows = [r for r in rows if (_num(r.get("dividend_yield")) or float("-inf")) >= min_div_yield]
    if sort_by == "market_cap":
        rows = [r for r in rows if (_num(r.get("market_cap")) or 0) > 0]
    if sort_by == "net_cash":
        rows = [r for r in rows if r.get("cash_and_deposits") is not None]
    if sort_by == "equity_ratio":
        rows = [r for r in rows if r.get("equity_ratio") is not None]
    if sort_by == "debt_equity":
        rows = [r for r in rows if r.get("debt_equity_ratio") is not None]
    if sort_by == "dividend_yield":
        rows = [r for r in rows if (_num(r.get("dividend_yield")) or 0) > 0]
    if min_nav is not None or sort_by == "real_estate_nav":
        rows = [
            r for r in rows
            if (_num(r.get("market_cap")) or 0) >= MIN_SANE_MARKET_CAP
            and r.get("real_estate_nav_ratio") is not None
        ]
    if min_nav is not None:
        rows = [r for r in rows if (_num(r.get("real_estate_nav_ratio")) or 0) >= min_nav]

    sort_key: dict[str, Callable[[dict], float | None]] = {
        "revenue": lambda r: _num(r.get("revenue")),
        "operating_margin": lambda r: _num(r.get("operating_margin")),
        "roe": lambda r: _num(r.get("roe")),
        "roa": lambda r: _num(r.get("roa")),
        "revenue_growth": lambda r: _num(r.get("revenue_growth")),
        "net_income": lambda r: _num(r.get("net_income")),
        "per": _per_val,
        "pbr": _pbr_val,
        "market_cap": lambda r: _num(r.get("market_cap")),
        "operating_cf": lambda r: _num(r.get("operating_cf")),
        "real_estate_nav": lambda r: _num(r.get("real_estate_nav_ratio")),
        "real_estate_book": lambda r: _num((r.get("real_estate") or {}).get("total_book_value_m")),
        "equity_ratio": lambda r: _num(r.get("equity_ratio")),
        "debt_equity": lambda r: _num(r.get("debt_equity_ratio")),
        "net_cash": _net_cash,
        "dividend_yield": lambda r: _num(r.get("dividend_yield")),
    }
    key_fn = sort_key.get(sort_by, sort_key["revenue"])
    reverse = order != "asc"

    def sort_tuple(row: dict) -> tuple:
        val = key_fn(row)
        if val is None:
            return (1, 0)
        return (0, -val if reverse else val)

    rows.sort(key=sort_tuple)
    return rows


def build_dashboard_home(screening_items: list[dict], *, limit: int = 100) -> dict:
    views: dict[str, dict] = {}
    for view_key, query in DASH_VIEW_QUERIES.items():
        params = dict(parse_qsl(query, keep_blank_values=True))
        filtered = filter_screening(screening_items, params)
        views[view_key] = {
            "total": len(filtered),
            "count": min(limit, len(filtered)),
            "offset": 0,
            "items": filtered[:limit],
        }
    return {"views": views}


def write_dashboard_home(data_dir: Path, screening_items: list[dict] | None = None, *, limit: int = 100) -> Path:
    data_dir = Path(data_dir)
    if screening_items is None:
        screening_path = data_dir / "screening/index.json"
        payload = json.loads(screening_path.read_text(encoding="utf-8"))
        screening_items = payload.get("items") or []
    home = build_dashboard_home(screening_items, limit=limit)
    out = data_dir / "dashboard/home.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(home, ensure_ascii=False, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parents[1]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "public_site" / "data"
    path = write_dashboard_home(target)
    print(f"Wrote {path}")
