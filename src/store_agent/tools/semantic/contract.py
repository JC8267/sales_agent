"""Strict contract between the agent and the semantic layer.

The agent produces a SemanticQuery (never warehouse SQL). store_id is always set by the
runtime from the caller's authorized scope; model-produced arguments never carry it.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

Dimension = Literal["department", "hour", "date"]
ComparisonKind = Literal["last_year", "prior_period"]


class TimeRange(BaseModel):
    type: Literal["yesterday", "today", "last_n_days", "date_range"]
    n: int | None = Field(default=None, gt=0)
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if self.type == "last_n_days" and self.n is None:
            raise ValueError("last_n_days requires a positive day count")
        if self.type == "date_range":
            if self.start is None or self.end is None:
                raise ValueError("date_range requires start and end dates")
            if self.start > self.end:
                raise ValueError("start must be on or before end")
        return self


class SemanticQuery(BaseModel):
    store_id: str
    metrics: list[str] = Field(min_length=1)
    dimensions: list[Dimension] = Field(default_factory=list)
    time_range: TimeRange
    comparison: ComparisonKind | None = None
    filters: dict[str, list[str]] = Field(default_factory=dict)
    order_by: str | None = None
    descending: bool = True
    limit: int | None = Field(default=None, gt=0)


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

    @field_validator("rows", mode="before")
    @classmethod
    def normalize_decimals(cls, rows):
        # Warehouse calculations retain Decimal precision; response values use floats
        # without intermediate rounding. Formatting rounds only for display.
        return [{k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()} for row in rows]


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
