"""実 EDINET から有報 type=5 CSV をDLし、新データを抽出する CLI（ネットワーク開放環境用）。

この spike 環境では外部 egress が遮断されており動かない。ローカル or prod の
daily-sync 環境（EDINET へ到達可、EDINET_API_KEY 設定済み）で実行する。

使い方:
    cd backend && source .venv/bin/activate
    python ../tools/spike/run_extract.py --doc-id S100XXXX
    # または証券コードから最新有報を解決
    python ../tools/spike/run_extract.py --sec-code 7203

出力: 抽出結果 JSON を標準出力へ。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from pathlib import Path

# backend/ を import path に追加（app.* を使うため）
BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import edinet_extractors as ex  # noqa: E402


def _read_all_csv_rows(zip_bytes: bytes) -> list[list[str]]:
    """type=5 zip 内の全 CSV 行を連結（asr を優先）。client.parse_financial_csv_zip と同順。"""
    rows: list[list[str]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_files = sorted(
            [n for n in zf.namelist() if n.lower().endswith(".csv")],
            key=lambda n: ("asr" not in n, n),
        )
        for name in csv_files:
            rows.extend(ex.read_csv_rows(zf.read(name)))
    return rows


def resolve_doc_id_from_sec(db, sec_code: str) -> str | None:
    from sqlalchemy import select
    from app.db import Company, Filing

    company = db.scalar(select(Company).where(Company.sec_code.like(f"{sec_code}%")))
    if not company:
        return None
    filing = db.scalar(
        select(Filing)
        .where(
            Filing.edinet_code == company.edinet_code,
            Filing.doc_type_code.in_(("120", "130", "140", "150")),
            Filing.has_csv.is_(True),
        )
        .order_by(Filing.file_date.desc())
    )
    return filing.doc_id if filing else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-id")
    ap.add_argument("--sec-code")
    args = ap.parse_args()

    from app.config import settings
    from app.edinet.client import EdinetClient

    api_key = os.environ.get("EDINET_API_KEY") or getattr(settings, "edinet_api_key", None)
    if not api_key:
        sys.exit("EDINET_API_KEY を設定してください")

    doc_id = args.doc_id
    if not doc_id and args.sec_code:
        from app.db import SessionLocal

        with SessionLocal() as db:
            doc_id = resolve_doc_id_from_sec(db, args.sec_code)
        if not doc_id:
            sys.exit(f"証券コード {args.sec_code} の有報が見つかりません")
    if not doc_id:
        sys.exit("--doc-id か --sec-code を指定してください")

    client = EdinetClient(api_key=api_key)
    zip_bytes = client.download_document(doc_id, doc_type="5")
    rows = _read_all_csv_rows(zip_bytes)
    result = ex.extract_all(rows)
    result["_meta"] = {"doc_id": doc_id, "csv_rows": len(rows)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
