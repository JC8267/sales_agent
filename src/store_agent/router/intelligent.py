"""Stage 2: classifier for requests the rules can't settle. Any classifier (Jev, a small
fine-tuned model, a general LLM) plugs in by implementing IntelligentRouter."""

import json
from typing import Protocol

from pydantic import BaseModel, ValidationError

from store_agent.context.conversation import Frame
from store_agent.contracts import Capability, RouteDecision, Slots, Tier
from store_agent.models.gateway import ModelGateway
from store_agent.models.types import AllModelsUnavailable, Message, ModelTask
from store_agent.observability.tracing import Trace
from store_agent.prompts import load_prompt


class IntelligentRouter(Protocol):
    def classify(
        self,
        message: str,
        slots: Slots,
        prior: Frame | None,
        allowed: frozenset[Capability],
        tier: Tier,
        hint: RouteDecision | None,
        trace: Trace | None,
    ) -> RouteDecision: ...


class _Classification(BaseModel):
    capability: Capability
    reasoning_tier: Tier
    confidence: float


class ModelRouter:
    """Classifies intent with a gateway model. Only authorized capabilities are offered."""

    def __init__(self, gateway: ModelGateway):
        self.gateway = gateway

    def classify(self, message, slots, prior, allowed, tier, hint, trace) -> RouteDecision:
        context = {
            "allowed_capabilities": sorted(allowed),
            "prior_capability": prior.capability if prior else None,
            "hint": hint.model_dump(mode="json", include={"capability", "reasoning_tier", "confidence"}) if hint else None,
        }
        task = ModelTask(
            kind="classify_intent",
            messages=[
                Message(role="system", content=load_prompt("classify_intent")),
                Message(role="user", content=json.dumps({"message": message, **context})),
            ],
            context=context,
            response_format="json",
        )
        try:
            result = self.gateway.generate(tier, task, trace)
            parsed = _Classification.model_validate_json(result.text or "")
        except (AllModelsUnavailable, ValidationError) as e:
            return RouteDecision(
                capability=Capability.GENERAL, reasoning_tier=Tier.STANDARD, confidence=0.0, slots=slots,
                router="model:unavailable", reason=f"stage-2 router failed: {type(e).__name__}",
            )
        return RouteDecision(
            capability=parsed.capability,
            reasoning_tier=parsed.reasoning_tier,
            confidence=parsed.confidence,
            slots=slots,
            router=f"model:{result.model}",
            reason="stage-2 classification",
        )
