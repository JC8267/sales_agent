"""SQLite persistence for the local slice. Each table sits behind a small repository so
production can move to Postgres/Cloud SQL (or Firestore/Cosmos) without touching callers."""

import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists conversation_frames (
    conversation_id text primary key,
    user_id text not null,
    frame_json text not null,
    updated_at text not null
);
create table if not exists conversation_refs (
    user_id text not null,
    channel text not null,
    reference_json text not null,
    updated_at text not null,
    primary key (user_id, channel)
);
create table if not exists automations (
    automation_id text primary key,
    owner text not null,
    status text not null,
    definition_json text not null,
    next_run_at text,
    created_at text not null,
    updated_at text not null
);
create index if not exists ix_automations_due on automations (status, next_run_at);
create index if not exists ix_automations_owner on automations (owner);
create table if not exists automation_runs (
    automation_id text not null,
    scheduled_for text not null,
    status text not null,
    started_at text not null,
    finished_at text,
    error text,
    request_id text,
    primary key (automation_id, scheduled_for)
);
create table if not exists outbox (
    delivery_id text primary key,
    user_id text not null,
    channel text not null,
    payload_json text not null,
    created_at text not null
);
create table if not exists traces (
    request_id text primary key,
    user_id text,
    store_id text,
    created_at text not null,
    capability text,
    status text,
    trace_json text not null
);
"""


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
