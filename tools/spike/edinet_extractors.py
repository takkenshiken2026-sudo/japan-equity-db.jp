"""EDINET 有報 type=5 CSV から「APIで簡単に取れない」高moatデータを抽出する試作モジュール。

方針: 既存の app/edinet/client.py の解析流儀に合わせる。
  - 有報 CSV(type=5) は UTF-16 のタブ区切りで、各行は
      [0]要素ID [1]項目名 [2]コンテキストID [3]相対年度 [4]連結・個別
      [5]期間・時点 [6]ユニットID [7]単位 [8]値
  - 値の特定は「要素ID の候補リスト（サフィックス一致可）」＋
    「項目名（日本語）フォールバック」で頑健に行う。
  - 大株主・セグメントは値そのものではなく "コンテキストID の次元メンバー"
    (例: CurrentYearInstant_No1MajorShareholdersMember) で行を束ねる。

このモジュールは prod には組み込まず、tools/spike 配下の検証用。
ネットワークが開いた環境（ローカル/prod daily-sync）でそのまま実行できる。
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Optional


# ---- CSV 読み込み（client._read_csv_rows と同一仕様） -----------------------

def read_csv_rows(content: bytes) -> list[list[str]]:
    """type=5 CSV(zip 展開済みの1ファイル bytes)を行リストへ。UTF-16 TSV。"""
    text = content.decode("utf-16")
    return list(csv.reader(io.StringIO(text), delimiter="\t"))


def _element_matches(cell: str, element_id: str) -> bool:
    """client._element_matches と同一。名前空間違いを吸収するサフィックス一致。"""
    if not cell or not element_id:
        return False
    if cell == element_id:
        return True
    needle = element_id.split(":")[-1]
    return cell.endswith(f":{needle}") or cell.split(":")[-1] == needle


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").replace("△", "-").strip().strip('"').replace("%", "")
    if cleaned in {"", "-", "―", "－", "－"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().strip('"').strip()
    return v or None


def _row(cell_list: list[str], i: int) -> str:
    return cell_list[i] if len(cell_list) > i else ""


def _find_value(
    rows: list[list[str]],
    element_ids: list[str],
    *,
    item_name_keywords: Optional[list[str]] = None,
    period_ok: Optional[set[str]] = None,
    consolidated: Optional[str] = None,
) -> Optional[str]:
    """要素ID 候補 → 項目名キーワードの順で最初に見つかった値(row[8])を返す。"""
    period_ok = period_ok or {"当期", "当期末"}
    # 1st: element id 一致
    for element_id in element_ids:
        for r in rows[1:]:
            if not _element_matches(_row(r, 0), element_id):
                continue
            if _row(r, 3) and period_ok and _row(r, 3) not in period_ok:
                continue
            if consolidated and _row(r, 4) and consolidated not in _row(r, 4):
                continue
            val = _clean_text(_row(r, 8))
            if val:
                return val
    # 2nd: 項目名（日本語）フォールバック
    if item_name_keywords:
        for r in rows[1:]:
            name = _row(r, 1)
            if not name or not any(k in name for k in item_name_keywords):
                continue
            if _row(r, 3) and period_ok and _row(r, 3) not in period_ok:
                continue
            val = _clean_text(_row(r, 8))
            if val:
                return val
    return None


# ============================================================================
# 1) 従業員の質: 平均年間給与 / 平均年齢 / 平均勤続年数 / 従業員数
# ============================================================================

@dataclass
class EmployeeQuality:
    number_of_employees: Optional[int] = None
    average_annual_salary_yen: Optional[float] = None
    average_age_years: Optional[float] = None
    average_length_of_service_years: Optional[float] = None


def parse_employee_quality(rows: list[list[str]]) -> EmployeeQuality:
    emp = _find_value(
        rows,
        [
            "jpcrp_cor:NumberOfEmployees",
            "jpcrp_cor:NumberOfEmployeesInformationAboutReportingCompanyInformationAboutEmployees",
        ],
        item_name_keywords=["従業員数"],
        consolidated="提出会社",
    )
    salary = _find_value(
        rows,
        ["jpcrp_cor:AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees"],
        item_name_keywords=["平均年間給与"],
    )
    age = _find_value(
        rows,
        ["jpcrp_cor:AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees"],
        item_name_keywords=["平均年齢"],
    )
    tenure = _find_value(
        rows,
        ["jpcrp_cor:AverageLengthOfServiceYearsInformationAboutReportingCompanyInformationAboutEmployees"],
        item_name_keywords=["平均勤続年数"],
    )
    return EmployeeQuality(
        number_of_employees=int(_to_float(emp)) if _to_float(emp) is not None else None,
        average_annual_salary_yen=_to_float(salary),
        average_age_years=_to_float(age),
        average_length_of_service_years=_to_float(tenure),
    )


# ============================================================================
# 2) 人的資本: 女性管理職比率 / 男女賃金格差 / 男性育児休業取得率
#    (2023年3月期以降の有報で開示義務。unit=pure は 0.024 形式 → %へ)
# ============================================================================

@dataclass
class HumanCapital:
    ratio_female_managers_pct: Optional[float] = None
    male_childcare_leave_pct: Optional[float] = None
    gender_wage_gap_all_pct: Optional[float] = None


def _pct(value: Optional[str]) -> Optional[float]:
    f = _to_float(value)
    if f is None:
        return None
    # pure(0.024)なら%へ。既に % 表記(24.0)ならそのまま。
    return round(f * 100, 2) if abs(f) <= 1.0 else round(f, 2)


def parse_human_capital(rows: list[list[str]]) -> HumanCapital:
    female = _find_value(
        rows,
        [
            "jpcrp_cor:RatioOfFemaleManagersToTotalNumberOfManagers",
            "jpcrp_cor:RatioOfFemaleEmployeesInManagementPositions",
            "jpcrp_cor:ProportionOfFemaleManagers",
        ],
        item_name_keywords=["管理職に占める女性労働者の割合", "女性管理職"],
    )
    childcare = _find_value(
        rows,
        [
            "jpcrp_cor:RatioOfMaleEmployeesWhoTookChildcareLeave",
            "jpcrp_cor:PercentageOfMaleWorkersTakingChildcareLeave",
        ],
        item_name_keywords=["男性労働者の育児休業取得率", "男性の育児休業取得率", "育児休業取得率"],
    )
    gap = _find_value(
        rows,
        [
            "jpcrp_cor:DifferenceInWagesBetweenMenAndWomenAllWorkers",
            "jpcrp_cor:GenderPayGapAllWorkers",
        ],
        item_name_keywords=["賃金の差異", "男女の賃金"],
    )
    return HumanCapital(
        ratio_female_managers_pct=_pct(female),
        male_childcare_leave_pct=_pct(childcare),
        gender_wage_gap_all_pct=_pct(gap),
    )


# ============================================================================
# 3) 大株主の状況: コンテキストの No{N}MajorShareholdersMember で行を束ねる
# ============================================================================

@dataclass
class MajorShareholder:
    rank: int
    name: Optional[str] = None
    shares_held: Optional[float] = None
    holding_ratio_pct: Optional[float] = None


_MEMBER_RE = re.compile(r"No(\d+)MajorShareholdersMember", re.I)

_NAME_ELEMS = ("NameMajorShareholders", "NameOfMajorShareholders")
_SHARES_ELEMS = ("NumberOfSharesHeld",)
_RATIO_ELEMS = (
    "ShareholdingRatio",
    "RatioOfNumberOfSharesHeldToTotalNumberOfIssuedShares",
    "ShareholdingRatioMajorShareholders",
)


def parse_major_shareholders(rows: list[list[str]]) -> list[MajorShareholder]:
    by_rank: dict[int, MajorShareholder] = {}
    for r in rows[1:]:
        ctx = _row(r, 2)
        m = _MEMBER_RE.search(ctx)
        if not m:
            continue
        rank = int(m.group(1))
        elem = _row(r, 0).split(":")[-1]
        val = _clean_text(_row(r, 8))
        sh = by_rank.setdefault(rank, MajorShareholder(rank=rank))
        if elem in _NAME_ELEMS or "氏名又は名称" in _row(r, 1):
            sh.name = val
        elif elem in _SHARES_ELEMS or _row(r, 1) == "所有株式数":
            sh.shares_held = _to_float(val)
        elif elem in _RATIO_ELEMS or "所有株式数の割合" in _row(r, 1):
            sh.holding_ratio_pct = _pct(val)
    return [by_rank[k] for k in sorted(by_rank)]


# ============================================================================
# 4) セグメント別業績: コンテキストの XxxReportableSegmentsMember で束ねる
#    セグメント名は同名メンバーの nonNumeric、売上/利益は数値で対応づく。
# ============================================================================

@dataclass
class SegmentRow:
    member: str
    net_sales: Optional[float] = None
    operating_income: Optional[float] = None


_SEG_MEMBER_RE = re.compile(r"([A-Za-z0-9]+)(ReportableSegmentsMember|SegmentsMember|Member)")

_SEG_SALES_ELEMS = ("NetSales", "NetSalesOfEachReportableSegment", "SalesToOutsideCustomers")
_SEG_OP_ELEMS = ("OperatingIncomeLoss", "SegmentIncomeLoss", "OperatingIncomeLossOfEachReportableSegment")


def parse_segments(rows: list[list[str]]) -> list[SegmentRow]:
    by_member: dict[str, SegmentRow] = {}
    for r in rows[1:]:
        ctx = _row(r, 2)
        if "Member" not in ctx or "Segment" not in ctx:
            continue
        m = _SEG_MEMBER_RE.search(ctx)
        member = m.group(1) if m else ctx
        elem = _row(r, 0).split(":")[-1]
        seg = by_member.setdefault(member, SegmentRow(member=member))
        if any(e in elem for e in _SEG_SALES_ELEMS) and seg.net_sales is None:
            seg.net_sales = _to_float(_row(r, 8))
        elif any(e in elem for e in _SEG_OP_ELEMS) and seg.operating_income is None:
            seg.operating_income = _to_float(_row(r, 8))
    return [v for v in by_member.values() if v.net_sales or v.operating_income]


# ============================================================================
# 5) 設備投資 / 研究開発費
# ============================================================================

@dataclass
class Investment:
    capex_yen: Optional[float] = None
    rd_expenses_yen: Optional[float] = None


def parse_investment(rows: list[list[str]]) -> Investment:
    capex = _find_value(
        rows,
        ["jpcrp_cor:TotalAmountOfCapitalInvestmentsSummaryOfCapitalInvestments",
         "jpcrp_cor:CapitalExpenditures"],
        item_name_keywords=["設備投資"],
    )
    rd = _find_value(
        rows,
        ["jpcrp_cor:ResearchAndDevelopmentExpenses",
         "jppfs_cor:ResearchAndDevelopmentExpensesSGA"],
        item_name_keywords=["研究開発費"],
    )
    return Investment(capex_yen=_to_float(capex), rd_expenses_yen=_to_float(rd))


def extract_all(rows: list[list[str]]) -> dict:
    """全抽出をまとめて JSON 化しやすい dict で返す。"""
    from dataclasses import asdict

    return {
        "employee_quality": asdict(parse_employee_quality(rows)),
        "human_capital": asdict(parse_human_capital(rows)),
        "major_shareholders": [asdict(s) for s in parse_major_shareholders(rows)],
        "segments": [asdict(s) for s in parse_segments(rows)],
        "investment": asdict(parse_investment(rows)),
    }
