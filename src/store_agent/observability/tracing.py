"""Per-request trace: route, tools, models, validation, outcome. Message text is only
stored when explicitly enabled; a hash and length are always kept for joins/dedup."""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from store_agent.clock import utc_iso
from store_agent.runtime.evidence import EvidenceItem

log = logging.getLogger("store_agent.trace")


class ToolCallRecord(BaseModel):
    name: str
    args: dict[str, Any]
    latency_ms: float
    status: str
    evidence_id: str | None = None
    error: str | None = None


class ModelCallRecord(BaseModel):
    purpose: str
    tier: str
    model: str
    latency_ms: float
    status: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class Trace(BaseModel):
    request_id: str
    user_id: str
    conversation_id: str
    channel: str
    timestamp: datetime
    message_sha256: str
    message_chars: int
    message_text: str | None = None
    store_id: str | None = None
    capability: str | None = None
    router: str | None = None
    router_confidence: float | None = None
    route_reason: str | None = None
    reasoning_tier: str | None = None
    router_latency_ms: float | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    model_calls: list[ModelCallRecord] = Field(default_factory=list)
    validation: dict[str, Any] | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)  # tool outputs behind the answer (audit)
    notes: list[str] = Field(default_factory=list)
    status: str | None = None
    response_source: str | None = None
    total_latency_ms: float | None = None

    @classmethod
    def start(cls, request_id: str, user_id: str, conversation_id: str, channel: str, message: str, at: datetime, keep_text: bool) -> "Trace":
        return cls(
            request_id=request_id,
            user_id=user_id,
            conversation_id=conversation_id,
            channel=channel,
            timestamp=at,
            message_sha256=hashlib.sha256(message.encode()).hexdigest()[:16],
            message_chars=len(message),
            message_text=message if keep_text else None,
        )

    @property
    def models_used(self) -> list[str]:
        return [m.model for m in self.model_calls if m.status == "ok"]

    @property
    def cost_usd(self) -> float:
        return round(sum(m.cost_usd for m in self.model_calls), 6)


class TraceStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save(self, trace: Trace) -> None:
        payload = trace.model_dump_json()
        self.conn.execute(
            "insert or replace into traces values (?, ?, ?, ?, ?, ?, ?)",
            (trace.request_id, trace.user_id, trace.store_id, utc_iso(trace.timestamp), trace.capability, trace.status, payload),
        )
        log.info(payload)

    def get(self, request_id: str) -> Trace | None:
        row = self.conn.execute("select trace_json from traces where request_id = ?", (request_id,)).fetchone()
        return Trace.model_validate_json(row["trace_json"]) if row else None

    def recent(self, limit: int = 20) -> list[Trace]:
        rows = self.conn.execute("select trace_json from traces order by created_at desc, rowid desc limit ?", (limit,)).fetchall()
        return [Trace.model_validate(json.loads(r["trace_json"])) for r in rows]
