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

## Cube connection framework

The Cube REST adapter is opt-in; offline runs still use mock data. Configure
`config/cube.yaml` with your REST base URL (including `/v1`), store/time members,
measure mappings, and an optional department mapping. The names in comments are examples,
not assumptions about your deployment. Set the environment variable named by `token_env`
to the complete Cube `Authorization` header value, then set `enabled: true`.
Do not put tokens in YAML or commit them. Missing connection settings fail at startup;
Cube errors never silently fall back to synthetic data.

The adapter supports aggregate measures and department breakdowns/filters, absolute or
relative date ranges, store-local timezone, ordering, and limits. It always adds the
runtime-selected store filter. Cube must also enforce the approved service account's
access policy; this framework does not mint user JWTs or configure Cube-side policies.
Measure values must already use our catalog units (availability is 0–100 percent).

Comparisons and date/hour breakdowns are explicitly unsupported until Cube's fiscal
calendar and member definitions are confirmed. Existing data-lag settings remain in
effect and need confirmation. Requests have a configured timeout; `Continue wait` is
retried at most twice. No live Cube deployment has been tested yet.

References: [Cube REST API](https://docs.cube.dev/reference/core-data-apis/rest-api/reference),
[query format](https://docs.cube.dev/reference/core-data-apis/rest-api/query-format).

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
