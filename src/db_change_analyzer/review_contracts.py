from __future__ import annotations

import json
import copy
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewContractError(ValueError):
    pass


_UNSAFE_TEXT = re.compile(
    r"https?://|<[^>]*>|```|\{\{|\}\}|\{%|%\}|\$[A-Z][A-Z0-9_]*",
    re.IGNORECASE,
)


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Basis = Literal["source_observation", "source_backed_inference", "hypothesis"]
Severity = Literal["info", "low", "medium", "high", "critical", "unknown"]
FindingKind = Literal["impact", "issue", "improvement"]


class ReviewExplanation(ReviewModel):
    explanation_id: str = Field(min_length=1, max_length=100)
    kind: Literal["change", "impact", "limitation"]
    text_tr: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=64)


class ReviewFinding(ReviewModel):
    finding_id: str = Field(min_length=1, max_length=120)
    kind: FindingKind
    category: Literal["correctness", "error_handling", "transaction", "performance", "security", "maintainability"]
    title_tr: str = Field(min_length=1, max_length=300)
    detail_tr: str = Field(min_length=1, max_length=2400)
    severity: Severity
    basis: Basis
    evidence_ids: list[str] = Field(min_length=1, max_length=64)
    verification_steps: list[str] = Field(max_length=12)
    execution: Literal["not_run"] = "not_run"


class SourceReviewResponse(ReviewModel):
    schema_version: Literal["source-review/1.0"]
    unit_id: str = Field(min_length=1, max_length=100)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    explanations: list[ReviewExplanation] = Field(max_length=20)
    findings: list[ReviewFinding] = Field(max_length=20)
    conclusion: Literal["no_finding", "findings", "insufficient_context", "not_applicable"]
    limitations: list[str] = Field(max_length=20)
    unfinished_research: list[str] = Field(max_length=20)


class ModelExecutionRecord(ReviewModel):
    execution_id: str = Field(min_length=1, max_length=120)
    run_id: str = Field(min_length=1, max_length=120)
    unit_id: str | None = Field(default=None, max_length=120)
    attempt: int = Field(ge=1)
    turn: int = Field(ge=1)
    generation: int = Field(ge=0)
    requested_route: str = Field(min_length=1, max_length=500)
    requested_model: str = Field(min_length=1, max_length=200)
    returned_model: str | None = Field(default=None, max_length=200)
    provider_request_id: str | None = Field(default=None, max_length=300)
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal["accepted", "failed", "refused", "truncated", "not_run"]
    policy_versions: dict[str, str] = Field(default_factory=dict)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_schema: str = Field(min_length=1, max_length=100)
    error_code: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def completed_after_started(self) -> "ModelExecutionRecord":
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.status == "accepted" and self.completed_at is None:
            raise ValueError("accepted execution requires completed_at")
        return self


def validate_source_review(content: str, *, unit_id: str, input_digest: str,
                           evidence_ids: set[str]) -> SourceReviewResponse:
    try:
        value = json.loads(content)
        response = SourceReviewResponse.model_validate(value, strict=True)
    except Exception as exc:
        raise ReviewContractError("SOURCE_REVIEW_INVALID") from exc
    if response.unit_id != unit_id or response.input_digest != input_digest:
        raise ReviewContractError("SOURCE_REVIEW_BINDING_MISMATCH")
    for item in response.explanations:
        if not set(item.evidence_ids) <= evidence_ids:
            raise ReviewContractError("SOURCE_REVIEW_UNKNOWN_EVIDENCE")
        if _UNSAFE_TEXT.search(item.text_tr):
            raise ReviewContractError("SOURCE_REVIEW_UNSAFE_TEXT")
    for item in response.findings:
        if not set(item.evidence_ids) <= evidence_ids:
            raise ReviewContractError("SOURCE_REVIEW_UNKNOWN_EVIDENCE")
        finding_text = " ".join((item.title_tr, item.detail_tr, *item.verification_steps))
        if _UNSAFE_TEXT.search(finding_text):
            raise ReviewContractError("SOURCE_REVIEW_UNSAFE_TEXT")
    return response


def bind_source_review(response: SourceReviewResponse, *, author_execution_id: str) -> dict[str, object]:
    """Add application-owned provenance after model output has been accepted."""
    if not author_execution_id or len(author_execution_id) > 120:
        raise ReviewContractError("SOURCE_REVIEW_EXECUTION_INVALID")
    value = response.model_dump(mode="json") if isinstance(response, SourceReviewResponse) else copy.deepcopy(response)
    for item in (*value["explanations"], *value["findings"]):
        item["author_execution_id"] = author_execution_id
    return value

