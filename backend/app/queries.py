from __future__ import annotations

from sqlalchemy import func, select

from app.db import Financial


def latest_financial_subquery():
    """各企業の最新年次財務（fiscal_year_end 最大）を返すサブクエリ。"""
    return (
        select(
            Financial.edinet_code.label("edinet_code"),
            func.max(Financial.fiscal_year_end).label("fiscal_year_end"),
        )
        .group_by(Financial.edinet_code)
        .subquery()
    )
