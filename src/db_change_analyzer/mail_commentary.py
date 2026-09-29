"""Bounded, evidence-bound acceptance for V5 model commentary."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .security import scan_secret
from .validation import ResponseValidationError, parse_json_object
from .v5_adapter import _fact, _object_id, _safe_path
from .oracle.changes import ChangeSet


_SCHEMAS = Path(__file__).parent / "schemas" / "v5"
_FORBIDDEN = re.compile(r"https?://|www\.|<[^>]*>|```|\b(?:sudo|curl|wget|rm\s+-|drop\s+table)\b", re.IGNORECASE)
_PII = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b\d{11}\b")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _validate_schema(value: dict[str, Any], filename: str) -> None:
    schema = json.loads((_SCHEMAS / filename).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda error: list(map(str, error.path)))
    if errors:
        raise ResponseValidationError("MAIL_SCHEMA_INVALID")


def build_unit_input(view: dict[str, Any], obj: dict[str, Any], unit_id: str) -> dict[str, Any]:
    """Only verified, display-safe facts and metadata enter the model context."""
    facts = [fact for fact in obj["facts"] if fact["verification"] == "verified"][:200]
    ids = {evidence_id for fact in facts for side in ("before", "after") for evidence_id in fact[side]["evidence_ids"]}
    registry = [item for item in view["evidence_registry"] if item["evidence_id"] in ids and item["object_id"] == obj["object_id"]]
    source = view["source"]
    result = {
        "schema_version": "mail-unit-input/1.0", "synthetic": view["synthetic"],
        "report_id": view["report_id"], "unit_id": unit_id, "object_id": obj["object_id"],
        "source_pair": {"base_sha": source["base_sha"], "target_sha": source["target_sha"],
                        "base_label": source["base_label"], "target_label": source["target_label"]},
        "facts": facts, "evidence_registry": registry,
        "allowed_claim_kinds": ["change_restatement"],
        "allowed_check_codes": [], "context_limit_codes": [],
    }
    result["input_digest"] = hashlib.sha256(_canonical(result)).hexdigest()
    _validate_schema(result, "ai-unit-input.schema.json")
    return result


def build_source_unit_input(
    *, report_id: str, unit_id: str, changes: ChangeSet,
    old_evidence: list[dict[str, Any]], new_evidence: list[dict[str, Any]],
    base_sha: str | None, target_sha: str, extra_secret_patterns: list[str],
) -> dict[str, Any]:
    """Prepare a model input without raw SQL snippets or legacy AI fields."""
    object_id = _object_id(changes.object_key)
    registry = []
    for side, items in (("base", old_evidence), ("target", new_evidence)):
        for evidence in items:
            registry.append({
                "evidence_id": evidence["evidence_id"], "kind": "source", "side": side,
                "path": _safe_path(evidence["path_display"], extra_secret_patterns), "start_line": evidence["start_line"],
                "end_line": evidence["end_line"], "sha256": evidence["fragment_sha256"],
                "detail_tr": "Git snapshot içindeki Oracle tanım aralığı.",
                "synthetic": False, "object_id": object_id,
                "scope_complete": None, "scope_kind": "none",
            })
    facts = [_fact(change, extra_secret_patterns)[0] for change in changes.facts]
    stub = {
        "synthetic": False, "report_id": report_id,
        "source": {"base_sha": base_sha, "target_sha": target_sha,
                   "base_label": base_sha[:12] if base_sha else "İlk snapshot", "target_label": target_sha[:12]},
        "evidence_registry": registry,
    }
    obj = {"object_id": object_id, "facts": facts}
    return build_unit_input(stub, obj, unit_id)


def synthetic_probe_input() -> dict[str, Any]:
    """Small, fixed V5 capability probe with no production source text."""
    base, target = "0" * 40, "1" * 40
    object_id = "obj-synthetic-probe"
    evidence = [
        {"evidence_id": f"synthetic-{side}", "kind": "source", "side": side,
         "path": "synthetic/sequence.sql", "start_line": 1, "end_line": 1,
         "sha256": digit * 64, "detail_tr": "Sentetik test kanıtı.",
         "synthetic": True, "object_id": object_id, "scope_complete": None, "scope_kind": "none"}
        for side, digit in (("base", "0"), ("target", "1"))
    ]
    fact = {
        "fact_id": "fact-synthetic-start", "taxonomy_id": "sequence.sequence_property.start_with",
        "subject_name": "START WITH", "subject_kind": "sequence_property", "component_path": [],
        "change_action": "modified", "category": "observed_value",
        "before": {"state": "present", "value": "1", "evidence_ids": ["synthetic-base"]},
        "after": {"state": "present", "value": "2", "evidence_ids": ["synthetic-target"]},
        "verification": "verified", "context_only": False,
    }
    return build_unit_input(
        {"synthetic": True, "report_id": "00000000-0000-4000-8000-000000000001",
         "source": {"base_sha": base, "target_sha": target,
                    "base_label": "sentetik-eski", "target_label": "sentetik-yeni"},
         "evidence_registry": evidence},
        {"object_id": object_id, "facts": [fact]}, "unit-synthetic-probe",
    )


def _restatement(fact: dict[str, Any]) -> str | None:
    before, after = fact["before"], fact["after"]
    if before["state"] != "present" or after["state"] != "present":
        return None
    text = f"Git kaynağında {fact['subject_name']} değeri {before['value']} iken {after['value']} oldu."
    return text if len(text) <= 360 and not _FORBIDDEN.search(text) else None


def accept_commentary(
    content: str, unit_input: dict[str, Any], *, prompt_json: bool = False,
    extra_secret_patterns: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate the full response and display only provable restatements.

    A syntactically valid free interpretation remains withheld. The returned
    response is suitable for an audit sidecar; accepted comments are separate.
    """
    if scan_secret(content, extra_secret_patterns).blocked:
        raise ResponseValidationError("SECRET_IN_RESPONSE")
    response = parse_json_object(content, prompt_json=prompt_json)
    _validate_schema(response, "ai-mail-commentary.schema.json")
    if any(response[key] != unit_input[key] for key in ("report_id", "unit_id", "input_digest")):
        raise ResponseValidationError("MAIL_RESPONSE_BINDING_MISMATCH")
    facts = {fact["fact_id"]: fact for fact in unit_input["facts"]}
    evidence = {item["evidence_id"] for item in unit_input["evidence_registry"]}
    comments: list[dict[str, Any]] = []
    statements = ([response["summary"]] if response["summary"] else []) + response["interpretations"]
    for statement in statements:
        if any(fid not in facts for fid in statement["fact_ids"]) or any(eid not in evidence for eid in statement["evidence_ids"]):
            raise ResponseValidationError("MAIL_RESPONSE_REFERENCE_INVALID")
        if statement["claim_kind"] not in unit_input["allowed_claim_kinds"]:
            raise ResponseValidationError("MAIL_RESPONSE_CLAIM_KIND_INVALID")
        related = {eid for fid in statement["fact_ids"] for side in ("before", "after") for eid in facts[fid][side]["evidence_ids"]}
        if not set(statement["evidence_ids"]) <= related:
            raise ResponseValidationError("MAIL_RESPONSE_SUPPORT_INVALID")
        if _FORBIDDEN.search(statement["text_tr"]) or _PII.search(statement["text_tr"]):
            raise ResponseValidationError("MAIL_RESPONSE_MARKUP_OR_COMMAND")
        if statement["claim_kind"] != "change_restatement" or len(statement["fact_ids"]) != 1:
            continue
        fact = facts[statement["fact_ids"][0]]
        allowed_ids = set(fact["before"]["evidence_ids"] + fact["after"]["evidence_ids"])
        if set(statement["evidence_ids"]) != allowed_ids or statement["text_tr"] != _restatement(fact):
            continue
        comments.append({
            "text_tr": statement["text_tr"], "kind": "summary" if statement is response["summary"] else "interpretation",
            "evidence_ids": statement["evidence_ids"], "origin": "validated_ai", "gate": "accepted",
            "fact_ids": statement["fact_ids"], "unit_id": unit_input["unit_id"],
            "input_digest": unit_input["input_digest"], "acceptance_method": "deterministic_rule",
        })
    for item in response["uncertainties"]:
        if item["limitation_code"] not in unit_input["context_limit_codes"]:
            raise ResponseValidationError("MAIL_RESPONSE_LIMITATION_INVALID")
        if any(eid not in evidence for eid in item["context_evidence_ids"]):
            raise ResponseValidationError("MAIL_RESPONSE_REFERENCE_INVALID")
        if _FORBIDDEN.search(item["text_tr"]) or _PII.search(item["text_tr"]):
            raise ResponseValidationError("MAIL_RESPONSE_MARKUP_OR_COMMAND")
    for item in response["recommended_checks"]:
        if item["check_code"] not in unit_input["allowed_check_codes"]:
            raise ResponseValidationError("MAIL_RESPONSE_CHECK_INVALID")
        if any(fid not in facts for fid in item["fact_ids"]) or any(eid not in evidence for eid in item["evidence_ids"]):
            raise ResponseValidationError("MAIL_RESPONSE_REFERENCE_INVALID")
        if _FORBIDDEN.search(item["text_tr"]) or _PII.search(item["text_tr"]):
            raise ResponseValidationError("MAIL_RESPONSE_MARKUP_OR_COMMAND")
    return response, comments[:6]
