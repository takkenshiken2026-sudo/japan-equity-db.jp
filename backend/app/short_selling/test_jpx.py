"""空売り残高パーサの単体テスト。

ライブ取得（www.jpx.co.jp）はこの環境の egress ポリシーでブロックされるため、
JPX の公表フォーマットを模した DataFrame でパースロジックを検証する。
"""
from __future__ import annotations

import pandas as pd

from app.short_selling.jpx import (
    _extract_latest_file_url,
    parse_short_selling_frame,
)


def _sample_frame() -> pd.DataFrame:
    # 実ファイル同様、先頭にタイトル行があり、その後に見出し行が来る構造
    rows = [
        ["空売り残高に関する情報", None, None, None, None, None],
        [None, None, None, None, None, None],
        ["計算年月日", "コード", "銘柄名", "商号・名称・氏名", "空売り残高割合", "空売り残高数量"],
        ["2026/07/09", "7203", "トヨタ自動車", "ABCキャピタル", "0.62", "1,234,000"],
        ["2026年7月9日", "6758", "ソニーグループ", "XYZファンド", "1.05%", "980000"],
        [None, None, None, None, None, None],
        ["2026/07/09", None, "欠損行", "無コード", "0.80", "100"],
    ]
    return pd.DataFrame(rows)


def test_parse_extracts_valid_rows():
    records = parse_short_selling_frame(_sample_frame(), published_date="2026-07-10")
    assert len(records) == 2

    toyota = records[0]
    assert toyota.sec_code == "7203"
    assert toyota.company_name == "トヨタ自動車"
    assert toyota.holder_name == "ABCキャピタル"
    assert toyota.short_ratio == 0.62
    assert toyota.short_shares == 1_234_000
    assert toyota.calc_date == "2026-07-09"
    assert toyota.published_date == "2026-07-10"

    sony = records[1]
    assert sony.sec_code == "6758"
    assert sony.short_ratio == 1.05  # "%" と全角を除去
    assert sony.short_shares == 980_000
    assert sony.calc_date == "2026-07-09"  # 和暦表記も正規化


def test_parse_skips_rows_missing_key_fields():
    records = parse_short_selling_frame(_sample_frame())
    # コード欠損行は除外される
    assert all(r.sec_code for r in records)
    assert "無コード" not in [r.holder_name for r in records]


def test_parse_returns_empty_without_header():
    frame = pd.DataFrame([["無関係", "データ"], ["1", "2"]])
    assert parse_short_selling_frame(frame) == []


def test_five_digit_code_normalized_to_four():
    frame = pd.DataFrame(
        [
            ["計算年月日", "コード", "銘柄名", "氏名", "空売り残高割合", "空売り残高数量"],
            ["2026/07/09", "72030", "トヨタ", "某ファンド", "0.5", "100"],
        ]
    )
    records = parse_short_selling_frame(frame)
    assert records[0].sec_code == "7203"


def test_extract_latest_file_url_picks_newest_dated():
    html = """
    <a href="/markets/short-selling/2026-07-08.xls">7/8</a>
    <a href="/markets/short-selling/2026-07-09.xls">7/9</a>
    <a href="/other/page.html">別ページ</a>
    """
    url, file_date = _extract_latest_file_url(html)
    assert url == "https://www.jpx.co.jp/markets/short-selling/2026-07-09.xls"
    assert file_date == "2026-07-09"
