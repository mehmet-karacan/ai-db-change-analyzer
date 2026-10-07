"""Build a conservative mail-view/2.0 sidecar from canonical report evidence."""

from __future__ import annotations

import json
import re
from typing import Any

from .identities import stable_id
from .oracle.changes import Change, ChangeSet, Value
from .security import scan_secret
from .taxonomy.catalog import change_catalog, supported_types
from .v5_rendering import validate_mail_view


_SAFE_FIELDS = {
    "common.object.presence",
    "common.object.editionable",
    "common.source.path",
    "table.column.data_type", "table.column.length", "table.column.length_semantics",
    "table.column.precision", "table.column.scale", "table.column.nullable",
    "table.column.visibility", "table.column.position", "table.column.collation",
    "table.constraint.kind", "table.constraint.columns", "table.constraint.reference",
    "table.constraint.delete_rule", "table.constraint.enabled", "table.constraint.validation",
    "table.constraint.deferrable", "table.constraint.initially", "table.constraint.rely",
    "table.table_property.organization", "table.table_property.temporary",
    "table.table_property.on_commit", "table.table_property.tablespace", "table.table_property.logging",
    "table.table_property.compression", "table.table_property.parallel",
    "index.index_property.target", "index.index_property.unique", "index.index_property.kind",
    "index.index_property.direction", "index.index_property.visibility",
    "index.index_property.partitioning", "index.index_property.storage", "index.index_property.compression",
    "index.index_property.tablespace", "index.index_property.logging",
    "index.index_property.parallel",
    "view.output_column.definition", "view.query_property.output_order",
    "package_spec.parameter.name", "package_spec.parameter.position",
    "package_spec.parameter.mode", "package_spec.parameter.data_type",
    "package_spec.parameter.nocopy", "package_spec.routine.return_type",
    "package_spec.package_property.authid",
    "package_body.parameter.name", "package_body.parameter.position",
    "package_body.parameter.mode", "package_body.parameter.data_type",
    "package_body.parameter.nocopy", "package_body.routine.return_type",
    "trigger.trigger_property.target", "trigger.trigger_property.enabled_state",
    "procedure.procedure_property.has_exception", "procedure.procedure_property.has_commit",
    "procedure.procedure_property.has_rollback", "function.function_property.has_exception",
    "function.function_property.has_commit", "function.function_property.has_rollback",
    "type.type_property.raw_definition", "type_body.type_property.raw_definition",
}
_SAFE_VALUE = re.compile(r"""[A-Za-z0-9_.$#",()/: +\-]+""")
_SAFE_PATH = re.compile(r"[A-Za-z0-9_./$#() +\-]{1,1000}")
_PII = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b\d{11}\b")
_SOURCE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ROUTINE_SIGNATURE = re.compile(r"[A-Za-z0-9_.$#(), +\-]{1,256}")
_SAFE_VIEW_JOIN = re.compile(r"[A-Za-z0-9_.$#(),*/ +=!|\-]{1,1200}")
_STANDALONE_NUMBER = re.compile(r"(?<![A-Za-z0-9_$#])[0-9]+(?![A-Za-z0-9_$#])")


def _safe_signature_group(text: str) -> bool:
    try:
        signatures = json.loads(text)
    except (ValueError, TypeError):
        return False
    return (isinstance(signatures, list) and 1 <= len(signatures) <= 16
            and all(isinstance(value, str) and _SAFE_ROUTINE_SIGNATURE.fullmatch(value) for value in signatures))


def _safe_path(path: str, extra_secret_patterns: list[str]) -> str | None:
    return path if _SAFE_PATH.fullmatch(path) and not _PII.search(path) and not scan_secret(path, extra_secret_patterns).blocked else None


def _object_id(key: str) -> str:
    return stable_id("obj", {"key": key})[:100]


def _display_value(taxonomy_id: str, value: Value, extra_secret_patterns: list[str]) -> tuple[dict[str, Any], bool]:
    result = {"state": value.state, "value": value.value, "evidence_ids": list(value.evidence_ids)}
    if value.state != "present":
        return result, False
    text = value.value or ""
    signature_group = taxonomy_id in {"package_spec.routine.signature", "package_body.routine.signature"} and _safe_signature_group(text)
    safe_join = (taxonomy_id == "view.query_property.join" and bool(_SAFE_VIEW_JOIN.fullmatch(text))
                 and not _STANDALONE_NUMBER.search(text)
                 and not any(marker in text for marker in ("--", "/*", "*/")))
    safe_analytic = (taxonomy_id == "view.query_property.analytic" and bool(_SAFE_VIEW_JOIN.fullmatch(text))
                     and not _STANDALONE_NUMBER.search(text)
                     and not any(marker in text for marker in ("--", "/*", "*/")))
    allowed = (taxonomy_id.startswith("sequence.sequence_property.") or taxonomy_id in _SAFE_FIELDS
               or (taxonomy_id == "common.source.occurrence_order" and bool(re.fullmatch(r"[1-9][0-9]{0,8}", text)))
               or (taxonomy_id in {"common.source.format", "common.source.text"} and bool(_SOURCE_DIGEST.fullmatch(text)))
               or signature_group or safe_join or safe_analytic)
    if len(text) > 1200:
        result.update(state="unparsed", value=None)
        return result, True
    safe_label = (taxonomy_id == "common.object.presence" and text == "Tanım kaynakta mevcut") or signature_group or safe_join or safe_analytic
    possible_pii = not taxonomy_id.startswith("sequence.sequence_property.") and bool(_PII.search(text))
    if not allowed or (not safe_label and not _SAFE_VALUE.fullmatch(text)) or possible_pii or scan_secret(text, extra_secret_patterns).blocked:
        result.update(state="redacted", value=None)
        return result, True
    return result, False


def _fact(change: Change, extra_secret_patterns: list[str]) -> tuple[dict[str, Any], bool]:
    before, before_limited = _display_value(change.taxonomy_id, change.before, extra_secret_patterns)
    after, after_limited = _display_value(change.taxonomy_id, change.after, extra_secret_patterns)
    family = change_catalog()[change.taxonomy_id]
    path = [part if len(part) <= 256 and _SAFE_VALUE.fullmatch(part) and not _PII.search(part)
            and not scan_secret(part, extra_secret_patterns).blocked else "GIZLI" for part in change.component_path]
    limited = before_limited or after_limited or tuple(path) != change.component_path
    return {
        "fact_id": change.fact_id, "taxonomy_id": change.taxonomy_id,
        "subject_name": family.label_tr, "subject_kind": family.component,
        "component_path": path, "change_action": change.action,
        "category": change.category, "before": before, "after": after,
        "verification": "redacted" if limited else change.verification,
        "context_only": change.context_only,
    }, limited


def _recommended_checks(
    object_type: str, operation: str, fact_types: set[str], evidence_ids: list[str]
) -> list[dict[str, Any]]:
    """Return bounded, evidence-linked impact prompts without asserting runtime dependencies."""
    evidence = list(dict.fromkeys(evidence_ids))[:8]
    if not evidence:
        return []
    checks: list[dict[str, Any]] = []

    def add(code: str, text_tr: str) -> None:
        if len(checks) < 6 and text_tr not in {item["text_tr"] for item in checks}:
            checks.append({"code": code, "text_tr": text_tr, "evidence_ids": evidence, "origin": "deterministic_rule"})

    if operation in {"added", "removed"}:
        add(
            "SOURCE_REFERENCES",
            "Bu nesneyi kullanan uygulama akışları, yetkiler ve kaynak bağımlılıkları kontrol edilmelidir.",
        )
    if object_type == "TABLE":
        if operation == "modified" or any(item.startswith("table.column.") for item in fact_types):
            add("COLUMN_CONTRACT", "Kolon sözleşmesi, veri yazma/okuma akışları ve geriye dönük uyumluluk kontrol edilmelidir.")
        if any(item.startswith("table.constraint.") for item in fact_types):
            add("SOURCE_REFERENCES", "İlişkili kayıt yazma akışları ve veri bütünlüğü davranışı kontrol edilmelidir.")
        if any(item.startswith("table.table_property.") for item in fact_types):
            add("MANUAL_SOURCE_REVIEW", "Tablonun fiziksel özellikleri, performans beklentisi ve bakım akışları kontrol edilmelidir.")
    elif object_type == "INDEX":
        add("INDEX_DEFINITION", "İlgili sorguların planı, indeks kullanımı ve beklenen performans etkisi kontrol edilmelidir.")
    elif object_type == "VIEW":
        add("QUERY_DEFINITION", "View'i kullanan rapor ve sorguların kolon sözleşmesi ile sonuç kümesi kontrol edilmelidir.")
    elif object_type in {"PACKAGE_SPEC", "PACKAGE_BODY"}:
        add("CALL_SIGNATURE", "Paketi çağıran uygulamalar, imza uyumluluğu ve regresyon testleri kontrol edilmelidir.")
    elif object_type == "SEQUENCE":
        add("SEQUENCE_DEFINITION", "Sequence'in canlı NEXTVAL değeri ve kullanan akışların tekrar/çakışma davranışı kontrol edilmelidir.")
    else:
        add("MANUAL_SOURCE_REVIEW", "Değişen tanımın uygulama tüketicileri ve operasyonel etkisi kaynak incelemesiyle kontrol edilmelidir.")
    return checks


def build_mail_view(
    report: dict[str, Any], changes_by_key: dict[str, ChangeSet], *,
    analysis_elapsed_ms: int | None, ai_phase_started_at: str | None,
    ai_phase_completed_at: str | None, ai_phase_elapsed_ms: int | None,
    returned_models_by_unit: dict[str, set[str]], analysis_duration_basis: str | None = None,
    extra_secret_patterns: list[str] | None = None,
    ai_comments_by_key: dict[str, list[dict[str, Any]]] | None = None,
    ai_unit_ids_by_key: dict[str, str] | None = None,
    ai_status_by_key: dict[str, str] | None = None,
) -> dict[str, Any]:
    run = report["run"]
    base_sha, target_sha = run["base_sha"], run["target_sha"]
    patterns = extra_secret_patterns or []
    comments_map = ai_comments_by_key or {}
    unit_map = ai_unit_ids_by_key or {}
    status_map = ai_status_by_key or {}
    object_ids = {item["identity"]["object_key"]: _object_id(item["identity"]["object_key"]) for item in report["objects"]}
    evidence_owner: dict[str, str] = {}
    for item in report["objects"]:
        for evidence_id in (*item["old_evidence_ids"], *item["new_evidence_ids"]):
            evidence_owner[evidence_id] = object_ids[item["identity"]["object_key"]]
    registry: list[dict[str, Any]] = []
    for evidence in report["evidence_registry"]:
        if evidence["kind"] != "source":
            continue
        side = "base" if evidence["revision"] == base_sha else "target" if evidence["revision"] == target_sha else "none"
        registry.append({
            "evidence_id": evidence["evidence_id"], "kind": "source", "side": side,
            "path": _safe_path(evidence["path_display"], patterns),
            "start_line": evidence["start_line"], "end_line": evidence["end_line"],
            "sha256": evidence["fragment_sha256"],
            "detail_tr": "Git snapshot içindeki Oracle tanım aralığı.",
            "synthetic": report["synthetic"], "object_id": evidence_owner.get(evidence["evidence_id"]),
            "scope_complete": None, "scope_kind": "none",
        })
    known_evidence = {item["evidence_id"] for item in registry}
    objects: list[dict[str, Any]] = []
    field_complete = True
    for item in report["objects"]:
        identity = item["identity"]
        key = identity["object_key"]
        change_set = changes_by_key.get(key)
        object_id = object_ids[key]
        source_ids = list(dict.fromkeys((*item["old_evidence_ids"], *item["new_evidence_ids"])))
        converted: list[dict[str, Any]] = []
        redacted = False
        if change_set:
            for change in change_set.facts:
                fact, limited = _fact(change, patterns)
                converted.append(fact)
                redacted |= limited
                for side_name, value in (("base", change.before), ("target", change.after)):
                    for evidence_id in value.evidence_ids:
                        if evidence_id in known_evidence:
                            continue
                        if value.state not in {"absent_in_snapshot", "not_applicable"}:
                            raise ValueError("Source fact references missing evidence")
                        registry.append({
                            "evidence_id": evidence_id, "kind": "snapshot_absence",
                            "side": side_name, "path": None, "start_line": None, "end_line": None,
                            "sha256": None, "detail_tr": "İzlenen Git snapshot kapsamında nesne tanımı bulunmadı.",
                            "synthetic": report["synthetic"], "object_id": object_id,
                            "scope_complete": True, "scope_kind": "snapshot",
                        })
                        known_evidence.add(evidence_id)
                        source_ids.append(evidence_id)
        if not source_ids:
            fallback_id = stable_id("ev", {"report": report["report_id"], "object": key, "limit": True})[:100]
            registry.append({
                "evidence_id": fallback_id, "kind": "context_limit", "side": "none",
                "path": None, "start_line": None, "end_line": None, "sha256": None,
                "detail_tr": "Nesne için görüntülenebilir kaynak kanıtı kaydedilemedi.",
                "synthetic": report["synthetic"], "object_id": object_id,
                "scope_complete": False, "scope_kind": "none",
            })
            source_ids.append(fallback_id)
            known_evidence.add(fallback_id)
        unsupported = change_set.unsupported_families if change_set else ()
        object_limited = bool(unsupported or redacted or not change_set or change_set.diagnostics)
        field_complete &= not object_limited
        notes: list[str] = []
        if unsupported:
            notes.append(f"{len(unsupported)} taxonomy özellik ailesi için ayrıntılı çıkarım doğrulanmadı.")
        if change_set and any(change.taxonomy_id in {"package_spec.routine.signature", "package_body.routine.signature"}
                              and ((change.before.value or "").startswith("[") or (change.after.value or "").startswith("["))
                              for change in change_set.facts):
            notes.append("Overload imzaları küme olarak karşılaştırıldı; tekil rutin eşlemesi doğrulanmadı.")
        if redacted:
            notes.append("Bazı kaynak değerleri gizlilik nedeniyle gösterilmedi.")
        if change_set and change_set.diagnostics:
            notes.append("Kaynak ayrıştırması sınırlı; kesin alan değeri üretilmedi.")
        if not notes:
            notes.append("Sonuç yalnız izlenen Git kaynak snapshot'larıyla sınırlıdır.")
        status = item["status"]
        verification = "unresolved" if status == "unresolved" else "limited" if object_limited or status == "limited" else "verified"
        operation = item["net_operation"]
        sequence_only = item["categories"] == ["sequence_observed_value"] and operation == "modified"
        source_paths = list(dict.fromkeys(safe for evidence in report["evidence_registry"]
                                          if evidence.get("evidence_id") in source_ids
                                          if (safe := _safe_path(evidence.get("path_display") or "", patterns))))
        if len(source_paths) > 32 or len(source_ids) > 64:
            raise ValueError("Mail-view object exceeds source/evidence limits")
        fact_types = {fact["taxonomy_id"] for fact in converted}
        technical_pattern = {
            frozenset({"common.source.format"}): "format_only",
            frozenset({"common.source.occurrence_order"}): "source_order_only",
            frozenset({"common.source.path"}): "relocation",
            frozenset({"common.source.text"}): "text_only",
        }.get(frozenset(fact_types))
        recommended_checks = _recommended_checks(identity["object_type"], operation, fact_types, source_ids)
        objects.append({
            "object_id": object_id,
            "identity": {
                "namespace": identity["namespace"], "schema_name": identity["schema_name"],
                "name": identity["name"], "schema_quoted": identity["schema_quoted"],
                "name_quoted": identity["name_quoted"],
                "object_type": identity["object_type"] if identity["object_type"] in supported_types() else "UNKNOWN",
                "parent_object_id": None,
                "raw_object_type": identity["object_type"] if identity["object_type"] not in supported_types() else None,
                "identity_confidence": identity["identity_confidence"],
            },
            "operation": operation,
            "pattern": "start_with_only" if sequence_only else technical_pattern or "structured" if converted else "text_only" if source_ids else "unknown",
            "verification": verification, "source_paths": source_paths,
            "evidence_ids": list(dict.fromkeys(source_ids)), "facts": converted,
            "deterministic_summary_tr": f"{len(converted)} kaynak alanı farkı kaydedildi." if converted else "Kaynak tanımında fark gözlendi; alan ayrıştırması sınırlı.",
            "limitations_tr": notes[:10], "ai_comments": comments_map.get(key, []),
            "recommended_checks": recommended_checks, "ai_status": status_map.get(key, "withheld" if item["assessments"] else "not_requested"),
            "unit_ids": [unit_map[key]] if key in unit_map else list(dict.fromkeys(assessment["unit_id"] for assessment in item["assessments"])),
            "source_checks": {
                "identity_match": "verified" if identity["identity_confidence"] == "known" else "uncertain",
                "presence_comparison": "verified" if operation in {"added", "removed", "modified"} and not report["counts"]["has_unknown_object_count"] else "uncertain",
                "start_with_only_proved": sequence_only,
                "remainder_equal": True if sequence_only else None,
                "conflict_detected": "CONFLICTING_DEFINITIONS" in item["diagnostics"],
            },
        })
    if report["counts"]["ai_http_attempts"] == 0:
        ai = {
            "status": "not_used", "phase_started_at": None, "phase_completed_at": None,
            "phase_elapsed_ms": None, "request_duration_sum_ms": None,
            "http_attempts": 0, "requested_units": 0, "models": [],
        }
    else:
        model_units: dict[tuple[str, ...], list[str]] = {}
        for unit_id in sorted(set(unit_map.values()) | set(returned_models_by_unit)):
            labels = tuple(sorted(returned_models_by_unit.get(unit_id, set())))
            model_units.setdefault(labels, []).append(unit_id)
        if not model_units:
            model_units[()] = []
        if len(model_units) > 32:
            raise ValueError("MODEL_UNIT_MAPPING_LIMIT")
        ai_status = "complete" if unit_map and len(unit_map) == report["counts"]["ai_units"] and all(
            status_map.get(key) in {"displayed", "withheld"} for key in unit_map
        ) else "partial"
        ai = {
            "status": ai_status, "phase_started_at": ai_phase_started_at,
            "phase_completed_at": ai_phase_completed_at, "phase_elapsed_ms": ai_phase_elapsed_ms,
            "request_duration_sum_ms": None, "http_attempts": report["counts"]["ai_http_attempts"],
            "requested_units": report["counts"]["ai_units"],
            "models": [{
                "configured_model": report["versions"]["configured_model"],
                "returned_models": list(labels), "resolved_model_version": None,
                "version_verification": "reported_unverified" if labels else "not_reported",
                "verification_record_id": None,
                "unit_ids": unit_ids,
            } for labels, unit_ids in sorted(model_units.items())],
        }
    limitations = list(report["limitations"])
    if not field_complete:
        limitations.append("Katalogdaki tüm özellik aileleri ayrıntılı olarak doğrulanmadı.")
    if not limitations:
        limitations.append("Canlı veritabanı durumu doğrulanmadı.")
    artifact_notices: list[dict[str, Any]] = []
    for artifact in report.get("artifacts", []):
        if artifact["parse_status"] == "parsed":
            continue
        evidence_id = stable_id("ev", {"report": report["report_id"], "artifact": artifact["path_b64"]})[:100]
        registry.append({
            "evidence_id": evidence_id, "kind": "context_limit", "side": "none",
            "path": _safe_path(artifact["path_display"], patterns), "start_line": None, "end_line": None,
            "sha256": None, "detail_tr": "Kaynak dosyasının nesne kapsamı tam doğrulanamadı.",
            "synthetic": report["synthetic"], "object_id": None,
            "scope_complete": False, "scope_kind": "none",
        })
        artifact_notices.append({
            "notice_id": stable_id("notice", {"report": report["report_id"], "artifact": artifact["path_b64"]})[:100],
            "path_display": _safe_path(artifact["path_display"], patterns) or "[gizli kaynak yolu]",
            "reason_code": artifact["diagnostic_codes"][0] if artifact["diagnostic_codes"] else "SOURCE_PARSE_LIMIT",
            "object_id": None,
            "detail_tr": "Bu dosyada ayrıştırma sınırlı; bilinmeyen nesne ve değişiklikler olabilir.",
            "evidence_ids": [evidence_id],
        })
    view = {
        "schema_version": "mail-view/2.0", "template_version": "v5.0",
        "taxonomy_version": "oracle-taxonomy/1.0", "report_id": report["report_id"],
        "synthetic": report["synthetic"], "language": "tr",
        "source": {
            "origin": "synthetic" if report["synthetic"] else "verified_git",
            "repository": run["repository_id"], "branch": run["branch"],
            "base_label": base_sha[:12] if base_sha else "İlk snapshot",
            "target_label": target_sha[:12], "base_sha": base_sha, "target_sha": target_sha,
            "observed_at": run["observed_git_at"], "snapshot_captured_at": run["snapshot_captured_at"],
            "actual_db_change_at": None, "sync_build": None, "comparison_mode": "net",
        },
        "changed_files": report["counts"]["net_files"],
        "coverage": {
            "analysis_status": "blocked" if report["quality"] == "blocked" else "limited" if not field_complete or report["quality"] == "limited" else "complete",
            "export_scope": "unknown", "unknown_artifacts": report["counts"]["unknown_artifacts"],
            "object_count_complete": not report["counts"]["has_unknown_object_count"] and report["counts"]["unknown_artifacts"] == 0,
            "limitations_tr": limitations[:12],
            "identity_scan_complete_base": bool(base_sha) and not report["counts"]["has_unknown_object_count"],
            "identity_scan_complete_target": not report["counts"]["has_unknown_object_count"],
            "field_coverage_complete": field_complete,
        },
        "evidence_registry": registry, "objects": objects, "artifact_notices": artifact_notices,
        "analysis": {
            "record_origin": "runtime", "started_at": run["analysis_started_at"],
            "completed_at": run["analysis_completed_at"], "elapsed_ms": analysis_elapsed_ms,
            "duration_basis": analysis_duration_basis or ("monotonic" if analysis_elapsed_ms is not None else "not_recorded"),
            "display_timezone": "Europe/Istanbul", "ai": ai,
        },
    }
    validate_mail_view(view)
    return view
