"""Diagnostic tool loop: the reasoning model picks queries; code executes them, keeps the
evidence, and validates the final answer. Only tool calls and evidence are persisted
(trace + ledger), never model reasoning text."""

import json

from pydantic import BaseModel, Field, ValidationError

from store_agent.context.conversation import Frame
from store_agent.contracts import Tier
from store_agent.models.gateway import ModelGateway
from store_agent.models.types import Message, ModelTask
from store_agent.observability.tracing import Trace
from store_agent.prompts import load_prompt
from store_agent.runtime.evidence import Claim, EvidenceLedger
from store_agent.tools.analysis import AnalysisTools
from store_agent.validation.numeric import ValidationResult, validate_claims, validate_text


class DiagnosisFailed(Exception):
    pass


class FinalAnswer(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)


def run_diagnosis(
    question: str,
    frame: Frame,
    tools: AnalysisTools,
    gateway: ModelGateway,
    tier: Tier,
    trace: Trace,
    ledger: EvidenceLedger,
    allowed_stores: frozenset[str],
    max_steps: int,
    max_retries: int,
) -> tuple[FinalAnswer, ValidationResult]:
    frame_json = frame.model_dump(mode="json", exclude={"updated_at", "store_id"})
    messages = [
        Message(role="system", content=load_prompt("diagnose")),
        Message(role="user", content=json.dumps({"question": question, "frame": frame_json})),
    ]
    retries = 0
    for _ in range(max_steps):
        task = ModelTask(kind="agent", messages=messages, tools=tools.specs(), context={"frame": frame_json}, response_format="json")
        result = gateway.generate(tier, task, trace)
        if result.tool_calls:
            messages.append(Message(role="assistant", tool_calls=result.tool_calls))
            for call in result.tool_calls:
                messages.append(Message(role="tool", tool_call_id=call.id, name=call.name, content=tools.execute(call)))
            continue

        try:
            final = FinalAnswer.model_validate_json(result.text or "")
        except ValidationError:
            violations = ["answer was not JSON with 'answer' and 'claims'"]
        else:
            check = validate_text(final.answer, ledger, allowed_stores)
            violations = check.violations + validate_claims(final.claims, ledger)
            if not violations:
                return final, check

        trace.notes.append(f"diagnosis answer failed validation: {violations}")
        if retries >= max_retries:
            raise DiagnosisFailed("; ".join(violations))
        retries += 1
        messages.append(Message(role="assistant", content=result.text or ""))
        messages.append(
            Message(role="user", content=json.dumps({"validation_failed": violations, "instruction": "Fix these issues using only numbers from tool results."}))
        )
    raise DiagnosisFailed("step limit reached without a final answer")
