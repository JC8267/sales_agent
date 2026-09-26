from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAYS = [0, 1, 2, 3, 4]
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


class Schedule(BaseModel):
    """Weekly day/time schedule in the owner's local timezone. Structured rather than a raw
    cron string so "move it to 7:30" / "only weekdays" are field edits; cron() exports it."""

    days_of_week: list[int] = Field(default_factory=lambda: list(EVERY_DAY))  # 0 = Monday
    time: str = "08:00"  # HH:MM local

    def _hm(self) -> tuple[int, int]:
        h, m = self.time.split(":")
        return int(h), int(m)

    def cron(self) -> str:
        h, m = self._hm()
        days = sorted(set(self.days_of_week))
        if days == EVERY_DAY:
            dow = "*"
        elif days == WEEKDAYS:
            dow = "1-5"
        else:
            dow = ",".join(str((d + 1) % 7) for d in days)  # cron: 0 = Sunday
        return f"{m} {h} * * {dow}"

    def describe(self) -> str:
        h, m = self._hm()
        clock = time(h, m).strftime("%I:%M %p").lstrip("0")
        days = sorted(set(self.days_of_week))
        if days == EVERY_DAY:
            when = "every day"
        elif days == WEEKDAYS:
            when = "every weekday"
        elif days == [5, 6]:
            when = "every weekend day"
        else:
            when = "every " + ", ".join(DAY_NAMES[d] for d in days)
        return f"{when} at {clock}"

    def next_run(self, after: datetime, tz: str) -> datetime:
        """First occurrence strictly after `after`, returned in UTC."""
        zone = ZoneInfo(tz)
        local_after = after.astimezone(zone)
        h, m = self._hm()
        for offset in range(8):
            day = local_after.date() + timedelta(days=offset)
            if day.weekday() not in self.days_of_week:
                continue
            candidate = datetime.combine(day, time(h, m), tzinfo=zone)
            if candidate.astimezone(timezone.utc) > after.astimezone(timezone.utc):
                return candidate.astimezone(timezone.utc)
        raise ValueError("schedule has no days")


class AutomationTask(BaseModel):
    type: Literal["DAILY_SALES_BRIEFING"] = "DAILY_SALES_BRIEFING"
    store_id: str


class Delivery(BaseModel):
    channel: Literal["teams_dm"] = "teams_dm"


class Condition(BaseModel):
    """Designed for conditional alerts (phase 7); evaluated deterministically, never re-read by a model."""

    metric: str
    operator: Literal["<", "<=", ">", ">="]
    threshold: float
    evaluation_frequency: Literal["hourly", "daily"] = "daily"


class Automation(BaseModel):
    automation_id: str
    owner: str
    type: Literal["scheduled", "conditional"] = "scheduled"
    status: Literal["active", "paused", "deleted"] = "active"
    schedule: Schedule
    timezone: str
    task: AutomationTask
    delivery: Delivery = Field(default_factory=Delivery)
    condition: Condition | None = None
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
