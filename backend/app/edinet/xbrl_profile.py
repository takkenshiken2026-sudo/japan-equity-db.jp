from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if not self._skip and tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "td"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)


BLOCK_MAP = {
    "DescriptionOfBusinessTextBlock": "business_description",
    "BusinessPolicyBusinessEnvironmentIssuesToAddressEtcTextBlock": "business_description",
    "CompanyHistoryTextBlock": "company_history",
    "InformationAboutEmployeesTextBlock": "employees_text",
    "OverviewOfAffiliatedEntitiesTextBlock": "affiliated_entities",
    "BusinessResultsOfReportingCompanyTextBlock": "business_results",
    "DescriptionOfReportableSegmentsTextBlock": "business_segments",
}


@dataclass
class ProfileParseResult:
    business_description: Optional[str] = None
    company_history: Optional[str] = None
    employees_text: Optional[str] = None
    affiliated_entities: Optional[str] = None
    employee_count: Optional[int] = None
    capital_stock_m: Optional[float] = None
    issued_shares: Optional[str] = None
    parse_status: str = "empty"
    error: Optional[str] = None


def _html_to_text(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_section_heading(text: str) -> str:
    return re.sub(r"^[０-９\d]+\s*【[^】]+】\s*", "", text.strip())


def _summarize_prose(text: str, max_chars: int = 1800) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    picked: list[str] = []
    for line in lines:
        if re.match(r"^[\d０-９]+【", line):
            continue
        if re.match(r"^(回次|決算期|百万円|千株|年月|概要|名称|住所|事業別)", line):
            break
        if re.fullmatch(r"[\d,．.]+", line):
            continue
        if len(line) <= 2:
            continue
        picked.append(line)
        joined = "\n".join(picked)
        if len(joined) >= max_chars:
            break
    result = "\n".join(picked)
    if len(result) > max_chars:
        result = result[: max_chars - 1].rstrip() + "…"
    return result


def _extract_employee_count(html: str, employees_text: str | None) -> int | None:
    match = re.search(
        r'name="jpcrp_cor:NumberOfEmployees"[^>]*>([\d,]+)',
        html,
        re.I,
    )
    if match:
        return int(match.group(1).replace(",", ""))
    if employees_text:
        match = re.search(
            r"提出会社の状況.*?従業員数.*?([\d,]+)",
            employees_text.replace("\n", " "),
            re.S,
        )
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def _extract_capital_and_shares(results_text: str | None) -> tuple[float | None, str | None]:
    if not results_text:
        return None, None
    capital = None
    shares = None
    cap_match = re.search(r"資本金\s*(?:\(百万円\))?\s*([\d,]+)", results_text.replace("\n", " "))
    if cap_match:
        capital = float(cap_match.group(1).replace(",", ""))
    share_match = re.search(
        r"発行済株式総数\s*普通株式\s*(?:\(千株\))?\s*([\d,]+)",
        results_text.replace("\n", " "),
    )
    if share_match:
        shares = f"{share_match.group(1)}千株"
    return capital, shares


def _extract_affiliate_names(text: str | None, limit: int = 6) -> str | None:
    if not text:
        return None
    names: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("（") or line.startswith("名称"):
            continue
        if "㈱" in line or "株式会社" in line:
            name = re.sub(r"\s*＊\d+.*$", "", line).strip()
            if name and name not in names:
                names.append(name)
        if len(names) >= limit:
            break
    return "、".join(names) if names else None


def _extract_blocks(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for tag in ("ix:nonNumeric", "nonNumeric"):
        pattern = rf"<{tag}[^>]*name=\"([^\"]+)\"[^>]*>(.*?)</{tag}>"
        for match in re.finditer(pattern, html, re.S | re.I):
            key = match.group(1).split(":")[-1]
            field = BLOCK_MAP.get(key)
            if field and field not in found:
                found[field] = _html_to_text(match.group(2))
    return found


def _honbun_html_files(names: list[str]) -> list[str]:
    honbun = [n for n in names if "honbun" in n.lower() and n.lower().endswith(".htm")]
    if honbun:
        return sorted(honbun)
    return [n for n in names if n.lower().endswith(".htm")]


def _merge_honbun_blocks(zf: zipfile.ZipFile) -> dict[str, str]:
    merged: dict[str, str] = {}
    for name in _honbun_html_files(zf.namelist()):
        html = zf.read(name).decode("utf-8", errors="replace")
        for key, value in _extract_blocks(html).items():
            if key not in merged and value:
                merged[key] = value
    return merged


def parse_profile_xbrl_zip(content: bytes) -> ProfileParseResult:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            blocks = _merge_honbun_blocks(zf)
            if not blocks:
                return ProfileParseResult(parse_status="empty")

            business_raw = blocks.get("business_description") or blocks.get("business_segments", "")
            business = _summarize_prose(_strip_section_heading(business_raw))
            history = _summarize_prose(
                _strip_section_heading(blocks.get("company_history", "")),
                max_chars=2400,
            )
            employees_text = blocks.get("employees_text")
            affiliates = blocks.get("affiliated_entities")
            results = blocks.get("business_results")

            html_blob = ""
            for name in _honbun_html_files(zf.namelist()):
                html_blob += zf.read(name).decode("utf-8", errors="replace")
            employee_count = _extract_employee_count(html_blob, employees_text)
            capital_m, issued_shares = _extract_capital_and_shares(results)

            if not any([business, history, employees_text, affiliates]):
                return ProfileParseResult(parse_status="empty")

            return ProfileParseResult(
                business_description=business or None,
                company_history=history or None,
                employees_text=employees_text,
                affiliated_entities=affiliates,
                employee_count=employee_count,
                capital_stock_m=capital_m,
                issued_shares=issued_shares,
                parse_status="ok",
            )
    except Exception as exc:
        return ProfileParseResult(parse_status="error", error=str(exc))


def affiliate_summary(text: str | None) -> str | None:
    return _extract_affiliate_names(text)
