"""Tools offered to the reasoning model during a diagnosis.

Store, period, and comparison come from the conversation frame and are not arguments, so
a model (or injected text) cannot redirect a query to another store or period.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from store_agent.analytics.contributions import with_contribution_shares
from store_agent.context.conversation import Frame
from store_agent.models.types import ToolCall, ToolSpec
from store_agent.runtime.evidence import EvidenceLedger
from store_agent.tools.semantic.contract import SemanticLayerError, SemanticQuery
from store_agent.tools.semantic.scoped import SemanticTool


class MetricSummaryArgs(BaseModel):
    metrics: list[Literal["sales", "transactions", "aov", "units"]] = Field(default_factory=lambda: ["sales", "transactions", "aov"])
    department: str | None = None


class BreakdownArgs(BaseModel):
    metric: Literal["sales", "transactions", "units"] = "sales"
    by: Literal["department", "hour"]
    department: str | None = None
    top_n: int | None = Field(default=None, ge=1, le=20)


class AvailabilityArgs(BaseModel):
    department: str | None = None


TOOL_DEFS: dict[str, tuple[type[BaseModel], str]] = {
    "get_metric_summary": (MetricSummaryArgs, "Headline metrics for the period vs the comparison period, optionally for one department."),
    "get_breakdown": (BreakdownArgs, "A metric broken down by department or hour, with change vs the comparison period and each department's share of total gains/losses."),
    "get_availability": (AvailabilityArgs, "Product availability (percent in stock) for the period vs the comparison period."),
}


class AnalysisTools:
    def __init__(self, semantic: SemanticTool, frame: Frame, ledger: EvidenceLedger):
        self.semantic, self.frame, self.ledger = semantic, frame, ledger

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(name=n, description=d, parameters=m.model_json_schema()) for n, (m, d) in TOOL_DEFS.items()]

    def execute(self, call: ToolCall) -> str:
        if call.name not in TOOL_DEFS:
            return json.dumps({"error": f"unknown tool {call.name}"})
        model, _ = TOOL_DEFS[call.name]
        try:
            args = model.model_validate(call.arguments)
            out = getattr(self, call.name)(args)
        except ValidationError as e:
            out = {"error": f"invalid arguments: {e.errors(include_url=False)}"}
        except SemanticLayerError as e:
            out = {"error": str(e)}
        return json.dumps({"tool": call.name, **out}, default=str)

    # -- tools -------------------------------------------------------------------------
    def get_metric_summary(self, a: MetricSummaryArgs) -> dict[str, Any]:
        eid, result = self.semantic.query(self._query(a.metrics, [], a.department), "get_metric_summary")
        return {**self._period(result), "evidence_id": eid, "department": a.department, "values": result.rows[0]}

    def get_breakdown(self, a: BreakdownArgs) -> dict[str, Any]:
        q = self._query([a.metric], [a.by], a.department)
        if a.by == "department":
            q.order_by, q.descending = f"{a.metric}_diff", False  # biggest declines first
        eid, result = self.semantic.query(q, "get_breakdown")
        rows = result.rows
        if a.by == "department":
            rows = with_contribution_shares(rows, a.metric)
            eid = self.ledger.add("contribution_shares", {"source": eid, "metric": a.metric}, rows, result.period)
        if a.top_n:
            rows = rows[: a.top_n]
        return {**self._period(result), "evidence_id": eid, "by": a.by, "department": a.department, "rows": rows}

    def get_availability(self, a: AvailabilityArgs) -> dict[str, Any]:
        eid, result = self.semantic.query(self._query(["availability"], [], a.department), "get_availability")
        return {**self._period(result), "evidence_id": eid, "department": a.department, "values": result.rows[0]}

    # -- helpers -------------------------------------------------------------------------
    def _query(self, metrics: list[str], dims: list[str], department: str | None) -> SemanticQuery:
        return SemanticQuery(
            store_id=self.frame.store_id,
            metrics=metrics,
            dimensions=dims,
            time_range=self.frame.time_range,
            comparison=self.frame.comparison,
            filters={"department": [department]} if department else {},
        )

    def _period(self, result) -> dict[str, Any]:
        tr = self.frame.time_range
        short = "yesterday" if tr.type == "yesterday" else f"over the last {tr.n} days" if tr.type == "last_n_days" else f"on {result.period.label}"
        return {"period": result.period.label, "period_short": short, "comparison": result.period.comparison_label}
