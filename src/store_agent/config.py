import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from store_agent.contracts import Capability, Tier

DEFAULT_CONFIG_DIR = Path(os.environ.get("STORE_AGENT_CONFIG", Path(__file__).resolve().parents[2] / "config"))


class TierModels(BaseModel):
    primary: str
    fallback: str | None = None
    degrade_to: Tier | None = None
    timeout_s: float = 30


class ModelPricing(BaseModel):
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = 3
    cooldown_s: float = 60


class ModelsConfig(BaseModel):
    tiers: dict[Tier, TierModels]
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)


class ConfidenceConfig(BaseModel):
    execute: float = 0.85
    escalate: float = 0.60


class RoutingConfig(BaseModel):
    confidence: ConfidenceConfig
    capability_tiers: dict[Capability, Tier]
    deep_triggers: list[str] = Field(default_factory=list)
    followup_ttl_minutes: int = 30
    diagnose_max_steps: int = 6
    diagnose_max_validation_retries: int = 1
    narration_max_validation_retries: int = 1


class RoleConfig(BaseModel):
    scope: Literal["home_store", "market"]
    capabilities: list[Capability]


class CapabilityConfig(BaseModel):
    enabled: bool = True
    tools: list[str] = Field(default_factory=list)
    unavailable_message: str | None = None


class CapabilitiesConfig(BaseModel):
    roles: dict[str, RoleConfig]
    capabilities: dict[Capability, CapabilityConfig]


class MetricDef(BaseModel):
    label: str
    unit: Literal["currency", "count", "percent"]
    synonyms: list[str] = Field(default_factory=list)


class ComparisonDef(BaseModel):
    label: str
    offset_days: int | None = None


class DepartmentDef(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)


class StoreDef(BaseModel):
    name: str
    market_id: str
    timezone: str


class SemanticCatalog(BaseModel):
    default_metric: str = "sales"
    data_lag_days: int = 1
    metrics: dict[str, MetricDef]
    comparisons: dict[str, ComparisonDef]
    departments: list[DepartmentDef]
    stores: dict[str, StoreDef]

    @property
    def department_names(self) -> list[str]:
        return [d.name for d in self.departments]


class DevUser(BaseModel):
    display_name: str
    role: str
    store_id: str
    market_id: str | None = None
    timezone: str | None = None


class Settings(BaseModel):
    models: ModelsConfig
    routing: RoutingConfig
    capabilities: CapabilitiesConfig
    catalog: SemanticCatalog
    dev_users: dict[str, DevUser]
    log_message_text: bool = False


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings(config_dir: Path | None = None) -> Settings:
    d = Path(config_dir or DEFAULT_CONFIG_DIR)
    routing = _load(d / "routing.yaml")
    return Settings(
        models=ModelsConfig(**_load(d / "models.yaml")),
        routing=RoutingConfig(
            confidence=routing["confidence"],
            capability_tiers=routing["capability_tiers"],
            deep_triggers=routing.get("deep_triggers", []),
            followup_ttl_minutes=routing.get("context", {}).get("followup_ttl_minutes", 30),
            diagnose_max_steps=routing.get("diagnose", {}).get("max_steps", 6),
            diagnose_max_validation_retries=routing.get("diagnose", {}).get("max_validation_retries", 1),
            narration_max_validation_retries=routing.get("narration", {}).get("max_validation_retries", 1),
        ),
        capabilities=CapabilitiesConfig(**_load(d / "capabilities.yaml")),
        catalog=SemanticCatalog(**_load(d / "semantic_catalog.yaml")),
        dev_users={k: DevUser(**v) for k, v in _load(d / "dev_users.yaml").get("users", {}).items()},
    )
