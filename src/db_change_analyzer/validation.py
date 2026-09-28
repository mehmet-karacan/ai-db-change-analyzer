from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .models import AnalysisUnit, UnitResponse
from .security import scan_secret


class ResponseValidationError(ValueError):
    def __init__(self, *codes: str) -> None:
        self.codes = tuple(sorted(set(codes))) or ("INVALID_RESPONSE",)
        super().__init__(",".join(self.codes))


@dataclass(frozen=True, slots=True)
class ValidatedResponse:
    response: UnitResponse
    canonical_json: bytes


def _reject_constant(value: str) -> None:
    raise ResponseValidationError("NONFINITE_NUMBER")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResponseValidationError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse_json_object(content: str, *, prompt_json: bool = False) -> dict[str, Any]:
    if prompt_json:
        stripped = content.strip()
        fence = re.fullmatch(r"```(?:json)?\s*\r?\n(?P<body>[\s\S]*?)\r?\n```", stripped, flags=re.IGNORECASE)
        if fence:
            content = fence.group("body")
    try:
        value = json.loads(content, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except ResponseValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResponseValidationError("INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise ResponseValidationError("JSON_NOT_OBJECT")
    return value


def validate_unit_response(
    content: str,
    unit: AnalysisUnit,
    *,
    prompt_json: bool = False,
    extra_secret_patterns: list[str] | None = None,
) -> ValidatedResponse:
    if scan_secret(content, extra_secret_patterns).blocked:
        raise ResponseValidationError("SECRET_IN_RESPONSE")
    value = parse_json_object(content, prompt_json=prompt_json)
    try:
        # JSON enum values are strings; strict Python validation incorrectly
        # requires pre-instantiated Enum objects that a model cannot emit.
        response = UnitResponse.model_validate_json(json.dumps(value, ensure_ascii=False), strict=True)
    except ValidationError as exc:
        # Do not include Pydantic's input values in diagnostics.
        codes = [f"SCHEMA_{item['type'].upper()}" for item in exc.errors(include_input=False, include_url=False)]
        raise ResponseValidationError(*codes) from None
    codes: list[str] = []
    if response.unit_id != unit.unit_id:
        codes.append("UNIT_ID_MISMATCH")
    registry = {str(item.get("evidence_id")) for item in unit.evidence_registry if item.get("evidence_id")}
    allowed = {item.value for item in unit.allowed_claim_kinds}
    for evidence_id in response.summary_evidence_ids:
        if evidence_id not in registry:
            codes.append("UNKNOWN_EVIDENCE_ID")
    for finding in response.findings:
        if finding.kind.value not in allowed:
            codes.append("DISALLOWED_FINDING_KIND")
        if any(evidence_id not in registry for evidence_id in finding.evidence_ids):
            codes.append("UNKNOWN_EVIDENCE_ID")
    for limitation in response.limitations:
        if any(evidence_id not in registry for evidence_id in limitation.evidence_ids):
            codes.append("UNKNOWN_EVIDENCE_ID")
    if codes:
        raise ResponseValidationError(*codes)
    canonical = json.dumps(response.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return ValidatedResponse(response, canonical)
