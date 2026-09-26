"""Hybrid router: rules first, then a stage-2 classifier, gated by confidence policy.

>= execute            run the route
[escalate, execute)   ask the stage-2 router at STANDARD tier and keep the stronger answer
< escalate            GENERAL at STANDARD: the primary reasoning model resolves intent
"""

import time

from store_agent.config import RoutingConfig
from store_agent.context.conversation import Frame
from store_agent.contracts import Capability, RouteDecision, Slots, Tier
from store_agent.observability.tracing import Trace
from store_agent.router.intelligent import IntelligentRouter
from store_agent.router.rules import RuleRouter


class HybridRouter:
    def __init__(self, rules: RuleRouter, intelligent: IntelligentRouter, routing: RoutingConfig):
        self.rules, self.intelligent, self.routing = rules, intelligent, routing

    def route(self, message: str, slots: Slots, prior: Frame | None, allowed: frozenset[Capability], trace: Trace | None = None) -> RouteDecision:
        t0 = time.perf_counter()
        decision = self._route(message, slots, prior, allowed, trace)
        if trace:
            trace.router_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        return decision

    def _route(self, message, slots, prior, allowed, trace) -> RouteDecision:
        execute, escalate = self.routing.confidence.execute, self.routing.confidence.escalate
        candidate = self.rules.route(message, slots, prior)
        if candidate and candidate.confidence >= execute:
            return candidate

        if candidate is None or candidate.confidence < escalate:
            fast = self.intelligent.classify(message, slots, prior, allowed, Tier.FAST, candidate, trace)
            if fast.confidence >= execute:
                return fast
            candidate = _stronger(candidate, fast)

        if candidate.confidence >= escalate:
            strong = self.intelligent.classify(message, slots, prior, allowed, Tier.STANDARD, candidate, trace)
            candidate = _stronger(candidate, strong)
            if candidate.confidence >= escalate:
                return candidate

        return RouteDecision(
            capability=Capability.GENERAL,
            reasoning_tier=Tier.STANDARD,
            confidence=candidate.confidence,
            slots=slots,
            router=candidate.router,
            reason="low routing confidence; primary model resolves intent",
        )


def _stronger(a: RouteDecision | None, b: RouteDecision) -> RouteDecision:
    return b if a is None or b.confidence >= a.confidence else a
