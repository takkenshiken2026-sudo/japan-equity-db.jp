from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Company, Filing, Financial
from app.edinet.client import (
    CURRENT_PARSE_VERSION,
    TARGET_DOC_TYPES,
    EdinetClient,
    calc_derived_metrics,
    parse_code_list_zip,
    parse_financial_csv_zip,
)


def sync_companies(db: Session, client: EdinetClient) -> int:
    content = client.download_code_list()
    records = parse_code_list_zip(content)
    count = 0
    for record in records:
        edinet_code = record.get("ＥＤＩＮＥＴコード") or record.get("EDINETコード")
        if not edinet_code:
            continue
        company = db.get(Company, edinet_code) or Company(edinet_code=edinet_code)
        company.name = record.get("提出者名", "")
        company.name_en = record.get("提出者名（英字）")
        company.sec_code = record.get("証券コード") or None
        company.jcn = record.get("提出者法人番号") or None
        company.listing_status = record.get("上場区分") or None
        company.industry = record.get("提出者業種") or None
        company.submitter_type = record.get("提出者種別") or None
        company.location = record.get("所在地") or None
        company.fiscal_year_end = record.get("決算日") or None
        company.updated_at = datetime.utcnow()
        db.add(company)
        count += 1
    db.commit()
    return count


def sync_filings_for_range(
    db: Session,
    client: EdinetClient,
    start: date,
    end: date,
    fetch_financials: bool = True,
    yuho_only: bool = False,
) -> dict[str, int]:
    stats = {"dates": 0, "filings": 0, "financials": 0, "errors": 0}

    for file_date in client.iter_dates(start, end):
        stats["dates"] += 1
        company_cache: dict[str, Company] = {}
        filing_cache: dict[str, Filing] = {}
        try:
            payload = client.list_documents(file_date, include_results=True)
        except Exception:
            stats["errors"] += 1
            continue

        for item in payload.get("results", []):
            doc_type = item.get("docTypeCode")
            allowed_types = {"120", "130"} if yuho_only else TARGET_DOC_TYPES
            if doc_type not in allowed_types:
                continue
            if item.get("csvFlag") != "1" and item.get("xbrlFlag") != "1":
                continue
            if item.get("withdrawalStatus", "0") != "0":
                continue
            if item.get("disclosureStatus", "0") == "2":
                continue

            edinet_code = item.get("edinetCode")
            doc_id = item.get("docID")
            if not edinet_code or not doc_id:
                continue

            company = company_cache.get(edinet_code) or db.get(Company, edinet_code)
            if company is None:
                company = Company(
                    edinet_code=edinet_code,
                    name=item.get("filerName") or edinet_code,
                    sec_code=item.get("secCode"),
                    jcn=item.get("JCN"),
                )
                db.add(company)
            elif not company.name and item.get("filerName"):
                company.name = item.get("filerName")
            company_cache[edinet_code] = company

            filing = filing_cache.get(doc_id) or db.get(Filing, doc_id)
            is_new_filing = filing is None
            if filing is None:
                filing = Filing(doc_id=doc_id, edinet_code=edinet_code)
                db.add(filing)
            filing.edinet_code = edinet_code
            filing.doc_type_code = doc_type
            filing.doc_description = item.get("docDescription")
            filing.period_start = item.get("periodStart")
            filing.period_end = item.get("periodEnd")
            filing.submit_date_time = item.get("submitDateTime")
            filing.file_date = file_date
            filing.has_xbrl = item.get("xbrlFlag") == "1"
            filing.has_pdf = item.get("pdfFlag") == "1"
            filing.has_csv = item.get("csvFlag") == "1"
            filing_cache[doc_id] = filing
            if is_new_filing:
                stats["filings"] += 1

            if fetch_financials and filing.has_csv and doc_type in {"120", "130", "140", "150"}:
                try:
                    if upsert_financial_from_doc(db, client, filing):
                        stats["financials"] += 1
                except Exception:
                    stats["errors"] += 1

        try:
            db.commit()
        except Exception:
            db.rollback()
            stats["errors"] += 1

    return stats


def upsert_financial_from_doc(db: Session, client: EdinetClient, filing: Filing) -> bool:
    existing = db.scalar(select(Financial).where(Financial.doc_id == filing.doc_id))
    if (
        existing
        and existing.revenue is not None
        and existing.parse_version >= CURRENT_PARSE_VERSION
    ):
        return False

    content = client.download_document(filing.doc_id, doc_type="5")
    metrics = parse_financial_csv_zip(content)

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
    financial = existing or Financial(edinet_code=filing.edinet_code, doc_id=filing.doc_id)
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
    return True
