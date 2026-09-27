"""Polling scheduler + executor. Timing is plain code over persisted definitions; no model
is involved. A cloud scheduler (Cloud Scheduler, Azure Functions timer, Temporal) can call
tick() instead of run_forever()."""

import logging
import time
from datetime import timedelta
from threading import Lock
from typing import Protocol

from store_agent.automations.models import Automation
from store_agent.automations.repository import AutomationRepository
from store_agent.channels.base import DeliveryError, ProactiveSender, send_with_retry
from store_agent.clock import Clock
from store_agent.contracts import AgentResponse

log = logging.getLogger("store_agent.scheduler")


class BriefingRunner(Protocol):
    def run_briefing(self, owner: str, store_id: str) -> AgentResponse: ...


class AutomationExecutor:
    def __init__(self, runner: BriefingRunner, repo: AutomationRepository, sender: ProactiveSender, clock: Clock, sleep=time.sleep):
        self.runner, self.repo, self.sender, self.clock, self.sleep = runner, repo, sender, clock, sleep

    def execute(self, a: Automation, scheduled_for) -> str:
        if not self.repo.claim_run(a.automation_id, scheduled_for, self.clock.now()):
            return "duplicate"
        request_id, error = None, None
        try:
            response = self.runner.run_briefing(a.owner, a.task.store_id)
            request_id = response.request_id
            if response.status in ("denied", "error"):
                status, error = response.status, response.text  # never deliver a failed/denied run as if it were data
            else:
                send_with_retry(self.sender, a.owner, response, sleep=self.sleep)
                status = "delivered"
        except DeliveryError as e:
            status, error = "delivery_failed", str(e)
        except Exception as e:  # keep the scheduler alive; the run record carries the failure
            log.exception("automation %s failed", a.automation_id)
            status, error = "error", f"{type(e).__name__}: {e}"

        now = self.clock.now()
        self.repo.finish_run(a.automation_id, scheduled_for, status, now, error, request_id)
        current = self.repo.get(a.automation_id) or a
        current.last_run_at, current.last_status, current.last_error, current.updated_at = now, status, error, now
        self.repo.save(current)
        return status


class PollingScheduler:
    # ponytail: serialize local SQLite ticks; use a durable outbox before external delivery.
    _tick_lock = Lock()

    def __init__(self, repo: AutomationRepository, executor: AutomationExecutor, clock: Clock, max_lateness: timedelta = timedelta(hours=2)):
        self.repo, self.executor, self.clock, self.max_lateness = repo, executor, clock, max_lateness

    def tick(self) -> list[tuple[str, str]]:
        # The local outbox, run record, and schedule commit together. An interrupted
        # transaction rolls back, leaving the occurrence due for the next process.
        with self._tick_lock, self.repo.conn:
            self.repo.conn.execute("BEGIN IMMEDIATE")
            return self._tick()

    def _tick(self) -> list[tuple[str, str]]:
        now = self.clock.now()
        results = []
        for a in self.repo.due(now):
            scheduled_for = a.next_run_at
            # This advance is committed only with the run and local delivery.
            a.next_run_at = a.schedule.next_run(now, a.timezone)
            a.updated_at = now
            self.repo.save(a)
            if now - scheduled_for > self.max_lateness:
                self.repo.claim_run(a.automation_id, scheduled_for, now)
                self.repo.finish_run(a.automation_id, scheduled_for, "missed", now, "scheduler was late", None)
                results.append((a.automation_id, "missed"))
                continue
            results.append((a.automation_id, self.executor.execute(a, scheduled_for)))
        return results

    def run_forever(self, interval_s: float = 30.0) -> None:
        while True:
            for automation_id, status in self.tick():
                log.info("automation %s -> %s", automation_id, status)
            time.sleep(interval_s)
