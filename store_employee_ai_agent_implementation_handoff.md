# Store Employee AI Agent — Implementation Handoff

## 1. Project Objective

Build an enterprise AI assistant for store employees that lives primarily in **Microsoft Teams**.

The assistant should give employees natural-language access to trusted business data through an **existing semantic layer**, while dynamically selecting the appropriate:

- capability/tool
- model tier
- reasoning depth
- workflow

The assistant must also support **scheduled and eventually conditional automations**, for example:

> “Every morning at 8 AM give me a briefing on yesterday’s sales.”

The system should feel like one agent to the employee even though requests may be handled by very different backend paths.

This should be built as a **model-agnostic orchestration platform**, not as a chatbot tied to one LLM vendor.

---

## 2. Existing Foundation

An enterprise **semantic layer already exists**.

Treat the semantic layer as the authoritative interface for:

- metrics
- dimensions
- business definitions
- filtering
- store-level data
- fiscal/calendar logic
- aggregations
- comparisons

Do not duplicate business logic inside prompts or the LLM.

The LLM should never become the source of truth for numerical calculations that can be performed by the semantic layer.

---

## 3. Core Design Principle

The architecture should separate four decisions:

1. **What does the user want?**
2. **Which capability/tool should handle it?**
3. **How much model intelligence/reasoning is required?**
4. **How should the result be delivered?**

Conceptually:

```text
Employee
   │
   ▼
Microsoft Teams
   │
   ▼
Identity / Authorization
   │
   ▼
Agent Runtime
   │
   ▼
Dynamic Router
   │
   ├── Semantic Layer
   ├── Document Retrieval
   ├── Analytics / Diagnostics
   ├── Forecasting
   ├── Automation Service
   └── Approved Actions
           │
           ▼
     Model Gateway
           │
    ┌──────┼──────┐
    │      │      │
  Fast  Standard  Deep
    │      │      │
    └──────┼──────┘
           ▼
      Validation
           │
           ▼
Microsoft Teams
```

---

## 4. Do Not Use Quail in the Initial Implementation

Quail has been evaluated conceptually but is intentionally excluded from the initial architecture.

Do not introduce Quail as a dependency.

The architecture should leave room for additional analytical/data tools later without requiring redesign.

---

## 5. Microsoft Teams Experience

Microsoft Teams should be the primary user interface.

Employees should be able to:

- DM the agent
- potentially invoke it from team/channel contexts
- ask follow-up questions conversationally
- receive scheduled proactive messages
- create automations conversationally
- use buttons/Adaptive Cards for common follow-ups

Examples:

> How were sales yesterday?

> Compare yesterday with last year.

> Why were Bedroom sales down?

> What were our top five departments yesterday?

> Give me yesterday’s performance every morning at 8.

> Alert me when sales are more than 10% below last year.

Responses should optimize for a store employee, not an analyst.

Avoid unnecessary technical terminology.

---

## 6. Identity and Authorization

Integrate with Microsoft/Entra identity.

Every request should contain authenticated user context.

At minimum resolve:

```text
user_id
employee role
store
market/region if applicable
timezone
permissions
```

Authorization must be deterministic.

Never rely on an LLM to determine whether a user may access a metric, store, market, document, or action.

Example:

```python
authorized_scope = resolve_scope(user_identity)

if requested_store not in authorized_scope:
    deny_request()
```

The router only sees capabilities the user is authorized to use.

---

## 7. Dynamic Router

Implement a routing layer separate from the main reasoning models.

The router should make two major classifications.

### A. Capability Route

Suggested initial taxonomy:

```text
LOOKUP
COMPARE
DIAGNOSE
DOCUMENT_QUESTION
FORECAST
CREATE_AUTOMATION
MANAGE_AUTOMATION
ACTION
GENERAL
```

Potential future classes:

```text
MIXED_DIAGNOSTIC
SEMANTIC_SEARCH
ANOMALY_INVESTIGATION
PLANNING
```

### B. Reasoning Tier

Use abstract capability tiers rather than hard-coded model names:

```text
NONE
FAST
STANDARD
DEEP
```

Example routing:

| Request | Capability | Reasoning |
|---|---|---|
| What were sales yesterday? | LOOKUP | FAST/NONE |
| Compare yesterday to LY | COMPARE | FAST |
| Why were sales down? | DIAGNOSE | STANDARD/DEEP |
| Forecast through Sunday | FORECAST | STANDARD |
| What does this SOP say? | DOCUMENT_QUESTION | STANDARD |
| Every day at 8 send sales | CREATE_AUTOMATION | FAST |

---

## 8. Router Implementation

Use a hybrid routing strategy.

### Stage 1 — deterministic rules

Route obvious intents without a model whenever possible.

Examples:

```python
if automation_pattern_detected(request):
    return CREATE_AUTOMATION

if exact_metric_lookup(request):
    return LOOKUP

if unauthorized(request):
    return DENY
```

### Stage 2 — intelligent router

Ambiguous requests should go through a lightweight classification system.

Jev is a candidate for this layer.

Do not make the architecture dependent on Jev.

Create an interface similar to:

```python
class Router:
    def route(
        self,
        request: UserRequest,
        context: UserContext
    ) -> RouteDecision:
        ...
```

Return:

```json
{
  "capability": "DIAGNOSE",
  "reasoning_tier": "DEEP",
  "confidence": 0.88,
  "tools": [
    "semantic_layer"
  ]
}
```

This allows Jev, another classifier, rules, or a conventional LLM to be swapped in later.

---

## 9. Routing Confidence

Routing confidence should matter.

Example policy:

```text
confidence >= 0.85
    execute route

0.60–0.85
    upgrade to stronger router/model

<0.60
    allow primary reasoning model to resolve intent
```

Do not repeatedly ask employees clarifying questions if the system can safely infer the likely intent.

---

## 10. Model Gateway

Create a model abstraction layer.

The orchestration code should request capabilities rather than specific models.

Example:

```python
model_gateway.generate(
    tier="fast",
    task=task,
    context=context
)
```

Configuration:

```yaml
fast:
  primary: MODEL_A
  fallback: MODEL_B

standard:
  primary: MODEL_C
  fallback: MODEL_D

deep:
  primary: MODEL_E
  fallback: MODEL_F
```

This allows the organization to change providers/models without changing agent logic.

Model selection should eventually consider:

- task complexity
- reasoning requirement
- latency
- model availability
- cost
- context-window requirement
- tool-use ability
- enterprise approval status
- data residency requirements

---

## 11. Semantic Layer Tool

Create a strict tool contract around the existing semantic layer.

The agent should produce a semantic query representation rather than arbitrary warehouse SQL whenever possible.

Example:

```json
{
  "metric": "sales",
  "dimensions": ["department"],
  "time_range": {
    "type": "yesterday"
  },
  "comparisons": [
    "last_year"
  ],
  "filters": {
    "store": "CURRENT_USER_STORE"
  }
}
```

The semantic service returns structured data:

```json
{
  "sales": 184291,
  "sales_ly": 171303,
  "yoy_pct": 7.58
}
```

The LLM then explains the result.

The model must not recalculate trusted metrics unless necessary.

---

## 12. Diagnostic Analytics

A key differentiator should be the ability to investigate rather than merely retrieve numbers.

Example:

> Why were sales down yesterday?

Provide the reasoning agent with analytical tools such as:

```text
get_sales
get_sales_by_department
get_sales_by_channel
get_sales_by_hour
get_transactions
get_aov
get_units
get_availability
get_comparison_period
```

The agent should dynamically determine which queries are necessary.

Example tool sequence:

```text
Overall sales
     ↓
Department contribution
     ↓
Transactions vs AOV
     ↓
Largest negative contributors
     ↓
Compare with historical variance
     ↓
Generate explanation
```

Do not expose chain-of-thought.

Persist tool calls and structured evidence for auditability.

---

## 13. Evidence-Based Answers

Every analytical answer should be grounded in retrieved data.

Internally maintain something similar to:

```json
{
  "claims": [
    {
      "text": "Bedroom accounted for approximately 42% of the decline.",
      "evidence": {
        "metric": "sales_delta",
        "department": "Bedroom",
        "value": -31842
      }
    }
  ]
}
```

This provides a foundation for:

- validation
- traceability
- debugging
- evaluation

---

## 14. Numerical Validation

Build deterministic validation between data results and the final response.

Example:

```text
Semantic result:
Sales = $184,291
YoY = +7.58%

Generated answer:
"Sales were $184K, up 7.6%."

PASS
```

If generated text says:

```text
"Sales increased 17.6%"
```

the validation layer should detect the discrepancy and regenerate or correct the answer.

Validation should include:

- numbers
- percentages
- directionality
- date ranges
- store scope
- comparison period

Do not use another expensive LLM for checks that ordinary code can perform.

---

## 15. Conversation Context

Support follow-ups.

Example:

```text
Employee:
How were sales yesterday?

Agent:
$184K, +7.6% vs LY.

Employee:
Which departments drove that?
```

The second message should inherit:

```text
store
date = yesterday
comparison = LY
metric = sales
```

Conversation memory should be scoped and explicit.

Avoid passing the entire conversation to every tool unnecessarily.

---

## 16. Scheduled Automations

Automations are a first-class feature.

Example:

> Every morning at 8 give me a summary of yesterday's sales.

The system should translate this into a structured automation definition.

Example:

```json
{
  "owner": "USER_ID",
  "type": "scheduled",
  "schedule": "0 8 * * *",
  "timezone": "America/New_York",
  "task": {
    "type": "DAILY_SALES_BRIEFING",
    "scope": {
      "store": "USER_STORE"
    }
  },
  "delivery": {
    "channel": "teams_dm"
  }
}
```

Use a proper scheduler/job system.

Do not ask the LLM to manage timing.

---

## 17. Automation Execution

Automations should invoke the same agent/tool infrastructure as interactive requests.

```text
Scheduler
   │
   ▼
Automation definition
   │
   ▼
Agent Runtime
   │
   ▼
Semantic Layer
   │
   ▼
Analysis
   │
   ▼
Validation
   │
   ▼
Teams proactive message
```

Avoid building a separate reporting stack.

---

## 18. Morning Briefing

Create a predefined briefing capability for the MVP.

Example output:

```text
Good morning — Yesterday's Performance

Sales
$184.3K
▲ 7.6% vs LY

Transactions
2,146
▲ 2.1%

AOV
$85.88
▲ 5.4%

Top contributors
Bedroom       +$8.4K
Living Room   +$6.1K
Kitchens      +$3.8K

Needs attention
Storage       -9.3%
```

Adaptive Card actions:

```text
Explain Storage decline
Show departments
Compare last 7 days
Show hourly trend
```

Clicking an action should initiate a normal agent interaction with appropriate context.

---

## 19. Conditional Automations

Design for these now, although they can come after scheduled automations.

Examples:

> Tell me if sales are down more than 10% vs LY.

> Let me know if Kitchen availability falls below 90%.

> Alert me if today's sales are materially below forecast.

Represent conditions structurally:

```json
{
  "metric": "sales_yoy_pct",
  "operator": "<",
  "threshold": -10,
  "evaluation_frequency": "hourly"
}
```

Do not let the model repeatedly reinterpret the condition.

---

## 20. Automation Management

Employees should eventually be able to ask:

> What briefings do I have?

> Stop my morning report.

> Move my report to 7:30.

> Only send it Monday through Friday.

Automation definitions therefore need:

```text
automation_id
owner
status
created_at
schedule
timezone
task definition
delivery target
last_run
last_status
```

---

## 21. Teams Delivery

Use Microsoft's current supported agent/bot architecture rather than legacy Bot Framework assumptions.

Implementation should support:

- authenticated Teams conversations
- proactive messages
- Adaptive Cards
- user-specific delivery
- conversation references/IDs required for proactive messaging
- retries
- failure logging

Keep Teams integration separate from the agent core.

Conceptually:

```python
AgentResponse -> ChannelAdapter -> Teams
```

so another UI could eventually be added.

---

## 22. Document Retrieval

Design an optional tool interface for SOPs, policies, product information, or operational documents.

This does not need to be part of the first sprint unless already available.

Interface:

```python
search_documents(query, user_scope)
```

Return:

```text
document
section
text
source
confidence
```

The agent must distinguish between:

```text
DATA ANSWER
DOCUMENT ANSWER
MODEL GENERAL KNOWLEDGE
```

Prefer enterprise sources over model memory for company-specific questions.

---

## 23. Forecasting

Do not ask an LLM to generate numerical forecasts.

Expose approved forecasting services as tools.

Example:

```python
get_forecast(
    metric="sales",
    store=user.store,
    horizon=7
)
```

The LLM may explain the forecast but should not create it.

---

## 24. Actions

Treat operational actions separately from analytical questions.

Examples might eventually include:

```text
create ticket
notify manager
submit request
update workflow
```

Every action must have:

- explicit authorization
- deterministic permission checks
- confirmation where appropriate
- audit logging
- idempotency protection

Do not give unrestricted write access to an autonomous reasoning model.

---

## 25. Observability

Instrument every request.

Capture at minimum:

```text
request_id
user
store
timestamp
intent/capability
router decision
router confidence
selected model tier
actual model
tools called
tool latency
LLM latency
token usage
estimated cost
validation result
final status
user feedback
```

Do not log sensitive content unnecessarily.

---

## 26. Router Evaluation

Create a labeled routing test set.

Example:

```json
{
  "query": "How were sales yesterday?",
  "expected_capability": "LOOKUP",
  "maximum_reasoning_tier": "FAST"
}
```

Include at least:

```text
simple metric lookups
comparisons
ambiguous questions
diagnostics
forecast requests
document questions
automation creation
automation modification
unauthorized requests
multi-turn follow-ups
```

Metrics:

```text
capability accuracy
reasoning-tier accuracy
unnecessary deep-model rate
incorrect fast-model rate
routing latency
cost per request
```

---

## 27. Answer Quality Evaluation

Build an evaluation suite rather than relying only on employee feedback.

Measure:

```text
numerical accuracy
semantic-query accuracy
store-scope accuracy
date interpretation
comparison-period accuracy
tool selection
unsupported-claim rate
response usefulness
latency
```

Create a golden dataset of realistic store employee questions and expected tool outputs.

---

## 28. Feedback

Teams responses should eventually include lightweight feedback:

```text
👍
👎
```

Optionally on negative feedback:

```text
Wrong number
Didn't understand me
Too slow
Not useful
Other
```

Associate feedback with:

```text
route
model
tool calls
response
```

This becomes training/evaluation data for improving routing.

---

## 29. Security Principles

Follow these rules:

1. Authorization happens before model/tool execution.
2. User identity comes from Entra, not conversation text.
3. The model cannot expand user permissions.
4. Semantic-layer security remains authoritative.
5. Sensitive data must only reach approved models.
6. Tool inputs and outputs should be auditable.
7. Prompt injection from documents/data must be treated as untrusted input.
8. Operational actions require stricter policies than analytical reads.

---

## 30. Reliability

Implement:

```text
timeouts
retries
fallback models
tool error handling
semantic-layer failure handling
scheduler retry policy
Teams delivery retries
circuit breakers where appropriate
```

Graceful degradation example:

```text
Deep model unavailable
      ↓
fallback deep model
      ↓
standard model
      ↓
return verified raw metrics if reasoning unavailable
```

Never invent numbers because a tool failed.

---

## 31. Proposed Service Boundaries

A reasonable initial repository/service structure:

```text
agent/
    runtime/
    router/
    models/
    prompts/
    context/

tools/
    semantic/
    documents/
    forecasting/

automations/
    scheduler/
    definitions/
    executor/

channels/
    teams/
    adaptive_cards/

security/
    auth/
    authorization/

validation/
    numeric/
    claims/

observability/
    tracing/
    metrics/

evals/
    router/
    answers/
    datasets/

config/
    models.yaml
    routing.yaml
    capabilities.yaml
```

Exact implementation language/framework is open, but Python is preferred unless Microsoft Teams integration makes another boundary advantageous.

---

## 32. Suggested Request Object

```python
class AgentRequest:
    request_id: str
    user_id: str
    conversation_id: str
    message: str
    channel: str
    timestamp: datetime
    context: dict
```

User context:

```python
class UserContext:
    user_id: str
    store_id: str
    role: str
    market_id: str | None
    timezone: str
    permissions: list[str]
```

Router response:

```python
class RouteDecision:
    capability: str
    reasoning_tier: str
    tools: list[str]
    confidence: float
```

---

## 33. MVP Scope

The first usable version should support:

### Interactive

```text
sales lookup
basic metric lookup
YoY/period comparison
department breakdown
basic diagnostic analysis
multi-turn follow-ups
```

### Automation

```text
create daily sales briefing
send proactive Teams DM
disable briefing
change briefing time
```

### Platform

```text
Entra identity
store-level authorization
dynamic router
model gateway
semantic-layer integration
numerical validation
logging/tracing
Teams integration
```

Do not expand the MVP into a generic autonomous employee assistant.

---

## 34. MVP Example Flow

Employee:

> Why were sales down yesterday?

Execution:

```text
Teams
  ↓
Entra context
  ↓
authorized store scope
  ↓
router
    capability = DIAGNOSE
    reasoning = STANDARD
  ↓
semantic tool
    total sales
  ↓
reasoning model sees decline
  ↓
semantic tool
    department breakdown
  ↓
semantic tool
    transactions + AOV
  ↓
reasoning model creates evidence-backed explanation
  ↓
numeric validator
  ↓
Teams Adaptive Card
```

---

## 35. Automation Example Flow

Employee:

> Every weekday at 8 AM send me yesterday's sales briefing.

Execution:

```text
Teams
  ↓
router = CREATE_AUTOMATION
  ↓
extract structured schedule
  ↓
validate schedule + scope
  ↓
save automation
  ↓
confirm
```

At 8 AM:

```text
scheduler
  ↓
load automation
  ↓
resolve user/store authorization
  ↓
run briefing capability
  ↓
semantic layer
  ↓
response generation
  ↓
validation
  ↓
Teams proactive message
```

---

## 36. Implementation Phases

### Phase 1 — Foundation

Build:

- request/response contracts
- Entra identity integration
- authorization layer
- semantic tool wrapper
- model gateway
- basic logging

### Phase 2 — Router

Build:

- capability taxonomy
- deterministic router
- intelligent router interface
- reasoning tier selection
- fallback behavior
- router evaluation dataset

Jev may be evaluated here.

### Phase 3 — Analytics Agent

Build:

- lookup
- comparison
- diagnostic tool loops
- conversation context
- numerical validation

### Phase 4 — Teams UX

Build:

- bot/agent interface
- Adaptive Cards
- proactive messaging
- conversation persistence
- feedback

### Phase 5 — Automations

Build:

- scheduler
- persisted automation definitions
- daily briefing
- create/update/delete flows
- timezone handling
- retry logic

### Phase 6 — Evaluation and Hardening

Build:

- golden question dataset
- router benchmarks
- answer-quality benchmarks
- latency/cost dashboard
- security testing
- prompt-injection testing
- load testing

### Phase 7 — Expansion

Potential additions:

- document retrieval
- conditional alerts
- forecasting
- richer diagnostics
- approved business actions
- additional channels

---

## 37. Initial Technical Spike

Before building the complete product, create a vertical slice supporting exactly these five interactions:

```text
1. "What were sales yesterday?"

2. "Compare that with last year."

3. "Which departments drove the difference?"

4. "Why was Bedroom down?"

5. "Send me this briefing every weekday at 8 AM."
```

This vertical slice should exercise:

```text
Teams
identity
semantic layer
conversation context
routing
multiple reasoning tiers
tool calling
validation
automation persistence
proactive Teams delivery
```

If these five work cleanly, the underlying architecture is likely sound.

---

## 38. Decisions to Keep Configurable

Do not hard-code:

```text
specific LLM provider
specific router model
Jev
specific scheduler technology
specific vector database
specific forecasting implementation
specific Teams presentation format
```

These should sit behind interfaces/configuration.

---

## 39. Success Criteria

The MVP is successful when a store employee can:

1. Open Teams.
2. Ask a plain-English performance question.
3. Receive a numerically correct answer from trusted enterprise data.
4. Ask natural follow-up questions without restating context.
5. Request a more complex explanation and have the system automatically use appropriate analytical tools/reasoning.
6. Ask for a recurring briefing conversationally.
7. Receive that briefing automatically in Teams.
8. Never access information outside their authorized scope.

Operationally, the platform should show:

- high numerical accuracy
- high routing accuracy
- low unnecessary use of expensive models
- traceable tool calls
- validated answers
- reliable automation delivery
- clear model/tool observability

---

## 40. Key Architectural Principle

The system should not be designed as:

```text
Employee → giant LLM → everything
```

It should be designed as:

```text
Employee
   ↓
Policy
   ↓
Routing
   ↓
Trusted capability/tool
   ↓
Appropriate reasoning model
   ↓
Validation
   ↓
Employee
```

The intelligence of the platform comes from **orchestration**, not simply from selecting the largest available model.

---

## 41. Immediate Next Task for Implementation Agent

Start by producing:

1. A concrete system architecture.
2. Component responsibilities and APIs.
3. Recommended Microsoft Teams integration approach.
4. Storage requirements.
5. Router design and schema.
6. Model gateway interface.
7. Semantic-layer tool interface.
8. Automation data model.
9. Request lifecycle/sequence diagrams.
10. Security/authorization flow.
11. Observability design.
12. Repository structure.
13. MVP backlog broken into implementation tickets.
14. A vertical-slice prototype plan for the five interactions defined above.

Prefer simple, replaceable components over premature framework complexity.

Do not introduce multi-agent orchestration unless there is a concrete requirement that cannot be handled by one orchestrator with tools.
