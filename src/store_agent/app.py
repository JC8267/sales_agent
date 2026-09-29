"""Composition root: the one place concrete implementations are chosen."""

from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from store_agent.automations.repository import AutomationRepository
from store_agent.automations.scheduler import AutomationExecutor, PollingScheduler
from store_agent.channels.adaptive_cards import render_card
from store_agent.channels.base import ConversationReferenceStore, OutboxSender
from store_agent.clock import AdjustableClock
from store_agent.config import Settings, load_settings
from store_agent.context.conversation import ConversationStore
from store_agent.models.fake import FakeProvider
from store_agent.models.gateway import ModelGateway
from store_agent.models.types import ModelProvider
from store_agent.observability.tracing import TraceStore
from store_agent.router.intelligent import ModelRouter
from store_agent.router.router import HybridRouter
from store_agent.router.rules import RuleRouter
from store_agent.runtime.agent import AgentRuntime
from store_agent.security.identity import DevIdentityProvider
from store_agent.storage import connect
from store_agent.tools.semantic.contract import SemanticLayer
from store_agent.tools.semantic.cube import CubeSemanticLayer
from store_agent.tools.semantic.mock import MockSemanticLayer


@dataclass
class App:
    settings: Settings
    conn: Connection
    clock: AdjustableClock
    runtime: AgentRuntime
    scheduler: PollingScheduler
    automations: AutomationRepository
    refs: ConversationReferenceStore
    outbox: OutboxSender
    traces: TraceStore
    conversations: ConversationStore
    gateway: ModelGateway


def build_app(
    db_path: str | Path = ":memory:",
    clock: AdjustableClock | None = None,
    settings: Settings | None = None,
    providers: dict[str, ModelProvider] | None = None,
    semantic_layer: SemanticLayer | None = None,
    sleep=lambda s: None,
) -> App:
    settings = settings or load_settings()
    clock = clock or AdjustableClock()
    if semantic_layer is None:
        semantic_layer = CubeSemanticLayer(settings.cube, settings.catalog, clock) if settings.cube.enabled else MockSemanticLayer(settings.catalog, clock)
    conn = connect(db_path)
    gateway = ModelGateway(settings.models, providers or {"fake": FakeProvider()})
    router = HybridRouter(RuleRouter(settings.routing), ModelRouter(gateway), settings.routing)
    automations = AutomationRepository(conn)
    traces = TraceStore(conn)
    conversations = ConversationStore(conn)
    runtime = AgentRuntime(
        settings=settings,
        clock=clock,
        identity=DevIdentityProvider(settings),
        semantic_layer=semantic_layer,
        gateway=gateway,
        router=router,
        conversations=conversations,
        automations=automations,
        traces=traces,
    )
    refs = ConversationReferenceStore(conn, clock)
    outbox = OutboxSender(conn, refs, clock, render_card)
    executor = AutomationExecutor(runtime, automations, outbox, clock, sleep=sleep)
    scheduler = PollingScheduler(automations, executor, clock)
    return App(settings, conn, clock, runtime, scheduler, automations, refs, outbox, traces, conversations, gateway)
