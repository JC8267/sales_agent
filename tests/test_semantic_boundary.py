from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from store_agent.config import load_settings
from store_agent.runtime.compose import compare
from store_agent.runtime.evidence import EvidenceLedger
from store_agent.tools.semantic.calendar import resolve_period
from store_agent.tools.semantic.contract import SemanticQuery, SemanticResult, TimeRange
from store_agent.validation.numeric import validate_text

CATALOG = load_settings().catalog


class FixedLayer:
    def __init__(self, rows):
        self.rows = rows

    def query(self, q):
        return SemanticResult(
            query=q, rows=self.rows, source="fixed",
            period=resolve_period(q.time_range, q.comparison, date(2026, 9, 26), CATALOG),
        )


@pytest.mark.parametrize("rows", [[], [{"sales": None}], [{}]])
@pytest.mark.parametrize("message", ["Sales yesterday", "Compare sales yesterday with last year", "Daily briefing", "Why were sales down yesterday?"])
def test_missing_data_is_reported_without_numbers(app, chat, rows, message):
    app.runtime.semantic_layer = FixedLayer(rows)
    response, trace = chat.ask(message)
    assert response.status == "error"
    assert "data is unavailable" in response.text
    assert response.kpis == [] and response.table == []
    assert trace.tool_calls[0].status == "error"
    assert trace.evidence == []
    scheduled = app.runtime.run_briefing("u-anna", "042")
    assert scheduled.status == "error" and "data is unavailable" in scheduled.text


@pytest.mark.parametrize("metric,value,expected", [("sales", 100.0, "$100"), ("transactions", 10, "+10")])
def test_zero_baseline_uses_absolute_metric_units(metric, value, expected):
    row = {metric: value, f"{metric}_cmp": 0, f"{metric}_diff": value, f"{metric}_pct": None}
    q = SemanticQuery(store_id="042", metrics=[metric], time_range=TimeRange(type="yesterday"), comparison="last_year")
    result = FixedLayer([row]).query(q)
    draft = compare(result, None, CATALOG)
    assert "flat" not in draft.text and "percentage points" not in draft.kpis[0].change
    assert expected in draft.kpis[0].change
    ledger = EvidenceLedger(CATALOG)
    ledger.add("fixed", {}, result.rows, result.period)
    assert validate_text(draft.text, ledger, {"042"}).passed


@pytest.mark.parametrize("baseline", [0, None])
def test_briefing_handles_zero_or_missing_comparison(app, chat, baseline):
    row = {}
    for metric in ("sales", "transactions", "aov"):
        row.update({metric: 100.0, f"{metric}_cmp": baseline, f"{metric}_diff": 100.0 if baseline == 0 else None, f"{metric}_pct": None})
    row["department"] = "Bedroom"
    app.runtime.semantic_layer = FixedLayer([row])
    response, trace = chat.ask("Daily briefing")
    assert response.status == "ok" and trace.validation["passed"]
    assert "percentage points" not in response.text and "None" not in response.text
    assert app.runtime.run_briefing("u-anna", "042").status == "ok"


def test_decimal_metrics_are_rendered_and_recorded_as_numeric_evidence(app, chat):
    app.runtime.semantic_layer = FixedLayer([{"sales": Decimal("1234.50")}])
    response, trace = chat.ask("Sales yesterday")
    assert response.status == "ok" and "$1.2K" in response.text
    assert trace.validation["passed"] and trace.evidence[0].values[0].value == 1234.5
    ledger = EvidenceLedger(CATALOG)
    ledger.add("fixed", {}, [{"sales": Decimal("1234.50")}])
    assert validate_text("Sales were $1,234.50.", ledger, {"042"}).passed


def test_diagnosis_with_undefined_percentages_returns_verified_absolute_changes(app, chat):
    row = {"sales": 100.0, "transactions": 10, "aov": 10.0}
    for m in ("sales", "transactions", "aov"):
        row.update({f"{m}_cmp": 0, f"{m}_diff": row[m], f"{m}_pct": None})
    app.runtime.semantic_layer = FixedLayer([row])
    response, trace = chat.ask("Why were sales down yesterday?")
    assert response.status == "degraded" and trace.validation["passed"]
    assert "flat" not in response.text and "percentage points" not in response.text


def test_percentage_metrics_still_use_percentage_points():
    q = SemanticQuery(store_id="042", metrics=["availability"], time_range=TimeRange(type="yesterday"), comparison="last_year")
    result = FixedLayer([{"availability": 95.0, "availability_cmp": 90.0, "availability_diff": 5.0, "availability_pct": None}]).query(q)
    draft = compare(result, None, CATALOG)
    assert "up 5.0 percentage points" in draft.text
    assert "+5.0 percentage points" in draft.kpis[0].change


def test_valid_time_ranges_accept_single_day_and_positive_count():
    assert TimeRange(type="last_n_days", n=1).n == 1
    assert TimeRange(type="date_range", start="2026-09-25", end="2026-09-25").start == date(2026, 9, 25)


@pytest.mark.parametrize("args", [
    {"type": "last_n_days", "n": 0},
    {"type": "last_n_days", "n": -3},
    {"type": "last_n_days"},
    {"type": "date_range"},
    {"type": "date_range", "start": "2026-09-25"},
    {"type": "date_range", "start": "2026-09-25", "end": "2026-09-20"},
])
def test_invalid_time_ranges_are_rejected(args):
    with pytest.raises(ValidationError):
        TimeRange(**args)


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_query_limits_are_rejected(limit):
    with pytest.raises(ValidationError):
        SemanticQuery(store_id="042", metrics=["sales"], time_range=TimeRange(type="yesterday"), limit=limit)


@pytest.mark.parametrize("message", ["Sales last zero days", "Top zero departments yesterday"])
def test_invalid_user_queries_do_not_reach_data(app, chat, message):
    response, trace = chat.ask(message)
    assert response.status == "error" and "positive" in response.text
    assert trace.tool_calls == []
