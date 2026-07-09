from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

_NAME_SUFFIXES = (
    "株式会社",
    "（株）",
    "(株)",
)

_MIN_STRONG_TERM_LEN = 4

# 地名・施設名などと紛れやすい語（単独では弱い）
_AMBIGUOUS_ROOTS = {
    "王子", "東京", "大阪", "京都", "神戸", "横浜", "名古屋", "札幌", "福岡",
    "日本", "中国", "韓国", "関西", "関東", "東北", "九州", "北海道", "沖縄",
    "中央", "東部", "西部", "南部", "北部", "新生", "明星", "大和", "平和",
    "新日本", "西日本", "東日本", "北日本", "南日本",
}

_FALSE_POSITIVE_SUFFIXES = (
    "動物園", "植物園", "水族館", "博物館", "美術館", "テーマパーク",
    "駅", "停留所", "空港", "港", "埠頭", "フェリー",
    "神社", "寺", "教会", "墓地", "霊園",
    "公園", "広場", "競馬場", "スタジアム", "球場", "体育館", "アリーナ", "スキー場",
    "病院", "クリニック", "医科大学", "大学", "高校", "中学校", "小学校",
    "幼稚園", "保育園", "カフェ", "レストラン", "食堂",
    "温泉", "ホテル", "旅館", "イン", "民宿",
    "町", "村", "市区", "区役所", "市役所", "県庁", "都庁", "役場",
    "川", "山", "岳", "湾", "島", "半島", "海岸", "海水浴場",
    "線", "道路", "街道", "トンネル", "橋", "高架",
    "祭", "まつり", "花火大会", "フェス", "イベント会場",
)

_CORPORATE_MARKERS = (
    "株式会社", "ホールディングス", "グループ", "HD", "ＨＤ",
    "銀行", "証券", "保険", "投信", "信託", "商事", "物産", "産業", "工業",
    "製作所", "重工", "化学", "製薬", "電機", "電気", "自動車", "建設", "造船",
    "製鉄", "鉱業", "通信", "放送", "飲料", "食品",
    "決算", "業績", "株価", "株式", "株主", "有価証券", "IR", "適時開示",
    "売上", "利益", "配当", "中計", "経営", "M&A", "買収", "合併",
)

_CORPORATE_SUFFIXES_IN_NAME = (
    "ホールディングス", "グループ", "コーポレーション", "コーポレーション",
    "商事", "工業", "製作所", "電機", "自動車", "銀行", "証券", "建設", "重工",
    "化学", "製薬", "物産", "産業", "製鉄", "造船", "航空", "運輸", "旅客",
    "通信", "放送", "飲料", "食品", "百貨店", "不動産", "倉庫", "海運", "鉄道", "旅客",
)

_QUERY_NEGATIVES = ("-動物園", "-駅", "-神社", "-まつり", "-公園")


@dataclass
class MatchProfile:
    strong_terms: list[str] = field(default_factory=list)
    sec_codes: list[str] = field(default_factory=list)
    watch_roots: list[str] = field(default_factory=list)
    english_names: list[str] = field(default_factory=list)


def brand_name(name: str) -> str:
    n = (name or "").strip()
    for suffix in _NAME_SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return re.sub(r"\s+", "", n).strip()


def _normalize_text(text: str) -> str:
    return (text or "").replace(" ", "").replace("　", "")


def build_match_profile(
    *,
    name: str,
    sec_code: Optional[str] = None,
    name_en: Optional[str] = None,
) -> MatchProfile:
    profile = MatchProfile()
    seen: set[str] = set()

    def add_strong(term: str) -> None:
        term = (term or "").strip()
        if not term:
            return
        key = term.lower()
        if key in seen:
            return
        seen.add(key)
        profile.strong_terms.append(term)

    def add_root(root: str) -> None:
        root = (root or "").strip()
        if not root or root in profile.watch_roots:
            return
        profile.watch_roots.append(root)

    brand = brand_name(name)
    if len(brand) >= _MIN_STRONG_TERM_LEN:
        add_strong(brand)

    root_for_corp: Optional[str] = None
    for corp_suffix in _CORPORATE_SUFFIXES_IN_NAME:
        if brand.endswith(corp_suffix) and len(brand) > len(corp_suffix):
            root_for_corp = brand[: -len(corp_suffix)]
            if root_for_corp:
                add_root(root_for_corp)
            if corp_suffix == "ホールディングス" and root_for_corp and len(root_for_corp) >= 2:
                add_strong(f"{root_for_corp}HD")
                add_strong(f"{root_for_corp}ＨＤ")
            break

    if root_for_corp and len(root_for_corp) <= 5:
        add_root(root_for_corp)
    elif len(brand) <= 5:
        add_root(brand)

    for root in list(profile.watch_roots):
        if root in _AMBIGUOUS_ROOTS:
            continue
        if len(root) <= 5:
            add_root(root)

    if sec_code:
        code = re.sub(r"\D", "", sec_code)[:4]
        if code and code not in profile.sec_codes:
            profile.sec_codes.append(code)
            add_strong(f"{code}株")

    en = (name_en or "").strip()
    if en and len(en) >= _MIN_STRONG_TERM_LEN:
        profile.english_names.append(en)
        add_strong(en)

    return profile


def build_news_query(
    *,
    name: str,
    sec_code: Optional[str] = None,
    name_en: Optional[str] = None,
) -> str:
    profile = build_match_profile(name=name, sec_code=sec_code, name_en=name_en)
    parts: list[str] = []
    for term in profile.strong_terms:
        if term.isdigit():
            parts.append(term)
        else:
            parts.append(f'"{term}"')
    for code in profile.sec_codes:
        if code not in parts:
            parts.append(code)
    if not parts:
        brand = brand_name(name)
        if brand:
            parts.append(f'"{brand}"')
    query = " OR ".join(parts[:5])
    return f"{query} {' '.join(_QUERY_NEGATIVES)}" if query else name


def build_trend_keyword(name: str) -> str:
    brand = brand_name(name)
    if len(brand) >= _MIN_STRONG_TERM_LEN:
        return brand
    return (name or "").strip()


def _text_blob(item: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            item.get("title") or "",
            item.get("summary") or "",
            item.get("source_name") or "",
        )
        if part
    )


def _contains_sec_code(text: str, code: str) -> bool:
    patterns = (
        code,
        f"[{code}]",
        f"（{code}）",
        f"({code})",
        f"【{code}】",
        f"{code}株",
        f"{code}銘柄",
    )
    compact = _normalize_text(text)
    return any(pattern in text or pattern in compact for pattern in patterns)


def _contains_term(text: str, term: str) -> bool:
    compact = _normalize_text(text)
    if term.isdigit():
        return _contains_sec_code(text, term)
    return term in text or term in compact


def _has_corporate_marker(text: str) -> bool:
    compact = _normalize_text(text)
    return any(marker in text or marker in compact for marker in _CORPORATE_MARKERS)


def _false_positive_compound_hit(text: str, roots: list[str]) -> bool:
    compact = _normalize_text(text)
    for root in roots:
        if not root:
            continue
        pos = 0
        while True:
            idx = compact.find(root, pos)
            if idx == -1:
                break
            tail = compact[idx + len(root) :]
            for suffix in _FALSE_POSITIVE_SUFFIXES:
                if tail.startswith(suffix):
                    return True
            pos = idx + max(1, len(root))
    return False


def is_relevant_article(
    item: dict[str, Any],
    *,
    name: str,
    sec_code: Optional[str] = None,
    name_en: Optional[str] = None,
) -> bool:
    profile = build_match_profile(name=name, sec_code=sec_code, name_en=name_en)
    text = _text_blob(item)
    compact = _normalize_text(text)

    strong_hits = [term for term in profile.strong_terms if _contains_term(text, term)]
    code_hits = [code for code in profile.sec_codes if _contains_sec_code(text, code)]

    if code_hits:
        return True

    if strong_hits:
        if _false_positive_compound_hit(text, profile.watch_roots):
            if not any(len(term) > len(root) for term in strong_hits for root in profile.watch_roots if root in term):
                return False
        longest = max(len(term) for term in strong_hits)
        if longest >= 6:
            return True
        if _has_corporate_marker(text):
            return True
        matched_roots = [root for root in profile.watch_roots if root and root in compact]
        if matched_roots and all(root in _AMBIGUOUS_ROOTS for root in matched_roots):
            return False
        if longest >= _MIN_STRONG_TERM_LEN:
            return True
        return False

    for en in profile.english_names:
        if _contains_term(text, en):
            return True

    return False


def filter_relevant_articles(
    items: list[dict[str, Any]],
    *,
    name: str,
    sec_code: Optional[str] = None,
    name_en: Optional[str] = None,
) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if is_relevant_article(item, name=name, sec_code=sec_code, name_en=name_en)
    ]
