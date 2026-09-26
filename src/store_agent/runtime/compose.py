"""Deterministic answer drafts built only from semantic-layer results. Tier NONE returns
these as-is; higher tiers may rephrase them, subject to validation."""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from store_agent.config import SemanticCatalog
from store_agent.contracts import AgentResponse, Kpi, SuggestedAction, TableRow
from store_agent.formatting import arrow, day, direction, hour_label, metric_value, pct, signed_money, signed_pct
from store_agent.tools.semantic.contract import SemanticResult

PLURAL = {"sales", "transactions", "units"}


class Draft(BaseModel):
    text: str
    title: str | None = None
    kpis: list[Kpi] = Field(default_factory=list)
    table_title: str | None = None
    table: list[TableRow] = Field(default_factory=list)
    actions: list[SuggestedAction] = Field(default_factory=list)

    def to_response(self, request_id: str, text: str | None = None, source: str = "data", status: str = "ok") -> AgentResponse:
        return AgentResponse(
            request_id=request_id,
            text=text or self.text,
            title=self.title,
            kpis=self.kpis,
            table_title=self.table_title,
            table=self.table,
            actions=self.actions,
            source=source,
            status=status,
        )


def lookup(result: SemanticResult, catalog: SemanticCatalog) -> Draft:
    if result.period.comparison:
        return compare(result, None, catalog)
    q, p = result.query, result.period
    if not q.dimensions:
        row = result.rows[0]
        kpis = [Kpi(label=_label(m, catalog), value=_fmt(m, row[m], catalog)) for m in q.metrics]
        if len(q.metrics) == 1:
            m = q.metrics[0]
            text = f"{_label(m, catalog)} {p.label} {'were' if m in PLURAL else 'was'} {_fmt(m, row[m], catalog)}."
        else:
            text = f"{p.label[0].upper()}{p.label[1:]}: " + ", ".join(f"{_label(m, catalog).lower()} {_fmt(m, row[m], catalog)}" for m in q.metrics) + "."
        actions = [
            SuggestedAction(title="Compare with last year", message="Compare that with last year."),
            SuggestedAction(title="Show departments", message=f"Show {q.metrics[0]} by department."),
        ]
        return Draft(text=text, kpis=kpis, actions=actions)

    m = q.metrics[0]
    table = [TableRow(label=_dim_label(r, q.dimensions), value=_fmt(m, r[m], catalog)) for r in result.rows]
    title = f"{_label(m, catalog)} by {' and '.join(q.dimensions)}, {p.label}"
    text = title + ":\n" + "\n".join(f"- {r.label}: {r.value}" for r in table)
    actions = [SuggestedAction(title="Compare with last year", message="Compare that with last year.")]
    return Draft(text=text, table_title=title, table=table, actions=actions)


def compare(result: SemanticResult, totals: SemanticResult | None, catalog: SemanticCatalog) -> Draft:
    q, p = result.query, result.period
    vs = p.comparison_label or "the comparison period"
    vs_short = "vs LY" if p.comparison == "last_year" else "vs prior"
    if not q.dimensions:
        row = result.rows[0]
        sentences, kpis = [], []
        for m in q.metrics:
            sentences.append(_change_sentence(m, row, p.label, vs, catalog))
            kpis.append(_kpi(m, row, vs_short, catalog))
        m0 = q.metrics[0]
        why = f"Why {'were' if m0 in PLURAL else 'was'} {m0} {direction(row.get(f'{m0}_diff') or 0)}?"
        actions = [
            SuggestedAction(title="Which departments drove it?", message="Which departments drove the difference?"),
            SuggestedAction(title=why, message=why),
        ]
        return Draft(text=" ".join(sentences), kpis=kpis, actions=actions)

    m = q.metrics[0]
    sentences, kpis = [], []
    if totals:
        sentences.append(_change_sentence(m, totals.rows[0], p.label, vs, catalog))
        kpis.append(_kpi(m, totals.rows[0], vs_short, catalog))
    rows = [r for r in result.rows if r.get(f"{m}_diff") is not None]
    table = [
        TableRow(label=_dim_label(r, q.dimensions), value=_fmt(m, r[m], catalog), change=_change_text(m, r, catalog))
        for r in sorted(rows, key=lambda r: r[f"{m}_diff"])
    ]
    if "department" in q.dimensions:
        losers = sorted((r for r in rows if r[f"{m}_diff"] < 0), key=lambda r: r[f"{m}_diff"])[:3]
        gainers = sorted((r for r in rows if r[f"{m}_diff"] > 0), key=lambda r: -r[f"{m}_diff"])[:3]
        if losers:
            sentences.append("Biggest declines: " + ", ".join(f"{r['department']} {_change_text(m, r, catalog)}" for r in losers) + ".")
        if gainers:
            sentences.append("Biggest gains: " + ", ".join(f"{r['department']} {_change_text(m, r, catalog)}" for r in gainers) + ".")
        actions = []
        if losers:
            worst = losers[0]["department"]
            actions.append(SuggestedAction(title=f"Why was {worst} down?", message=f"Why was {worst} down?"))
        actions.append(SuggestedAction(title="Show hourly trend", message=f"Show hourly {m} vs last year."))
    else:
        worst = table[0] if table else None
        if worst:
            sentences.append(f"Biggest gap: {worst.label} {worst.change}.")
        actions = []
    title = f"{_label(m, catalog)} by {' and '.join(q.dimensions)}, {p.label} {vs_short}"
    return Draft(text=" ".join(sentences) or title, kpis=kpis, table_title=title, table=table, actions=actions)


def _change_sentence(m: str, row: dict[str, Any], period_label: str, vs: str, catalog: SemanticCatalog) -> str:
    label, verb = _label(m, catalog), "were" if m in PLURAL else "was"
    value, cmp_value, diff, pc = row[m], row.get(f"{m}_cmp"), row.get(f"{m}_diff"), row.get(f"{m}_pct")
    if cmp_value is None or diff is None:
        return f"{label} {period_label} {verb} {_fmt(m, value, catalog)}; there is no comparison data."
    if catalog.metrics[m].unit == "percent":
        change = f"{direction(diff)} {abs(diff):.1f} percentage points" if diff else "unchanged"
    else:
        change = f"{direction(pc)} {pct(pc)}" if pc else "flat"
    return f"{label} {period_label} {verb} {_fmt(m, value, catalog)}, {change} vs {_fmt(m, cmp_value, catalog)} on {vs}."


def _change_text(m: str, row: dict[str, Any], catalog: SemanticCatalog) -> str:
    diff, pc = row.get(f"{m}_diff") or 0, row.get(f"{m}_pct")
    unit = catalog.metrics[m].unit
    if unit == "percent":
        return f"{diff:+.1f} percentage points"
    d = signed_money(diff) if unit == "currency" else f"{diff:+,.0f}"
    return f"{d} ({signed_pct(pc)})" if pc is not None else d


def _kpi(m: str, row: dict[str, Any], vs_short: str, catalog: SemanticCatalog) -> Kpi:
    pc, diff = row.get(f"{m}_pct"), row.get(f"{m}_diff")
    moved = pc if pc is not None else diff
    change = None
    if moved is not None:
        change = f"{arrow(moved)} {pct(pc)} {vs_short}" if pc is not None else f"{arrow(moved)} {abs(diff):.1f} percentage points"
    return Kpi(label=_label(m, catalog), value=_fmt(m, row[m], catalog), change=change, direction=direction(moved or 0) if moved is not None else None)


def _label(m: str, catalog: SemanticCatalog) -> str:
    return catalog.metrics[m].label


def _fmt(m: str, v: float, catalog: SemanticCatalog) -> str:
    return metric_value(catalog.metrics[m].unit, v)


def _dim_label(row: dict[str, Any], dims: list[str]) -> str:
    parts = []
    for d in dims:
        if d == "hour":
            parts.append(hour_label(row["hour"]))
        elif d == "date":
            parts.append(day(date.fromisoformat(row["date"])))
        else:
            parts.append(str(row[d]))
    return " / ".join(parts)

