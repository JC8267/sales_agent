from datetime import date
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from store_agent.app import build_app
from store_agent.runtime.compose import Draft
from store_agent.contracts import Kpi

from conftest import Chat


def test_failed_draft_is_not_delivered(chat):
    bad = Draft(text="Sales were $999999999.00.", kpis=[Kpi(label="Sales", value="$999999999.00")])
    with patch("store_agent.runtime.agent.compose.lookup", return_value=bad):
        response, trace = chat.ask("Sales yesterday")
    assert not trace.validation["passed"]
    assert response.status == "error" and "999999999" not in response.text
    assert response.kpis == [] and response.table == [] and response.actions == []


def test_failed_diagnosis_fallback_is_not_delivered(app):
    from store_agent.models.fake import FakeProvider
    from store_agent.validation.numeric import ValidationResult

    app.gateway.providers["fake"] = FakeProvider(unavailable={f"fake/{tier}-{n}" for tier in ("fast", "standard", "deep") for n in ("a", "b")})
    with patch("store_agent.runtime.agent.validate_text", return_value=ValidationResult(passed=False, checked=1, violations=["bad draft"])):
        response, trace = Chat(app).ask("Why were sales down yesterday?")
    assert response.status == "error" and response.kpis == []
    assert "verified numbers" not in response.text and not trace.validation["passed"]


def test_explicit_store_overrides_home_and_previous_store(app):
    chat = Chat(app, "u-rita")
    chat.ask("Sales for store 042 yesterday")
    response, trace = chat.ask("Show the briefing for store 017")
    assert response.status == "ok" and "Store 017" in response.text
    assert {c.args["store_id"] for c in trace.tool_calls} == {"017"}
    chat.ask("Send me this briefing every day at 8 AM")
    chat.ask("Send me a briefing for store 042 every day at 9 AM")
    assert {(a.task.store_id, a.schedule.time) for a in app.automations.list_for_owner("u-rita")} == {("017", "08:00"), ("042", "09:00")}


@pytest.mark.parametrize("text,start,end", [
    ("Sales on 2026-09-20", "2026-09-20", "2026-09-20"),
    ("Sales from 2026-09-20 to 2026-09-22", "2026-09-20", "2026-09-22"),
])
def test_explicit_dates_reach_semantic_query(chat, text, start, end):
    response, trace = chat.ask(text)
    assert response.status == "ok"
    assert trace.tool_calls[0].args["time_range"] == {"type": "date_range", "start": start, "end": end, "n": None}
    assert trace.evidence[0].period.start == date.fromisoformat(start)


@pytest.mark.parametrize("text", [
    "Sales on September 20", "Sales on 09/20/2026", "Sales last month", "Sales tomorrow",
    "Sales on 2026-02-30", "Sales from 2026-09-22 to 2026-09-20",
    "Compare sales on 2026-09-20 vs 2026-09-22", "Sales yesterday on 2026-09-20",
    "Sales on Monday", "Send me sales from last month every day",
])
def test_unsupported_dates_request_clarification(chat, text):
    response, trace = chat.ask(text)
    assert response.status == "clarify" and "YYYY-MM-DD" in response.text
    assert trace.tool_calls == []


def test_followup_preserves_store_department_and_comparison(app):
    chat = Chat(app, "u-rita")
    chat.ask("Compare Bedroom sales for store 017 yesterday with the prior period")
    response, trace = chat.ask("And transactions?")
    assert response.status == "ok"
    args = trace.tool_calls[0].args
    assert args["store_id"] == "017" and args["filters"] == {"department": ["Bedroom"]}
    assert args["metrics"] == ["transactions"] and args["comparison"] == "prior_period"


def test_briefing_resets_prior_period_to_the_displayed_period(chat):
    chat.ask("Sales last 7 days")
    chat.ask("Daily briefing")
    _, trace = chat.ask("And transactions?")
    assert trace.tool_calls[0].args["time_range"]["type"] == "yesterday"


def test_overlapping_scheduler_ticks_deliver_once(app, chat):
    chat.ask("Send me a briefing every day at 8 AM")
    [automation] = app.automations.list_for_owner("u-anna")
    app.clock.set(automation.next_run_at)
    path = app.conn.execute("pragma database_list").fetchone()["file"]
    second = build_app(path, clock=app.clock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda scheduler: scheduler.tick(), [app.scheduler, second.scheduler]))
        assert sum(len(r) for r in results) == 1
        assert len(app.outbox.messages()) == 1
    finally:
        second.conn.close()


@pytest.mark.parametrize("interrupt_at", ["execute", "finish_run"])
def test_interrupted_scheduler_recovers_after_restart(app, chat, interrupt_at):
    chat.ask("Send me a briefing every day at 8 AM")
    [automation] = app.automations.list_for_owner("u-anna")
    app.clock.set(automation.next_run_at)
    target = app.scheduler.executor if interrupt_at == "execute" else app.automations
    with patch.object(target, interrupt_at, side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            app.scheduler.tick()
    assert app.automations.get(automation.automation_id).next_run_at == automation.next_run_at
    assert app.automations.runs(automation.automation_id) == []
    assert app.outbox.messages() == []
    path = app.conn.execute("pragma database_list").fetchone()["file"]
    app.conn.close()
    restarted = build_app(path, clock=app.clock)
    try:
        assert restarted.scheduler.tick() == [(automation.automation_id, "delivered")]
        assert restarted.scheduler.tick() == []
        assert len(restarted.outbox.messages()) == 1
        assert restarted.automations.runs(automation.automation_id)[0]["status"] == "delivered"
    finally:
        restarted.conn.close()
