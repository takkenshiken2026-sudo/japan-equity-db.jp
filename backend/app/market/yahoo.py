from __future__ import annotations

import csv
import io
import re
import time
from datetime import datetime
from typing import Any, Optional

MIN_SANE_EPS = 0.01
MAX_SANE_EPS = 100_000.0
MIN_SANE_BPS = 0.01
MAX_SANE_BPS = 1_000_000.0
MIN_SANE_PER = 0.5
MAX_SANE_PER = 500.0
MIN_SANE_PBR = 0.01
MAX_SANE_PBR = 100.0

import httpx

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
USER_AGENT = "Mozilla/5.0 (compatible; KabuCheck/1.0)"


def sec_code_to_ticker(sec_code: Optional[str]) -> Optional[str]:
    if not sec_code:
        return None
    code = sec_code.strip()
    if len(code) < 4:
        return None
    return f"{code[:4]}.T"


def ticker_to_stooq_symbol(ticker: str) -> str:
    return f"{ticker.replace('.T', '').lower()}.jp"


def fetch_price_history(ticker: str, range_: str = "1y") -> list[dict[str, Any]]:
    """Yahoo Chart API から日足終値を取得。"""
    url = YAHOO_CHART_URL.format(ticker=ticker)
    params = {"interval": "1d", "range": range_}
    headers = {"User-Agent": USER_AGENT}
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        closes = (result.get("indicators") or {}).get("quote", [{}])[0].get("close") or []
        out: list[dict[str, Any]] = []
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            out.append({"date": datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"), "close": float(close)})
        return out
    except Exception:
        return []


def fetch_quote(
    ticker: str,
    sleep_seconds: float = 0.5,
    max_retries: int = 3,
    *,
    fast: bool = False,
) -> dict[str, Any]:
    if fast:
        return fetch_quote_fast(ticker, max_retries=max_retries)

    time.sleep(sleep_seconds)
    quote: dict[str, Any] = {
        "ticker": ticker,
        "price": None,
        "market_cap": None,
        "per": None,
        "pbr": None,
        "dividend_yield": None,
        "fifty_two_week_high": None,
        "fifty_two_week_low": None,
    }

    quote["price"] = _fetch_price_yahoo_chart(ticker, max_retries=max_retries)
    if quote["price"] is None:
        quote["price"] = _fetch_price_stooq(ticker_to_stooq_symbol(ticker))

    yahoo = _fetch_yahoo_enrichment(ticker, max_retries=max_retries)
    for key, value in yahoo.items():
        if value is not None:
            quote[key] = value
    if quote["price"] is None and yahoo.get("price") is not None:
        quote["price"] = yahoo["price"]

    if quote["price"] is None:
        raise RuntimeError(f"Price unavailable for {ticker}")

    return quote


def fetch_quote_fast(ticker: str, max_retries: int = 2) -> dict[str, Any]:
    """Yahoo Chart API のみ（sleep/yfinance なし）で高速取得。"""
    meta = _fetch_chart_meta(ticker, max_retries=max_retries)
    price = None
    if meta:
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        if price is not None:
            price = float(price)

    if price is None:
        price = _fetch_price_stooq(ticker_to_stooq_symbol(ticker))

    if price is None:
        raise RuntimeError(f"Price unavailable for {ticker}")

    return {
        "ticker": ticker,
        "price": price,
        "market_cap": _first_float(meta or {}, "marketCap"),
        "per": None,
        "pbr": None,
        "dividend_yield": None,
        "fifty_two_week_high": _first_float(meta or {}, "fiftyTwoWeekHigh"),
        "fifty_two_week_low": _first_float(meta or {}, "fiftyTwoWeekLow"),
    }


def _fetch_chart_meta(ticker: str, max_retries: int = 2) -> Optional[dict[str, Any]]:
    url = YAHOO_CHART_URL.format(ticker=ticker)
    params = {"interval": "1d", "range": "5d"}
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                response = client.get(url, params=params, headers=headers)
                response.raise_for_status()
            payload = response.json()
            result = payload["chart"]["result"][0]
            return result.get("meta") or {}
        except Exception:
            if attempt + 1 < max_retries:
                time.sleep(0.2 * (attempt + 1))
    return None


def _fetch_price_yahoo_chart(ticker: str, max_retries: int = 3) -> Optional[float]:
    meta = _fetch_chart_meta(ticker, max_retries=max_retries)
    if not meta:
        return None
    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    return float(price) if price is not None else None


def _fetch_price_stooq(symbol: str) -> Optional[float]:
    url = "https://stooq.pl/q/l/"
    params = {"s": symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"}
    headers = {"User-Agent": USER_AGENT}
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url, params=params, headers=headers)
            if response.status_code != 200:
                return None
        rows = list(csv.reader(io.StringIO(response.text)))
        if len(rows) < 2 or len(rows[1]) < 7:
            return None
        return float(rows[1][6])
    except Exception:
        return None


def _fetch_yahoo_enrichment(ticker: str, max_retries: int = 2) -> dict[str, Any]:
    if yf is None:
        return {}
    for attempt in range(max_retries):
        try:
            return _fetch_yahoo_once(ticker)
        except Exception as exc:
            if "Rate" in type(exc).__name__:
                time.sleep(2.0 * (attempt + 1))
                continue
            return {}
    return {}


def _fetch_yahoo_once(ticker: str) -> dict[str, Any]:
    stock = yf.Ticker(ticker)
    info: dict[str, Any] = {}
    try:
        info = stock.info or {}
    except Exception:
        info = {}

    price = _first_float(info, "currentPrice", "regularMarketPrice", "previousClose")
    return {
        "price": price,
        "market_cap": _first_float(info, "marketCap"),
        "per": _first_float(info, "trailingPE", "forwardPE"),
        "pbr": _first_float(info, "priceToBook"),
        "dividend_yield": _first_float(info, "dividendYield"),
        "fifty_two_week_high": _first_float(info, "fiftyTwoWeekHigh"),
        "fifty_two_week_low": _first_float(info, "fiftyTwoWeekLow"),
    }


def is_sane_eps(eps: Optional[float]) -> bool:
    return eps is not None and MIN_SANE_EPS < eps <= MAX_SANE_EPS


def is_sane_bps(bps: Optional[float]) -> bool:
    return bps is not None and MIN_SANE_BPS < bps <= MAX_SANE_BPS


def is_sane_per(per: Optional[float]) -> bool:
    return per is not None and MIN_SANE_PER <= per <= MAX_SANE_PER


def is_sane_pbr(pbr: Optional[float]) -> bool:
    return pbr is not None and MIN_SANE_PBR <= pbr <= MAX_SANE_PBR


def parse_issued_shares_text(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    cleaned = text.replace(" ", "").replace("　", "")
    match = re.search(r"([\d,]+)", cleaned)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    if value <= 0:
        return None
    if "千株" in cleaned:
        return value * 1000
    return value


def estimate_shares_outstanding(
    *,
    eps: Optional[float] = None,
    bps: Optional[float] = None,
    net_income: Optional[float] = None,
    net_assets: Optional[float] = None,
    issued_shares_text: Optional[str] = None,
) -> Optional[float]:
    shares = parse_issued_shares_text(issued_shares_text)
    if shares and shares >= 1_000:
        return shares
    if is_sane_eps(eps) and net_income and net_income > 0:
        estimated = net_income / eps
        if estimated >= 1_000:
            return estimated
    if is_sane_bps(bps) and net_assets and net_assets > 0:
        estimated = net_assets / bps
        if estimated >= 1_000:
            return estimated
    return None


def calc_market_cap(
    price: Optional[float],
    shares_outstanding: Optional[float],
    *,
    yahoo_market_cap: Optional[float] = None,
) -> Optional[float]:
    if yahoo_market_cap and yahoo_market_cap > 0:
        return yahoo_market_cap
    if price and shares_outstanding and price > 0 and shares_outstanding > 0:
        return price * shares_outstanding
    return None


def calc_valuation_from_financials(
    price: Optional[float],
    eps: Optional[float],
    bps: Optional[float],
    net_assets: Optional[float],
    shares_outstanding: Optional[float] = None,
) -> dict[str, Optional[float]]:
    per = None
    pbr = None
    sane_eps = eps if is_sane_eps(eps) else None
    sane_bps = bps if is_sane_bps(bps) else None
    if price and sane_eps:
        per = price / sane_eps
        if not is_sane_per(per):
            per = None
    if price and sane_bps:
        pbr = price / sane_bps
        if not is_sane_pbr(pbr):
            pbr = None
    elif price and net_assets and shares_outstanding and shares_outstanding > 0:
        implied_bps = net_assets / shares_outstanding
        if is_sane_bps(implied_bps):
            pbr = price / implied_bps
            if not is_sane_pbr(pbr):
                pbr = None
    return {"per_edinet": per, "pbr_edinet": pbr}


def _first_float(source: Any, *keys: str) -> Optional[float]:
    for key in keys:
        value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
