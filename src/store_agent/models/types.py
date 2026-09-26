from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class ModelTask(BaseModel):
    """What the orchestrator asks of a model. `kind` names the job (classify_intent,
    narrate, agent, general) so providers and evals can tell tasks apart."""

    kind: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    response_format: Literal["text", "json"] = "text"


class ProviderResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class ModelResult(ProviderResponse):
    model: str
    tier: str
    latency_ms: float
    cost_usd: float = 0.0


class ModelUnavailable(Exception):
    pass


class AllModelsUnavailable(Exception):
    pass


class ModelProvider(Protocol):
    def complete(self, model: str, task: ModelTask, timeout_s: float) -> ProviderResponse:
        """Raise ModelUnavailable (or TimeoutError) on outage so the gateway can fall back."""
        ...
