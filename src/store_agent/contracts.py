"""Request/response contracts shared by channels, router, runtime, and automations."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from store_agent.tools.semantic.contract import TimeRange


class Capability(StrEnum):
    LOOKUP = "LOOKUP"
    COMPARE = "COMPARE"
    DIAGNOSE = "DIAGNOSE"
    DOCUMENT_QUESTION = "DOCUMENT_QUESTION"
    FORECAST = "FORECAST"
    CREATE_AUTOMATION = "CREATE_AUTOMATION"
    MANAGE_AUTOMATION = "MANAGE_AUTOMATION"
    ACTION = "ACTION"
    GENERAL = "GENERAL"
    DENY = "DENY"


class Tier(StrEnum):
    NONE = "NONE"
    FAST = "FAST"
    STANDARD = "STANDARD"
    DEEP = "DEEP"


TIER_ORDER = [Tier.NONE, Tier.FAST, Tier.STANDARD, Tier.DEEP]


class UserContext(BaseModel):
    user_id: str
    display_name: str
    store_id: str
    role: str
    market_id: str | None = None
    timezone: str
    permissions: list[str] = Field(default_factory=list)


class AgentRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    conversation_id: str
    message: str
    channel: str
    timestamp: datetime
    context: dict[str, Any] = Field(default_factory=dict)


class Slots(BaseModel):
    """Parameters extracted deterministically from the message text."""

    metrics: list[str] = Field(default_factory=list)
    time_range: TimeRange | None = None
    comparison: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    store_ids: list[str] = Field(default_factory=list)
    limit: int | None = None
    ascending: bool = False
    anaphora: bool = False
    recurrence: bool = False
    schedule_days: list[int] | None = None
    schedule_time: str | None = None
    condition: bool = False
    briefing: bool = False


class RouteDecision(BaseModel):
    capability: Capability
    reasoning_tier: Tier
    confidence: float
    tools: list[str] = Field(default_factory=list)
    slots: Slots = Field(default_factory=Slots)
    router: str = "rules"
    reason: str = ""


class Kpi(BaseModel):
    label: str
    value: str
    change: str | None = None
    direction: Literal["up", "down", "flat"] | None = None


class TableRow(BaseModel):
    label: str
    value: str
    change: str | None = None


class SuggestedAction(BaseModel):
    title: str
    message: str  # sent back as a normal user turn when clicked


class AgentResponse(BaseModel):
    """Channel-neutral response. `text` is always a complete answer; kpis/table/actions are
    optional structure a channel (e.g. an Adaptive Card) may render."""

    request_id: str
    text: str
    title: str | None = None
    kpis: list[Kpi] = Field(default_factory=list)
    table_title: str | None = None
    table: list[TableRow] = Field(default_factory=list)
    actions: list[SuggestedAction] = Field(default_factory=list)
    source: Literal["data", "document", "general", "automation", "system"] = "system"
    status: Literal["ok", "degraded", "denied", "unsupported", "clarify", "error"] = "ok"
