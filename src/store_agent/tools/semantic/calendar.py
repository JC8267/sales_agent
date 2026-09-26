"""Relative-period resolution. In production this belongs to the semantic layer's
fiscal calendar (e.g. a BigQuery calendar table); the LLM never does date math."""

from datetime import date, timedelta

from store_agent.config import SemanticCatalog
from store_agent.formatting import day, day_year
from store_agent.tools.semantic.contract import ComparisonKind, Period, TimeRange, UnsupportedQuery


def resolve_period(tr: TimeRange, comparison: ComparisonKind | None, today: date, catalog: SemanticCatalog) -> Period:
    if tr.type == "yesterday":
        start = end = today - timedelta(days=1)
        label = f"yesterday ({day(start)})"
    elif tr.type == "today":
        start = end = today
        label = f"today ({day(today)})"
    elif tr.type == "last_n_days":
        n = tr.n or 7
        end = today - timedelta(days=1)
        start = end - timedelta(days=n - 1)
        label = f"the last {n} days ({day(start)} - {day(end)})"
    elif tr.type == "date_range" and tr.start and tr.end:
        start, end = tr.start, tr.end
        label = day(start) if start == end else f"{day(start)} - {day(end)}"
    else:
        raise UnsupportedQuery("I couldn't work out which dates you meant.")

    period = Period(start=start, end=end, label=label)
    if comparison is None:
        return period

    if comparison == "last_year":
        offset = timedelta(days=catalog.comparisons["last_year"].offset_days or 364)
        cmp_label = "the same day last year" if start == end else "the same days last year"
    else:
        offset = timedelta(days=(end - start).days + 1)
        cmp_label = "the day before" if start == end else f"the previous {(end - start).days + 1} days"
    c_start, c_end = start - offset, end - offset
    period.comparison = comparison
    period.comparison_start, period.comparison_end = c_start, c_end
    span = day_year(c_start) if c_start == c_end else f"{day(c_start)} - {day_year(c_end)}"
    period.comparison_label = f"{cmp_label} ({span})"
    return period
