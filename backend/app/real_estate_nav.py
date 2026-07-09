from __future__ import annotations

MAX_SANE_NAV_RATIO = 5.0
MIN_SANE_MARKET_CAP = 1_000_000_000  # 10億円未満の時価総額はNAV算出から除外


def book_m_to_yen(book_m: float, total_assets: float | None = None) -> float | None:
    """Convert stored book total (field suffix _m) to yen with unit sanity check."""
    if book_m <= 0:
        return None
    book_yen = book_m * 1_000_000
    if total_assets and total_assets > 0 and book_yen > total_assets * 1.5:
        alt_yen = book_m * 1_000
        if alt_yen <= total_assets * 1.5:
            book_yen = alt_yen
        else:
            return None
    return book_yen


def compute_nav_ratio(
    book_m: float | None,
    market_cap: float | None,
    *,
    total_assets: float | None = None,
) -> float | None:
    if book_m is None or not market_cap or market_cap < MIN_SANE_MARKET_CAP:
        return None
    book_yen = book_m_to_yen(float(book_m), total_assets)
    if book_yen is None:
        return None
    ratio = book_yen / market_cap
    if ratio <= 0 or ratio > MAX_SANE_NAV_RATIO:
        return None
    return round(ratio, 3)
