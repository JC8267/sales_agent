import io
import json
from datetime import datetime
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from unittest.mock import Mock

import pytest

from store_agent.app import build_app
from store_agent.clock import AdjustableClock
from store_agent.config import CubeConfig, load_settings
from store_agent.tools.semantic.contract import DataNotAvailable, SemanticLayerUnavailable, SemanticQuery, TimeRange, UnsupportedQuery
from store_agent.tools.semantic.cube import CubeSemanticLayer, _NoRedirect
from store_agent.tools.semantic.mock import MockSemanticLayer

from conftest import Chat


@pytest.fixture
def cube(monkeypatch):
    monkeypatch.setenv("STORE_AGENT_CUBE_TOKEN", "test-token")
    config = CubeConfig(
        enabled=True, api_url="https://cube.example/cubejs-api/v1",
        store_member="Retail.store", time_member="Retail.date",
        measures={"sales": "Retail.sales", "availability": "Retail.availability"},
        dimensions={"department": "Retail.department"},
    )
    layer = CubeSemanticLayer(config, load_settings().catalog, AdjustableClock(datetime.fromisoformat("2026-09-26T14:00:00-04:00")))
    layer._opener = Mock()
    return layer


def query(**changes):
    return SemanticQuery(store_id="042", metrics=["sales"], time_range=TimeRange(type="yesterday"), **changes)


def respond(cube, *payloads):
    cube._opener.open.side_effect = [io.BytesIO(json.dumps(p).encode()) for p in payloads]


def test_cube_request_and_result(cube):
    respond(cube, {"data": [{"Retail.department": "Bedroom", "Retail.sales": "1234.56"}]})
    result = cube.query(query(dimensions=["department"], filters={"department": ["Bedroom"]}, order_by="sales", limit=3))
    request = cube._opener.open.call_args.args[0]
    sent = json.loads(request.data)["query"]
    assert request.full_url == "https://cube.example/cubejs-api/v1/load"
    assert request.get_method() == "POST" and request.get_header("Authorization") == "test-token"
    assert sent == {
        "measures": ["Retail.sales"], "dimensions": ["Retail.department"],
        "timeDimensions": [{"dimension": "Retail.date", "dateRange": ["2026-09-25", "2026-09-25"]}],
        "timezone": "America/New_York", "limit": 3, "order": {"Retail.sales": "desc"},
        "filters": [
            {"member": "Retail.store", "operator": "equals", "values": ["042"]},
            {"member": "Retail.department", "operator": "equals", "values": ["Bedroom"]},
        ],
    }
    assert cube._opener.open.call_args.kwargs["timeout"] == 10
    assert result.source == "cube" and result.rows == [{"department": "Bedroom", "sales": 1234.56}]


def test_wait_retries_are_bounded(cube, monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("store_agent.tools.semantic.cube.time.sleep", sleep)
    respond(cube, {"error": "Continue wait"}, {"data": [{"Retail.sales": 0}]})
    assert cube.query(query()).rows == [{"sales": 0.0}]
    assert cube._opener.open.call_count == 2 and sleep.call_count == 1
    cube._opener.open.reset_mock()
    respond(cube, *[{"error": "Continue wait"}] * 3)
    with pytest.raises(SemanticLayerUnavailable):
        cube.query(query())
    assert cube._opener.open.call_count == 3


@pytest.mark.parametrize("payload", [[], {}, {"data": {}}, {"data": [None]}, {"data": [{}]}, {"data": [{"Retail.sales": "nan"}]}, {"data": [{"Retail.sales": True}]}, {"error": "private test-token", "data": []}])
def test_invalid_results_fail_safely(cube, payload):
    respond(cube, payload)
    with pytest.raises(SemanticLayerUnavailable) as caught:
        cube.query(query())
    assert "test-token" not in str(caught.value) and "private" not in str(caught.value)


@pytest.mark.parametrize("error", [URLError("private test-token"), TimeoutError(), IncompleteRead(b"private"), HTTPError("private-url", 401, "test-token", {}, None)])
def test_transport_errors_are_sanitized(cube, error):
    cube._opener.open.side_effect = error
    with pytest.raises(SemanticLayerUnavailable) as caught:
        cube.query(query())
    assert "test-token" not in str(caught.value) and "private" not in str(caught.value)


def test_missing_and_oversized_data(cube):
    respond(cube, {"data": [{"Retail.sales": None}]})
    with pytest.raises(DataNotAvailable):
        cube.query(query())
    respond(cube, {"data": [{"Retail.sales": "1"}] * 10_001})
    with pytest.raises(DataNotAvailable):
        cube.query(query())


@pytest.mark.parametrize("changes", [{"comparison": "last_year"}, {"dimensions": ["hour"]}, {"dimensions": ["date"]}, {"filters": {"department": []}}, {"filters": {"store_id": ["017"]}}, {"order_by": "units"}])
def test_unsupported_queries_never_call_cube(cube, changes):
    with pytest.raises(UnsupportedQuery):
        cube.query(query(**changes))
    cube._opener.open.assert_not_called()


def test_store_measure_and_freshness_guards(cube):
    for change in [{"store_id": "999"}, {"metrics": ["units"]}, {"filters": {"department": ["Unknown"]}}]:
        q = query().model_copy(update=change)
        with pytest.raises(UnsupportedQuery):
            cube.query(q)
    with pytest.raises(DataNotAvailable):
        cube.query(query().model_copy(update={"time_range": TimeRange(type="today")}))
    cube._opener.open.assert_not_called()


def test_config_selection_and_explicit_override(cube, monkeypatch):
    settings = load_settings()
    assert isinstance(build_app(settings=settings).runtime.semantic_layer, MockSemanticLayer)
    settings.cube = cube.config
    assert isinstance(build_app(settings=settings).runtime.semantic_layer, CubeSemanticLayer)
    monkeypatch.delenv("STORE_AGENT_CUBE_TOKEN")
    with pytest.raises(ValueError, match="authorization"):
        build_app(settings=settings)
    assert build_app(settings=settings, semantic_layer=cube).runtime.semantic_layer is cube


def test_runtime_uses_cube_evidence_and_denies_other_stores(cube):
    app = build_app(clock=cube.clock, semantic_layer=cube)
    respond(cube, {"data": [{"Retail.sales": "1234.56"}]})
    response, trace = Chat(app).ask("What were sales yesterday?")
    assert response.status == "ok" and "$1.2K" in response.text
    assert trace.tool_calls[0].status == "ok" and trace.evidence
    cube._opener.open.reset_mock()
    response, trace = Chat(app).ask("What were sales at store 017 yesterday?")
    assert response.status == "denied" and not trace.tool_calls
    cube._opener.open.assert_not_called()
    cube._opener.open.side_effect = URLError("private test-token")
    response, trace = Chat(app).ask("What were sales yesterday?")
    assert response.status == "error" and not trace.evidence
    assert "test-token" not in response.text and "test-token" not in str(trace)


@pytest.mark.parametrize("changes", [{"api_url": "http://cube.example/v1"}, {"api_url": "https://user:secret@cube.example/v1"}, {"api_url": "https://cube.example/v1?token=secret"}, {"store_member": ""}, {"measures": {}}, {"measures": {"sales": ""}}])
def test_incomplete_or_unsafe_config_fails_at_startup(cube, changes):
    with pytest.raises(ValueError):
        CubeSemanticLayer(cube.config.model_copy(update=changes), cube.catalog, cube.clock)


def test_redirects_do_not_forward_authorization():
    handler = _NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://other.example") is None
