"""Conversational create/manage for automations. Schedules come from deterministic slot
parsing; the model never manages timing."""

import re
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from store_agent.automations.models import EVERY_DAY, Automation, AutomationTask, Schedule
from store_agent.automations.repository import AutomationRepository
from store_agent.contracts import AgentResponse, Slots, SuggestedAction

DISABLE_RE = re.compile(r"\b(stop|cancel|pause|disable|turn off|unsubscribe|delete|remove)\b")
RESUME_RE = re.compile(r"\b(resume|restart|turn on|re-?enable|enable|start again)\b")
LIST_RE = re.compile(r"\b(what|which|list|show)\b")

EXAMPLE = "For example: \"Send me yesterday's sales every weekday at 8 AM.\""


def _local(dt: datetime, tz: str) -> str:
    local = dt.astimezone(ZoneInfo(tz))
    return f"{local:%a %b} {local.day} at {local:%I:%M %p}".replace(" 0", " ") + f" {local.tzname()}"


def create(request_id: str, owner: str, store_id: str, tz: str, slots: Slots, repo: AutomationRepository, now: datetime) -> AgentResponse:
    if slots.condition:
        return AgentResponse(
            request_id=request_id,
            text="Alerts based on conditions (like sales dropping below last year) aren't available yet. "
            "I can send you a daily sales briefing instead.",
            actions=[SuggestedAction(title="Daily briefing at 8 AM", message="Send me a sales briefing every day at 8 AM")],
            source="automation",
            status="unsupported",
        )
    if not slots.recurrence:
        return AgentResponse(request_id=request_id, text=f"When should I send it? {EXAMPLE}", source="automation", status="clarify")

    schedule = Schedule(days_of_week=slots.schedule_days or list(EVERY_DAY), time=slots.schedule_time or "08:00")
    for existing in repo.list_for_owner(owner):
        if existing.status == "active" and existing.task.store_id == store_id and existing.schedule == schedule:
            return AgentResponse(
                request_id=request_id,
                text=f"You already get this briefing {schedule.describe()}.",
                source="automation",
            )

    a = Automation(
        automation_id=f"a-{uuid4().hex[:8]}",
        owner=owner,
        schedule=schedule,
        timezone=tz,
        task=AutomationTask(store_id=store_id),
        created_at=now,
        updated_at=now,
        next_run_at=schedule.next_run(now, tz),
    )
    repo.save(a)
    defaulted = "" if slots.schedule_time else " (I picked 8 AM; tell me if you'd like a different time)"
    return AgentResponse(
        request_id=request_id,
        text=f"Done. I'll send your sales briefing {schedule.describe()}{defaulted}. The first one arrives {_local(a.next_run_at, tz)}.",
        actions=[SuggestedAction(title="My briefings", message="What briefings do I have?")],
        source="automation",
    )


def manage(request_id: str, owner: str, message: str, slots: Slots, repo: AutomationRepository, now: datetime) -> AgentResponse:
    t = message.lower()
    items = repo.list_for_owner(owner)
    if not items:
        return AgentResponse(request_id=request_id, text=f"You don't have any briefings set up. {EXAMPLE}", source="automation")

    wants_change = slots.schedule_time is not None or slots.schedule_days is not None
    if LIST_RE.search(t) and not (DISABLE_RE.search(t) or RESUME_RE.search(t) or wants_change):
        lines = [f"- {_describe(a)}" for a in items]
        return AgentResponse(request_id=request_id, text="Your briefings:\n" + "\n".join(lines), source="automation")

    explicit = [a for a in items if a.automation_id in t]
    active = [a for a in items if a.status == "active"]
    targets = explicit or (active if RESUME_RE.search(t) is None else [a for a in items if a.status == "paused"])
    if len(targets) != 1:
        options = [SuggestedAction(title=_describe(a), message=f"{message.strip()} ({a.automation_id})") for a in items]
        return AgentResponse(request_id=request_id, text="Which briefing do you mean?", actions=options, source="automation", status="clarify")
    a = targets[0]

    if DISABLE_RE.search(t):
        a.status, a.updated_at = "paused", now
        repo.save(a)
        return AgentResponse(
            request_id=request_id,
            text=f"Stopped your sales briefing ({a.schedule.describe()}). Say \"resume my briefing\" to turn it back on.",
            source="automation",
        )
    if RESUME_RE.search(t):
        a.status, a.updated_at, a.next_run_at = "active", now, a.schedule.next_run(now, a.timezone)
        repo.save(a)
        return AgentResponse(request_id=request_id, text=f"Your briefing is back on: {a.schedule.describe()}.", source="automation")
    if wants_change:
        a.schedule = Schedule(days_of_week=slots.schedule_days or a.schedule.days_of_week, time=slots.schedule_time or a.schedule.time)
        a.updated_at, a.next_run_at = now, a.schedule.next_run(now, a.timezone)
        repo.save(a)
        return AgentResponse(
            request_id=request_id,
            text=f"Updated. Your sales briefing now arrives {a.schedule.describe()}. Next one: {_local(a.next_run_at, a.timezone)}.",
            source="automation",
        )
    return AgentResponse(
        request_id=request_id,
        text="I can list, stop, resume, or reschedule your briefings. For example: \"Move my briefing to 7:30\".",
        source="automation",
        status="clarify",
    )


def _describe(a: Automation) -> str:
    state = "" if a.status == "active" else f" [{a.status}]"
    return f"Sales briefing, {a.schedule.describe()} ({a.automation_id}){state}"
