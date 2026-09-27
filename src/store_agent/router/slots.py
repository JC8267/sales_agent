"""Deterministic slot extraction from employee messages, driven by the semantic catalog."""

import re
from datetime import date

from store_agent.config import SemanticCatalog
from store_agent.contracts import Slots
from store_agent.tools.semantic.contract import TimeRange

NUMBER_WORDS = {w: i for i, w in enumerate(
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
     "eleven", "twelve", "thirteen", "fourteen", "fifteen"])}
_NUM = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"
DAY_WORDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

LY_RE = re.compile(r"\b(last year|ly|yoy|year[- ]over[- ]year|a year ago|prior year|previous year)\b")
PP_RE = re.compile(r"\b(prior period|previous period|the week before|the day before|week[- ]over[- ]week)\b")
ANAPHORA_RE = re.compile(r"\b(that|this|it|those|these|them|there|the difference|same)\b")
CONTINUE_RE = re.compile(r"^(and|what about|how about|same for|now|also)\b")
RECURRENCE_RE = re.compile(
    r"\b(daily|weekly|recurring|mornings)\b|\b(every|each)\s+(day|morning|evening|night|week|weekday|weekend|monday|"
    r"tuesday|wednesday|thursday|friday|saturday|sunday)s?\b"
)
CONDITION_RE = re.compile(r"\b(alert|notify|tell|ping|warn|let)\s+(me|us)\b.*\b(if|when|whenever)\b")
BRIEFING_RE = re.compile(r"\b(briefing|performance|recap|summary|how did we do)\b")


class DateClarification(ValueError):
    pass


def _explicit_period(text: str) -> TimeRange | None:
    dates = re.findall(r"\b\d{4}-\d{1,2}-\d{1,2}\b", text)
    month = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    unsupported = re.search(
        rf"\b{month}\s+\d|\b\d{{1,2}}\s+{month}\b|\b\d{{1,4}}/\d{{1,2}}"
        r"|\btomorrow\b|\b(?:last|this|next)\s+(?:month|quarter)\b|\b(?:this|next)\s+(?:week|year)\b", text,
    )
    hint = "Please specify a date as YYYY-MM-DD or a range as from YYYY-MM-DD to YYYY-MM-DD."
    weekday = re.search(r"\b(?:on|last|this|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text)
    if unsupported or (weekday and not RECURRENCE_RE.search(text) and not re.search(r"\bonly send\b|\bsend it\b", text)):
        raise DateClarification(hint)
    if not dates:
        return None
    if re.search(r"\b(yesterday|today|tomorrow)\b|\b(?:last|past|previous)\s+(?:\w+\s+)?(?:days?|weeks?)\b", text):
        raise DateClarification(hint)
    if len(dates) > 2 or (len(dates) == 2 and not re.search(rf"\bfrom {dates[0]} (?:to|through) {dates[1]}\b|\bbetween {dates[0]} and {dates[1]}\b", text)):
        raise DateClarification(hint)
    try:
        start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        return TimeRange(type="date_range", start=start, end=end)
    except ValueError as e:
        raise DateClarification(hint) from e


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else NUMBER_WORDS[tok]


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![\w']){re.escape(phrase)}(?![\w'])", text) is not None


def extract_slots(message: str, catalog: SemanticCatalog) -> Slots:
    t = " ".join(message.lower().replace("’", "'").split())
    s = Slots()

    for name, m in catalog.metrics.items():
        if any(_has(t, syn) for syn in sorted(m.synonyms, key=len, reverse=True)):
            s.metrics.append(name)

    for d in catalog.departments:
        if any(_has(t, a) for a in d.aliases):
            s.departments.append(d.name)

    s.time_range = _explicit_period(t)
    if s.time_range is not None:
        pass
    elif re.search(r"\byesterday('s)?\b", t):
        s.time_range = TimeRange(type="yesterday")
    elif re.search(r"\btoday('s)?\b|\bso far\b", t):
        s.time_range = TimeRange(type="today")
    elif m := re.search(rf"\b(?:last|past|previous)\s+{_NUM}\s+days\b", t):
        s.time_range = TimeRange(type="last_n_days", n=_num(m.group(1)))
    elif m := re.search(rf"\b(?:last|past|previous)\s+{_NUM}\s+weeks\b", t):
        s.time_range = TimeRange(type="last_n_days", n=7 * _num(m.group(1)))
    elif re.search(r"\b(?:last|past|previous)\s+week\b", t):
        s.time_range = TimeRange(type="last_n_days", n=7)

    if LY_RE.search(t):
        s.comparison = "last_year"
    elif PP_RE.search(t):
        s.comparison = "prior_period"

    if re.search(r"\b(departments|depts|categories)\b|\bby (department|dept|category)\b", t) or (
        re.search(r"\b(department|dept)\b", t) and not s.departments
    ):
        s.dimensions.append("department")
    if re.search(r"\b(hourly|by hour|per hour|each hour|hour by hour|hours)\b", t):
        s.dimensions.append("hour")
    elif re.search(r"\b(by day|each day|per day|day by day|trend)\b", t):
        s.dimensions.append("date")

    if m := re.search(rf"\b(top|bottom|best|worst)\s+{_NUM}\b", t):
        s.limit = _num(m.group(2))
        s.ascending = m.group(1) in ("bottom", "worst")
    elif re.search(r"\b(bottom|worst)\b", t):
        s.ascending = True

    s.store_ids = [g.zfill(3) for pair in re.findall(r"\bstore\s*#?\s*(\d{1,4})\b|#(\d{3})\b", t) for g in pair if g]
    s.anaphora = bool(ANAPHORA_RE.search(t) or CONTINUE_RE.search(t) or (
        len(t.split()) <= 3 and (s.metrics or s.departments or s.time_range)
    ))
    s.condition = bool(CONDITION_RE.search(t))
    s.briefing = bool(BRIEFING_RE.search(t))

    s.schedule_days = _days(t)
    s.recurrence = bool(RECURRENCE_RE.search(t)) or s.schedule_days is not None
    s.schedule_time = _time_of_day(t)
    return s


def _days(t: str) -> list[int] | None:
    if re.search(r"\bweekdays?\b|\bworkdays?\b|\bmon(?:day)?s?\s*(?:-|to|through|thru)\s*fri(?:day)?s?\b", t):
        return [0, 1, 2, 3, 4]
    if re.search(r"\bweekends?\b", t):
        return [5, 6]
    if re.search(r"\b(every ?day|daily|every (morning|evening|night)|each (morning|day|evening))\b", t):
        return [0, 1, 2, 3, 4, 5, 6]
    named = [i for i, d in enumerate(DAY_WORDS) if re.search(rf"\b{d}s?\b", t)]
    return named or None


def _time_of_day(t: str) -> str | None:
    if re.search(r"\bnoon\b", t):
        return "12:00"
    m = re.search(r"\b(?:at|to|for|around|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\d%])", t) or re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", t
    )
    if not m:
        return None
    h, mins, mer = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if h > 23 or mins > 59:
        return None
    if mer == "pm" and h < 12:
        h += 12
    elif mer == "am" and h == 12:
        h = 0
    elif not mer and h < 12 and re.search(r"\b(evening|night|tonight)\b", t):
        h += 12
    return f"{h:02d}:{mins:02d}"
