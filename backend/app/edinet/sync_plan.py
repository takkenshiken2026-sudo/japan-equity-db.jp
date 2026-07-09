from __future__ import annotations

from calendar import monthrange
from datetime import date

# 決算期末ごとの有報提出ピーク月
# 3月決算→6月、12月決算→3月、9月決算→12月、6月決算→9月
PEAK_MONTHS = {
    3: (3, 15, 4, 15),   # 12月決算企業の有報
    6: (6, 1, 6, 30),    # 3月決算企業の有報（最大）
    9: (9, 1, 9, 30),    # 6月決算企業の有報
    12: (12, 1, 12, 31), # 9月決算企業の有報
}


def peak_window(year: int, peak_key: int) -> tuple[date, date]:
    start_month, start_day, end_month, end_day = PEAK_MONTHS[peak_key]
    end_day = min(end_day, monthrange(year if end_month >= start_month else year, end_month)[1])
    start = date(year, start_month, start_day)
    end = date(year, end_month, end_day)
    return start, end


def iter_quarterly_windows(years: list[int]) -> list[tuple[date, date, str]]:
    """四半期報告書提出のピーク期間（3月決算企業中心）"""
    ranges = [
        (2, 1, 3, 20),   # Q3
        (5, 10, 6, 20),  # 遅延提出
        (8, 1, 9, 20),   # Q1
        (11, 1, 12, 20), # Q2
    ]
    windows: list[tuple[date, date, str]] = []
    for year in sorted(years):
        for start_m, start_d, end_m, end_d in ranges:
            end_d = min(end_d, monthrange(year, end_m)[1])
            start = date(year, start_m, start_d)
            end = date(year, end_m, end_d)
            windows.append((start, end, f"{year}-Qpeak-{start_m:02d}"))
    return windows


def iter_peak_windows(years: list[int], peak_keys: list[int] | None = None) -> list[tuple[date, date, str]]:
    keys = peak_keys or list(PEAK_MONTHS.keys())
    windows: list[tuple[date, date, str]] = []
    for year in sorted(years):
        for key in keys:
            start, end = peak_window(year, key)
            label = f"{year}-{key:02d}peak"
            windows.append((start, end, label))
    return windows


def estimate_api_calls(
    years: list[int],
    include_financial_download: bool = False,
    listed_yuho_per_year: int = 3800,
) -> dict[str, int]:
    windows = iter_peak_windows(years)
    list_calls = sum((end - start).days + 1 for start, end, _ in windows)
    doc_calls = listed_yuho_per_year * len(years) if include_financial_download else 0
    return {
        "peak_windows": len(windows),
        "list_api_calls": list_calls,
        "document_api_calls": doc_calls,
        "total_api_calls": list_calls + doc_calls,
    }
