"""Model gateway: callers ask for a tier; config decides which models back it.

Fallback order for a tier: primary -> fallback -> (degrade_to tier's chain). A simple
per-model circuit breaker skips models that keep failing.
"""

import time

from store_agent.config import ModelsConfig
from store_agent.contracts import Tier
from store_agent.models.types import AllModelsUnavailable, ModelProvider, ModelResult, ModelTask, ModelUnavailable
from store_agent.observability.tracing import ModelCallRecord, Trace


class CircuitBreaker:
    def __init__(self, failure_threshold: int, cooldown_s: float, now=time.monotonic):
        self.threshold, self.cooldown, self.now = failure_threshold, cooldown_s, now
        self.failures: dict[str, int] = {}
        self.open_until: dict[str, float] = {}

    def allows(self, model: str) -> bool:
        return self.now() >= self.open_until.get(model, 0.0)

    def record(self, model: str, ok: bool) -> None:
        if ok:
            self.failures.pop(model, None)
            return
        self.failures[model] = self.failures.get(model, 0) + 1
        if self.failures[model] >= self.threshold:
            self.open_until[model] = self.now() + self.cooldown
            self.failures[model] = 0


class ModelGateway:
    def __init__(self, config: ModelsConfig, providers: dict[str, ModelProvider]):
        self.config = config
        self.providers = providers
        self.breaker = CircuitBreaker(config.circuit_breaker.failure_threshold, config.circuit_breaker.cooldown_s)

    def chain(self, tier: Tier) -> list[tuple[Tier, str]]:
        out: list[tuple[Tier, str]] = []
        seen: set[Tier] = set()
        current: Tier | None = tier
        while current and current not in seen:
            seen.add(current)
            cfg = self.config.tiers.get(current)
            if cfg is None:
                break
            out += [(current, m) for m in (cfg.primary, cfg.fallback) if m]
            current = cfg.degrade_to
        return out

    def generate(self, tier: Tier, task: ModelTask, trace: Trace | None = None) -> ModelResult:
        if tier == Tier.NONE:
            raise ValueError("Tier NONE must not call a model")
        errors = []
        for served_tier, model in self.chain(tier):
            if not self.breaker.allows(model):
                errors.append(f"{model}: circuit open")
                continue
            provider_name = model.split("/", 1)[0]
            provider = self.providers.get(provider_name)
            if provider is None:
                errors.append(f"{model}: no provider '{provider_name}'")
                continue
            t0 = time.perf_counter()
            try:
                resp = provider.complete(model, task, self.config.tiers[served_tier].timeout_s)
            except (ModelUnavailable, TimeoutError) as e:
                self.breaker.record(model, ok=False)
                errors.append(f"{model}: {e}")
                if trace:
                    trace.model_calls.append(
                        ModelCallRecord(purpose=task.kind, tier=served_tier, model=model, latency_ms=_ms(t0), status="error", error=str(e))
                    )
                continue
            self.breaker.record(model, ok=True)
            price = self.config.pricing.get(model)
            cost = (resp.input_tokens * price.input_per_mtok + resp.output_tokens * price.output_per_mtok) / 1e6 if price else 0.0
            result = ModelResult(**resp.model_dump(), model=model, tier=served_tier, latency_ms=_ms(t0), cost_usd=cost)
            if trace:
                trace.model_calls.append(
                    ModelCallRecord(
                        purpose=task.kind,
                        tier=served_tier,
                        model=model,
                        latency_ms=result.latency_ms,
                        status="ok",
                        input_tokens=resp.input_tokens,
                        output_tokens=resp.output_tokens,
                        cost_usd=cost,
                    )
                )
            return result
        raise AllModelsUnavailable("; ".join(errors) or f"no models configured for {tier}")


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)
