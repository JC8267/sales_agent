from datetime import datetime, timezone

import pytest

from store_agent.observability.tracing import Trace
from store_agent.runtime.evidence import EvidenceLedger
from store_agent.security.authorization import AuthorizationError, resolve_scope
from store_agent.tools.semantic.contract import SemanticQuery, TimeRange
from store_agent.tools.semantic.scoped import SemanticTool

from conftest import Chat


def test_other_store_is_denied_before_any_tool_or_model(chat):
    r, t = chat.ask("What were sales at store 017 yesterday?")
    assert r.status == "denied" and "017" in r.text
    assert t.capability == "DENY" and t.router == "policy"
    assert t.tool_calls == [] and t.model_calls == []


def test_market_manager_can_query_stores_in_market(app):
    r, t = Chat(app, "u-rita").ask("What were sales at store 017 yesterday?")
    assert r.status == "ok" and r.text.startswith("Store 017:")
    assert t.tool_calls[0].args["store_id"] == "017"


def test_market_manager_cannot_query_other_market(app):
    r, _ = Chat(app, "u-rita").ask("What were sales at store 210 yesterday?")
    assert r.status == "denied"


def test_unknown_principal_fails_closed(app):
    r, t = Chat(app, "u-nobody").ask("What were sales yesterday?")
    assert r.status == "denied" and t.tool_calls == []


def test_semantic_tool_enforces_scope_even_if_called_directly(app):
    user = app.runtime.identity.resolve("u-anna")
    scope = resolve_scope(user, app.settings)
    trace = Trace.start("r", "u-anna", "c", "test", "", datetime.now(timezone.utc), False)
    tool = SemanticTool(app.runtime.semantic_layer, app.settings.catalog, scope, trace, EvidenceLedger(app.settings.catalog))
    with pytest.raises(AuthorizationError):
        tool.query(SemanticQuery(store_id="017", metrics=["sales"], time_range=TimeRange(type="yesterday")), "test")


def test_capability_outside_role_is_denied_and_disabled_one_is_unsupported(app):
    r, _ = Chat(app, "u-anna").ask("Forecast sales through Sunday")  # coworker: FORECAST not granted
    assert r.status == "denied"
    r, _ = Chat(app, "u-marco").ask("Forecast sales through Sunday")  # manager: granted but not built yet
    assert r.status == "unsupported"


def test_scheduled_briefing_rechecks_scope_at_run_time(app, chat):
    chat.ask("Send me a sales briefing every day at 8 AM")
    app.settings.dev_users["u-anna"].store_id = "210"  # anna transferred to another store
    app.clock.set(datetime(2026, 9, 27, 12, 0, 30, tzinfo=timezone.utc))
    [(_, status)] = app.scheduler.tick()
    assert status == "denied"
    assert app.outbox.messages() == []
