"""Predefined daily sales briefing (handoff section 18). Used interactively and by the scheduler."""

from store_agent.config import SemanticCatalog
from store_agent.contracts import SuggestedAction, TableRow
from store_agent.formatting import signed_money, signed_pct
from store_agent.runtime.compose import Draft, _kpi
from store_agent.tools.semantic.contract import SemanticQuery, TimeRange
from store_agent.tools.semantic.scoped import SemanticTool

ATTENTION_PCT = -5.0


def briefing(semantic: SemanticTool, catalog: SemanticCatalog, store_id: str, greeting: bool) -> Draft:
    yesterday = TimeRange(type="yesterday")
    _, totals = semantic.query(
        SemanticQuery(store_id=store_id, metrics=["sales", "transactions", "aov"], time_range=yesterday, comparison="last_year"),
        "briefing_totals",
    )
    _, depts = semantic.query(
        SemanticQuery(
            store_id=store_id, metrics=["sales"], dimensions=["department"], time_range=yesterday,
            comparison="last_year", order_by="sales_diff",
        ),
        "briefing_departments",
    )
    t, p = totals.rows[0], totals.period

    kpis = [_kpi(m, t, "vs LY", catalog) for m in ("sales", "transactions", "aov")]
    gainers = [r for r in depts.rows if (r.get("sales_diff") or 0) > 0][:3]
    attention = sorted((r for r in depts.rows if (r.get("sales_pct") or 0) <= ATTENTION_PCT), key=lambda r: r["sales_pct"])[:2]

    title = f"{'Good morning - ' if greeting else ''}Yesterday's performance ({p.label.removeprefix('yesterday ').strip('()')})"
    lines = [title, ""] + [f"{k.label}: {k.value}  {k.change or 'comparison unavailable'}" for k in kpis]
    if gainers:
        lines += ["", "Top contributors:"] + [f"  {r['department']} {signed_money(r['sales_diff'])}" for r in gainers]
    if attention:
        lines += ["", "Needs attention:"] + [f"  {r['department']} {signed_pct(r['sales_pct'])}" for r in attention]

    table = [TableRow(label=f"▲ {r['department']}", value=signed_money(r["sales_diff"])) for r in gainers]
    table += [TableRow(label=f"▼ {r['department']}", value=signed_pct(r["sales_pct"]), change="needs attention") for r in attention]

    actions = []
    if attention:
        worst = attention[0]["department"]
        actions.append(SuggestedAction(title=f"Explain {worst} decline", message=f"Why was {worst} down yesterday?"))
    actions += [
        SuggestedAction(title="Show departments", message="Show sales by department yesterday vs last year"),
        SuggestedAction(title="Compare last 7 days", message="Compare sales for the last 7 days with last year"),
        SuggestedAction(title="Show hourly trend", message="Show hourly sales yesterday"),
    ]
    return Draft(text="\n".join(lines), title=title, kpis=kpis, table_title="Departments", table=table, actions=actions)
