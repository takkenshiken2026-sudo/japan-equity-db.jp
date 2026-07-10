"""SQLite から GitHub Pages 用静的 JSON を書き出す。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _json_dump(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


def _resolve_db_path() -> Path | None:
    for candidate in (ROOT / "backend/data/edinet.db", ROOT / "data/edinet.db"):
        if candidate.exists() and candidate.stat().st_size > 100_000:
            return candidate
    return None


def _setup_app(db_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve()}"
    sys.path.insert(0, str(ROOT / "backend"))
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _fetch_all_screening(client, params: str = "listing=上場") -> list[dict]:
    items: list[dict] = []
    offset = 0
    total = None
    while total is None or offset < total:
        path = f"/api/screening?{params}&limit=500&offset={offset}&sort_by=revenue&order=desc"
        response = client.get(path)
        response.raise_for_status()
        payload = response.json()
        total = payload.get("total") or 0
        batch = payload.get("items") or []
        if not batch:
            break
        items.extend(batch)
        offset += len(batch)
        if len(batch) < 500:
            break
    return items


def _export_all_disclosures(client) -> dict:
    items: list[dict] = []
    offset = 0
    total = None
    while total is None or offset < total:
        response = client.get(f"/api/calendar/disclosures?limit=200&offset={offset}")
        response.raise_for_status()
        payload = response.json()
        total = payload.get("total") or 0
        batch = payload.get("items") or []
        if not batch:
            break
        items.extend(batch)
        offset += len(batch)
        if len(batch) < 200:
            break
    return {"total": total or len(items), "count": len(items), "offset": 0, "items": items}


def _export_globals(client, data_dir: Path) -> dict:
    stats: dict[str, int] = {}
    endpoints = {
        "industries.json": "/api/screening/industries",
        "trending/home.json": "/api/trending/home?limit=8&days=7",
        "themes/weekly.json": "/api/themes/weekly?limit=8",
        "calendar/earnings.json": "/api/calendar/earnings?days=0&past_days=365&limit=200",
        "explore/quarterly-momentum.json": "/api/explore/quarterly-momentum?limit=100&min_revenue_yoy=0.1",
        "explore/prefectures.json": "/api/explore/prefectures?limit=20&top_companies=5",
    }
    for rel, path in endpoints.items():
        response = client.get(path)
        if response.status_code == 200:
            _json_dump(data_dir / rel, response.json())
            stats[rel] = 1
        else:
            print(f"  skip {rel} ({response.status_code})")
    _json_dump(data_dir / "calendar/disclosures.json", _export_all_disclosures(client))
    stats["calendar/disclosures.json"] = 1
    return stats


def _export_search_catalog(client, data_dir: Path) -> int:
    items: list[dict] = []
    offset = 0
    total = None
    while total is None or offset < total:
        response = client.get(f"/api/companies?limit=200&offset={offset}")
        response.raise_for_status()
        payload = response.json()
        total = payload.get("total") or 0
        batch = payload.get("items") or []
        if not batch:
            break
        items.extend(batch)
        offset += len(batch)
        if len(batch) < 200:
            break
    _json_dump(data_dir / "search/catalog.json", {"total": len(items), "items": items})
    return len(items)


def _export_company_bundle(client, code: str) -> dict | None:
    bundle: dict = {}
    specs = [
        ("summary", f"/api/companies/{code}?financial_limit=12"),
        ("news", f"/api/companies/{code}/news?limit=8"),
        ("trend_7", f"/api/companies/{code}/search-trend?days=7"),
        ("trend_30", f"/api/companies/{code}/search-trend?days=30"),
        ("trend_90", f"/api/companies/{code}/search-trend?days=90"),
        ("profile", f"/api/companies/{code}/profile"),
        ("real_estate", f"/api/companies/{code}/real-estate"),
        ("quarterly", f"/api/companies/{code}/quarterly?limit=32"),
        ("valuation_history", f"/api/companies/{code}/valuation-history"),
    ]
    for key, path in specs:
        response = client.get(path)
        if response.status_code == 200:
            bundle[key] = response.json()

    for range_key in ("1m", "3m", "6m", "1y", "2y", "5y"):
        response = client.get(f"/api/companies/{code}/price-history?range={range_key}")
        if response.status_code == 200:
            bundle.setdefault("price_history", {})[range_key] = response.json()

    return bundle if bundle.get("summary") else None


def export_static_data(out_dir: Path) -> dict:
    db_path = _resolve_db_path()
    if not db_path:
        print("No database found — skipping static data export")
        return {"ok": False, "reason": "no_db"}

    print(f"Exporting static data from {db_path}")
    client = _setup_app(db_path)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    _export_globals(client, data_dir)
    search_count = _export_search_catalog(client, data_dir)

    screening_items = _fetch_all_screening(client)
    _json_dump(
        data_dir / "screening/index.json",
        {"total": len(screening_items), "items": screening_items},
    )
    print(f"  screening index: {len(screening_items)} companies")

    codes = sorted({row["edinet_code"] for row in screening_items if row.get("edinet_code")})
    companies_dir = data_dir / "companies"
    companies_dir.mkdir(parents=True, exist_ok=True)
    exported = 0
    for i, code in enumerate(codes):
        if i and i % 200 == 0:
            print(f"  companies {i}/{len(codes)}")
        bundle = _export_company_bundle(client, code)
        if bundle:
            _json_dump(companies_dir / f"{code}.json", bundle)
            exported += 1

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "screening_count": len(screening_items),
        "company_bundles": exported,
        "search_catalog": search_count,
        "mode": "static",
    }
    _json_dump(data_dir / "manifest.json", manifest)
    print(f"Exported {exported} company bundles, manifest written")

    from synthesize_trending import write_synthesized_trending

    home_path = data_dir / "trending/home.json"
    try:
        home = json.loads(home_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        home = {}
    if not home.get("search_trending") and not home.get("news_trending"):
        write_synthesized_trending(data_dir)

    return manifest


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "public_site"
    result = export_static_data(target)
    if not result.get("ok", True):
        sys.exit(1)
