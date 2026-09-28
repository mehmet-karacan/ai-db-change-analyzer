from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


StrictStr100 = Annotated[str, Field(min_length=1, max_length=100)]
EvidenceIds = Annotated[list[StrictStr100], Field(min_length=1, max_length=64)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class ExitCode(IntEnum):
    OK = 0
    LIMITED = 10
    RETRY_PENDING = 11
    CONFIG = 20
    GIT = 21
    HISTORY = 22
    STATE = 23
    AI_TRANSPORT = 30
    AI_RESPONSE = 31
    NOTIFICATION = 40
    NOTIFICATION_UNKNOWN = 41
    SAFETY = 50
    INTERNAL = 90


class Quality(StrEnum):
    COMPLETE = "complete"
    LIMITED = "limited"
    BLOCKED = "blocked"


class Risk(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingKind(StrEnum):
    INTERPRETATION = "interpretation"
    POSSIBLE_IMPACT = "possible_impact"
    RECOMMENDED_CHECK = "recommended_check"
    UNCERTAINTY = "uncertainty"


class EvidenceLevel(StrEnum):
    SOURCE_BACKED_INFERENCE = "source_backed_inference"
    HYPOTHESIS = "hypothesis"


class Finding(StrictModel):
    kind: FindingKind
    claim: Annotated[str, Field(min_length=1, max_length=1400)]
    risk: Risk
    evidence_level: EvidenceLevel
    evidence_ids: EvidenceIds


class Limitation(StrictModel):
    code: Annotated[str, Field(min_length=1, max_length=80)]
    detail: Annotated[str, Field(min_length=1, max_length=1200)]
    evidence_ids: EvidenceIds


class UnitResponse(StrictModel):
    schema_version: Literal["1.0"]
    unit_id: StrictStr100
    summary_tr: Annotated[str, Field(min_length=1, max_length=1500)]
    summary_evidence_ids: EvidenceIds
    findings: Annotated[list[Finding], Field(max_length=20)]
    limitations: Annotated[list[Limitation], Field(max_length=20)]


class SourcePair(StrictModel):
    old_revision: str | None
    new_revision: str | None


class SourceEvidence(StrictModel):
    kind: Literal["source"] = "source"
    evidence_id: StrictStr100
    revision: str
    path_display: str
    path_b64: str
    blob_oid: str
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    start_byte: Annotated[int, Field(ge=0)]
    end_byte_exclusive: Annotated[int, Field(ge=0)]
    fragment_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    snippet: str
    redacted: bool = False

    @model_validator(mode="after")
    def ordered_ranges(self) -> "SourceEvidence":
        if self.end_line < self.start_line or self.end_byte_exclusive < self.start_byte:
            raise ValueError("source range is reversed")
        return self


class ContextLimitEvidence(StrictModel):
    kind: Literal["context_limit"] = "context_limit"
    evidence_id: StrictStr100
    code: str
    detail: str
    related_evidence_ids: list[str]


class ArtifactMetadataEvidence(StrictModel):
    kind: Literal["artifact_metadata"] = "artifact_metadata"
    evidence_id: StrictStr100
    revision: str
    path_display: str
    path_b64: str
    mode: str
    blob_oid: str | None
    size: int | None


Evidence = SourceEvidence | ContextLimitEvidence | ArtifactMetadataEvidence


class DeterministicFact(StrictModel):
    fact_id: str
    property: str
    before: str | None
    after: str | None
    category: str
    evidence_ids: list[str]
    source_pair: SourcePair
    event_ids: list[str]
    view_tags: list[str]


class ResultRecord(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: str
    run_id: str | None
    report_id: str | None
    report_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")]
    mode: str
    outcome: str
    exit_code: int
    checkpoint_before: str | None
    checkpoint_after: str | None
    notification_status: str
    quality: Quality | None
    ai_http_attempts: Annotated[int, Field(ge=0)] = 0
    smtp_attempts: Annotated[int, Field(ge=0)] = 0
    smtp_accepted_transactions: Annotated[int, Field(ge=0)] = 0
    originating_build: dict[str, Any] | None
    current_build: dict[str, Any] | None
    emitted_files: list[str]
    error_code: str | None


class DeliveryRecipient(StrictModel):
    address: str
    status: Literal["PENDING", "ACCEPTED", "REFUSED", "UNKNOWN"]
    smtp_code: int | None = None
    accepted_at: datetime | None = None


class DeliveryAttempt(StrictModel):
    attempt_id: str
    state: Literal["INFLIGHT", "ACCEPTED", "FAILED", "UNKNOWN"]
    intended_recipients: list[str]
    started_at: datetime
    completed_at: datetime | None = None
    sanitized_error: str | None = None


class DeliveryRecord(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    notification_id: str
    report_id: str
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    generation: Annotated[int, Field(ge=0)]
    message_id: str
    status: str
    recipients: list[DeliveryRecipient]
    attempts: list[DeliveryAttempt]


class ObjectIdentity(StrictModel):
    object_key: str
    namespace: str
    schema_name: str | None
    name: str
    object_type: str
    raw_schema: str | None
    raw_name: str
    schema_quoted: bool
    name_quoted: bool
    identity_confidence: Literal["known", "uncertain", "unknown"]
    parent_key: str | None = None
    routine_signature: str | None = None


class AnalysisUnit(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    unit_id: str
    object_identity: ObjectIdentity | None
    artifact_paths: list[str]
    related_object_keys: list[str]
    view_tags: list[str]
    source_pair: SourcePair
    deterministic_facts: list[DeterministicFact]
    evidence_registry: list[dict[str, Any]]
    dependency_edges: list[dict[str, Any]]
    coverage_manifest: dict[str, Any]
    allowed_claim_kinds: list[FindingKind]
