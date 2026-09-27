"""Agent runtime: identity -> scope -> slots -> route -> capability handler -> validation.

One orchestrator with tools; no multi-agent layer. Interactive requests and scheduled
automations share this code path.
"""

import json
import logging
import time
from dataclasses import dataclass

from pydantic import ValidationError

from store_agent.automations.repository import AutomationRepository
from store_agent.clock import Clock
from store_agent.config import Settings
from store_agent.context.conversation import ConversationStore, Frame, is_followup, resolve_frame
from store_agent.contracts import AgentRequest, AgentResponse, Capability, RouteDecision, SuggestedAction, Tier, UserContext
from store_agent.models.gateway import ModelGateway
from store_agent.models.types import AllModelsUnavailable, Message, ModelTask
from store_agent.observability.tracing import Trace, TraceStore
from store_agent.prompts import load_prompt
from store_agent.router.router import HybridRouter
from store_agent.router.slots import DateClarification, extract_slots
from store_agent.runtime import automation_intents, compose
from store_agent.runtime.briefing import briefing
from store_agent.runtime.compose import Draft
from store_agent.runtime.diagnose import DiagnosisFailed, run_diagnosis
from store_agent.runtime.evidence import EvidenceLedger
from store_agent.security.authorization import AuthorizationError, AuthorizedScope, resolve_scope
from store_agent.security.identity import IdentityProvider
from store_agent.tools.analysis import AnalysisTools
from store_agent.tools.semantic.contract import SemanticLayer, SemanticLayerError, SemanticQuery, TimeRange
from store_agent.tools.semantic.scoped import SemanticTool
from store_agent.validation.numeric import ValidationResult, validate_text

log = logging.getLogger("store_agent.runtime")
C = Capability


@dataclass
class Turn:
    request: AgentRequest
    user: UserContext
    scope: AuthorizedScope
    trace: Trace
    ledger: EvidenceLedger
    semantic: SemanticTool
    route: RouteDecision
    prior: Frame | None


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        identity: IdentityProvider,
        semantic_layer: SemanticLayer,
        gateway: ModelGateway,
        router: HybridRouter,
        conversations: ConversationStore,
        automations: AutomationRepository,
        traces: TraceStore,
    ):
        self.settings, self.clock, self.identity = settings, clock, identity
        self.semantic_layer, self.gateway, self.router = semantic_layer, gateway, router
        self.conversations, self.automations, self.traces = conversations, automations, traces

    # -- entry points ---------------------------------------------------------------
    def handle(self, request: AgentRequest) -> AgentResponse:
        t0 = time.perf_counter()
        trace = Trace.start(
            request.request_id, request.user_id, request.conversation_id, request.channel, request.message,
            self.clock.now(), self.settings.log_message_text,
        )
        try:
            response = self._handle(request, trace)
        except DateClarification as e:
            response = AgentResponse(request_id=request.request_id, text=str(e), status="clarify")
        except SemanticLayerError as e:
            trace.notes.append(f"semantic layer: {e}")
            response = AgentResponse(request_id=request.request_id, text=str(e) or "I couldn't get the data right now.", status="error")
        except AuthorizationError as e:
            trace.notes.append(f"authorization: {e}")
            response = AgentResponse(request_id=request.request_id, text="You don't have access to that data.", status="denied")
        except ValidationError as e:
            trace.notes.append(f"invalid request: {e}")
            response = AgentResponse(request_id=request.request_id, text="Please use positive day counts and limits, and provide a date range with the start on or before the end.", status="error")
        except Exception as e:
            log.exception("request %s failed", request.request_id)
            trace.notes.append(f"unhandled: {type(e).__name__}: {e}")
            response = AgentResponse(request_id=request.request_id, text="Something went wrong on my side. Please try again.", status="error")
        return self._finish(trace, response, t0)

    def run_briefing(self, owner: str, store_id: str) -> AgentResponse:
        """Scheduled path: re-resolves identity and scope at run time, so revoked access stops delivery."""
        t0 = time.perf_counter()
        now = self.clock.now()
        request = AgentRequest(user_id=owner, conversation_id=f"automation:{owner}", message="[scheduled] DAILY_SALES_BRIEFING", channel="scheduler", timestamp=now)
        trace = Trace.start(request.request_id, owner, request.conversation_id, "scheduler", request.message, now, keep_text=True)
        trace.capability, trace.router, trace.reasoning_tier, trace.store_id = "LOOKUP", "automation", "NONE", store_id
        user = self.identity.resolve(owner)
        scope = resolve_scope(user, self.settings) if user else None
        if scope is None or store_id not in scope.stores or C.LOOKUP not in scope.capabilities:
            response = AgentResponse(request_id=request.request_id, text=f"{owner} is no longer authorized for store {store_id}.", status="denied")
            return self._finish(trace, response, t0)
        ledger = EvidenceLedger(self.settings.catalog, trace.evidence)
        semantic = SemanticTool(self.semantic_layer, self.settings.catalog, scope, trace, ledger)
        try:
            draft = briefing(semantic, self.settings.catalog, store_id, greeting=True)
        except SemanticLayerError as e:
            trace.notes.append(f"semantic layer: {e}")
            return self._finish(trace, AgentResponse(request_id=request.request_id, text=str(e), status="error"), t0)
        check = validate_text(draft.text, ledger, scope.stores)
        trace.validation = check.model_dump()
        status = "ok" if check.passed else "error"  # a briefing that fails validation is not sent
        return self._finish(trace, draft.to_response(request.request_id, status=status), t0)

    # -- pipeline ---------------------------------------------------------------------
    def _handle(self, request: AgentRequest, trace: Trace) -> AgentResponse:
        user = self.identity.resolve(request.user_id)
        if user is None:
            return AgentResponse(request_id=request.request_id, text="I couldn't verify your account, so I can't show store data.", status="denied")
        scope = resolve_scope(user, self.settings)
        trace.store_id = user.store_id
        now = self.clock.now()
        prior = self.conversations.get_fresh(request.conversation_id, now, self.settings.routing.followup_ttl_minutes)
        slots = extract_slots(request.message, self.settings.catalog)

        out_of_scope = [s for s in slots.store_ids if s not in scope.stores]
        if out_of_scope:
            self._record_route(trace, RouteDecision(capability=C.DENY, reasoning_tier=Tier.NONE, confidence=1.0, slots=slots, router="policy", reason="store outside scope"))
            return AgentResponse(
                request_id=request.request_id,
                text=f"You don't have access to store {', '.join(out_of_scope)}. I can answer questions about store {scope.home_store}.",
                status="denied",
            )

        route = self.router.route(request.message, slots, prior, scope.capabilities, trace)
        self._record_route(trace, route)
        if route.capability not in scope.capabilities:
            return AgentResponse(request_id=request.request_id, text="That isn't something I can help you with in your role.", status="denied")
        cap_cfg = self.settings.capabilities.capabilities.get(route.capability)
        if cap_cfg and not cap_cfg.enabled:
            return AgentResponse(request_id=request.request_id, text=cap_cfg.unavailable_message or "That isn't available yet.", status="unsupported")

        ledger = EvidenceLedger(self.settings.catalog, trace.evidence)
        turn = Turn(request, user, scope, trace, ledger, SemanticTool(self.semantic_layer, self.settings.catalog, scope, trace, ledger), route, prior)
        handler = {
            C.LOOKUP: self._lookup,
            C.COMPARE: self._compare,
            C.DIAGNOSE: self._diagnose,
            C.CREATE_AUTOMATION: self._create_automation,
            C.MANAGE_AUTOMATION: self._manage_automation,
            C.GENERAL: self._general,
        }.get(route.capability)
        if handler is None:
            return AgentResponse(request_id=request.request_id, text="That isn't available yet.", status="unsupported")
        return handler(turn)

    # -- capability handlers ----------------------------------------------------------
    def _frame(self, turn: Turn) -> Frame:
        slots = turn.route.slots
        store = slots.store_ids[0] if slots.store_ids else (turn.prior.store_id if turn.prior and is_followup(slots, turn.prior) else turn.scope.home_store)
        return resolve_frame(turn.route.capability, slots, turn.prior, store, self.settings.catalog.default_metric, self.clock.now())

    def _lookup(self, turn: Turn) -> AgentResponse:
        slots = turn.route.slots
        if slots.briefing and not slots.metrics and not slots.dimensions:
            if slots.time_range and slots.time_range.type != "yesterday":
                return AgentResponse(request_id=turn.request.request_id, text="Daily briefings cover yesterday. For another date, ask for sales on YYYY-MM-DD.", status="clarify")
            frame = self._frame(turn)
            frame.time_range, frame.comparison = TimeRange(type="yesterday"), "last_year"
            frame.metrics, frame.departments = ["sales", "transactions", "aov"], []
            draft = briefing(turn.semantic, self.settings.catalog, frame.store_id, greeting=False)
            return self._respond(turn, draft, Tier.NONE, frame=frame)
        frame = self._frame(turn)
        _, result = turn.semantic.query(self._query(frame, slots), "lookup")
        return self._respond(turn, compose.lookup(result, self.settings.catalog), turn.route.reasoning_tier, frame)

    def _compare(self, turn: Turn) -> AgentResponse:
        slots = turn.route.slots
        frame = self._frame(turn)
        q = self._query(frame, slots)
        _, result = turn.semantic.query(q, "compare")
        totals = None
        if q.dimensions:
            _, totals = turn.semantic.query(q.model_copy(update={"dimensions": [], "order_by": None, "limit": None, "metrics": q.metrics[:1]}), "compare_totals")
        return self._respond(turn, compose.compare(result, totals, self.settings.catalog), turn.route.reasoning_tier, frame)

    def _diagnose(self, turn: Turn) -> AgentResponse:
        frame = self._frame(turn)
        frame.dimensions = []
        tools = AnalysisTools(turn.semantic, frame, turn.ledger)
        rc = self.settings.routing
        try:
            final, check = run_diagnosis(
                turn.request.message, frame, tools, self.gateway, turn.route.reasoning_tier, turn.trace,
                turn.ledger, turn.scope.stores, rc.diagnose_max_steps, rc.diagnose_max_validation_retries,
            )
        except (AllModelsUnavailable, DiagnosisFailed) as e:
            turn.trace.notes.append(f"diagnosis fell back to verified metrics: {type(e).__name__}: {e}")
            return self._verified_fallback(turn, frame)
        turn.trace.validation = check.model_dump()
        self._save_frame(turn, frame)
        dept = frame.departments[0] if frame.departments else None
        actions = [
            SuggestedAction(title="Show hourly trend", message=f"Show hourly {'sales' if not dept else dept + ' sales'} vs last year"),
            SuggestedAction(title="Compare last 7 days", message=f"Compare {dept + ' ' if dept else ''}sales for the last 7 days with last year"),
        ]
        return AgentResponse(request_id=turn.request.request_id, text=final.answer, actions=actions, source="data")

    def _verified_fallback(self, turn: Turn, frame: Frame) -> AgentResponse:
        q = SemanticQuery(
            store_id=frame.store_id, metrics=["sales", "transactions", "aov"], time_range=frame.time_range,
            comparison=frame.comparison or "last_year", filters={"department": frame.departments} if frame.departments else {},
        )
        _, result = turn.semantic.query(q, "fallback_summary")
        draft = compose.compare(result, None, self.settings.catalog)
        prefix = "I couldn't finish the full analysis just now, but here are the verified numbers. "
        check = validate_text(draft.text, turn.ledger, turn.scope.stores)
        turn.trace.validation = check.model_dump()
        self._save_frame(turn, frame)
        return draft.to_response(turn.request.request_id, text=prefix + draft.text, status="degraded")

    def _create_automation(self, turn: Turn) -> AgentResponse:
        store = self._frame(turn).store_id
        return automation_intents.create(
            turn.request.request_id, turn.user.user_id, store, turn.user.timezone, turn.route.slots, self.automations, self.clock.now()
        )

    def _manage_automation(self, turn: Turn) -> AgentResponse:
        return automation_intents.manage(turn.request.request_id, turn.user.user_id, turn.request.message, turn.route.slots, self.automations, self.clock.now())

    def _general(self, turn: Turn) -> AgentResponse:
        tier = turn.route.reasoning_tier if turn.route.reasoning_tier != Tier.NONE else Tier.FAST
        task = ModelTask(
            kind="general",
            messages=[
                Message(role="system", content=load_prompt("general")),
                Message(role="user", content=json.dumps({"message": turn.request.message, "capabilities": sorted(turn.scope.capabilities)})),
            ],
        )
        try:
            text = self.gateway.generate(tier, task, turn.trace).text or ""
        except AllModelsUnavailable:
            text = "I can answer questions about your store's sales and send daily briefings. Try: \"How were sales yesterday?\""
        # General answers must not carry store numbers; the ledger is empty, so any figure fails.
        check = validate_text(text, turn.ledger, turn.scope.stores, check_periods=False)
        turn.trace.validation = check.model_dump()
        if not check.passed:
            text = "I can only share store numbers that come from your store's data. Try asking about sales, transactions, or departments."
        return AgentResponse(request_id=turn.request.request_id, text=text, source="general")

    # -- helpers ----------------------------------------------------------------------
    def _query(self, frame: Frame, slots) -> SemanticQuery:
        m0 = frame.metrics[0]
        by_value = frame.dimensions == ["department"]
        order_by = None
        if by_value:
            order_by = f"{m0}_diff" if frame.comparison else m0
        return SemanticQuery(
            store_id=frame.store_id,
            metrics=frame.metrics if not frame.dimensions else [m0],
            dimensions=frame.dimensions,
            time_range=frame.time_range,
            comparison=frame.comparison,
            filters={"department": frame.departments} if frame.departments else {},
            order_by=order_by,
            descending=not slots.ascending if not frame.comparison else slots.ascending,
            limit=slots.limit,
        )

    def _respond(self, turn: Turn, draft: Draft, tier: Tier, frame: Frame | None) -> AgentResponse:
        if frame and frame.store_id != turn.scope.home_store:
            draft.text = f"Store {frame.store_id}: {draft.text}"
        text, check = self._narrate(turn, draft.text, tier)
        turn.trace.validation = check.model_dump()
        if frame:
            self._save_frame(turn, frame)
        status = "ok" if check.passed else "degraded"
        return draft.to_response(turn.request.request_id, text=text, status=status)

    def _narrate(self, turn: Turn, draft_text: str, tier: Tier) -> tuple[str, ValidationResult]:
        base = validate_text(draft_text, turn.ledger, turn.scope.stores)
        if not base.passed:
            turn.trace.notes.append(f"deterministic draft failed validation: {base.violations}")
            return draft_text, base
        if tier == Tier.NONE:
            return draft_text, base
        violations = None
        for _ in range(1 + self.settings.routing.narration_max_validation_retries):
            task = ModelTask(
                kind="narrate",
                messages=[
                    Message(role="system", content=load_prompt("narrate")),
                    Message(role="user", content=json.dumps({"draft": draft_text, "violations": violations})),
                ],
                context={"draft": draft_text, "violations": violations},
            )
            try:
                text = self.gateway.generate(tier, task, turn.trace).text or ""
            except AllModelsUnavailable as e:
                turn.trace.notes.append(f"narration unavailable, returned verified draft: {e}")
                return draft_text, base
            check = validate_text(text, turn.ledger, turn.scope.stores)
            if check.passed:
                return text, check
            violations = check.violations
            turn.trace.notes.append(f"narration failed validation: {violations}")
        return draft_text, base

    def _save_frame(self, turn: Turn, frame: Frame) -> None:
        self.conversations.save(turn.request.conversation_id, turn.user.user_id, frame)

    def _record_route(self, trace: Trace, route: RouteDecision) -> None:
        trace.capability, trace.router, trace.router_confidence = route.capability, route.router, route.confidence
        trace.reasoning_tier, trace.route_reason = route.reasoning_tier, route.reason

    def _finish(self, trace: Trace, response: AgentResponse, t0: float) -> AgentResponse:
        if trace.validation and not trace.validation["passed"]:
            response = AgentResponse(request_id=response.request_id, text="I couldn't verify the answer against the data. Please try again.", status="error")
        trace.status, trace.response_source = response.status, response.source
        trace.total_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        self.traces.save(trace)
        return response
