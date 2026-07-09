from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal


def checkpoint_sqlite_wal(mode: str = "PASSIVE") -> bool:
    """SQLite WAL をチェックポイント（Docker との DB 不整合防止）。"""
    if "sqlite" not in settings.database_url.lower():
        return False
    allowed = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
    if mode.upper() not in allowed:
        mode = "PASSIVE"
    db = SessionLocal()
    try:
        db.execute(text(f"PRAGMA wal_checkpoint({mode.upper()})"))
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def checkpoint_after_write(db: Session | None = None) -> None:
    """書き込みバッチ後に TRUNCATE チェックポイント。"""
    if db is not None:
        try:
            db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            db.commit()
        except Exception:
            db.rollback()
        return
    checkpoint_sqlite_wal("TRUNCATE")
