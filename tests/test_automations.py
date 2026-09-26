from datetime import datetime, timedelta, timezone

import pytest

from store_agent.automations.models import Automation, AutomationTask, Schedule
from store_agent.config import load_settings
from store_agent.router.slots import extract_slots

CATALOG = load_settings().catalog


@pytest.mark.parametrize(
    "text,days,time",
    [
        ("Send me this briefing every weekday at 8 AM.", [0, 1, 2, 3, 4], "08:00"),
        ("Give me yesterday's performance every morning at 8.", [0, 1, 2, 3, 4, 5, 6], "08:00"),
        ("Move my report to 7:30", None, "07:30"),
        ("send it every day at 6pm", [0, 1, 2, 3, 4, 5, 6], "18:00"),
        ("every monday and thursday at 9 send me sales", [0, 3], "09:00"),
        ("weekends at noon", [5, 6], "12:00"),
        ("Only send it Monday through Friday", [0, 1, 2, 3, 4], None),
        ("Alert me when sales are more than 10% below last year", None, None),
    ],
)
def test_schedule_slots(text, days, time):
    s = extract_slots(text, CATALOG)
    assert (s.schedule_days, s.schedule_time) == (days, time)


def test_cron_and_description():
    assert Schedule(days_of_week=[0, 1, 2, 3, 4], time="08:00").cron() == "0 8 * * 1-5"
    assert Schedule(time="07:30").cron() == "30 7 * * *"
    assert Schedule(days_of_week=[0, 3], time="09:05").cron() == "5 9 * * 1,4"
    assert Schedule(days_of_week=[0, 1, 2, 3, 4], time="08:00").describe() == "every weekday at 8:00 AM"


def test_next_run_handles_dst_change():
    s = Schedule(time="08:00")
    sat_after_run = datetime(2026, 10, 31, 12, 30, tzinfo=timezone.utc)  # Sat 8:30 EDT
    assert s.next_run(sat_after_run, "America/New_York") == datetime(2026, 11, 1, 13, 0, tzinfo=timezone.utc)  # Sun 8:00 EST


def test_manage_flow(app, chat):
    chat.ask("Send me this briefing every weekday at 8 AM.")
    r, _ = chat.ask("What briefings do I have?")
    assert "every weekday at 8:00 AM" in r.text

    r, t = chat.ask("Move my report to 7:30.")
    assert t.capability == "MANAGE_AUTOMATION" and "7:30 AM" in r.text
    [a] = app.automations.list_for_owner("u-anna")
    assert a.schedule.time == "07:30" and a.next_run_at == datetime(2026, 9, 28, 11, 30, tzinfo=timezone.utc)

    chat.ask("Only send it Monday through Friday.")
    r, _ = chat.ask("Stop my morning report.")
    [a] = app.automations.list_for_owner("u-anna")
    assert a.status == "paused" and "Stopped" in r.text
    app.clock.set(datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc))
    assert app.scheduler.tick() == []

    r, _ = chat.ask("Resume my briefing")
    [a] = app.automations.list_for_owner("u-anna")
    assert a.status == "active" and a.next_run_at == datetime(2026, 9, 29, 11, 30, tzinfo=timezone.utc)


def test_duplicate_and_conditional_and_unscheduled_requests(app, chat):
    chat.ask("Send me this briefing every weekday at 8 AM.")
    r, _ = chat.ask("Send me this briefing every weekday at 8 AM.")
    assert "already" in r.text and len(app.automations.list_for_owner("u-anna")) == 1

    r, t = chat.ask("Alert me when sales are more than 10% below last year.")
    assert t.capability == "CREATE_AUTOMATION" and r.status == "unsupported"

    r, _ = chat.ask("Schedule a sales briefing for me")
    assert r.status == "clarify"


def test_missed_runs_are_recorded_not_sent(app, chat):
    chat.ask("Send me a sales briefing every day at 8 AM")
    [a] = app.automations.list_for_owner("u-anna")
    app.clock.set(a.next_run_at + timedelta(hours=3))
    assert app.scheduler.tick() == [(a.automation_id, "missed")]
    assert app.outbox.messages() == []
    assert app.automations.runs(a.automation_id)[0]["status"] == "missed"


def test_delivery_without_conversation_reference_fails_and_is_recorded(app):
    now = app.clock.now()
    s = Schedule(time="08:00")
    a = Automation(
        automation_id="a-noref", owner="u-marco", schedule=s, timezone="America/New_York",
        task=AutomationTask(store_id="042"), created_at=now, updated_at=now, next_run_at=s.next_run(now, "America/New_York"),
    )
    app.automations.save(a)
    app.clock.set(a.next_run_at)
    assert app.scheduler.tick() == [("a-noref", "delivery_failed")]
    assert "no conversation reference" in app.automations.get("a-noref").last_error
