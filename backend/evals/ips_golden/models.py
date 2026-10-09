"""Typed contracts for IPS Golden cases, runs, and reports.

These models are intentionally independent from the product request schemas.
They describe evidence and expectations; they do not change agent behavior.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "structured_search",
    "site_information",
    "capabilities_comparisons",
    "semantic_stt",
    "multiturn",
    "jev_adaptation",
    "security_scope",
    "failure_recovery",
]


class CapacityRecord(BaseModel):
    """One installed-capacity row copied from the frozen IPS snapshot."""

    model_config = ConfigDict(extra="forbid")

    group: str
    description: str
    registered_quantity: int
    source_row_hash: str


class SiteRecord(BaseModel):
    """Snapshot-backed site evidence used by deterministic checks."""

    model_config = ConfigDict(extra="forbid")

    site_id: str
    snapshot_id: str
    site_code: str
    site_number: str
    site_name: str
    provider_code: str
    provider_name: str
    nit: str | None = None
    verification_digit: str | None = None
    nature: str | None = None
    care_level: str | None = None
    address: str | None = None
    email: str | None = None
    phone: str | None = None
    department: str
    municipality: str
    cutoff: str
    source: str
    capacities: list[CapacityRecord] = Field(default_factory=list)

    def card(self) -> dict[str, Any]:
        """Return the exact public search-card shape used by the IPS tools."""

        return {
            "site_code": self.site_code,
            "site_name": self.site_name,
            "municipality": self.municipality,
            "department": self.department,
            "phone": self.phone,
            "nature": self.nature,
            "level": self.care_level,
        }

    def detail(self) -> dict[str, Any]:
        """Return a serializable exact record for evidence comparisons."""

        return self.model_dump(mode="json")


class SnapshotMetadata(BaseModel):
    """Identity and provenance of one immutable PostgreSQL snapshot."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    snapshot_id: str
    source_hash: str
    status: str
    source_row_count: int
    site_count: int
    cutoff_values: list[str] = Field(default_factory=list)
    source_values: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    fetched_at: datetime | None = None
    activated_at: datetime | None = None


class ToolExpectation(BaseModel):
    """Expected tool call and exact arguments for one scripted turn."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool | None = True
    result_status: str | None = None


class ProviderStep(BaseModel):
    """Deterministic provider response used only by offline execution."""

    kind: Literal["tool_calls", "text", "error"]
    calls: list[ToolExpectation] = Field(default_factory=list)
    text: str = ""
    retryable: bool = True


class TurnPlan(BaseModel):
    """One user input plus all evidence needed to replay and evaluate it."""

    prompt: str
    tools: list[ToolExpectation] = Field(default_factory=list)
    provider_steps: list[ProviderStep] = Field(default_factory=list)
    relevant_site_codes: list[str] = Field(default_factory=list)
    exact_records: list[SiteRecord] = Field(default_factory=list)
    expected_status: str | None = None
    expected_null_fields: dict[str, list[str]] = Field(default_factory=dict)
    expected_snapshot_id: str | None = None
    expected_behavior: str | None = None
    jev_signals: dict[str, str] = Field(default_factory=dict)
    system_initiated: bool = False
    failure_mode: str | None = None
    critical: bool = False
    tags: list[str] = Field(default_factory=list)


class GoldenCase(BaseModel):
    """A case is isolated in one conversation, except JEV counterfactuals."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: Category
    title: str
    description: str
    turns: list[TurnPlan] = Field(min_length=1)
    snapshot_id: str
    source_hash: str
    seed: int
    critical: bool = False
    counterfactual: bool = False
    tags: list[str] = Field(default_factory=list)


class CheckResult(BaseModel):
    name: str
    passed: bool
    critical: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class SpanSummary(BaseModel):
    name: str
    duration_ms: int = 0
    attributes: dict[str, Any] = Field(default_factory=dict)


class TurnResult(BaseModel):
    turn_index: int
    prompt: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    response: str = ""
    events: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)
    behavior: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    spans: list[SpanSummary] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    fallbacks: list[str] = Field(default_factory=list)
    recovered: bool = False
    latency_ms: dict[str, Any] = Field(default_factory=dict)


class JudgeScore(BaseModel):
    """External judge output. It never carries deterministic pass/fail state."""

    model_config = ConfigDict(extra="forbid")

    clarity: int = Field(ge=0, le=4)
    patient_usefulness: int = Field(ge=0, le=4)
    context_continuity: int = Field(ge=0, le=4)
    behavior_adaptation: int = Field(ge=0, le=4)
    justification: str = Field(min_length=1, max_length=1200)
    provider: str
    model: str
    prompt_version: str


class CounterfactualResult(BaseModel):
    control: list[TurnResult] = Field(default_factory=list)
    treatment: list[TurnResult] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)


class CaseResult(BaseModel):
    case_id: str
    category: Category
    repetition: int = 1
    conversation_id: str | None = None
    snapshot_id: str
    source_hash: str
    passed: bool = False
    critical_failure: bool = False
    checks: list[CheckResult] = Field(default_factory=list)
    turns: list[TurnResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    fallbacks: list[str] = Field(default_factory=list)
    judge: JudgeScore | None = None
    counterfactual: CounterfactualResult | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunManifest(BaseModel):
    run_id: str
    created_at: datetime
    mode: Literal["offline", "live"]
    seed: int
    repetitions: int
    snapshot: SnapshotMetadata
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    agent_configuration: dict[str, Any] = Field(default_factory=dict)
    embedding_configuration: dict[str, Any] = Field(default_factory=dict)
    jev_configuration: dict[str, Any] = Field(default_factory=dict)
    cases: list[str] = Field(default_factory=list)
    case_count: int = 0
    limitations: list[str] = Field(default_factory=list)


__all__ = [
    "CapacityRecord",
    "CaseResult",
    "Category",
    "CheckResult",
    "CounterfactualResult",
    "GoldenCase",
    "JudgeScore",
    "ProviderStep",
    "RunManifest",
    "SiteRecord",
    "SnapshotMetadata",
    "SpanSummary",
    "ToolExpectation",
    "TurnPlan",
    "TurnResult",
]
