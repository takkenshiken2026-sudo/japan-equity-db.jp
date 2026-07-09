from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional

PREFECTURE_RE = re.compile(
    r"(北海道|(?:京都|大阪)府|東京都|[一-龥]{2,3}県)"
)
NAME_LOCATION_RE = re.compile(
    r"^(.+?)\s*[（(]([^）)]+)[）)]\s*$"
)
FOOTNOTE_ONLY_RE = re.compile(r"^[\[［].*[\]］]$")
AREA_IN_PARENS_RE = re.compile(r"\(([\d,]+(?:\.\d+)?)\)")


@dataclass
class ParsedProperty:
    facility_name: str
    location: str
    section: str = "major_facilities"
    category: Optional[str] = None
    building_scale: Optional[str] = None
    building_area_sqm: Optional[float] = None
    building_book_value: Optional[float] = None
    land_area_sqm: Optional[float] = None
    land_book_value: Optional[float] = None
    other_book_value: Optional[float] = None
    total_book_value: Optional[float] = None
    completion_year: Optional[str] = None
    employees: Optional[int] = None
    machinery_book_value: Optional[float] = None
    lease_book_value: Optional[float] = None
    footnotes: Optional[str] = None


@dataclass
class ParseResult:
    properties: list[ParsedProperty] = field(default_factory=list)
    parse_status: str = "empty"
    error: Optional[str] = None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = (
        value.replace(",", "")
        .replace("△", "-")
        .replace("－", "-")
        .replace("―", "-")
        .replace("〃", "")
        .strip()
    )
    if cleaned in {"", "-", "－"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    num = _to_float(value)
    if num is None:
        return None
    return int(num)


def _extract_year(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{4})年", value)
    return match.group(1) if match else None


def _prefecture(location: str | None) -> str | None:
    if not location:
        return None
    match = PREFECTURE_RE.search(location)
    return match.group(1) if match else None


def _looks_like_location(value: str) -> bool:
    return bool(value and PREFECTURE_RE.search(value))


HEADER_NAME_RE = re.compile(
    r"^(建物|土地|その他|合計|名称|所在地|百万円|規模|延面積|帳簿価額|竣工|事業所名|セグメント|設備|従業|①|②|③|[(（][ア-ン]+[)）])"
)


def _is_valid_property(prop: ParsedProperty) -> bool:
    if not prop.facility_name or not prop.location:
        return False
    if HEADER_NAME_RE.match(prop.facility_name.strip()):
        return False
    if not _looks_like_location(prop.location):
        return False
    if len(prop.facility_name) > 120:
        return False
    return True


def _row_text(row: list[str]) -> str:
    return " ".join(cell for cell in row if cell).strip()


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
        elif self._in_row and tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False
        elif tag == "tr" and self._in_row:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._in_row = False
        elif tag in ("td", "th") and self._in_cell:
            text = "".join(self._current_cell).replace("\xa0", " ")
            text = re.sub(r"\s+", " ", text).strip()
            self._current_row.append(text)
            self._in_cell = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def _parse_tables(html: str) -> list[list[list[str]]]:
    parser = _TableHTMLParser()
    parser.feed(html)
    return parser.tables


def _is_property_table(table: list[list[str]]) -> bool:
    flat = _row_text([cell for row in table[:4] for cell in row])
    if "名称" in flat and "所在地" in flat:
        return True
    if "事業所名" in flat and ("帳簿価額" in flat or "設備の内容" in flat):
        return True
    if "土地面積" in flat and "名称" in flat and "所在地" in flat:
        return True
    return False


def _parse_name_location_table(table: list[list[str]], section: str) -> list[ParsedProperty]:
    properties: list[ParsedProperty] = []
    pending: ParsedProperty | None = None

    for row in table:
        if not row:
            continue
        joined = _row_text(row)
        if FOOTNOTE_ONLY_RE.match(joined) and pending is not None:
            pending.footnotes = (pending.footnotes or "") + joined
            continue
        if "名称" in joined and "所在地" in joined:
            continue
        if any(token in joined for token in ("規模", "延面積", "帳簿価額", "竣工", "土地面積")):
            if not _looks_like_location(row[1] if len(row) > 1 else ""):
                continue

        if len(row) >= 2 and row[0] and _looks_like_location(row[1]):
            prop = ParsedProperty(
                facility_name=row[0].strip(),
                location=row[1].strip(),
                section=section,
            )
            if len(row) > 2:
                prop.building_scale = row[2] or None
            if len(row) > 3:
                prop.building_area_sqm = _to_float(row[3])
            if len(row) > 4:
                prop.building_book_value = _to_float(row[4])
            if len(row) > 5:
                prop.completion_year = _extract_year(row[5])
            if len(row) > 6:
                prop.land_area_sqm = _to_float(row[6])
            if len(row) > 7:
                prop.land_book_value = _to_float(row[7])
            if len(row) > 8:
                prop.other_book_value = _to_float(row[8])
            if len(row) > 9:
                prop.total_book_value = _to_float(row[9])
            properties.append(prop)
            pending = prop
            continue

        if len(row) >= 4 and row[0] and "面積" in _row_text(table[0]):
            prop = ParsedProperty(
                facility_name=row[0].strip(),
                location=row[1].strip(),
                section=section,
                land_area_sqm=_to_float(row[2]) if len(row) > 2 else None,
                total_book_value=_to_float(row[3]) if len(row) > 3 else None,
            )
            properties.append(prop)
            pending = prop

    return properties


def _parse_office_table(table: list[list[str]], section: str) -> list[ParsedProperty]:
    properties: list[ParsedProperty] = []
    pending: ParsedProperty | None = None

    for row in table:
        if not row:
            continue
        joined = _row_text(row)
        if FOOTNOTE_ONLY_RE.match(joined) and pending is not None:
            pending.footnotes = (pending.footnotes or "") + joined
            continue
        if "事業所名" in joined or "セグメント" in joined or "設備の内容" in joined:
            continue
        if not row[0]:
            continue

        name = row[0].strip()
        location = name
        facility_name = name
        match = NAME_LOCATION_RE.match(name)
        if match:
            facility_name = match.group(1).strip()
            location = match.group(2).strip()

        if not _looks_like_location(location) and not match:
            continue

        prop = ParsedProperty(
            facility_name=facility_name,
            location=location,
            section=section,
            category=row[1].strip() if len(row) > 1 and row[1] else None,
        )
        if len(row) > 3:
            prop.building_book_value = _to_float(row[3])
        if len(row) > 4:
            prop.machinery_book_value = _to_float(row[4])
        if len(row) > 5:
            land_cell = row[5]
            prop.land_book_value = _to_float(re.sub(r"\(.*?\)", "", land_cell))
            area_match = AREA_IN_PARENS_RE.search(land_cell)
            if area_match:
                prop.land_area_sqm = _to_float(area_match.group(1))
        if len(row) > 6:
            prop.lease_book_value = _to_float(row[6])
        if len(row) > 7:
            prop.other_book_value = _to_float(row[7])
        if len(row) > 8:
            prop.total_book_value = _to_float(row[8])
        if len(row) > 9:
            prop.employees = _to_int(row[9])

        properties.append(prop)
        pending = prop

    return properties


def _detect_section(html_before: str) -> str:
    tail = html_before[-400:]
    if "建設中" in tail:
        return "under_construction"
    if "賃貸用建物" in tail:
        return "lease_building"
    if "自社使用" in tail:
        return "own_use"
    if "工場" in tail:
        return "factory"
    if "店舗" in tail:
        return "store"
    if "事業所" in tail or "本社" in tail:
        return "office"
    return "major_facilities"


def parse_facilities_html(html: str) -> list[ParsedProperty]:
    properties: list[ParsedProperty] = []
    tables = _parse_tables(html)
    cursor = 0
    for table in tables:
        if not _is_property_table(table):
            continue
        table_pos = html.find(_row_text(table[0])) if table and table[0] else -1
        section = _detect_section(html[max(0, table_pos - 500) : table_pos]) if table_pos >= 0 else "major_facilities"
        flat = _row_text([cell for row in table[:3] for cell in row])
        if "事業所名" in flat:
            properties.extend(_parse_office_table(table, section))
        else:
            properties.extend(_parse_name_location_table(table, section))
        cursor += 1
    return properties


def parse_facilities_xbrl_zip(content: bytes) -> ParseResult:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            html_chunks: list[str] = []
            for name in zf.namelist():
                if "honbun" not in name.lower() or not name.lower().endswith(".htm"):
                    continue
                html = zf.read(name).decode("utf-8", errors="replace")
                if "主要な設備" in html or "MajorFacilities" in html:
                    html_chunks.append(html)
            if not html_chunks:
                return ParseResult(parse_status="empty")

            properties: list[ParsedProperty] = []
            for html in html_chunks:
                properties.extend(parse_facilities_html(html))

            deduped: list[ParsedProperty] = []
            seen: set[tuple[str, str, str]] = set()
            for prop in properties:
                if not _is_valid_property(prop):
                    continue
                key = (prop.section, prop.facility_name, prop.location)
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(prop)

            if not deduped:
                return ParseResult(parse_status="empty")
            return ParseResult(properties=deduped, parse_status="ok")
    except Exception as exc:
        return ParseResult(parse_status="error", error=str(exc))


def property_to_dict(prop: ParsedProperty) -> dict:
    return {
        "facility_name": prop.facility_name,
        "location": prop.location,
        "prefecture": _prefecture(prop.location),
        "section": prop.section,
        "category": prop.category,
        "building_scale": prop.building_scale,
        "building_area_sqm": prop.building_area_sqm,
        "building_book_value": prop.building_book_value,
        "land_area_sqm": prop.land_area_sqm,
        "land_book_value": prop.land_book_value,
        "other_book_value": prop.other_book_value,
        "total_book_value": prop.total_book_value,
        "machinery_book_value": prop.machinery_book_value,
        "lease_book_value": prop.lease_book_value,
        "completion_year": prop.completion_year,
        "employees": prop.employees,
        "footnotes": prop.footnotes,
    }
