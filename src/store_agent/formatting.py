"""Employee-facing number/date formatting. Validation relies on these conventions:
money and percentages always carry $ / %, counts >= 1,000 always carry separators."""

from datetime import date


def money(v: float) -> str:
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}${a / 1_000:.1f}K"
    return f"{sign}${a:,.2f}"


def signed_money(v: float) -> str:
    """Changes: whole dollars below $1K, so lists of deltas read consistently."""
    text = money(v) if abs(v) >= 1_000 else f"{'-' if v < 0 else ''}${abs(v):,.0f}"
    return text if v < 0 else "+" + text


def count(v: float) -> str:
    return f"{v:,.0f}"


def pct(v: float) -> str:
    return f"{abs(v):.1f}%"


def signed_pct(v: float) -> str:
    return f"{v:+.1f}%"


def direction(v: float) -> str:
    return "up" if v > 0 else "down" if v < 0 else "flat"


def arrow(v: float) -> str:
    return "▲" if v > 0 else "▼" if v < 0 else "■"


def day(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def day_year(d: date) -> str:
    return f"{d:%a %b} {d.day}, {d.year}"


def hour_label(h: int) -> str:
    def one(x: int) -> str:
        x %= 24
        return f"{x % 12 or 12} {'AM' if x < 12 else 'PM'}"

    return f"{one(h)}-{one(h + 1)}"


def metric_value(unit: str, v: float) -> str:
    if unit == "currency":
        return money(v)
    if unit == "percent":
        return f"{v:.1f}%"
    return count(v)
