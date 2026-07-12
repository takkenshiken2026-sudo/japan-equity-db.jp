"""空売り残高レコードを DB に upsert する。"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collection_log import save_collection_snapshot
from app.db import Company, ShortSellingBalance
from app.short_selling.jpx import ShortBalanceRecord, fetch_latest_short_selling


def _build_code_map(db: Session) -> dict[str, str]:
    """sec_code 先頭4桁 -> edinet_code の対応表を作る。"""
    rows = db.execute(
        select(Company.sec_code, Company.edinet_code).where(Company.sec_code.is_not(None))
    ).all()
    mapping: dict[str, str] = {}
    for sec_code, edinet_code in rows:
        if not sec_code:
            continue
        key = str(sec_code).strip()[:4]
        if key and key not in mapping:
            mapping[key] = edinet_code
    return mapping


def persist_short_selling(
    db: Session, records: Iterable[ShortBalanceRecord]
) -> dict[str, int]:
    code_map = _build_code_map(db)
    stats = {"processed": 0, "inserted": 0, "updated": 0}
    now = datetime.utcnow()

    for rec in records:
        stats["processed"] += 1
        edinet_code = code_map.get(rec.sec_code)
        existing = db.scalar(
            select(ShortSellingBalance).where(
                ShortSellingBalance.sec_code == rec.sec_code,
                ShortSellingBalance.holder_name == rec.holder_name,
                ShortSellingBalance.calc_date == rec.calc_date,
            )
        )
        if existing is None:
            db.add(
                ShortSellingBalance(
                    sec_code=rec.sec_code,
                    edinet_code=edinet_code,
                    company_name=rec.company_name,
                    holder_name=rec.holder_name,
                    short_ratio=rec.short_ratio,
                    short_shares=rec.short_shares,
                    prev_ratio=rec.prev_ratio,
                    prev_calc_date=rec.prev_calc_date,
                    calc_date=rec.calc_date,
                    published_date=rec.published_date,
                    first_seen_at=now,
                    updated_at=now,
                )
            )
            stats["inserted"] += 1
        else:
            changed = False
            for attr in ("short_ratio", "short_shares", "prev_ratio", "prev_calc_date", "company_name"):
                new_val = getattr(rec, attr)
                if new_val is not None and getattr(existing, attr) != new_val:
                    setattr(existing, attr, new_val)
                    changed = True
            if edinet_code and existing.edinet_code != edinet_code:
                existing.edinet_code = edinet_code
                changed = True
            if changed:
                existing.updated_at = now
                stats["updated"] += 1
    db.commit()
    return stats


def collect_short_selling(
    db: Session,
    *,
    file_url: Optional[str] = None,
    log_snapshot: bool = True,
) -> dict[str, int]:
    """公表ページから最新の空売り残高を取得して DB に保存する。"""
    records, meta = fetch_latest_short_selling(file_url=file_url)
    stats = persist_short_selling(db, records)
    stats["fetched"] = len(records)
    # 収集ゼロ時の原因究明用に診断情報を残す（CIログで確認）
    stats["diag"] = {
        k: meta.get(k)
        for k in (
            "index_status",
            "index_len",
            "file_url",
            "file_status",
            "file_bytes",
            "error",
            "diag",
        )
        if meta.get(k) is not None
    }
    if log_snapshot:
        save_collection_snapshot(
            source="jpx_short_selling",
            edinet_code=None,
            payload={
                "command": "collect-short-selling",
                "count": len(records),
                "file_url": meta.get("file_url"),
                "published_date": meta.get("published_date"),
                "stats": stats,
                "error": meta.get("error"),
            },
            fresh=len(records) > 0,
            error=meta.get("error"),
        )
    return stats
