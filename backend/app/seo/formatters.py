from __future__ import annotations


def format_sec_code(sec_code: str | None) -> str:
    if not sec_code:
        return "-"
    s = sec_code.strip()
    if len(s) == 5 and s.endswith("0"):
        return s[:-1]
    return s


def format_yen(value: float | int | None) -> str:
    if value is None:
        return "-"
    v = float(value)
    abs_v = abs(v)
    if abs_v >= 1_000_000_000_000:
        return f"{v / 1_000_000_000_000:.1f}兆円"
    if abs_v >= 100_000_000:
        return f"{v / 100_000_000:.0f}億円"
    if abs_v >= 10_000:
        return f"{v / 10_000:.0f}万円"
    return f"{v:,.0f}円"


def format_pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "-"
    pct = value * 100
    sign = "+" if signed and pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def format_million_yen(value: float | None) -> str:
    if value is None:
        return "-"
    v = float(value)
    abs_v = abs(v)
    if abs_v >= 1_000_000:
        return f"{v / 1_000_000:.1f}兆円"
    if abs_v >= 100:
        return f"{v / 100:.0f}億円"
    if abs_v >= 10:
        return f"{v:.0f}億円"
    return f"{v:.1f}百万円"


def format_chg(value: float | None) -> str:
    if value is None:
        return "-"
    pct = value * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def format_num(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    if digits == 0:
        return f"{float(value):,.0f}"
    return f"{float(value):,.{digits}f}"


def truncate_text(text: str | None, max_len: int = 320) -> str:
    if not text:
        return ""
    t = " ".join(text.split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def business_excerpt(text: str | None, max_len: int = 600) -> str:
    if not text:
        return ""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    out = []
    total = 0
    for p in paragraphs:
        if total + len(p) > max_len:
            break
        out.append(p)
        total += len(p)
    return "\n\n".join(out)
