"""Local CLI: interactive chat, a scripted demo of the spike, and the scheduler loop."""

import argparse
import json
import sys
from datetime import datetime

from store_agent.app import App, build_app
from store_agent.channels.adaptive_cards import render_card
from store_agent.channels.console import ConsoleChannel, render_text
from store_agent.clock import AdjustableClock
from store_agent.contracts import AgentResponse
from store_agent.observability.tracing import Trace

SPIKE = [
    "What were sales yesterday?",
    "Compare that with last year.",
    "Which departments drove the difference?",
    "Why was Bedroom down?",
    "Send me this briefing every weekday at 8 AM.",
]

HELP = """commands:
  /user <id>      switch user (u-anna, u-marco, u-rita, u-wes); starts a new conversation
  /now <iso>|off  pin the clock (e.g. 2026-09-28T08:00:30-04:00) or return to system time
  /tick           run the scheduler once at the current clock
  /outbox         show proactive messages delivered so far
  /automations    list the current user's automations
  /trace          show the last request's trace
  /card           show the last response as Adaptive Card JSON
  /new            start a new conversation
  /quit
  <number>        click a suggested action from the last response"""


def _summary(t: Trace) -> str:
    tools = ", ".join(f"{c.name}({c.latency_ms:.0f}ms)" for c in t.tool_calls) or "-"
    models = ", ".join(f"{m.model}:{m.purpose}" + ("" if m.status == "ok" else "!") for m in t.model_calls) or "-"
    v = t.validation or {}
    val = "-" if not v else ("pass" if v.get("passed") else f"FAIL {v.get('violations')}") + f" ({v.get('checked')} checks)"
    return (
        f"  route={t.capability} tier={t.reasoning_tier} conf={t.router_confidence} via {t.router} ({t.route_reason})\n"
        f"  tools: {tools}\n  models: {models}\n  validation: {val}  status={t.status}  {t.total_latency_ms}ms"
        + (f"\n  notes: {t.notes}" if t.notes else "")
    )


class Session:
    def __init__(self, app: App, user: str):
        self.app, self.user = app, user
        self.channel = ConsoleChannel(app.clock, app.refs)
        self.last: AgentResponse | None = None

    def send(self, text: str, from_action: bool = False) -> AgentResponse:
        self.last = self.app.runtime.handle(self.channel.to_request(self.user, text, from_action))
        return self.last

    def show(self, response: AgentResponse, with_trace: bool) -> None:
        print(f"agent> {render_text(response)}")
        if with_trace:
            t = self.app.traces.get(response.request_id)
            if t:
                print(_summary(t))

    def tick(self) -> None:
        results = self.app.scheduler.tick()
        print(f"scheduler @ {self.app.clock.now():%Y-%m-%d %H:%M} UTC -> {results or 'nothing due'}")


def cmd_chat(app: App, args) -> None:
    s = Session(app, args.user)
    print(f"Store agent (local). User: {s.user}. Type /help for commands.")
    while True:
        try:
            line = input(f"{s.user}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line.isdigit() and s.last and 1 <= int(line) <= len(s.last.actions):
            action = s.last.actions[int(line) - 1]
            print(f"  (clicked: {action.title} -> {action.message})")
            s.show(s.send(action.message, from_action=True), args.trace)
            continue
        cmd, _, arg = line.partition(" ")
        if cmd == "/quit":
            return
        elif cmd == "/help":
            print(HELP)
        elif cmd == "/user":
            s.user = arg.strip() or s.user
            s.channel.new_conversation()
            print(f"now chatting as {s.user}")
        elif cmd == "/new":
            s.channel.new_conversation()
            print("new conversation")
        elif cmd == "/now":
            app.clock.set(None if arg.strip() in ("", "off") else datetime.fromisoformat(arg.strip()))
            print(f"clock: {app.clock.now().isoformat()}")
        elif cmd == "/tick":
            s.tick()
        elif cmd == "/outbox":
            for m in app.outbox.messages():
                print(f"--- to {m['user_id']} at {m['created_at']}\n{m['payload']['text']}")
        elif cmd == "/automations":
            for a in app.automations.list_for_owner(s.user, include_deleted=True):
                print(f"  {a.automation_id} {a.status} {a.schedule.describe()} cron='{a.schedule.cron()}' tz={a.timezone} next={a.next_run_at} last={a.last_status}")
        elif cmd == "/trace":
            t = app.traces.get(s.last.request_id) if s.last else None
            print(t.model_dump_json(indent=2) if t else "no trace yet")
        elif cmd == "/card":
            print(json.dumps(render_card(s.last), indent=2, ensure_ascii=False) if s.last else "no response yet")
        elif cmd.startswith("/"):
            print("unknown command; /help")
        else:
            s.show(s.send(line), args.trace)


def cmd_demo(app: App, args) -> None:
    s = Session(app, args.user)
    print(f"clock pinned to {app.clock.now().isoformat()} (UTC)\n")
    for msg in SPIKE:
        print(f"{s.user}> {msg}")
        s.show(s.send(msg), with_trace=True)
        print()
    nxt = [a.next_run_at for a in app.automations.list_for_owner(s.user) if a.next_run_at]
    if nxt:
        fire = min(nxt)
        app.clock.set(fire.replace(second=30))
        s.tick()
        for m in app.outbox.messages(s.user):
            print(f"--- proactive DM to {m['user_id']} ---\n{m['payload']['text']}")
        s.tick()  # second tick at the same time must not re-send


def cmd_scheduler(app: App, args) -> None:
    if args.once:
        print(app.scheduler.tick())
    else:
        app.scheduler.run_forever(args.interval)


def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    p = argparse.ArgumentParser(prog="store-agent")
    p.add_argument("--db", default=".data/dev.db", help="SQLite path (':memory:' for throwaway)")
    p.add_argument("--now", help="pin the clock, ISO 8601 with offset")
    sub = p.add_subparsers(dest="command", required=True)
    chat = sub.add_parser("chat")
    chat.add_argument("--user", default="u-anna")
    chat.add_argument("--trace", action="store_true", help="print a trace summary after each answer")
    demo = sub.add_parser("demo", help="run the five spike interactions and the 8 AM briefing")
    demo.add_argument("--user", default="u-anna")
    sched = sub.add_parser("scheduler")
    sched.add_argument("--once", action="store_true")
    sched.add_argument("--interval", type=float, default=30.0)
    args = p.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else None
    if args.command == "demo":
        now = now or datetime.fromisoformat("2026-09-26T14:00:00-04:00")
        args.db = ":memory:" if args.db == ".data/dev.db" else args.db
    app = build_app(args.db, AdjustableClock(now))
    {"chat": cmd_chat, "demo": cmd_demo, "scheduler": cmd_scheduler}[args.command](app, args)


if __name__ == "__main__":
    main()
