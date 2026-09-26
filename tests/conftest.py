from datetime import datetime

import pytest

from store_agent.app import App, build_app
from store_agent.channels.console import ConsoleChannel
from store_agent.clock import AdjustableClock
from store_agent.contracts import AgentResponse
from store_agent.observability.tracing import Trace

SAT_AFTERNOON = datetime.fromisoformat("2026-09-26T14:00:00-04:00")  # yesterday = Fri 2026-09-25


class Chat:
    def __init__(self, app: App, user: str = "u-anna"):
        self.app, self.user = app, user
        self.channel = ConsoleChannel(app.clock, app.refs)

    def ask(self, text: str) -> tuple[AgentResponse, Trace]:
        response = self.app.runtime.handle(self.channel.to_request(self.user, text))
        return response, self.app.traces.get(response.request_id)


@pytest.fixture
def app(tmp_path) -> App:
    return build_app(tmp_path / "test.db", AdjustableClock(SAT_AFTERNOON))


@pytest.fixture
def chat(app) -> Chat:
    return Chat(app)
