"""Evidence ledger: every number a response may state, keyed back to the tool call that
produced it. Validation checks generated text against this ledger."""

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from store_agent.config import SemanticCatalog
from store_agent.tools.semantic.contract import Period

Unit = Literal["currency", "count", "percent"]
DIMENSION_KEYS = {"department", "hour", "date"}


class EvidenceValue(BaseModel):
    key: str
    value: float
    unit: Unit


class EvidenceItem(BaseModel):
    id: str
    tool: str
    args: dict[str, Any]
    rows: list[dict[str, Any]]
    values: list[EvidenceValue]
    period: Period | None = None


class Claim(BaseModel):
    text: str
    evidence: list[str] = Field(default_factory=list)  # "e2" or "e2:department=Bedroom.sales_pct"


class EvidenceLedger:
    def __init__(self, catalog: SemanticCatalog, items: list[EvidenceItem] | None = None):
        """Pass a trace's evidence list as `items` to persist evidence with the trace."""
        self.catalog = catalog
        self.items: list[EvidenceItem] = items if items is not None else []

    def add(self, tool: str, args: dict[str, Any], rows: list[dict[str, Any]], period: Period | None = None) -> str:
        eid = f"e{len(self.items) + 1}"
        values = []
        for row in rows:
            label = ",".join(f"{k}={row[k]}" for k in row if k in DIMENSION_KEYS) or "total"
            for k, v in row.items():
                if k in DIMENSION_KEYS or isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
                    continue
                values.append(EvidenceValue(key=f"{eid}:{label}.{k}", value=float(v), unit=self.unit_of(k)))
        self.items.append(EvidenceItem(id=eid, tool=tool, args=args, rows=rows, values=values, period=period))
        return eid

    def unit_of(self, key: str) -> Unit:
        if key.endswith("_pct") or "share" in key:
            return "percent"
        base = key.removesuffix("_cmp").removesuffix("_diff")
        metric = self.catalog.metrics.get(base)
        return metric.unit if metric else "count"

    def values(self) -> list[EvidenceValue]:
        return [v for item in self.items for v in item.values]

    def periods(self) -> list[Period]:
        return [item.period for item in self.items if item.period]

    def has_comparison(self, kind: str) -> bool:
        return any(p.comparison == kind for p in self.periods())

    def known_refs(self) -> set[str]:
        return {i.id for i in self.items} | {v.key.split(".")[0] for v in self.values()} | {v.key for v in self.values()}
