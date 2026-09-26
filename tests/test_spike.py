"""The five-interaction vertical slice from the handoff (section 37), end to end."""

from datetime import datetime, timezone

from store_agent.formatting import money, pct
from store_agent.tools.semantic.contract import SemanticQuery, TimeRange


def _truth(app, **kw):
    q = SemanticQuery(store_id="042", time_range=TimeRange(type="yesterday"), **kw)
    return app.runtime.semantic_layer.query(q)


def test_five_interactions_end_to_end(app, chat):
    # 1. lookup: no model, numbers straight from the semantic layer
    r1, t1 = chat.ask("What were sales yesterday?")
    truth = _truth(app, metrics=["sales"]).rows[0]
    assert (t1.capability, t1.reasoning_tier) == ("LOOKUP", "NONE")
    assert money(truth["sales"]) in r1.text and "Fri Sep 25" in r1.text
    assert t1.model_calls == [] and t1.validation["passed"]

    # 2. follow-up inherits metric + date, adds the comparison; FAST tier phrasing
    r2, t2 = chat.ask("Compare that with last year.")
    truth = _truth(app, metrics=["sales"], comparison="last_year").rows[0]
    assert (t2.capability, t2.reasoning_tier) == ("COMPARE", "FAST")
    assert t2.tool_calls[0].args["time_range"]["type"] == "yesterday"
    assert t2.tool_calls[0].args["comparison"] == "last_year"
    assert pct(truth["sales_pct"]) in r2.text
    assert [m.tier for m in t2.model_calls] == ["FAST"] and t2.validation["passed"]

    # 3. department breakdown of the difference, still inheriting context
    r3, t3 = chat.ask("Which departments drove the difference?")
    assert t3.capability == "COMPARE"
    assert t3.tool_calls[0].args["dimensions"] == ["department"]
    assert t3.tool_calls[0].args["comparison"] == "last_year"
    assert "Biggest declines: Bedroom" in r3.text  # planted scenario
    assert t3.validation["passed"]

    # 4. diagnosis: STANDARD tier, model-chosen tool calls, evidence-validated answer
    r4, t4 = chat.ask("Why was Bedroom down?")
    assert (t4.capability, t4.reasoning_tier) == ("DIAGNOSE", "STANDARD")
    assert [c.name for c in t4.tool_calls] == ["get_metric_summary", "get_availability", "get_breakdown"]
    assert all(c.args["filters"] == {"department": ["Bedroom"]} for c in t4.tool_calls)
    assert "Bedroom sales" in r4.text and "Availability was" in r4.text and "down" in r4.text
    assert t4.validation["passed"] and r4.status == "ok"

    # 5. automation persisted as a structured definition
    r5, t5 = chat.ask("Send me this briefing every weekday at 8 AM.")
    assert t5.capability == "CREATE_AUTOMATION"
    [auto] = app.automations.list_for_owner("u-anna")
    assert auto.schedule.cron() == "0 8 * * 1-5"
    assert auto.timezone == "America/New_York"
    assert auto.task.store_id == "042"
    assert auto.next_run_at == datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)  # Mon 8:00 EDT
    assert "every weekday at 8:00 AM" in r5.text

    # ...and delivered proactively by the scheduler, exactly once
    app.clock.set(datetime(2026, 9, 28, 12, 0, 30, tzinfo=timezone.utc))
    assert app.scheduler.tick() == [(auto.automation_id, "delivered")]
    assert app.scheduler.tick() == []
    [msg] = app.outbox.messages("u-anna")
    assert msg["payload"]["text"].startswith("Good morning - Yesterday's performance (Sun Sep 27)")
    assert msg["payload"]["card"]["type"] == "AdaptiveCard"
    assert any(a["title"] == "Explain Bedroom decline" for a in msg["payload"]["card"]["actions"])
    stored = app.automations.get(auto.automation_id)
    assert stored.last_status == "delivered"
    assert stored.next_run_at == datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_briefing_card_action_starts_a_normal_turn(app, chat):
    r, t = chat.ask("Why was Bedroom down yesterday?")  # the briefing's "Explain Bedroom decline" message
    assert t.capability == "DIAGNOSE" and r.status == "ok"


def test_followups_do_not_leak_across_conversations(app, chat):
    chat.ask("Why was Bedroom down yesterday?")
    chat.channel.new_conversation()
    _, t = chat.ask("Show sales by department yesterday vs last year")
    assert "department" not in t.tool_calls[0].args["filters"]


def test_stale_context_is_not_inherited(app, chat):
    chat.ask("Compare sales last 7 days with last year")
    app.clock.set(datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc))  # +60 min > 30 min TTL
    _, t = chat.ask("transactions")
    assert t.tool_calls[0].args["time_range"]["type"] == "yesterday"
    assert t.tool_calls[0].args["comparison"] is None


def test_short_followups_inherit_capability(chat):
    chat.ask("Why was Bedroom down yesterday?")
    _, t = chat.ask("What about Kitchens?")
    assert t.capability == "DIAGNOSE"
    assert t.tool_calls[0].args["filters"] == {"department": ["Kitchens"]}

    chat.channel.new_conversation()
    chat.ask("Compare sales yesterday with last year")
    _, t = chat.ask("And transactions?")
    assert t.capability == "COMPARE" and t.tool_calls[0].args["metrics"] == ["transactions"]


def test_data_not_available_is_reported_not_invented(chat):
    r, t = chat.ask("How are sales today?")
    assert r.status == "error" and "only available through Fri Sep 25" in r.text
    assert t.tool_calls[0].status == "error"
