from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .identities import stable_id


class AggregateReviewError(ValueError):
    """Raised when the bounded aggregate-review contract is not satisfied."""


_SCHEMAS = Path(__file__).parent / "schemas"
_UNSAFE_TEXT = re.compile(
    r"https?://|<[^>]*>|```|\{\{|\}\}|\{%|%}|\$[A-Z][A-Z0-9_]*",
    re.IGNORECASE,
)
_SQL_TEXT = re.compile(r"\b(?:select|insert|update|delete|alter|drop|create)\s+", re.IGNORECASE)
_UNSUPPORTED_LIVE_CLAIM = re.compile(
    r"(?:canlı\s+veritabanı|runtime|production|deployment).*(?:kullanıyor|etkiliyor|etkiler|doğrulandı|kesin)",
    re.IGNORECASE,
)


def _schema(name: str) -> dict[str, Any]:
    return json.loads((_SCHEMAS / name).read_text(encoding="utf-8"))


def _validate_schema(value: dict[str, Any], name: str, code: str) -> None:
    errors = sorted(
        Draft202012Validator(_schema(name), format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: list(map(str, error.path)),
    )
    if errors:
        raise AggregateReviewError(code)


def _reject_unsafe_text(value: Any) -> None:
    if isinstance(value, str):
        if _UNSAFE_TEXT.search(value) or _SQL_TEXT.search(value) or _UNSUPPORTED_LIVE_CLAIM.search(value):
            raise AggregateReviewError("AGGREGATE_REVIEW_UNSAFE_TEXT")
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_unsafe_text(item)
    elif isinstance(value, list):
        for item in value:
            _reject_unsafe_text(item)


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _counts(items: list[dict[str, Any]], field: str, keys: tuple[str, ...]) -> dict[str, int]:
    values = Counter(str(item.get(field, "unknown")) for item in items)
    return {key: values.get(key, 0) for key in keys} | {
        key: values[key] for key in sorted(values) if key not in keys
    }


def _object_summary(item: dict[str, Any], *, evidence_id: str) -> dict[str, Any]:
    result = {
        "evidence_ids": [evidence_id],
        "object_type": str(item.get("object_type") or "UNKNOWN"),
        "object_name": str(item.get("object_name") or "[adı doğrulanamadı]"),
        "operation": str(item.get("operation") or "unknown"),
        "risk_level": str(item.get("risk_level") or "unknown"),
        "priority": int(item.get("priority") or 999),
        "deterministic_summary": str(item.get("deterministic_summary") or "Deterministik fark kaydedildi."),
        "categories": [str(value) for value in (item.get("categories") or [])[:8]],
    }
    _reject_unsafe_text(result)
    return result


def build_aggregate_review_input(*, report_id: str, unit_id: str, threshold: int,
                                 object_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact, source-free aggregate request for large change sets."""
    if threshold < 1 or len(object_summaries) <= threshold:
        raise AggregateReviewError("AGGREGATE_REVIEW_THRESHOLD_NOT_EXCEEDED")
    ordered = sorted(
        object_summaries,
        key=lambda item: (int(item.get("priority") or 999), str(item.get("object_name") or "")),
    )
    object_evidence: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for index, item in enumerate(ordered):
        evidence_id = stable_id("aggregate-evidence", {"unit_id": unit_id, "index": index, "item": item})[:100]
        summary = _object_summary(item, evidence_id=evidence_id)
        summaries.append(summary)
        object_evidence.append({
            "evidence_id": evidence_id,
            "kind": "deterministic_summary",
            "detail_tr": summary["deterministic_summary"],
        })
    evidence_registry = [
        {
            "evidence_id": stable_id("aggregate-evidence", {"unit_id": unit_id, "kind": "counts"})[:100],
            "kind": "aggregate_counts",
            "detail_tr": "Deterministik nesne, işlem ve risk dağılımları.",
        },
        *object_evidence,
    ]
    payload_without_digest = {
        "schema_version": "aggregate-review-input/1.0",
        "synthetic": False,
        "report_id": report_id,
        "unit_id": unit_id,
        "threshold": threshold,
        "summary": {
            "object_count": len(summaries),
            "object_type_counts": _counts(summaries, "object_type", ()),
            "operation_counts": _counts(summaries, "operation", ("added", "modified", "removed")),
            "risk_counts": _counts(summaries, "risk_level", ("critical", "high", "medium", "low", "unknown")),
            "priority_objects": summaries[:10],
            "deterministic_summaries": summaries,
        },
        "evidence_registry": evidence_registry,
    }
    _reject_unsafe_text(payload_without_digest)
    payload = {**payload_without_digest, "input_digest": _digest(payload_without_digest)}
    _validate_schema(payload, "aggregate-review-input.schema.json", "AGGREGATE_INPUT_INVALID")
    return payload


def accept_aggregate_review(content: str, input_payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and bind one aggregate model response to its request."""
    try:
        value = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AggregateReviewError("AGGREGATE_REVIEW_INVALID") from exc
    if not isinstance(value, dict):
        raise AggregateReviewError("AGGREGATE_REVIEW_INVALID")
    _validate_schema(value, "aggregate-review.schema.json", "AGGREGATE_REVIEW_INVALID")
    if value["unit_id"] != input_payload["unit_id"] or value["input_digest"] != input_payload["input_digest"]:
        raise AggregateReviewError("AGGREGATE_REVIEW_BINDING_MISMATCH")
    known_evidence = {item["evidence_id"] for item in input_payload["evidence_registry"]}
    if not set(value["evidence_ids"]).issubset(known_evidence):
        raise AggregateReviewError("AGGREGATE_REVIEW_UNKNOWN_EVIDENCE")
    _reject_unsafe_text(value)
    return value
