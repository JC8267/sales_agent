"""Local chat channel for development. Plays the role Teams plays in production:
authenticated user id in, AgentResponse out, conversation reference captured."""

import textwrap
from uuid import uuid4

from store_agent.clock import Clock
from store_agent.contracts import AgentRequest, AgentResponse
from store_agent.channels.base import ConversationReferenceStore


class ConsoleChannel:
    name = "console"

    def __init__(self, clock: Clock, refs: ConversationReferenceStore):
        self.clock, self.refs = clock, refs
        self.conversation_id = f"console-{uuid4().hex[:8]}"

    def new_conversation(self) -> None:
        self.conversation_id = f"console-{uuid4().hex[:8]}"

    def to_request(self, user_id: str, text: str, from_action: bool = False) -> AgentRequest:
        # Stand-in for capturing the Teams conversation reference on every inbound message.
        self.refs.upsert(user_id, "teams_dm", {"channel": self.name, "conversation_id": f"dm-{user_id}"})
        return AgentRequest(
            user_id=user_id,
            conversation_id=self.conversation_id,
            message=text,
            channel=self.name,
            timestamp=self.clock.now(),
            context={"source": "card_action" if from_action else "text"},
        )


def render_text(response: AgentResponse, width: int = 96) -> str:
    lines = []
    for para in response.text.split("\n"):
        lines.append(textwrap.fill(para, width, subsequent_indent="  ") if para else "")
    if response.status not in ("ok",):
        lines.append(f"  [{response.status}]")
    for i, a in enumerate(response.actions, 1):
        lines.append(f"  [{i}] {a.title}")
    return "\n".join(lines)
