"""Offline, deterministic stand-in for real LLM providers.

It follows the same task protocol a real provider would (tool calls, JSON answers) so
the gateway, tool loop, and validation are exercised end to end. The diagnosis policy
below is a scripted imitation of what a tool-calling model should do; it is a test
double, not production logic.
"""

import json
from typing import Any

from store_agent.formatting import direction, hour_label, money, pct, signed_money
from store_agent.models.types import ModelTask, ModelUnavailable, ProviderResponse, ToolCall

HELP_TEXT = (
    "I can help with your store's numbers: sales, transactions, average order value, units, "
    "and availability, compared with last year, broken down by department or hour. "
    "I can also explain why a number moved and send you a daily sales briefing. "
    "Try: \"How were sales yesterday?\""
)


class FakeProvider:
    def __init__(self, unavailable: set[str] | None = None):
        self.unavailable = unavailable or set()
        self.calls: list[tuple[str, str]] = []

    def complete(self, model: str, task: ModelTask, timeout_s: float) -> ProviderResponse:
        self.calls.append((model, task.kind))
        if model in self.unavailable:
            raise ModelUnavailable(f"{model} is unavailable")
        handler = {
            "narrate": self._narrate,
            "classify_intent": self._classify,
            "general": lambda t: ProviderResponse(text=HELP_TEXT),
            "agent": self._agent_step,
        }.get(task.kind)
        if handler is None:
            raise ModelUnavailable(f"fake provider has no behaviour for task kind '{task.kind}'")
        resp = handler(task)
        resp.input_tokens = sum(len(m.content) for m in task.messages) // 4
        resp.output_tokens = len(resp.text or json.dumps([c.model_dump() for c in resp.tool_calls])) // 4
        return resp

    def _narrate(self, task: ModelTask) -> ProviderResponse:
        return ProviderResponse(text=task.context["draft"])

    def _classify(self, task: ModelTask) -> ProviderResponse:
        hint = task.context.get("hint")
        if hint:
            out = {"capability": hint["capability"], "reasoning_tier": hint["reasoning_tier"], "confidence": 0.9}
        else:
            out = {"capability": "GENERAL", "reasoning_tier": "STANDARD", "confidence": 0.4}
        return ProviderResponse(text=json.dumps(out))

    # -- scripted diagnosis ---------------------------------------------------------
    def _agent_step(self, task: ModelTask) -> ProviderResponse:
        frame = task.context["frame"]
        dept = (frame.get("departments") or [None])[0]
        results = [json.loads(m.content) for m in task.messages if m.role == "tool"]
        if any("error" in r for r in results):
            raise ModelUnavailable("Tool data is unavailable for the scripted diagnosis")

        if dept:
            plan = [
                ("get_metric_summary", {"metrics": ["sales", "transactions", "aov"], "department": dept}),
                ("get_availability", {"department": dept}),
                ("get_breakdown", {"metric": "sales", "by": "hour", "department": dept}),
            ]
        else:
            plan = [
                ("get_metric_summary", {"metrics": ["sales", "transactions", "aov"]}),
                ("get_breakdown", {"metric": "sales", "by": "department"}),
            ]
        if len(results) < len(plan):
            name, args = plan[len(results)]
            return ProviderResponse(tool_calls=[ToolCall(id=f"call_{len(results) + 1}", name=name, arguments=args)])

        claims = _explain(frame, dept, results)
        answer = {"answer": " ".join(c["text"] for c in claims), "claims": claims}
        return ProviderResponse(text=json.dumps(answer))


def _explain(frame: dict[str, Any], dept: str | None, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = results[0]
    v, eid = summary["values"], summary["evidence_id"]
    if any(v.get(f"{m}_pct") is None for m in ("sales", "transactions", "aov")):
        raise ModelUnavailable("Comparison percentages are unavailable for the scripted diagnosis")
    vs = "vs last year" if frame.get("comparison") == "last_year" else f"vs {summary['comparison']}"
    subject = f"{dept} sales" if dept else "Sales"
    claims = [
        {
            "text": f"{subject} were {money(v['sales'])} {summary['period_short']}, "
            f"{direction(v['sales_pct'])} {pct(v['sales_pct'])} {vs} ({signed_money(v['sales_diff'])}).",
            "evidence": [eid],
        }
    ]
    t, a = v["transactions_pct"], v["aov_pct"]
    if abs(t) >= abs(a):
        driver = "fewer purchases" if t < 0 else "more purchases"
        claims.append(
            {
                "text": f"Most of the change came from {driver}: transactions were {direction(t)} {pct(t)}, "
                f"while the average order value was {direction(a)} {pct(a)}.",
                "evidence": [eid],
            }
        )
    else:
        claims.append(
            {
                "text": f"Most of the change came from order size: the average order value was {direction(a)} {pct(a)}, "
                f"while transactions were {direction(t)} {pct(t)}.",
                "evidence": [eid],
            }
        )

    for r in results[1:]:
        if r["tool"] == "get_availability":
            av = r["values"]
            if av["availability_diff"] <= -3:
                text = (
                    f"Availability was {av['availability']:.1f}% compared with {av['availability_cmp']:.1f}% a year ago, "
                    "so customers were more likely to find items out of stock."
                )
            else:
                text = f"Availability held at {av['availability']:.1f}%, so stock levels don't look like the cause."
            claims.append({"text": text, "evidence": [r["evidence_id"]]})
        elif r["tool"] == "get_breakdown" and r["by"] == "hour":
            worst = min(r["rows"], key=lambda x: x["sales_diff"])
            if worst["sales_diff"] < 0:
                claims.append(
                    {
                        "text": f"The biggest hourly gap was {hour_label(worst['hour'])} ({signed_money(worst['sales_diff'])}).",
                        "evidence": [r["evidence_id"]],
                    }
                )
        elif r["tool"] == "get_breakdown" and r["by"] == "department":
            losers = sorted((x for x in r["rows"] if x["sales_diff"] < 0), key=lambda x: x["sales_diff"])
            if losers:
                top = losers[0]
                text = (
                    f"{top['department']} was the biggest drag ({signed_money(top['sales_diff'])}), "
                    f"about {top['share_of_losses_pct']:.0f}% of the losses across departments that declined."
                )
                if len(losers) > 1:
                    text += f" Next was {losers[1]['department']} ({signed_money(losers[1]['sales_diff'])})."
            else:
                best = max(r["rows"], key=lambda x: x["sales_diff"])
                text = f"Every department was ahead of last year; {best['department']} led with {signed_money(best['sales_diff'])}."
            claims.append({"text": text, "evidence": [r["evidence_id"]]})
    return claims
