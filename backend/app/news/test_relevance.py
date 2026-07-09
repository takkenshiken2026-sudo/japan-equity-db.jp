from __future__ import annotations

from app.news.relevance import build_match_profile, is_relevant_article


def test_oji_holdings_excludes_zoo() -> None:
    name = "王子ホールディングス株式会社"
    bad = {"title": "鉄道の聖地へ里帰り｜王子動物園のSL「鷹取工場1号機D51」", "summary": ""}
    good = {"title": "王子ホールディングス(株)【3861】：今の株価の理由は？", "summary": ""}
    assert not is_relevant_article(bad, name=name, sec_code="38610")
    assert is_relevant_article(good, name=name, sec_code="38610")


def test_jr_east_excludes_station_event() -> None:
    name = "東日本旅客鉄道株式会社"
    bad = {"title": "東日本の桜まつり開催", "summary": "地域イベント"}
    good = {"title": "東日本旅客鉄道(9020)の業績", "summary": ""}
    assert not is_relevant_article(bad, name=name, sec_code="90200")
    assert is_relevant_article(good, name=name, sec_code="90200")


def test_short_brand_uses_sec_code_only() -> None:
    name = "東レ株式会社"
    good = {"title": "東レ(3402)が新素材を発表", "summary": ""}
    bad = {"title": "東のレストランがオープン", "summary": ""}
    assert is_relevant_article(good, name=name, sec_code="34020")
    assert not is_relevant_article(bad, name=name, sec_code="34020")


def test_holdings_hd_variant() -> None:
    name = "住友ホールディングス株式会社"
    good = {"title": "住友HDの中期経営計画", "summary": ""}
    assert is_relevant_article(good, name=name, sec_code="83160")
