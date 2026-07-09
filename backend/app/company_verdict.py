from __future__ import annotations

from typing import Any, Optional

from app.db import Financial
from app.edinet.client import CURRENT_PARSE_VERSION
from app.market.yahoo import is_sane_per
from app.real_estate_nav import compute_nav_ratio

DISCLAIMER = (
    "本表示はEDINET等の公開データに基づく自動分析であり、"
    "特定銘柄の売買を推奨する投資助言ではありません。"
    "投資判断はご自身の責任で行い、リスクを十分にご理解ください。"
)

GENERAL_RISKS = [
    "株式価格は変動し、投資元本が保証されるものではありません",
    "過去の財務実績は将来の業績・株価を保証しません",
    "データの欠損・更新遅延により指標が実態と乖離する場合があります",
]

BUY_RATING_RISKS = [
    "好材料が既に株価に織り込み済みの可能性があります",
    "業績改善が一時的で、トレンド継続は保証されません",
    "市場全体・金利・為替の影響で個別材料と無関係に株価が下落する可能性があります",
]

BUY_RATINGS = frozenset({"強い買い寄り", "買い寄り"})


def _pct(v: float | None, signed: bool = False) -> str | None:
    if v is None:
        return None
    n = v * 100
    sign = "+" if signed and n > 0 else ""
    return f"{sign}{n:.1f}%"


def _trust_score(data_freshness: dict | None) -> int:
    if not data_freshness:
        return 0
    score = 0
    fin = data_freshness.get("financial") or {}
    if fin.get("has_operating_cf"):
        score += 30
    if fin.get("parse_version") and fin.get("parse_version") >= fin.get("parse_version_current", CURRENT_PARSE_VERSION):
        score += 25
    q = data_freshness.get("quarterly") or {}
    if (q.get("row_count") or 0) > 0:
        score += 20
    if data_freshness.get("stock", {}).get("updated_at"):
        score += 15
    re = data_freshness.get("real_estate") or {}
    if re.get("parse_status") == "ok":
        score += 10
    return min(100, score)


def _merge_risk_factors(rating: str, bears: list[str]) -> tuple[list[str], list[str]]:
    """銘柄固有リスクと、買い寄り系評価時に必ず示す一般リスクを返す。"""
    specific = list(bears)
    general = list(GENERAL_RISKS)

    if rating in BUY_RATINGS:
        for item in BUY_RATING_RISKS:
            if item not in specific and len(specific) < 5:
                specific.append(item)
    elif not specific:
        general = GENERAL_RISKS[:2]

    return specific[:5], general


def build_company_verdict(
    *,
    financials: list[Financial],
    stock: dict | None,
    benchmark: dict | None,
    quarterly_latest: dict | None,
    real_estate: dict | None,
    data_freshness: dict | None,
) -> dict[str, Any]:
    latest = financials[0] if financials else None
    prev = financials[1] if len(financials) > 1 else None
    bulls: list[str] = []
    bears: list[str] = []
    score = 45

    if not latest:
        return {
            "score": 0,
            "rating": "データ不足",
            "stars": 0,
            "bulls": [],
            "bears": ["年次財務データがありません"],
            "general_risks": list(GENERAL_RISKS),
            "disclaimer": DISCLAIMER,
            "summary": "財務データが不足しているため判断できません。",
            "trust_score": _trust_score(data_freshness),
        }

    if latest.revenue_growth is not None:
        if latest.revenue_growth >= 0.1:
            bulls.append(f"売上高が前年比{_pct(latest.revenue_growth, True)}と高成長")
            score += 7
        elif latest.revenue_growth >= 0:
            bulls.append(f"売上高は前年比{_pct(latest.revenue_growth, True)}で増収")
            score += 3
        else:
            bears.append(f"売上高が前年比{_pct(latest.revenue_growth, True)}で減収")
            score -= 8

    if latest.operating_margin is not None:
        if latest.operating_margin >= 0.15:
            bulls.append(f"営業利益率{_pct(latest.operating_margin)}と高収益")
            score += 6
        elif latest.operating_margin < 0:
            bears.append(f"営業利益率{_pct(latest.operating_margin)}で赤字")
            score -= 12

    if benchmark and latest.operating_margin is not None and benchmark.get("avg_operating_margin") is not None:
        diff = latest.operating_margin - benchmark["avg_operating_margin"]
        if diff >= 0.03:
            bulls.append(f"営業利益率が同業平均より{_pct(diff, True)}高い")
            score += 4
        elif diff <= -0.03:
            bears.append(f"営業利益率が同業平均より{_pct(abs(diff))}低い")
            score -= 5

    if latest.roe is not None:
        if latest.roe >= 0.12:
            bulls.append(f"ROE {_pct(latest.roe)}で資本効率が高い")
            score += 5
        elif latest.roe < 0.05:
            bears.append(f"ROE {_pct(latest.roe)}と低水準")
            score -= 4

    if benchmark and latest.roe is not None and benchmark.get("avg_roe") is not None:
        if latest.roe >= benchmark["avg_roe"] + 0.03:
            bulls.append("ROEが同業平均を上回る")
            score += 3

    if latest.operating_cf is not None:
        if latest.operating_cf > 0:
            bulls.append("営業キャッシュフローが黒字")
            score += 6
        else:
            bears.append("営業キャッシュフローが赤字")
            score -= 10
    else:
        bears.append("営業CFデータが未取得（判断材料不足）")
        score -= 5

    if latest.operating_cf is not None and latest.investing_cf is not None:
        fcf = latest.operating_cf + latest.investing_cf
        if fcf > 0:
            bulls.append("フリーキャッシュフローがプラス")
            score += 4

    if prev and latest.operating_cf is not None and prev.operating_cf is not None:
        if latest.operating_cf > 0 and prev.operating_cf <= 0:
            bulls.append("営業CFが黒字化に転じた")
            score += 5
        elif latest.operating_cf < prev.operating_cf * 0.7 and latest.operating_cf > 0:
            bears.append("営業CFが前期比で大幅減")
            score -= 4

    per = None
    if stock:
        per = stock.get("per_edinet") or stock.get("per")
        pbr = stock.get("pbr_edinet") or stock.get("pbr")
        if is_sane_per(per) and per <= 12:
            bulls.append(f"PER {per:.1f}倍と割安感")
            score += 5
        elif is_sane_per(per) and per >= 30:
            bears.append(f"PER {per:.1f}倍と高バリュエーション")
            score -= 5
        elif per is not None and not is_sane_per(per):
            bears.append("PERデータに異常値があり参考外")
            score -= 2
        if pbr is not None and 0 < pbr <= 1.0:
            bulls.append(f"PBR {pbr:.1f}倍（純資産割れ圏）")
            score += 3

    if quarterly_latest:
        yoy = quarterly_latest.get("revenue_yoy")
        if yoy is not None and yoy >= 0.1:
            bulls.append(f"直近四半期売上YoY {_pct(yoy, True)}")
            score += 4
        elif yoy is not None and yoy < -0.05:
            bears.append(f"直近四半期売上YoY {_pct(yoy, True)}")
            score -= 5

    if real_estate and stock and stock.get("market_cap"):
        nav_ratio = compute_nav_ratio(
            real_estate.get("total_book_value_m"),
            stock["market_cap"],
            total_assets=latest.total_assets,
        )
        if nav_ratio is not None:
            if nav_ratio >= 0.5:
                bulls.append(f"不動産帳簿価額が時価総額の{nav_ratio * 100:.0f}%（NAV割安の可能性）")
                score += 5
            real_estate["nav_ratio"] = nav_ratio

    if latest.equity_ratio is not None and latest.equity_ratio >= 0.4:
        bulls.append(f"自己資本比率{_pct(latest.equity_ratio)}で財務安定")
        score += 2
    elif latest.equity_ratio is not None and latest.equity_ratio < 0.2:
        bears.append(f"自己資本比率{_pct(latest.equity_ratio)}と低め")
        score -= 4

    trust = _trust_score(data_freshness)
    if trust < 70:
        bears.append(f"データ信頼度{trust}%（追加確認推奨）")

    score = max(0, min(85, score))
    if score >= 72:
        rating = "強い買い寄り"
        stars = 4
    elif score >= 58:
        rating = "買い寄り"
        stars = 3
    elif score >= 45:
        rating = "様子見"
        stars = 2
    else:
        rating = "慎重"
        stars = 1

    parts: list[str] = []
    if latest.revenue_growth is not None:
        parts.append(f"売上{_pct(latest.revenue_growth, True)}")
    if latest.operating_margin is not None:
        parts.append(f"営業利益率{_pct(latest.operating_margin)}")
    if per is not None and is_sane_per(per):
        parts.append(f"PER {per:.1f}倍")
    summary = f"【参考情報】{rating} — " + ("、".join(parts) if parts else "主要指標を確認してください")
    if benchmark and benchmark.get("industry"):
        summary += f"（{benchmark['industry']}比較済み）"
    if rating in BUY_RATINGS:
        summary += "。好材料とあわせて下記リスクも必ず確認してください"

    bears_out, general_risks = _merge_risk_factors(rating, bears)

    return {
        "score": score,
        "rating": rating,
        "stars": stars,
        "bulls": bulls[:5],
        "bears": bears_out,
        "general_risks": general_risks,
        "disclaimer": DISCLAIMER,
        "summary": summary,
        "trust_score": trust,
    }
