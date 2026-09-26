"""Scoped, explicit conversation memory: one analytic frame per conversation, not the
transcript. Follow-ups inherit frame fields they don't restate."""

import sqlite3
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from store_agent.clock import utc_iso
from store_agent.contracts import Capability, Slots
from store_agent.tools.semantic.contract import TimeRange


class Frame(BaseModel):
    capability: Capability
    store_id: str
    metrics: list[str]
    time_range: TimeRange
    comparison: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    updated_at: datetime


class ConversationStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_fresh(self, conversation_id: str, now: datetime, ttl_minutes: int) -> Frame | None:
        row = self.conn.execute("select frame_json from conversation_frames where conversation_id = ?", (conversation_id,)).fetchone()
        if not row:
            return None
        frame = Frame.model_validate_json(row["frame_json"])
        return frame if now - frame.updated_at <= timedelta(minutes=ttl_minutes) else None

    def save(self, conversation_id: str, user_id: str, frame: Frame) -> None:
        self.conn.execute(
            "insert or replace into conversation_frames values (?, ?, ?, ?)",
            (conversation_id, user_id, frame.model_dump_json(), utc_iso(frame.updated_at)),
        )

    def clear(self, conversation_id: str) -> None:
        self.conn.execute("delete from conversation_frames where conversation_id = ?", (conversation_id,))


def is_followup(slots: Slots, prior: Frame | None) -> bool:
    return prior is not None and (slots.anaphora or (not slots.metrics and slots.time_range is None))


def resolve_frame(
    capability: Capability, slots: Slots, prior: Frame | None, store_id: str, default_metric: str, now: datetime
) -> Frame:
    follow = is_followup(slots, prior)
    inherit = prior if follow else None
    comparison = slots.comparison or (inherit.comparison if inherit else None)
    if capability in (Capability.COMPARE, Capability.DIAGNOSE) and comparison is None:
        comparison = "last_year"
    return Frame(
        capability=capability,
        store_id=store_id,
        metrics=slots.metrics or (prior.metrics if prior else [default_metric]),
        time_range=slots.time_range or (prior.time_range if prior else TimeRange(type="yesterday")),
        comparison=comparison,
        dimensions=list(slots.dimensions),
        departments=slots.departments or (inherit.departments if inherit else []),
        updated_at=now,
    )
