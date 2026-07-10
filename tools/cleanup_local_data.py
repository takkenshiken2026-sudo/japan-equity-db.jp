#!/usr/bin/env python3
"""API 取得データのローカルキャッシュを削除する。

DB・収集ログは GitHub Release / 本番 /data に置く前提。ローカルに残すと容量を圧迫する。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _reset_backend_data_dir() -> list[str]:
    """backend/data を .gitkeep と空の collection-logs のみにする。"""
    base = ROOT / "backend/data"
    removed: list[str] = []
    base.mkdir(parents=True, exist_ok=True)
    for path in list(base.iterdir()):
        if path.name == ".gitkeep":
            continue
        rel = str(path.relative_to(ROOT))
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(f"{rel}/")
        else:
            path.unlink()
            removed.append(rel)
    (base / "collection-logs").mkdir(parents=True, exist_ok=True)
    return removed


def _cleanup_root_data_dir() -> list[str]:
    """data/ 直下の DB・ログ・収集ログを削除（運用スクリプトは残す）。"""
    base = ROOT / "data"
    removed: list[str] = []
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
        return removed
    for path in list(base.iterdir()):
        if path.name == ".gitkeep":
            continue
        if path.is_dir() and path.name == "collection-logs":
            shutil.rmtree(path)
            removed.append("data/collection-logs/")
            continue
        if path.is_dir():
            continue
        if path.suffix == ".log" or ".db" in path.name:
            path.unlink()
            removed.append(str(path.relative_to(ROOT)))
    (base / "collection-logs").mkdir(parents=True, exist_ok=True)
    return removed


def cleanup_local_data(*, keep_public_site: bool = True) -> list[str]:
    removed = _reset_backend_data_dir() + _cleanup_root_data_dir()

    if not keep_public_site:
        public_site = ROOT / "public_site"
        if public_site.exists():
            shutil.rmtree(public_site)
            removed.append("public_site/")

    return removed


def main() -> None:
    keep_public_site = "--keep-public-site" in sys.argv
    removed = cleanup_local_data(keep_public_site=keep_public_site)
    if removed:
        print("Removed local API data:")
        for item in removed:
            print(f"  - {item}")
    else:
        print("No local API data to remove.")


if __name__ == "__main__":
    main()
