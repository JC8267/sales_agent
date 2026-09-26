"""Channel boundary: the agent core produces AgentResponse; adapters own delivery."""

import json
import sqlite3
import time
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from store_agent.clock import Clock, utc_iso
from store_agent.contracts import AgentResponse


class DeliveryError(Exception):
    pass


class ProactiveSender(Protocol):
    def send(self, user_id: str, response: AgentResponse) -> str:
        """Deliver without an inbound message; returns a delivery id or raises DeliveryError."""
        ...


class ConversationReferenceStore:
    """Proactive messages need the channel's conversation reference, captured from the
    user's inbound traffic (Teams: from the activity, or after app install via Graph)."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock):
        self.conn, self.clock = conn, clock

    def upsert(self, user_id: str, channel: str, reference: dict) -> None:
        self.conn.execute(
            "insert or replace into conversation_refs values (?, ?, ?, ?)",
            (user_id, channel, json.dumps(reference), utc_iso(self.clock.now())),
        )

    def get(self, user_id: str, channel: str) -> dict | None:
        row = self.conn.execute("select reference_json from conversation_refs where user_id = ? and channel = ?", (user_id, channel)).fetchone()
        return json.loads(row["reference_json"]) if row else None


class OutboxSender:
    """Local stand-in for Teams proactive DMs: requires a stored conversation reference
    (like Teams does) and writes the rendered payload to the outbox table."""

    channel = "teams_dm"

    def __init__(self, conn: sqlite3.Connection, refs: ConversationReferenceStore, clock: Clock, render: Callable[[AgentResponse], dict]):
        self.conn, self.refs, self.clock, self.render = conn, refs, clock, render

    def send(self, user_id: str, response: AgentResponse) -> str:
        ref = self.refs.get(user_id, self.channel)
        if ref is None:
            raise DeliveryError(f"no conversation reference for {user_id}; the user must install/message the app first")
        delivery_id = uuid4().hex
        payload = {"conversation": ref, "text": response.text, "card": self.render(response), "request_id": response.request_id}
        self.conn.execute(
            "insert into outbox values (?, ?, ?, ?, ?)",
            (delivery_id, user_id, self.channel, json.dumps(payload), utc_iso(self.clock.now())),
        )
        return delivery_id

    def messages(self, user_id: str | None = None) -> list[dict]:
        sql, args = "select * from outbox order by created_at, rowid", ()
        if user_id:
            sql, args = "select * from outbox where user_id = ? order by created_at, rowid", (user_id,)
        return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in self.conn.execute(sql, args).fetchall()]


def send_with_retry(
    sender: ProactiveSender, user_id: str, response: AgentResponse, attempts: int = 3, backoff_s: float = 2.0, sleep=time.sleep
) -> tuple[str, int]:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return sender.send(user_id, response), attempt
        except DeliveryError as e:
            last = e
            if attempt < attempts:
                sleep(backoff_s * 2 ** (attempt - 1))
    raise DeliveryError(f"delivery failed after {attempts} attempts: {last}")
