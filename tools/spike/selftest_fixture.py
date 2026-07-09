"""合成フィクスチャで edinet_extractors が実際に動くことを実証する。

注意: この環境は外部 egress が遮断されており実 EDINET へ到達できないため、
      本テストは EDINET type=5 CSV の**行フォーマットを模した合成データ**で
      パーサの動作を検証する。実データ取得は run_extract.py（ネットワーク開放環境）で。

行フォーマット: [要素ID, 項目名, コンテキストID, 相対年度, 連結・個別,
                期間・時点, ユニットID, 単位, 値]
"""

from __future__ import annotations

import io
import json

import edinet_extractors as ex

HEADER = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別",
          "期間・時点", "ユニットID", "単位", "値"]

# トヨタ風の値を模した合成行（実データではない）
FIXTURE_ROWS = [
    HEADER,
    # 従業員の質（提出会社・単体）
    ["jpcrp_cor:NumberOfEmployees", "従業員数", "CurrentYearInstant_NonConsolidatedMember",
     "当期末", "提出会社", "時点", "shares", "人", "70710"],
    ["jpcrp_cor:AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees",
     "平均年間給与", "CurrentYearDuration", "当期", "提出会社", "期間", "JPY", "円", "8579000"],
    ["jpcrp_cor:AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees",
     "平均年齢", "CurrentYearDuration", "当期", "提出会社", "期間", "pure", "歳", "40.4"],
    ["jpcrp_cor:AverageLengthOfServiceYearsInformationAboutReportingCompanyInformationAboutEmployees",
     "平均勤続年数", "CurrentYearDuration", "当期", "提出会社", "期間", "pure", "年", "16.3"],
    # 人的資本（pure 0.024 形式と % 形式の両方を混ぜる）
    ["jpcrp_cor:RatioOfFemaleManagersToTotalNumberOfManagers",
     "管理職に占める女性労働者の割合", "CurrentYearDuration", "当期", "提出会社", "期間", "pure", "", "0.027"],
    ["jpcrp_cor:RatioOfMaleEmployeesWhoTookChildcareLeave",
     "男性労働者の育児休業取得率", "CurrentYearDuration", "当期", "提出会社", "期間", "pure", "", "0.63"],
    ["jpcrp_cor:DifferenceInWagesBetweenMenAndWomenAllWorkers",
     "労働者の男女の賃金の差異", "CurrentYearDuration", "当期", "提出会社", "期間", "pure", "", "60.4"],
    # 大株主（次元メンバー No1..No3）
    ["jpcrp_cor:NameMajorShareholders", "氏名又は名称",
     "CurrentYearInstant_No1MajorShareholdersMember", "当期", "提出会社", "時点", "", "", "日本マスタートラスト信託銀行"],
    ["jpcrp_cor:NumberOfSharesHeld", "所有株式数",
     "CurrentYearInstant_No1MajorShareholdersMember", "当期", "提出会社", "時点", "shares", "千株", "1985420"],
    ["jpcrp_cor:ShareholdingRatio", "発行済株式（自己株式を除く。）の総数に対する所有株式数の割合",
     "CurrentYearInstant_No1MajorShareholdersMember", "当期", "提出会社", "時点", "pure", "％", "14.52"],
    ["jpcrp_cor:NameMajorShareholders", "氏名又は名称",
     "CurrentYearInstant_No2MajorShareholdersMember", "当期", "提出会社", "時点", "", "", "豊田自動織機"],
    ["jpcrp_cor:NumberOfSharesHeld", "所有株式数",
     "CurrentYearInstant_No2MajorShareholdersMember", "当期", "提出会社", "時点", "shares", "千株", "830100"],
    ["jpcrp_cor:ShareholdingRatio", "発行済株式（自己株式を除く。）の総数に対する所有株式数の割合",
     "CurrentYearInstant_No2MajorShareholdersMember", "当期", "提出会社", "時点", "pure", "％", "6.07"],
    ["jpcrp_cor:NameMajorShareholders", "氏名又は名称",
     "CurrentYearInstant_No3MajorShareholdersMember", "当期", "提出会社", "時点", "", "", "ステート ストリート"],
    ["jpcrp_cor:NumberOfSharesHeld", "所有株式数",
     "CurrentYearInstant_No3MajorShareholdersMember", "当期", "提出会社", "時点", "shares", "千株", "512000"],
    ["jpcrp_cor:ShareholdingRatio", "発行済株式（自己株式を除く。）の総数に対する所有株式数の割合",
     "CurrentYearInstant_No3MajorShareholdersMember", "当期", "提出会社", "時点", "pure", "％", "3.74"],
    # セグメント（自動車 / 金融）
    ["jpcrp_cor:NetSalesOfEachReportableSegment", "売上高",
     "CurrentYearDuration_AutomotiveReportableSegmentsMember", "当期", "連結", "期間", "JPY", "百万円", "38000000"],
    ["jpcrp_cor:OperatingIncomeLoss", "セグメント利益",
     "CurrentYearDuration_AutomotiveReportableSegmentsMember", "当期", "連結", "期間", "JPY", "百万円", "3200000"],
    ["jpcrp_cor:NetSalesOfEachReportableSegment", "売上高",
     "CurrentYearDuration_FinancialServicesReportableSegmentsMember", "当期", "連結", "期間", "JPY", "百万円", "2800000"],
    ["jpcrp_cor:OperatingIncomeLoss", "セグメント利益",
     "CurrentYearDuration_FinancialServicesReportableSegmentsMember", "当期", "連結", "期間", "JPY", "百万円", "620000"],
    # 設備投資 / 研究開発費
    ["jpcrp_cor:TotalAmountOfCapitalInvestmentsSummaryOfCapitalInvestments", "設備投資額",
     "CurrentYearDuration", "当期", "連結", "期間", "JPY", "百万円", "1700000"],
    ["jpcrp_cor:ResearchAndDevelopmentExpenses", "研究開発費",
     "CurrentYearDuration", "当期", "連結", "期間", "JPY", "百万円", "1240000"],
]


def build_utf16_tsv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    for r in rows:
        buf.write("\t".join(r) + "\r\n")
    return buf.getvalue().encode("utf-16")


def main() -> None:
    content = build_utf16_tsv(FIXTURE_ROWS)
    rows = ex.read_csv_rows(content)
    result = ex.extract_all(rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 最低限のアサーション（パーサが機能していることの担保）
    eq = result["employee_quality"]
    assert eq["number_of_employees"] == 70710, eq
    assert eq["average_annual_salary_yen"] == 8579000.0, eq
    assert eq["average_age_years"] == 40.4, eq
    hc = result["human_capital"]
    assert hc["ratio_female_managers_pct"] == 2.7, hc          # 0.027 -> 2.7%
    assert hc["male_childcare_leave_pct"] == 63.0, hc          # 0.63 -> 63%
    assert hc["gender_wage_gap_all_pct"] == 60.4, hc           # 既に%表記
    ms = result["major_shareholders"]
    assert len(ms) == 3 and ms[0]["name"].startswith("日本マスタートラスト"), ms
    assert ms[0]["holding_ratio_pct"] == 14.52, ms
    seg = result["segments"]
    assert len(seg) == 2, seg
    inv = result["investment"]
    assert inv["capex_yen"] == 1700000.0 and inv["rd_expenses_yen"] == 1240000.0, inv
    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
