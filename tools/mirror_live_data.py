#!/usr/bin/env python3
"""本番サイトの /data を取得して public_site/data に複製する（高速デプロイ用）。"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GLOBAL_FILES = (
    "manifest.json",
    "industries.json",
    "screening/index.json",
    "search/catalog.json",
    "trending/home.json",
    "themes/weekly.json",
    "calendar/earnings.json",
    "calendar/disclosures.json",
)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "japan-equity-db-static-mirror/1.0"})
    with urllib.request.urlopen(req, timeout=120) as res:
        return res.read()


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def mirror_live_data(out_dir: Path, site_url: str, *, workers: int = 24) -> dict:
    base = site_url.rstrip("/")
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    for rel in GLOBAL_FILES:
        dest = data_dir / rel
        print(f"  fetch {rel}")
        _write(dest, _fetch(f"{base}/data/{rel}"))

    screening = json.loads((data_dir / "screening/index.json").read_text(encoding="utf-8"))
    codes = sorted({row["edinet_code"] for row in screening.get("items", []) if row.get("edinet_code")})
    companies_dir = data_dir / "companies"
    companies_dir.mkdir(parents=True, exist_ok=True)

    def download_one(code: str) -> tuple[str, bool]:
        dest = companies_dir / f"{code}.json"
        try:
            _write(dest, _fetch(f"{base}/data/companies/{code}.json"))
            return code, True
        except urllib.error.HTTPError:
            return code, False

    ok = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_one, code): code for code in codes}
        for i, future in enumerate(as_completed(futures), start=1):
            _, success = future.result()
            if success:
                ok += 1
            else:
                failed += 1
            if i % 400 == 0:
                print(f"  companies {i}/{len(codes)}")

    print(f"Mirrored {ok} company bundles ({failed} missing)")
    return {
        "screening_count": len(codes),
        "company_bundles": ok,
        "mirrored_companies": ok,
        "missing_companies": failed,
        "mode": "mirror",
    }


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "public_site"
    site = sys.argv[2] if len(sys.argv) > 2 else "https://japan-equity-db.jp"
    mirror_live_data(target, site)
