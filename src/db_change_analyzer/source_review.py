from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from .security import scan_secret
from .v5_adapter import _fact, _object_id, _safe_path
from .oracle.changes import ChangeSet


class SourceReviewInputError(ValueError):
    pass


_SCHEMA = Path(__file__).parent / "schemas" / "source-review-input.schema.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sanitize(value: Any, patterns: list[str]) -> Any:
    if isinstance(value, str):
        if scan_secret(value, patterns).blocked:
            return "[GİZLENDİ]"
        return value[:12000]
    if isinstance(value, list):
        return [_sanitize(item, patterns) for item in value[:200]]
    if isinstance(value, dict):
        return {str(key): _sanitize(item, patterns) for key, item in list(value.items())[:200]}
    return value


def _digestable_research_receipt(value: Any) -> Any:
    """Remove runtime-only timing from the unit identity while preserving it in the payload."""
    if isinstance(value, list):
        return [_digestable_research_receipt(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _digestable_research_receipt(item)
            for key, item in value.items()
            if key != "elapsed_ms"
        }
    return value


def _evidence_item(item: dict[str, Any], *, side: str, patterns: list[str]) -> dict[str, Any]:
    snippet = item.get("snippet", "")
    return {
        "evidence_id": item["evidence_id"],
        "kind": "source",
        "side": side,
        "revision": item["revision"],
        "path_display": _safe_path(item["path_display"], patterns),
        "path_b64": item["path_b64"],
        "blob_oid": item["blob_oid"],
        "start_line": item["start_line"],
        "end_line": item["end_line"],
        "fragment_sha256": item["fragment_sha256"],
        "snippet": _sanitize(snippet, patterns),
        "redacted": bool(item.get("redacted")) or scan_secret(snippet, patterns).blocked,
    }


def build_source_review_input(
    *, report_id: str, unit_id: str, changes: ChangeSet,
    old_evidence: Iterable[dict[str, Any]], new_evidence: Iterable[dict[str, Any]],
    research_receipts: Iterable[dict[str, Any] | Any], base_sha: str | None,
    target_sha: str, extra_secret_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """Create the bounded, evidence-bound payload for the source-review model."""
    patterns = extra_secret_patterns or []
    object_id = _object_id(changes.object_key)
    evidence_registry = [
        _evidence_item(item, side=side, patterns=patterns)
        for side, items in (("base", old_evidence), ("target", new_evidence))
        for item in items
    ]
    receipts: list[dict[str, Any]] = []
    for receipt in research_receipts:
        value = receipt.as_dict() if hasattr(receipt, "as_dict") else receipt
        if not isinstance(value, dict):
            raise SourceReviewInputError("RESEARCH_RECEIPT_INVALID")
        receipts.append(_sanitize(value, patterns))
    payload: dict[str, Any] = {
        "schema_version": "source-review-input/1.0",
        "synthetic": False,
        "report_id": report_id,
        "unit_id": unit_id,
        "object_id": object_id,
        "source_pair": {"base_sha": base_sha, "target_sha": target_sha},
        "object": {"object_key": changes.object_key, "object_type": changes.object_type, "operation": changes.operation},
        "facts": [_fact(change, patterns)[0] for change in changes.facts],
        "evidence_registry": evidence_registry[:128],
        "research_receipts": receipts[:32],
        "allowed_claim_kinds": ["change", "impact", "limitation"],
        "allowed_finding_kinds": ["impact", "issue", "improvement"],
        "allowed_categories": ["correctness", "error_handling", "transaction", "performance", "security", "maintainability"],
        "execution": {"author_execution_id": "application-assigned-after-acceptance"},
    }
    digest_payload = dict(payload)
    digest_payload["research_receipts"] = _digestable_research_receipt(payload["research_receipts"])
    payload["input_digest"] = hashlib.sha256(_canonical(digest_payload)).hexdigest()
    errors = sorted(
        Draft202012Validator(json.loads(_SCHEMA.read_text(encoding="utf-8"))).iter_errors(payload),
        key=lambda error: list(map(str, error.path)),
    )
    if errors:
        raise SourceReviewInputError("SOURCE_REVIEW_INPUT_INVALID")
    return payload


def source_review_evidence_ids(payload: dict[str, Any]) -> set[str]:
    ids = {item["evidence_id"] for item in payload.get("evidence_registry", []) if isinstance(item, dict) and "evidence_id" in item}
    for fact in payload.get("facts", []):
        for side in ("before", "after"):
            value = fact.get(side, {})
            if isinstance(value, dict):
                ids.update(value.get("evidence_ids", []))
    for receipt in payload.get("research_receipts", []):
        ids.update(receipt.get("evidence_ids", []))
        for item in receipt.get("items", []):
            if isinstance(item, dict) and item.get("evidence_id"):
                ids.add(item["evidence_id"])
    return ids


def source_review_comments(response: Any, *, execution_id: str, unit_id: str, input_digest: str) -> list[dict[str, Any]]:
    """Project accepted structured review items into the existing bounded view comment shape."""
    bound = response if isinstance(response, dict) else response.model_dump(mode="json")
    comments: list[dict[str, Any]] = []
    for item in bound.get("explanations", []):
        comments.append({
            "text_tr": item["text_tr"], "kind": "summary" if item["kind"] == "change" else "interpretation" if item["kind"] == "impact" else "uncertainty",
            "evidence_ids": item["evidence_ids"], "origin": "source_review", "gate": "accepted",
            "fact_ids": [], "unit_id": unit_id, "input_digest": input_digest,
            "acceptance_method": "source_review_contract", "author_execution_id": execution_id,
        })
    for item in bound.get("findings", []):
        checks = " ".join(f"Kontrol: {step}" for step in item.get("verification_steps", [])[:2])
        text = f"[{item['severity'].upper()}] {item['title_tr']}: {item['detail_tr']} {checks}".strip()
        comments.append({
            "text_tr": text[:360], "kind": "recommended_check" if item["kind"] == "improvement" else "interpretation",
            "evidence_ids": item["evidence_ids"], "origin": "source_review", "gate": "accepted",
            "fact_ids": [], "unit_id": unit_id, "input_digest": input_digest,
            "acceptance_method": "source_review_contract", "author_execution_id": execution_id,
        })
    return comments[:6]
