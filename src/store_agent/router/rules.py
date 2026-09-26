"""Stage 1: deterministic routing for unambiguous requests (no model call)."""

import re

from store_agent.config import RoutingConfig
from store_agent.context.conversation import Frame
from store_agent.contracts import Capability, RouteDecision, Slots, Tier

C = Capability

DELIVERY_RE = re.compile(r"\b(send|give|remind|deliver|dm|message|email|brief|ping)\s+(me|us)\b|\b(schedule|set up|subscribe)\b")
SETUP_RE = re.compile(r"\b(schedule|set up|subscribe)\b.*\b(briefing|report|summary|alert|update)s?\b")
MANAGE_RE = re.compile(
    r"\b(stop|cancel|pause|resume|restart|disable|enable|turn (off|on)|unsubscribe|delete|remove|move|change|reschedule|switch)\b"
    r".*\b(briefings?|reports?|automations?|alerts?|reminders?|summar(y|ies)|subscriptions?)\b"
    r"|\b(what|which|list|show)\b.*\b(briefings|reports|automations|alerts|reminders|subscriptions)\b"
    r"|\bmy (briefings?|reports?|automations?|alerts?|reminders?)\b"
    r"|\bonly send\b|\bsend it (only )?on\b"
)
ACTION_RE = re.compile(r"\b(create|open|submit|file|raise|log)\s+(a\s+|an\s+)?(ticket|request|case|incident)\b|\bnotify (my )?manager\b")
FORECAST_RE = re.compile(r"\b(forecast\w*|projection|projected|will we|going to (hit|make|do)|on track|expect(ed)? to)\b")
DOCUMENT_RE = re.compile(r"\b(policy|policies|procedure|sop|guidelines?|handbook|how do i|how to|process for|rules for)\b")
DIAGNOSE_RE = re.compile(
    r"\b(why|what caused|what happened|what drove|what's (driving|behind)|what is (driving|behind)|explain|reasons? for|"
    r"root cause|how come|what's going on)\b"
)
MOVE_RE = re.compile(r"\b(down|up|drop|dropped|decline|declined|fell|low|lower|high|higher|increase|decrease|miss|missed|weak|strong)\b")
COMPARE_RE = re.compile(r"\b(compare|compared|comparison|vs\.?|versus|difference|drove|driving|contribut\w*|changed?|against)\b")
LOOKUP_RE = re.compile(r"\b(how (were|was|are|is|did)|what (were|was|are|is)|show|give me|tell me|numbers)\b")
CONTINUE_RE = re.compile(r"^(and|what about|how about|same for|now|also)\b")


class RuleRouter:
    def __init__(self, routing: RoutingConfig):
        self.routing = routing

    def route(self, message: str, slots: Slots, prior: Frame | None) -> RouteDecision | None:
        t = " ".join(message.lower().replace("’", "'").split())

        def decide(cap: Capability, confidence: float, reason: str, tier: Tier | None = None) -> RouteDecision:
            return RouteDecision(
                capability=cap,
                reasoning_tier=tier or self.routing.capability_tiers.get(cap, Tier.FAST),
                confidence=confidence,
                slots=slots,
                router="rules",
                reason=reason,
            )

        if slots.condition:
            return decide(C.CREATE_AUTOMATION, 0.9, "conditional alert request")
        if slots.recurrence and DELIVERY_RE.search(t):
            return decide(C.CREATE_AUTOMATION, 0.95 if slots.schedule_time else 0.9, "recurring delivery request")
        if SETUP_RE.search(t) and not MANAGE_RE.search(t):
            return decide(C.CREATE_AUTOMATION, 0.88, "automation setup without a schedule")
        if MANAGE_RE.search(t):
            return decide(C.MANAGE_AUTOMATION, 0.92, "automation management phrase")
        if ACTION_RE.search(t):
            return decide(C.ACTION, 0.88, "operational action phrase")
        if FORECAST_RE.search(t):
            return decide(C.FORECAST, 0.88, "forecast phrase")
        if DOCUMENT_RE.search(t) and not slots.metrics:
            return decide(C.DOCUMENT_QUESTION, 0.88, "policy/procedure phrase")

        grounded = bool(slots.metrics or slots.departments or prior)
        if DIAGNOSE_RE.search(t):
            deep = any(p in t for p in self.routing.deep_triggers)
            confidence = 0.9 if grounded or MOVE_RE.search(t) else 0.75
            return decide(C.DIAGNOSE, confidence, "why/explain phrase", Tier.DEEP if deep else None)
        if slots.comparison or COMPARE_RE.search(t):
            confidence = 0.9 if (slots.metrics or slots.comparison or prior) else 0.72
            return decide(C.COMPARE, confidence, "comparison phrase")

        if prior and prior.capability in (C.LOOKUP, C.COMPARE, C.DIAGNOSE) and (CONTINUE_RE.search(t) or len(t.split()) <= 3):
            if slots.metrics or slots.departments or slots.time_range:
                return decide(prior.capability, 0.86, "short follow-up inherits previous capability", prior_tier(self.routing, prior))
        if slots.briefing and not slots.metrics:
            return decide(C.LOOKUP, 0.9, "performance briefing request")
        if slots.metrics or slots.dimensions or slots.departments or LOOKUP_RE.search(t):
            if slots.metrics and (slots.time_range or prior):
                confidence = 0.93
            elif slots.metrics or slots.dimensions:
                confidence = 0.88
            else:
                confidence = 0.72
            return decide(C.LOOKUP, confidence, "metric lookup")
        return None


def prior_tier(routing: RoutingConfig, prior: Frame) -> Tier:
    return routing.capability_tiers.get(prior.capability, Tier.FAST)
