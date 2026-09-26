import pytest

from store_agent.app import build_app
from store_agent.clock import AdjustableClock
from store_agent.config import load_settings
from store_agent.contracts import Capability, RouteDecision, Tier
from store_agent.models.fake import FakeProvider
from store_agent.models.gateway import ModelGateway
from store_agent.models.types import AllModelsUnavailable, Message, ModelTask
from store_agent.router.router import HybridRouter
from store_agent.router.rules import RuleRouter
from store_agent.router.slots import extract_slots

from conftest import SAT_AFTERNOON, Chat

SETTINGS = load_settings()
TASK = ModelTask(kind="general", messages=[Message(role="user", content="hi")])


def test_fallback_model_used_when_primary_down():
    gw = ModelGateway(SETTINGS.models, {"fake": FakeProvider(unavailable={"fake/standard-a"})})
    assert gw.generate(Tier.STANDARD, TASK).model == "fake/standard-b"


def test_deep_degrades_to_standard():
    gw = ModelGateway(SETTINGS.models, {"fake": FakeProvider(unavailable={"fake/deep-a", "fake/deep-b"})})
    result = gw.generate(Tier.DEEP, TASK)
    assert (result.model, result.tier) == ("fake/standard-a", "STANDARD")


def test_all_models_down_raises():
    gw = ModelGateway(SETTINGS.models, {"fake": FakeProvider(unavailable={"fake/fast-a", "fake/fast-b"})})
    with pytest.raises(AllModelsUnavailable):
        gw.generate(Tier.FAST, TASK)


def test_circuit_breaker_skips_failing_model():
    provider = FakeProvider(unavailable={"fake/fast-a"})
    gw = ModelGateway(SETTINGS.models, {"fake": provider})
    for _ in range(SETTINGS.models.circuit_breaker.failure_threshold):
        gw.generate(Tier.FAST, TASK)
    provider.calls.clear()
    gw.generate(Tier.FAST, TASK)
    assert provider.calls == [("fake/fast-b", "general")]


def test_tier_none_never_calls_a_model():
    with pytest.raises(ValueError):
        ModelGateway(SETTINGS.models, {"fake": FakeProvider()}).generate(Tier.NONE, TASK)


def test_diagnosis_with_no_models_returns_verified_numbers(tmp_path):
    down = {f"fake/{t}-{x}" for t in ("fast", "standard", "deep") for x in "ab"}
    app = build_app(tmp_path / "t.db", AdjustableClock(SAT_AFTERNOON), providers={"fake": FakeProvider(unavailable=down)})
    r, t = Chat(app).ask("Why was Bedroom down yesterday?")
    assert r.status == "degraded" and "Bedroom" not in r.text or "Sales" in r.text
    assert t.validation["passed"]
    assert all(m.status == "error" for m in t.model_calls)


class StubRouter:
    def __init__(self, confidence: float, capability=Capability.LOOKUP):
        self.confidence, self.capability, self.calls = confidence, capability, []

    def classify(self, message, slots, prior, allowed, tier, hint, trace):
        self.calls.append(tier)
        return RouteDecision(capability=self.capability, reasoning_tier=Tier.FAST, confidence=self.confidence, slots=slots, router="stub")


def _route(message, stub):
    router = HybridRouter(RuleRouter(SETTINGS.routing), stub, SETTINGS.routing)
    return router.route(message, extract_slots(message, SETTINGS.catalog), None, frozenset(Capability))


def test_confident_rules_skip_stage_two():
    stub = StubRouter(0.99)
    assert _route("How were sales yesterday?", stub).router == "rules" and stub.calls == []


def test_medium_confidence_escalates_to_stronger_router():
    stub = StubRouter(0.9)
    decision = _route("Bedroom?", stub)  # rules: LOOKUP @ 0.72
    assert stub.calls == [Tier.STANDARD] and decision.router == "stub"


def test_low_confidence_hands_intent_to_primary_model():
    stub = StubRouter(0.3, Capability.COMPARE)
    decision = _route("blorp", stub)
    assert stub.calls == [Tier.FAST]
    assert (decision.capability, decision.reasoning_tier) == (Capability.GENERAL, Tier.STANDARD)


def test_router_eval_dataset_passes():
    from evals.run_router_eval import evaluate, load_cases

    report = evaluate(load_cases())
    assert report["failures"] == []
    assert report["unnecessary_deep_rate"] == 0
