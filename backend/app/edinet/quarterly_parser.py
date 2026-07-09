from __future__ import annotations

import csv
import io
import re
import zipfile
from typing import Optional

QUARTERLY_PERIOD_LABELS = {
    "current_cumulative": "当四半期累計期間",
    "prior_year_cumulative": "前年度同四半期累計期間",
    "current_quarter": "当四半期会計期間",
    "prior_year_quarter": "前年度同四半期会計期間",
    "prior_cumulative": "前期",
    "current_instant": "当四半期会計期間末",
    "prior_year_instant": "前年度同四半期会計期間末",
}

REVENUE_ELEMENTS = [
    "jppfs_cor:NetSales",
    "jppfs_cor:OperatingRevenue1",
    "jppfs_cor:OperatingRevenue2",
    "jpcrp_cor:NetSalesSummaryOfBusinessResults",
    "jpcrp_cor:OperatingRevenue1SummaryOfBusinessResults",
    "OperatingRevenuesIFRSKeyFinancialData",
    "TotalNetRevenuesIFRS",
]

METRIC_ELEMENTS: dict[str, list[str]] = {
    "revenue": REVENUE_ELEMENTS,
    "operating_income": [
        "jppfs_cor:OperatingIncome",
        "jpcrp_cor:OperatingIncomeSummaryOfBusinessResults",
        "OperatingProfitLossIFRS",
    ],
    "net_income": [
        "jppfs_cor:ProfitLoss",
        "jpcrp_cor:NetIncomeLossSummaryOfBusinessResults",
        "ProfitLossAttributableToOwnersOfParentIFRS",
        "ProfitLossIFRS",
    ],
    "eps": [
        "jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults",
        "jpcrp_cor:NetIncomeLossPerShareSummaryOfBusinessResults",
    ],
    "operating_cf": [
        "jppfs_cor:NetCashProvidedByUsedInOperatingActivities",
    ],
}

CURRENT_PARSE_VERSION = 3


def _element_matches(cell: str, element_id: str) -> bool:
    if not cell or not element_id:
        return False
    if cell == element_id:
        return True
    needle = element_id.split(":")[-1]
    return cell.endswith(f":{needle}") or cell.split(":")[-1] == needle


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").replace("△", "-").replace("－", "-").replace("―", "-").strip().strip('"')
    if cleaned in {"", "-", "－", "―"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _growth_rate(current: float | None, prior: float | None) -> float | None:
    if current is None or prior in (None, 0):
        return None
    return (current - prior) / prior


def _read_csv_rows(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-16")), delimiter="\t"))


def _pick_value(rows: list[list[str]], element_ids: list[str], period_label: str) -> float | None:
    for element_id in element_ids:
        for row in rows[1:]:
            if len(row) < 9:
                continue
            if not _element_matches(row[0], element_id):
                continue
            if row[3] != period_label:
                continue
            value = _to_float(row[8])
            if value is not None:
                return value
    return None


def parse_quarter_number(doc_description: str | None, period_start: str | None, period_end: str | None) -> int | None:
    if doc_description:
        match = re.search(r"第([1-4１-４])四半期", doc_description)
        if match:
            return int(match.group(1).translate(str.maketrans("１２３４", "1234")))
    if period_end:
        month = int(period_end[5:7])
        # 決算月からの累積四半期（近似）
        return ((month - 1) // 3 % 4) + 1
    return None


def parse_quarterly_csv_zip(content: bytes) -> dict[str, float | None]:
    metrics: dict[str, float | None] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_files = sorted(
            [name for name in zf.namelist() if name.lower().endswith(".csv")],
            key=lambda name: ("q" not in name.lower(), name),
        )
        rows: list[list[str]] = []
        for csv_name in csv_files:
            if "jpcrp" in csv_name or "q" in csv_name.lower():
                rows = _read_csv_rows(zf.read(csv_name))
                if any("四半期" in (row[3] if len(row) > 3 else "") for row in rows[1:6]):
                    break

        for metric, elements in METRIC_ELEMENTS.items():
            for period_key, period_label in QUARTERLY_PERIOD_LABELS.items():
                field = f"{metric}_{period_key}"
                if field not in metrics:
                    metrics[field] = _pick_value(rows, elements, period_label)

    return metrics


def calc_quarterly_yoy(metrics: dict[str, float | None]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for metric in ("revenue", "operating_income", "net_income", "eps"):
        current = metrics.get(f"{metric}_current_quarter")
        prior_year = metrics.get(f"{metric}_prior_year_quarter")
        if current is None:
            current = metrics.get(f"{metric}_current_cumulative")
            prior_year = metrics.get(f"{metric}_prior_year_cumulative")
        result[f"{metric}_yoy"] = _growth_rate(current, prior_year)
    return result


def derive_single_quarter_from_cumulative(
    current_cumulative: float | None,
    prior_cumulative: float | None,
) -> float | None:
    if current_cumulative is None or prior_cumulative is None:
        return None
    return current_cumulative - prior_cumulative


def calc_quarterly_qoq(
    current_single: float | None,
    prior_single: float | None,
) -> float | None:
    return _growth_rate(current_single, prior_single)
