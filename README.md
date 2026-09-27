# Store Employee AI Agent (spike)

Orchestration platform for a store-employee assistant in Microsoft Teams. This repo holds
the design (`docs/ARCHITECTURE.md`), the MVP backlog (`docs/BACKLOG.md`), and a runnable
vertical slice of the five spike interactions from the implementation handoff.

The slice runs fully offline: a deterministic mock semantic layer, a scripted fake LLM
provider behind the model gateway, SQLite storage, and a local chat channel standing in
for Teams. Every boundary (identity, semantic layer, models, scheduler, channel) is an
interface, so real implementations replace fakes without touching orchestration code.

## Run it

GitHub Actions runs the tests and router evaluation on every push and pull request.
The router regression test requires all labeled cases to pass capability and tier checks,
with no unnecessary DEEP routing. CI installs dependencies from `uv.lock` on Python 3.12.

```powershell
uv sync
uv run pytest
uv run store-agent demo                      # the five spike interactions + the 8 AM briefing
uv run store-agent --now 2026-09-26T14:00:00-04:00 chat --trace
uv run python evals/run_router_eval.py      # routing accuracy / tier metrics
```

In `chat`, type `/help`. Useful commands: `/tick` runs the scheduler at the pinned clock,
`/outbox` shows proactive DMs, `/card` prints the Adaptive Card JSON for the last answer,
and typing a number clicks a suggested action.

Dev users (`config/dev_users.yaml`): `u-anna` (coworker, store 042), `u-marco` (manager,
042), `u-rita` (market manager, US-EAST stores 042 and 017), `u-wes` (coworker, 210).

## Layout

```text
config/                 models, routing, capabilities/roles, semantic catalog, dev users
src/store_agent/
  contracts.py          AgentRequest, UserContext, RouteDecision, AgentResponse
  security/             identity provider, deterministic authorization
  router/               slot extraction, rule router, stage-2 interface, confidence policy
  models/               model gateway (tiers, fallback, circuit breaker), fake provider
  tools/semantic/       semantic-layer contract, calendar, mock layer, scoped tool wrapper
  tools/analysis.py     tools offered to the reasoning model during diagnosis
  analytics/            deterministic contribution math
  context/              conversation frame (scoped follow-up memory)
  runtime/              orchestrator, drafts, diagnosis loop, briefing, automation intents
  validation/           numeric/date/scope/direction checks against the evidence ledger
  automations/          automation model, repository, polling scheduler + executor
  channels/             channel boundary, outbox sender, Adaptive Cards, Teams mapping, console
  observability/        per-request traces
  prompts/              prompts for real models (the fake provider ignores them)
evals/                  labeled routing dataset + runner
tests/                  unit tests and the end-to-end spike test
```
