"""Deterministic checks between retrieved data and generated text (no LLM involved).

Checked: money, percentages, and separated counts (must match an evidence value at the
precision shown), direction words/signs next to them, store ids, calendar dates, and
comparison-period mentions. Plain small integers, clock times, and years are ignored.
Known gap: direction stated *after* a number ("18% lower") is not checked yet.
"""

import re
from datetime import date, timedelta

from pydantic import BaseModel, Field

from store_agent.runtime.evidence import Claim, EvidenceLedger

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:AM|PM|am|pm|a\.m\.|p\.m\.)")
DATE_RE = re.compile(
    r"\b(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?,?\s+)?"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:,\s*(\d{4}))?\b"
)
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
NUM_RE = re.compile(
    r"(?P<sign>[+\-−])?(?P<cur>\$)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>[KkMm]\b)?"
    r"(?P<pct>\s?%|\s?(?:pp|percentage points?)\b)?"
)
DIR_RE = re.compile(
    r"(?:\b(?P<word>up|down|increased?|decreased?|rose|fell|dropped|drop|declined?|grew|gained?|lost|higher|lower)"
    r"(?:\s+(?:by|of))?|(?P<arrow>[▲▼]))\s*\(?$",
    re.I,
)
UP_WORDS = {"up", "increase", "increased", "rose", "grew", "gain", "gained", "higher"}
STORE_RE = re.compile(r"\bstore\s*#?\s*(\d{2,4})\b", re.I)
LY_RE = re.compile(r"\b(last year|year ago|year[- ]over[- ]year|yoy)\b|\bLY\b", re.I)


class ValidationResult(BaseModel):
    passed: bool
    checked: int
    violations: list[str] = Field(default_factory=list)


def validate_text(
    text: str, ledger: EvidenceLedger, allowed_stores: set[str] | frozenset[str], check_periods: bool = True
) -> ValidationResult:
    """check_periods=False skips date/comparison-wording checks (for non-data answers that
    may describe capabilities) but still rejects any unsupported figure or store."""
    violations: list[str] = []
    checked = 0

    for m in STORE_RE.finditer(text):
        checked += 1
        if m.group(1).zfill(3) not in allowed_stores:
            violations.append(f"mentions store {m.group(1)} outside the user's scope")

    periods = ledger.periods() if check_periods else []
    if periods:
        covered = _covered_dates(ledger)
        for m in DATE_RE.finditer(text):
            checked += 1
            month, dom, year = MONTHS[m.group(1).lower()[:3]], int(m.group(2)), m.group(3)
            if not any(d.month == month and d.day == dom and (year is None or d.year == int(year)) for d in covered):
                violations.append(f"date '{m.group(0)}' is not in the data that was retrieved")

    if check_periods and LY_RE.search(text) and not ledger.has_comparison("last_year"):
        checked += 1
        violations.append("mentions last year but no last-year comparison was retrieved")

    scrubbed = ISO_DATE_RE.sub(" ", DATE_RE.sub(" ", TIME_RE.sub(" ", text)))
    values = ledger.values()
    for m in NUM_RE.finditer(scrubbed):
        is_pct, is_cur, suf, raw = bool(m.group("pct")), bool(m.group("cur")), m.group("suf"), m.group("num")
        if not (is_pct or is_cur or suf or "," in raw):
            continue
        checked += 1
        mult = {"k": 1e3, "m": 1e6}.get((suf or "").lower(), 1.0)
        shown = float(raw.replace(",", "")) * mult
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        tol = 0.5 * 10 ** (-decimals) * mult + 1e-6

        units = {"percent"} if is_pct else {"currency"} if is_cur else {"count", "currency"}
        magnitude_hits = [v for v in values if v.unit in units and abs(abs(v.value) - shown) <= tol]
        wanted = _direction(m.group("sign"), scrubbed[max(0, m.start() - 30) : m.start()])
        token = m.group(0).strip()
        if not magnitude_hits:
            violations.append(f"'{token}' does not match any retrieved value")
        elif wanted and not any(v.value == 0 or (v.value > 0) == (wanted > 0) for v in magnitude_hits):
            violations.append(f"'{token}' has the wrong direction ({'up' if wanted > 0 else 'down'} stated)")

    return ValidationResult(passed=not violations, checked=checked, violations=violations)


def validate_claims(claims: list[Claim], ledger: EvidenceLedger) -> list[str]:
    known = ledger.known_refs()
    return [f"claim cites unknown evidence '{ref}'" for c in claims for ref in c.evidence if ref not in known]


def _direction(sign: str | None, prefix: str) -> int:
    if sign:
        return 1 if sign == "+" else -1
    m = DIR_RE.search(prefix)
    if not m:
        return 0
    if m.group("arrow"):
        return 1 if m.group("arrow") == "▲" else -1
    return 1 if m.group("word").lower() in UP_WORDS else -1


def _covered_dates(ledger: EvidenceLedger) -> set[date]:
    out: set[date] = set()
    for p in ledger.periods():
        for start, end in ((p.start, p.end), (p.comparison_start, p.comparison_end)):
            if start and end:
                out.update(start + timedelta(days=i) for i in range((end - start).days + 1))
    return out
