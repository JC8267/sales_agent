"""Strict contract between the agent and the semantic layer.

The agent produces a SemanticQuery (never warehouse SQL). store_id is always set by the
runtime from the caller's authorized scope; model-produced arguments never carry it.
"""

from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Dimension = Literal["department", "hour", "date"]
ComparisonKind = Literal["last_year", "prior_period"]


class TimeRange(BaseModel):
    type: Literal["yesterday", "today", "last_n_days", "date_range"]
    n: int | None = None
    start: date | None = None
    end: date | None = None


class SemanticQuery(BaseModel):
    store_id: str
    metrics: list[str]
    dimensions: list[Dimension] = Field(default_factory=list)
    time_range: TimeRange
    comparison: ComparisonKind | None = None
    filters: dict[str, list[str]] = Field(default_factory=dict)
    order_by: str | None = None
    descending: bool = True
    limit: int | None = None


class Period(BaseModel):
    start: date
    end: date
    label: str
    comparison: ComparisonKind | None = None
    comparison_start: date | None = None
    comparison_end: date | None = None
    comparison_label: str | None = None


class SemanticResult(BaseModel):
    """rows: one dict per dimension combination. For each metric m the row holds m and,
    when a comparison was requested, m_cmp, m_diff and m_pct (percent change; None for
    ratio-of-percent metrics like availability, whose diff is in percentage points)."""

    query: SemanticQuery
    period: Period
    rows: list[dict[str, Any]]
    source: str


class SemanticLayerError(Exception):
    """Base class; message is safe to show to an employee."""


class DataNotAvailable(SemanticLayerError):
    pass


class UnsupportedQuery(SemanticLayerError):
    pass


class SemanticLayerUnavailable(SemanticLayerError):
    pass


class SemanticLayer(Protocol):
    def query(self, q: SemanticQuery) -> SemanticResult: ...
