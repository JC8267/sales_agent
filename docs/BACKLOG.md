# MVP Backlog

Tickets follow the handoff phases. "Spike" means already implemented in this repo against
fakes; the remaining work swaps in real infrastructure behind the existing interface.
Blocking questions refer to `ARCHITECTURE.md` §0 (A1–A7).

## Phase 1: Foundation

| ID | Ticket | Acceptance | Status / blockers |
|---|---|---|---|
| B-01 | Contracts, config loading, composition root | `AgentRequest/UserContext/RouteDecision/AgentResponse` stable; `build_app` wires all components | Spike done |
| B-02 | Deterministic authorization engine | Store + capability scope from role policy; denial before tools/models; tests | Spike done |
| B-03 | Entra identity provider | aadObjectId → store, role, market, tz from directory; cached (TTL); unknown → denied | A5 |
| B-04 | Role source | Entra app roles or groups → `capabilities.yaml` roles; change propagates without deploy | A5 |
| B-05 | Postgres persistence | Repositories for automations, runs, refs, frames on Postgres with migrations; same tests pass | Hosting decision |
| B-06 | Trace export | OpenTelemetry spans + trace rows in BigQuery; message text off by default | — |
| B-07 | BigQuery semantic adapter | `SemanticQuery` → parameterized SQL; store filter + row-level security; byte cap, timeout, cache; golden queries match hand-written SQL | A1 |
| B-08 | Fiscal calendar | LY alignment and "last week" from the calendar table; replaces `calendar.py` rules | A2 |

## Phase 2: Router

| ID | Ticket | Acceptance | Status / blockers |
|---|---|---|---|
| B-09 | Real utterance collection | 200+ pilot-store questions captured (hashed users); rules/slots extended | Pilot access |
| B-10 | Router eval as CI gate | ≥200 labeled cases incl. multi-turn and unauthorized; CI fails below agreed capability/tier accuracy; tracks unnecessary-deep rate | Spike: 43 cases, runner done |
| B-11 | First real model provider | `ModelProvider` adapter; tiers mapped in `models.yaml`; pricing, timeouts; fallback verified | A4 |
| B-12 | Stage-2 router bake-off | Evaluate a subset of the [System One candidates](ARCHITECTURE.md#stage-2-system-one-model-candidates) vs small LLM classifier and rules-only on B-10 set: accuracy, calibration, fallback rate, latency, cost | B-10, B-11, A7 |

## Phase 3: Analytics agent

| ID | Ticket | Acceptance | Status / blockers |
|---|---|---|---|
| B-13 | Diagnosis with a real model | Prompt/tool tuning; ≥90% of golden diagnostics pass validation first try; fallback rate tracked | B-11, B-25 |
| B-14 | Validation hardening | Direction after numbers ("18% lower"); claim-level number binding; unit mismatch checks | Spike: core validator done |
| B-15 | Semantic coverage | Channel dimension (store vs online), units, availability by department/day; intraday if a feed exists (A3) | B-07 |

## Phase 4: Teams UX

| ID | Ticket | Acceptance | Status / blockers |
|---|---|---|---|
| B-16 | Teams host | Agents SDK service + Azure Bot + app manifest; `activity_to_request` / `response_to_activity` wired; works in Agents Playground and a test tenant | A6; spike: mapping done |
| B-17 | Conversation references + proactive install | Reference captured on message and install events; Graph proactive install for pre-provisioned users | B-16 |
| B-18 | Teams proactive sender | `ProactiveSender` using stored reference; honors 429/Retry-After; failures recorded | B-17; spike: outbox stand-in |
| B-19 | Cards + feedback | Card polish; `Action.Execute` refresh; 👍/👎 + reasons stored against request_id | B-16 |
| B-20 | Latency UX | Typing indicator / streaming for STANDARD+ requests | B-16 |

## Phase 5: Automations

| ID | Ticket | Acceptance | Status / blockers |
|---|---|---|---|
| B-21 | Hosted scheduler | Cloud Scheduler or Functions timer calls an authenticated `tick` endpoint each minute; idempotent under concurrent ticks | B-05; spike: tick + claim done |
| B-22 | Manage automations in cards | List / pause / resume / reschedule from card buttons | B-19; spike: conversational manage done |
| B-23 | Delivery reliability | Retry policy, dead-letter, alert on `delivery_failed` rate | B-18 |
| B-24 | Conditional alerts | Deterministic evaluator for `Condition` (hourly/daily), cooldown/dedup, conversational create | B-07; model designed |

## Phase 6: Evaluation and hardening

| ID | Ticket | Acceptance | Status / blockers |
|---|---|---|---|
| B-25 | Golden answer set | Realistic questions → expected semantic queries + numbers; runner reports numerical, scope, date, comparison accuracy and unsupported-claim rate | B-07 |
| B-26 | Latency/cost dashboard | Looker over trace tables: route mix, tiers, fallbacks, validation failures, p50/p95, cost | B-06 |
| B-27 | Security testing | Scope-bypass attempts, prompt injection via tool data, red-team set in CI | — |
| B-28 | Load test | Target concurrency; BigQuery quota and model rate-limit headroom | B-07, B-11 |

## Phase 7: Expansion

| ID | Ticket | Acceptance |
|---|---|---|
| B-29 | Document retrieval | `search_documents(query, user_scope)`; answers labelled DOCUMENT vs DATA vs GENERAL; injection-safe |
| B-30 | Forecast tool | Approved forecasting service behind `get_forecast`; the LLM only explains |
| B-31 | Approved actions | Confirmation, idempotency keys, audit, stricter policy than reads |
| B-32 | Additional channels | Second `ChannelAdapter` (e.g. Web Chat / M365 Copilot) with no agent-core changes |

## Suggested order for the next sprint

1. Close A1/A5/A6 (data table, identity attributes, Teams app approval path).
2. B-07 BigQuery adapter and B-03 Entra identity. These carry the most risk.
3. B-16 to B-18 Teams host and proactive send, so the spike runs in a test tenant.
4. B-11 first real provider once A4 is decided, then B-13 and B-25.
