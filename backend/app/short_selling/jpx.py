"""JPX「空売り残高に関する情報」の取得・パース。

公式 API は提供されておらず、公表ページの Excel/CSV から個別ポジション
（空売り残高割合0.5%以上）を抽出する。ページの HTML 構造やファイル名は
将来変わり得るため、取得元は設定で上書きでき、列はヘッダー文字列の
あいまい一致で解決する。fetch と parse を分離してパースは単体テスト可能。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
import pandas as pd

INDEX_URL = "https://www.jpx.co.jp/markets/statistics-equities/short-selling/index.html"
BASE_URL = "https://www.jpx.co.jp"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ページ内の全リンクと、表計算ファイル（.xls/.xlsx/.csv）リンクを検出する
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_SHEET_RE = re.compile(r"\.(xlsx?|csv)(?:[?#]|$)", re.IGNORECASE)
_DATE_IN_NAME_RE = re.compile(r"(20\d{2})[-_/]?(\d{2})[-_/]?(\d{2})")

# 論理フィールド -> ヘッダーに含まれ得るキーワード（部分一致）
_COLUMN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "calc_date": ("計算年月日", "算定日", "計算日"),
    "sec_code": ("コード", "銘柄コード"),
    "company_name": ("銘柄名", "銘柄名称", "発行会社"),
    "holder_name": ("商号", "名称", "氏名", "報告義務者"),
    "short_ratio": ("空売り残高割合", "残高割合"),
    "short_shares": ("空売り残高数量", "残高数量"),
    "prev_ratio": ("直近", "前回"),
    "prev_calc_date": ("直近計算年月日", "前回計算年月日"),
}


@dataclass
class ShortBalanceRecord:
    sec_code: str
    company_name: Optional[str]
    holder_name: str
    short_ratio: Optional[float]
    short_shares: Optional[float]
    prev_ratio: Optional[float]
    prev_calc_date: Optional[str]
    calc_date: str
    published_date: Optional[str] = None


def _normalize_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat"):
        return None
    text = text.replace("年", "/").replace("月", "/").replace("日", "")
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", text)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def _normalize_code(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    # "7203" / "7203.0" / "72030" などを 4 桁（or 英数コード）に正規化
    text = re.sub(r"\.0$", "", text)
    digits = re.sub(r"\s+", "", text)
    if len(digits) >= 4:
        return digits[:4]
    return digits or None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text.lower() in ("nan", "-", "―", "－"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read_frame(content: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith(".csv"):
        for encoding in ("cp932", "utf-8-sig", "utf-8"):
            try:
                return pd.read_csv(io.BytesIO(content), header=None, dtype=str, encoding=encoding)
            except (UnicodeDecodeError, pd.errors.ParserError):
                continue
        return pd.read_csv(io.BytesIO(content), header=None, dtype=str, encoding="cp932", errors="replace")
    # xls / xlsx は pandas がエンジンを自動選択（openpyxl / xlrd）
    return pd.read_excel(io.BytesIO(content), header=None, dtype=object)


def _find_header_row(frame: pd.DataFrame) -> Optional[int]:
    """先頭数行から「計算年月日」「空売り残高割合」等を含む見出し行を特定。"""
    for idx in range(min(15, len(frame))):
        cells = [str(v) for v in frame.iloc[idx].tolist()]
        joined = " ".join(cells)
        if any(k in joined for k in _COLUMN_KEYWORDS["calc_date"]) and (
            any(k in joined for k in _COLUMN_KEYWORDS["short_ratio"])
            or any(k in joined for k in _COLUMN_KEYWORDS["holder_name"])
        ):
            return idx
    return None


def _resolve_columns(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for field, keywords in _COLUMN_KEYWORDS.items():
        for col_idx, header in enumerate(headers):
            if col_idx in mapping.values():
                continue
            if any(k in header for k in keywords):
                mapping[field] = col_idx
                break
    return mapping


def parse_short_selling_frame(
    frame: pd.DataFrame, *, published_date: Optional[str] = None
) -> list[ShortBalanceRecord]:
    """ヘッダーなしで読み込んだ表から空売り残高レコードを抽出する（純粋関数）。"""
    header_row = _find_header_row(frame)
    if header_row is None:
        return []
    headers = [str(v).replace("\n", "").strip() for v in frame.iloc[header_row].tolist()]
    cols = _resolve_columns(headers)
    if "sec_code" not in cols or "holder_name" not in cols or "calc_date" not in cols:
        return []

    records: list[ShortBalanceRecord] = []
    for _, row in frame.iloc[header_row + 1 :].iterrows():
        values = row.tolist()

        def cell(field: str) -> Any:
            idx = cols.get(field)
            if idx is None or idx >= len(values):
                return None
            return values[idx]

        sec_code = _normalize_code(cell("sec_code"))
        calc_date = _normalize_date(cell("calc_date"))
        holder = cell("holder_name")
        holder_name = str(holder).strip() if holder is not None else ""
        if not sec_code or not calc_date or not holder_name or holder_name.lower() == "nan":
            continue

        records.append(
            ShortBalanceRecord(
                sec_code=sec_code,
                company_name=(str(cell("company_name")).strip() or None)
                if cell("company_name") is not None
                else None,
                holder_name=holder_name[:255],
                short_ratio=_to_float(cell("short_ratio")),
                short_shares=_to_float(cell("short_shares")),
                prev_ratio=_to_float(cell("prev_ratio")),
                prev_calc_date=_normalize_date(cell("prev_calc_date")),
                calc_date=calc_date,
                published_date=published_date,
            )
        )
    return records


def parse_short_selling_bytes(
    content: bytes, filename: str, *, published_date: Optional[str] = None
) -> list[ShortBalanceRecord]:
    frame = _read_frame(content, filename)
    return parse_short_selling_frame(frame, published_date=published_date)


def _file_date(href: str) -> Optional[str]:
    m = _DATE_IN_NAME_RE.search(href)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{mo}-{d}"


def _extract_latest_file_url(
    index_html: str, base_url: str = INDEX_URL
) -> tuple[Optional[tuple[str, Optional[str]]], dict[str, Any]]:
    """公表ページ HTML から最新データファイルの URL と日付を推定する。

    戻り値は ((url, 日付) | None, 診断情報)。表計算リンクを幅広く検出し、
    空売り関連パス・日付付きを優先する。相対 URL は base_url で解決する。
    """
    hrefs = _HREF_RE.findall(index_html)
    sheets = [h for h in hrefs if _SHEET_RE.search(h)]
    diag: dict[str, Any] = {
        "total_links": len(hrefs),
        "sheet_links": len(sheets),
        "sheet_sample": sheets[:10],
        "href_sample": [] if sheets else hrefs[:15],
    }
    if not sheets:
        return None, diag

    scored: list[tuple[int, str, str, Optional[str]]] = []
    for href in sheets:
        url = urljoin(base_url, href)
        fdate = _file_date(href)
        priority = 1 if "short" in href.lower() else 0
        scored.append((priority, fdate or "", url, fdate))
    # 空売り関連パス優先 → 日付が新しい順
    scored.sort(key=lambda c: (c[0], c[1]), reverse=True)
    best = scored[0]
    return (best[2], best[3]), diag


def fetch_latest_short_selling(
    *,
    index_url: str = INDEX_URL,
    file_url: Optional[str] = None,
    timeout: float = 30.0,
) -> tuple[list[ShortBalanceRecord], dict[str, Any]]:
    """公表ページから最新の空売り残高ファイルを取得してパースする。

    file_url を指定すればページ探索を省略して直接そのファイルを取得する。
    戻り値は (レコード一覧, メタ情報)。meta には診断情報を含む。
    """
    headers = {
        "User-Agent": USER_AGENT,
        "accept-language": "ja-JP,ja;q=0.9,en;q=0.8",
    }
    meta: dict[str, Any] = {"index_url": index_url}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        published_date = None
        if not file_url:
            resp = client.get(index_url)
            meta["index_status"] = resp.status_code
            resp.raise_for_status()
            meta["index_len"] = len(resp.text)
            found, diag = _extract_latest_file_url(resp.text, str(resp.url))
            meta["diag"] = diag
            if not found:
                meta["error"] = "no_data_link_found"
                return [], meta
            file_url, published_date = found
        meta["file_url"] = file_url
        meta["published_date"] = published_date

        file_resp = client.get(file_url)
        meta["file_status"] = file_resp.status_code
        file_resp.raise_for_status()
        meta["file_bytes"] = len(file_resp.content)
        records = parse_short_selling_bytes(
            file_resp.content, file_url, published_date=published_date
        )
    meta["count"] = len(records)
    return records, meta
