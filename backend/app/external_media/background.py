from __future__ import annotations

from app.db import Company, ExternalMediaSync, SessionLocal
from app.external_media.collector import refresh_company_news, refresh_company_trend
from app.external_media.store import should_refresh_news, should_refresh_trend


def run_news_refresh(edinet_code: str) -> None:
    db = SessionLocal()
    try:
        company = db.get(Company, edinet_code)
        if not company:
            return
        refresh_company_news(db, company, log_delta=True)
    except Exception:
        pass
    finally:
        db.close()


def run_trend_refresh(edinet_code: str, *, days: int = 90) -> None:
    db = SessionLocal()
    try:
        company = db.get(Company, edinet_code)
        if not company:
            return
        refresh_company_trend(db, company, days=days, log_delta=True)
    except Exception:
        pass
    finally:
        db.close()


def needs_background_news_refresh(sync_row: ExternalMediaSync | None, *, has_data: bool) -> bool:
    if not has_data:
        return True
    return should_refresh_news(sync_row)


def needs_background_trend_refresh(sync_row: ExternalMediaSync | None, *, has_data: bool) -> bool:
    if not has_data:
        return True
    return should_refresh_trend(sync_row)
