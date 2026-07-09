from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Filing, Financial
from app.edinet.client import CURRENT_PARSE_VERSION, EdinetClient, parse_financial_csv_zip
from app.edinet.sync import upsert_financial_from_doc

YUBO_DOC_TYPES = ("120", "130", "140", "150")


def _download_csv(client: EdinetClient, doc_id: str) -> tuple[str, bytes]:
    return doc_id, client.download_document(doc_id, doc_type="5")


def backfill_financials_fast(
    db: Session,
    client: EdinetClient,
    *,
    limit: int = 500,
    workers: int = 12,
) -> dict[str, int]:
    filings = db.scalars(
        select(Filing)
        .outerjoin(Financial, Filing.doc_id == Financial.doc_id)
        .where(
            Filing.doc_type_code.in_(YUBO_DOC_TYPES),
            Filing.has_csv.is_(True),
            Financial.id.is_(None),
        )
        .limit(limit)
    ).all()

    stats = {"processed": len(filings), "financials": 0, "errors": 0}
    if not filings:
        return stats

    filing_by_doc = {f.doc_id: f for f in filings}
    downloaded: dict[str, bytes] = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_download_csv, client, filing.doc_id): filing.doc_id
            for filing in filings
        }
        for future in as_completed(futures):
            doc_id = futures[future]
            try:
                _, content = future.result()
                downloaded[doc_id] = content
            except Exception:
                stats["errors"] += 1

    for doc_id, content in downloaded.items():
        filing = filing_by_doc[doc_id]
        try:
            existing = db.scalar(select(Financial).where(Financial.doc_id == doc_id))
            if (
                existing
                and existing.revenue is not None
                and existing.parse_version >= CURRENT_PARSE_VERSION
            ):
                continue
            metrics = parse_financial_csv_zip(content)
            from app.edinet.client import calc_derived_metrics

            previous_metrics = None
            if filing.period_end:
                prev = db.scalar(
                    select(Financial)
                    .where(
                        Financial.edinet_code == filing.edinet_code,
                        Financial.fiscal_year_end < filing.period_end,
                    )
                    .order_by(Financial.fiscal_year_end.desc())
                    .limit(1)
                )
                if prev:
                    previous_metrics = {
                        "revenue": prev.revenue,
                        "operating_income": prev.operating_income,
                        "net_income": prev.net_income,
                    }
            derived = calc_derived_metrics(metrics, previous_metrics)
            financial = existing or Financial(
                edinet_code=filing.edinet_code, doc_id=filing.doc_id
            )
            financial.fiscal_year_end = filing.period_end
            financial.revenue = metrics.get("revenue")
            financial.operating_income = metrics.get("operating_income")
            financial.ordinary_income = metrics.get("ordinary_income")
            financial.net_income = metrics.get("net_income")
            financial.total_assets = metrics.get("total_assets")
            financial.net_assets = metrics.get("net_assets")
            financial.eps = metrics.get("eps")
            financial.operating_cf = metrics.get("operating_cf")
            financial.investing_cf = metrics.get("investing_cf")
            financial.financing_cf = metrics.get("financing_cf")
            financial.cash_and_deposits = metrics.get("cash_and_deposits")
            financial.interest_bearing_debt = metrics.get("interest_bearing_debt")
            financial.total_liabilities = metrics.get("total_liabilities")
            financial.bps = metrics.get("bps")
            financial.dividend_per_share = metrics.get("dividend_per_share")
            financial.operating_margin = derived.get("operating_margin")
            financial.roe = derived.get("roe")
            financial.roa = derived.get("roa")
            financial.equity_ratio = derived.get("equity_ratio")
            financial.debt_equity_ratio = derived.get("debt_equity_ratio")
            financial.revenue_growth = derived.get("revenue_growth")
            financial.parse_version = CURRENT_PARSE_VERSION
            financial.updated_at = datetime.utcnow()
            db.add(financial)
            stats["financials"] += 1
        except Exception:
            stats["errors"] += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        stats["errors"] += stats["financials"]
        stats["financials"] = 0

    return stats
