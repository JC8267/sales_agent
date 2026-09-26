"""AgentResponse -> Adaptive Card (schema 1.5, supported by Teams). Presentation only."""

from typing import Any

from store_agent.contracts import AgentResponse

CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
_COLOR = {"up": "Good", "down": "Attention", "flat": "Default"}


def render_card(response: AgentResponse) -> dict[str, Any]:
    body: list[dict[str, Any]] = []
    if response.title:
        body.append({"type": "TextBlock", "text": response.title, "weight": "Bolder", "size": "Medium", "wrap": True})

    if response.kpis:
        body.append(
            {
                "type": "ColumnSet",
                "columns": [
                    {
                        "type": "Column",
                        "width": "stretch",
                        "items": [
                            {"type": "TextBlock", "text": k.label, "isSubtle": True, "size": "Small", "wrap": True},
                            {"type": "TextBlock", "text": k.value, "weight": "Bolder", "size": "Large", "spacing": "None"},
                            *(
                                [{"type": "TextBlock", "text": k.change, "color": _COLOR.get(k.direction or "flat"), "size": "Small", "spacing": "None", "wrap": True}]
                                if k.change
                                else []
                            ),
                        ],
                    }
                    for k in response.kpis
                ],
            }
        )

    if response.table:
        if response.table_title:
            body.append({"type": "TextBlock", "text": response.table_title, "weight": "Bolder", "spacing": "Medium", "wrap": True})
        body.append(
            {
                "type": "FactSet",
                "facts": [{"title": r.label, "value": f"{r.value}  {r.change}" if r.change else r.value} for r in response.table],
            }
        )

    if not response.kpis and not response.table:
        body.append({"type": "TextBlock", "text": response.text, "wrap": True})
    elif response.status == "degraded":
        body.append({"type": "TextBlock", "text": response.text, "wrap": True, "isSubtle": True})

    return {
        "type": "AdaptiveCard",
        "$schema": CARD_SCHEMA,
        "version": "1.5",
        "body": body,
        "actions": [{"type": "Action.Submit", "title": a.title, "data": {"msg": a.message}} for a in response.actions],
    }
