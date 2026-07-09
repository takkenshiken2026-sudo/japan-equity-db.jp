from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import date, timedelta
from typing import Any, Optional

import httpx

from app.config import settings

EDINET_CODE_LIST_URL = (
    "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
)

# 有価証券報告書・四半期・半期
TARGET_DOC_TYPES = {"120", "130", "140", "150", "160", "170"}


class EdinetClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        sleep_seconds: float = 1.0,
        max_retries: int = 5,
    ) -> None:
        self.api_key = api_key or settings.edinet_api_key
        self.base_url = settings.edinet_base_url
        self.sleep_seconds = sleep_seconds
        self.max_retries = max_retries

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> httpx.Response:
        params = dict(params or {})
        params["Subscription-Key"] = self.api_key
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            if attempt > 0:
                time.sleep(self.sleep_seconds * (2 ** attempt))
            else:
                time.sleep(self.sleep_seconds)

            try:
                with httpx.Client(timeout=120.0) as client:
                    response = client.get(f"{self.base_url}{path}", params=params)
                    if response.status_code == 429:
                        last_error = httpx.HTTPStatusError(
                            "429 Too Many Requests",
                            request=response.request,
                            response=response,
                        )
                        continue
                    response.raise_for_status()
                    return response
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 429:
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("EDINET API request failed")

    def list_documents(self, file_date: str, include_results: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {"date": file_date}
        if include_results:
            params["type"] = "2"
        return self._get("/documents.json", params).json()

    def download_document(self, doc_id: str, doc_type: str = "5") -> bytes:
        response = self._get(f"/documents/{doc_id}", {"type": doc_type})
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            raise ValueError(response.json())
        return response.content

    def download_code_list(self) -> bytes:
        with httpx.Client(timeout=120.0) as client:
            response = client.get(EDINET_CODE_LIST_URL)
            response.raise_for_status()
            return response.content

    def iter_dates(self, start: date, end: date):
        current = start
        while current <= end:
            yield current.isoformat()
            current += timedelta(days=1)


def parse_code_list_zip(content: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        raw = zf.read(csv_name)

    text = raw.decode("cp932", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    headers = rows[1]
    records: list[dict[str, str]] = []
    for row in rows[2:]:
        if len(row) < len(headers):
            continue
        records.append(dict(zip(headers, row)))
    return records


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").replace("△", "-").strip().strip('"')
    if cleaned in {"", "-", "―", "－"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


FINANCIAL_ELEMENT_MAP: dict[str, list[str]] = {
    "revenue": [
        "jpcrp_cor:NetSalesSummaryOfBusinessResults",
        "jppfs_cor:NetSales",
        "OperatingRevenuesIFRSKeyFinancialData",
        "TotalNetRevenuesIFRS",
    ],
    "operating_income": [
        "jppfs_cor:OperatingIncome",
        "jpcrp_cor:OperatingIncomeSummaryOfBusinessResults",
        "OperatingProfitLossIFRS",
    ],
    "ordinary_income": ["jppfs_cor:OrdinaryIncome"],
    "net_income": [
        "jpcrp_cor:NetIncomeLossSummaryOfBusinessResults",
        "jppfs_cor:ProfitLoss",
        "ProfitLossAttributableToOwnersOfParentIFRS",
        "ProfitLossIFRS",
    ],
    "total_assets": [
        "jpcrp_cor:TotalAssetsSummaryOfBusinessResults",
        "jppfs_cor:Assets",
    ],
    "net_assets": [
        "jpcrp_cor:NetAssetsSummaryOfBusinessResults",
        "jppfs_cor:NetAssets",
    ],
    "eps": [
        "jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults",
        "jpcrp_cor:NetIncomeLossPerShareSummaryOfBusinessResults",
    ],
    "operating_cf": [
        "jppfs_cor:NetCashProvidedByUsedInOperatingActivities",
        "jppfs_cor:CashFlowsFromUsedInOperatingActivities",
        "jpcrp_cor:CashFlowsFromUsedInOperatingActivitiesIFRSSummaryOfBusinessResults",
        "jpigp_cor:NetCashProvidedByUsedInOperatingActivitiesIFRS",
    ],
    "investing_cf": [
        "jppfs_cor:NetCashProvidedByUsedInInvestingActivities",
        "jppfs_cor:CashFlowsFromUsedInInvestingActivities",
    ],
    "financing_cf": [
        "jppfs_cor:NetCashProvidedByUsedInFinancingActivities",
        "jppfs_cor:CashFlowsFromUsedInFinancingActivities",
    ],
    "cash_and_deposits": [
        "jppfs_cor:CashAndDeposits",
        "jpcrp_cor:CashAndDepositsSummaryOfBusinessResults",
    ],
    "interest_bearing_debt": [
        "jppfs_cor:InterestBearingDebt",
        "jpcrp_cor:InterestBearingLiabilitiesSummaryOfBusinessResults",
    ],
    "total_liabilities": [
        "jppfs_cor:Liabilities",
        "jpcrp_cor:TotalLiabilitiesSummaryOfBusinessResults",
    ],
    "bps": [
        "jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults",
        "jpcrp_cor:EquityPerShareSummaryOfBusinessResults",
    ],
    "dividend_per_share": [
        "jpcrp_cor:AnnualDividendPerShareSummaryOfBusinessResults",
        "jpcrp_cor:CashDividendsPaidPerShareSummaryOfBusinessResults",
        "jpcrp_cor:DividendPaidPerShareSummaryOfBusinessResults",
    ],
}

PERIOD_FILTERS: dict[str, set[str]] = {
    "revenue": {"当期"},
    "operating_income": {"当期"},
    "ordinary_income": {"当期"},
    "net_income": {"当期"},
    "total_assets": {"当期末"},
    "net_assets": {"当期末"},
    "eps": {"当期"},
    "operating_cf": {"当期"},
    "investing_cf": {"当期"},
    "financing_cf": {"当期"},
    "cash_and_deposits": {"当期末"},
    "interest_bearing_debt": {"当期末"},
    "total_liabilities": {"当期末"},
    "bps": {"当期末"},
    "dividend_per_share": {"当期"},
}

CURRENT_PARSE_VERSION = 4


def _element_matches(cell: str, element_id: str) -> bool:
    if not cell or not element_id:
        return False
    if cell == element_id:
        return True
    needle = element_id.split(":")[-1]
    return cell.endswith(f":{needle}") or cell.split(":")[-1] == needle


def _read_csv_rows(content: bytes) -> list[list[str]]:
    text = content.decode("utf-16")
    return list(csv.reader(io.StringIO(text), delimiter="\t"))


def _pick_value_from_rows(rows: list[list[str]], key: str) -> float | None:
    element_ids = FINANCIAL_ELEMENT_MAP[key]
    period_ok = PERIOD_FILTERS.get(key, {"当期"})

    for element_id in element_ids:
        for row in rows[1:]:
            if len(row) < 9:
                continue
            if not _element_matches(row[0], element_id):
                continue
            relative_period = row[3]
            if relative_period not in period_ok:
                continue
            value = _to_float(row[8])
            if value is not None:
                return value
    return None


def parse_financial_csv_zip(content: bytes) -> dict[str, float | None]:
    metrics: dict[str, float | None] = {key: None for key in FINANCIAL_ELEMENT_MAP}
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_files = sorted(
            [name for name in zf.namelist() if name.lower().endswith(".csv")],
            key=lambda name: ("asr" not in name, name),
        )
        for csv_name in csv_files:
            rows = _read_csv_rows(zf.read(csv_name))
            for key in metrics:
                if metrics[key] is None:
                    metrics[key] = _pick_value_from_rows(rows, key)
    return metrics


def calc_derived_metrics(
    current: dict[str, float | None],
    previous: dict[str, float | None] | None = None,
) -> dict[str, float | None]:
    revenue = current.get("revenue")
    operating_income = current.get("operating_income")
    net_income = current.get("net_income")
    net_assets = current.get("net_assets")
    total_assets = current.get("total_assets")
    interest_bearing_debt = current.get("interest_bearing_debt")

    operating_margin = None
    if revenue and operating_income is not None and revenue != 0:
        operating_margin = operating_income / revenue

    roe = None
    if net_assets and net_income is not None and net_assets != 0:
        roe = net_income / net_assets

    roa = None
    if total_assets and net_income is not None and total_assets != 0:
        roa = net_income / total_assets

    equity_ratio = None
    if total_assets and net_assets is not None and total_assets != 0:
        equity_ratio = net_assets / total_assets

    debt_equity_ratio = None
    if net_assets and interest_bearing_debt is not None and net_assets != 0:
        debt_equity_ratio = interest_bearing_debt / net_assets

    revenue_growth = None
    if previous and revenue and previous.get("revenue") not in (None, 0):
        prev_revenue = previous["revenue"]
        if prev_revenue:
            revenue_growth = (revenue - prev_revenue) / prev_revenue

    return {
        "operating_margin": operating_margin,
        "roe": roe,
        "roa": roa,
        "equity_ratio": equity_ratio,
        "debt_equity_ratio": debt_equity_ratio,
        "revenue_growth": revenue_growth,
    }
