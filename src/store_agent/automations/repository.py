import sqlite3
from datetime import datetime

from store_agent.automations.models import Automation
from store_agent.clock import utc_iso


class AutomationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save(self, a: Automation) -> None:
        self.conn.execute(
            "insert or replace into automations values (?, ?, ?, ?, ?, ?, ?)",
            (
                a.automation_id,
                a.owner,
                a.status,
                a.model_dump_json(),
                utc_iso(a.next_run_at) if a.next_run_at and a.status == "active" else None,
                utc_iso(a.created_at),
                utc_iso(a.updated_at),
            ),
        )

    def get(self, automation_id: str) -> Automation | None:
        row = self.conn.execute("select definition_json from automations where automation_id = ?", (automation_id,)).fetchone()
        return Automation.model_validate_json(row["definition_json"]) if row else None

    def list_for_owner(self, owner: str, include_deleted: bool = False) -> list[Automation]:
        rows = self.conn.execute("select definition_json from automations where owner = ? order by created_at", (owner,)).fetchall()
        items = [Automation.model_validate_json(r["definition_json"]) for r in rows]
        return items if include_deleted else [a for a in items if a.status != "deleted"]

    def due(self, now: datetime) -> list[Automation]:
        rows = self.conn.execute(
            "select definition_json from automations where status = 'active' and next_run_at is not null and next_run_at <= ? order by next_run_at",
            (utc_iso(now),),
        ).fetchall()
        return [Automation.model_validate_json(r["definition_json"]) for r in rows]

    def claim_run(self, automation_id: str, scheduled_for: datetime, now: datetime) -> bool:
        """Idempotency guard: exactly one executor wins a given (automation, fire time)."""
        cur = self.conn.execute(
            "insert or ignore into automation_runs (automation_id, scheduled_for, status, started_at) values (?, ?, 'running', ?)",
            (automation_id, utc_iso(scheduled_for), utc_iso(now)),
        )
        return cur.rowcount == 1

    def finish_run(self, automation_id: str, scheduled_for: datetime, status: str, now: datetime, error: str | None, request_id: str | None) -> None:
        self.conn.execute(
            "update automation_runs set status = ?, finished_at = ?, error = ?, request_id = ? where automation_id = ? and scheduled_for = ?",
            (status, utc_iso(now), error, request_id, automation_id, utc_iso(scheduled_for)),
        )

    def runs(self, automation_id: str) -> list[dict]:
        rows = self.conn.execute("select * from automation_runs where automation_id = ? order by scheduled_for", (automation_id,)).fetchall()
        return [dict(r) for r in rows]
