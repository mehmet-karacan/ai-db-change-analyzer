from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .analysis import analyze_with_single_repair
from .config import read_secret
from .git_client import GitClient, RawDelta
from .history import RangePlan
from .identities import stable_id
from .inventory import FileInventory, inventory_revision
from .litellm_http import LiteLLMClient, ModelTransportError
from .models import AnalysisUnit, FindingKind, ObjectIdentity, SourcePair
from .notification import persist_notification, send_persisted_notification
from .reporting import ReportError, build_message, render_report
from .security import scan_secret
from .source_classification import sequence_start_value_only
from .smtp_transport import SmtpTransport
from .validation import ResponseValidationError


@dataclass(frozen=True, slots=True)
class OccurrenceRef:
    inventory: FileInventory
    occurrence: Any
    raw: bytes


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _index(git: GitClient, revision: str, inventories: list[FileInventory], maximum: int) -> dict[str, list[OccurrenceRef]]:
    result: dict[str, list[OccurrenceRef]] = defaultdict(list)
    for inventory in inventories:
        if not inventory.occurrences:
            continue
        raw = git.read_blob(inventory.entry.oid, maximum)
        for occurrence in inventory.occurrences:
            result[occurrence.object_key].append(OccurrenceRef(inventory, occurrence, raw))
    return result


def _signature(items: list[OccurrenceRef]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((item.inventory.entry.path_b64, item.occurrence.fragment_sha256) for item in items))


def _evidence(item: OccurrenceRef, revision: str) -> dict[str, Any]:
    occurrence = item.occurrence
    fragment = item.raw[occurrence.start_byte : occurrence.end_byte_exclusive]
    snippet = fragment.decode(item.inventory.encoding or "utf-8", errors="strict")
    evidence_id = stable_id("ev", {"revision": revision, "path": item.inventory.entry.path_b64, "start": occurrence.start_byte, "hash": occurrence.fragment_sha256})[:100]
    return {
        "kind": "source", "evidence_id": evidence_id, "revision": revision,
        "path_display": item.inventory.entry.path_display, "path_b64": item.inventory.entry.path_b64,
        "blob_oid": item.inventory.entry.oid, "source_sha256": hashlib.sha256(item.raw).hexdigest(),
        "start_line": occurrence.start_line, "end_line": occurrence.end_line,
        "start_byte": occurrence.start_byte, "end_byte_exclusive": occurrence.end_byte_exclusive,
        "fragment_sha256": occurrence.fragment_sha256, "snippet": snippet, "redacted": False,
    }


def _operation(delta: RawDelta) -> str:
    return {"A": "added", "D": "removed", "M": "modified", "R": "relocated", "C": "relocated"}.get(delta.status[:1], "unknown")


def _persist_file(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return str(path)


def execute_analysis(args, config, store, plan: RangePlan, git: GitClient, *, mode: str = "AUTO") -> int:
    from .cli import _finish

    run_id = str(uuid.uuid4())
    planned_at = _now()
    epoch = store.verify()["epoch"]
    git.pin_target(run_id, plan.target_sha)
    connection = store.connection()
    try:
        with connection:
            connection.execute(
                "INSERT INTO runs(run_id,scope_hash,mode,base_sha,target_sha,epoch,analysis_generation,status,config_digest,fingerprint_json,originating_build_json,planned_at,last_attempt_at) VALUES (?,?,?,?,?,?,0,'ANALYZING',?,?,?,?,?)",
                (run_id, config.scope_hash, mode, plan.base_sha, plan.target_sha, epoch, config.config_digest, json.dumps({"model": config.model.id, "prompt": "db-change-tr-1.1"}, separators=(",", ":")), None, planned_at, planned_at),
            )
    finally:
        connection.close()

    roots = {root.path: root.default_schema for root in config.scope.roots}
    changed_paths = {delta.path for delta in plan.scope_deltas}
    old_inventories = [] if plan.base_sha is None else inventory_revision(git, plan.base_sha, roots, max_file_bytes=config.parser.max_file_bytes, timeout_seconds=config.parser.worker_timeout_seconds, parse_paths=changed_paths)
    new_inventories = inventory_revision(git, plan.target_sha, roots, max_file_bytes=config.parser.max_file_bytes, timeout_seconds=config.parser.worker_timeout_seconds, parse_paths=changed_paths)
    old_index = _index(git, plan.base_sha, old_inventories, config.parser.max_file_bytes) if plan.base_sha else {}
    new_index = _index(git, plan.target_sha, new_inventories, config.parser.max_file_bytes)
    changed_keys = sorted(key for key in old_index.keys() | new_index.keys() if _signature(old_index.get(key, [])) != _signature(new_index.get(key, [])))
    if len(changed_keys) > config.analysis.max_new_units_per_invocation:
        return _finish(args, mode="RUN", outcome="RETRY_PENDING", exit_code=11, error_code="UNIT_BUDGET", checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)

    system_prompt = (Path(__file__).parent / "prompts" / "system.tr.txt").read_text(encoding="utf-8")
    repair_prompt = (Path(__file__).parent / "prompts" / "repair.tr.txt").read_text(encoding="utf-8")
    response_schema = json.loads((Path(__file__).resolve().parents[2] / "schemas" / "unit-response.schema.json").read_text(encoding="utf-8"))
    evidence_registry: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    http_attempts = 0
    analysis_started = _now()
    try:
        with LiteLLMClient(config.model) as model:
            for key in changed_keys:
                old_refs, new_refs = old_index.get(key, []), new_index.get(key, [])
                all_refs = [*old_refs, *new_refs]
                representative = (new_refs or old_refs)[0]
                local_evidence = [*(_evidence(item, plan.base_sha) for item in old_refs), *(_evidence(item, plan.target_sha) for item in new_refs)]
                evidence_registry.extend(local_evidence)
                conflict = len({item.occurrence.fragment_sha256 for item in new_refs}) > 1
                secret = any(scan_secret(item["snippet"], config.security.extra_secret_patterns).blocked for item in local_evidence)
                mandatory_bytes = sum(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in local_evidence)
                diagnostics: list[str] = []
                assessments: list[dict[str, Any]] = []
                facts: list[dict[str, Any]] = []
                categories = ["unknown"]
                status = "limited" if any(item.inventory.parse_status != "parsed" for item in all_refs) else "analyzed"
                sequence_change = (
                    sequence_start_value_only(local_evidence[0]["snippet"], local_evidence[1]["snippet"])
                    if len(old_refs) == len(new_refs) == 1
                    and old_refs[0].occurrence.object_type == new_refs[0].occurrence.object_type == "SEQUENCE"
                    else None
                )
                if conflict:
                    status, diagnostics = "unresolved", ["CONFLICTING_DEFINITIONS"]
                elif secret:
                    status, diagnostics = "unresolved", ["SECRET_IN_MANDATORY_SOURCE"]
                elif mandatory_bytes > config.analysis.max_request_utf8_bytes:
                    status, diagnostics = "unresolved", ["MANDATORY_CONTEXT_EXCEEDS_BUDGET"]
                elif sequence_change:
                    before, after = sequence_change
                    categories = ["sequence_observed_value"]
                    facts.append({
                        "fact_id": stable_id("fact", {"key": key, "base": plan.base_sha, "target": plan.target_sha, "property": "START WITH"})[:100],
                        "property": "START WITH", "before": before, "after": after,
                        "category": "sequence_observed_value",
                        "evidence_ids": [item["evidence_id"] for item in local_evidence],
                        "source_pair": {"old_revision": plan.base_sha, "new_revision": plan.target_sha},
                        "event_ids": [], "view_tags": ["net"],
                    })
                else:
                    occurrence = representative.occurrence
                    identity = ObjectIdentity(
                        object_key=key, namespace=key.split("|", 1)[0], schema_name=occurrence.raw_schema,
                        name=occurrence.raw_name.upper() if not occurrence.name_quoted else occurrence.raw_name,
                        object_type=occurrence.object_type, raw_schema=occurrence.raw_schema, raw_name=occurrence.raw_name,
                        schema_quoted=occurrence.schema_quoted, name_quoted=occurrence.name_quoted,
                        identity_confidence="known", parent_key=None, routine_signature=None,
                    )
                    unit_id = stable_id("unit", {"run": run_id, "key": key})[:100]
                    unit = AnalysisUnit(
                        unit_id=unit_id, object_identity=identity,
                        artifact_paths=sorted({item.inventory.entry.path_display for item in all_refs}), related_object_keys=[], view_tags=["net"],
                        source_pair=SourcePair(old_revision=plan.base_sha, new_revision=plan.target_sha), deterministic_facts=[],
                        evidence_registry=local_evidence, dependency_edges=[],
                        coverage_manifest={"mandatory_spans_total": len(local_evidence), "mandatory_spans_covered": len(local_evidence), "optional_omissions": []},
                        allowed_claim_kinds=list(FindingKind),
                    )

                    def call(extra_system: str = system_prompt) -> str:
                        return model.complete(api_key=read_secret("LITELLM_API_KEY").get_secret_value(), system_message=extra_system, user_payload=unit.model_dump(mode="json"), response_schema=response_schema, output_tokens=config.analysis.output_tokens).content

                    outcome = analyze_with_single_repair(
                        unit,
                        call,
                        lambda codes: call(system_prompt + "\n" + repair_prompt.format(error_codes=",".join(codes))),
                        prompt_json=config.model.output_mode == "prompt_json",
                        extra_secret_patterns=config.security.extra_secret_patterns,
                    )
                    http_attempts += outcome.attempts
                    response = outcome.response.model_dump(mode="json")
                    assessments.append({
                        "unit_id": unit_id, "source_pair": unit.source_pair.model_dump(mode="json"), "view_tags": ["net"], "event_ids": [],
                        "context_digest": hashlib.sha256(json.dumps(local_evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
                        "coverage": unit.coverage_manifest, "response": response,
                    })
                    connection = store.connection()
                    try:
                        with connection:
                            connection.execute("INSERT INTO units(run_id,analysis_generation,unit_id,canonical_sources_json,alias_events_json,context_digest,request_digest,status,result_json,diagnostics_json,http_attempts) VALUES (?,0,?,?,?,?,?,'VALIDATED',?,?,?)", (run_id, unit_id, json.dumps(local_evidence), "[]", assessments[0]["context_digest"], assessments[0]["context_digest"], json.dumps(response), "[]", outcome.attempts))
                    finally:
                        connection.close()
                objects.append({
                    "identity": {
                        "object_key": key, "namespace": key.split("|", 1)[0], "schema_name": representative.occurrence.raw_schema,
                        "name": representative.occurrence.raw_name.upper() if not representative.occurrence.name_quoted else representative.occurrence.raw_name,
                        "object_type": representative.occurrence.object_type, "raw_schema": representative.occurrence.raw_schema,
                        "raw_name": representative.occurrence.raw_name, "schema_quoted": representative.occurrence.schema_quoted,
                        "name_quoted": representative.occurrence.name_quoted, "identity_confidence": "known", "parent_key": None, "routine_signature": None,
                    },
                    "net_operation": "added" if not old_refs else "removed" if not new_refs else "modified",
                    "categories": categories, "status": status,
                    "parser_level": "structural" if status == "analyzed" else "limited" if status == "limited" else "unresolved",
                    "old_evidence_ids": [item["evidence_id"] for item in local_evidence[:len(old_refs)]],
                    "new_evidence_ids": [item["evidence_id"] for item in local_evidence[len(old_refs):]],
                    "facts": facts, "assessments": assessments, "diagnostics": diagnostics,
                })
    except ModelTransportError as exc:
        return _finish(args, mode="RUN", outcome="AI_TRANSPORT_OR_AUTH", exit_code=30, error_code=exc.code, checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)
    except ResponseValidationError as exc:
        return _finish(args, mode="RUN", outcome="AI_RESPONSE_INVALID", exit_code=31, error_code=exc.codes[0], checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)

    quality = "blocked" if any(item["status"] == "unresolved" for item in objects) else "limited" if any(item["status"] == "limited" for item in objects) else "complete"
    report_id = str(uuid.uuid4())
    commits = [{"sha": item.sha, "parents": list(item.parents), "git_author_time": item.git_author_time, "git_committer_time": item.git_committer_time, "position": item.position, "delta_kind": item.delta_kind, "integration_role": "target_first_parent_chain"} for item in plan.commits]
    unique_deltas = {item.path: item for item in plan.scope_deltas}
    artifacts = [{"path_display": item.path_display, "path_b64": item.path_b64, "net_operation": _operation(item), "old_mode": item.old_mode or None, "new_mode": item.new_mode or None, "old_blob": item.old_oid if set(item.old_oid) != {"0"} else None, "new_blob": item.new_oid if set(item.new_oid) != {"0"} else None, "known_objects": None, "parse_status": "metadata_only", "diagnostic_codes": [], "assessments": []} for item in unique_deltas.values()]
    events = [{"event_id": stable_id("event", {"target": plan.target_sha, "path": item.path_b64})[:100], "commit_sha": plan.target_sha, "parent_sha": plan.base_sha, "object_key": None, "artifact_paths_b64": [item.path_b64], "operation": _operation(item), "categories": ["unknown"], "before_evidence_ids": [], "after_evidence_ids": []} for item in unique_deltas.values()]
    counts = {"net_files": len(plan.net_deltas), "history_files": len(unique_deltas), "net_known_objects": len(changed_keys), "history_known_objects": len(changed_keys), "change_events": len(events), "ai_units": sum(len(item["assessments"]) for item in objects), "ai_http_attempts": http_attempts, "analyzed_objects": sum(item["status"] == "analyzed" for item in objects), "limited_objects": sum(item["status"] == "limited" for item in objects), "unresolved_objects": sum(item["status"] == "unresolved" for item in objects), "unknown_artifacts": 0, "has_unknown_object_count": False}
    sequence_only = bool(objects) and all(item["categories"] == ["sequence_observed_value"] for item in objects)
    summary = (
        f"Git kaynağında {len(objects)} sequence için yalnız START WITH değeri değişti; diğer tanım metni aynı. Canlı veritabanı etkisi doğrulanmadı."
        if sequence_only else
        f"Git snapshot'ında {len(changed_keys)} nesne kaynak geçişi incelendi."
    )
    limitations = ["Analiz Git snapshot kaynaklarıyla sınırlıdır; canlı veritabanı doğrulaması değildir."]
    if sequence_only:
        limitations.append("START WITH kaynak farkı, mevcut NEXTVAL veya dağıtım sonucu hakkında tek başına kanıt değildir.")
    report = {
        "schema_version": "1.0", "synthetic": False, "report_id": report_id, "supersedes_report_id": None,
        "run": {"run_id": run_id, "mode": mode, "repository_id": config.repository.id, "branch": config.repository.branch, "scope_hash": config.scope_hash, "epoch": epoch, "base_sha": plan.base_sha, "target_sha": plan.target_sha, "planned_at": planned_at, "analysis_started_at": analysis_started, "analysis_completed_at": _now(), "analysis_generation": 0, "observed_git_at": _now(), "snapshot_captured_at": None, "actual_db_change_at": None, "originating_analyzer_build": None, "sync_build": None},
        "quality": quality, "automatic_commit_eligible": quality != "blocked", "summary_tr": summary, "overall_ai_risk": "unknown",
        "counts": counts, "versions": {"analyzer": "0.1.0", "grammar_commit": config.parser.grammar_commit, "parser_adapter": "1.0", "prompt": "db-change-tr-1.1", "unit_response_schema": "1.0", "report_schema": "1.0", "configured_model": config.model.id, "returned_model": None, "resolved_model_version": None, "config_digest": config.config_digest},
        "commits": commits, "events": events, "artifacts": artifacts, "evidence_registry": evidence_registry, "objects": objects,
        "limitations": limitations,
    }
    try:
        rendered = render_report(report, max_object_details=config.reports.mail_max_object_details)
    except ReportError as exc:
        return _finish(args, mode="RUN", outcome="SAFETY_POLICY_VIOLATION", exit_code=50, error_code=str(exc), checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)
    emit_dir = Path(args.emit_dir or config.reports.emit_dir).resolve()
    emitted = [
        _persist_file(emit_dir / "report.json", rendered.canonical_json + b"\n"),
        _persist_file(emit_dir / "report.html", rendered.html),
        _persist_file(emit_dir / "report.txt", rendered.text),
    ]
    connection = store.connection()
    try:
        with connection:
            connection.execute("INSERT INTO reports(report_id,run_id,canonical_json,content_sha256,html,text,rendered_version,quality,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (report_id, run_id, rendered.canonical_json, rendered.sha256, rendered.html, rendered.text, "1.0", quality, _now()))
            connection.execute("UPDATE runs SET report_id=?,status='REPORTED',quality=? WHERE run_id=?", (report_id, quality, run_id))
    finally:
        connection.close()
    if mode == "MANUAL":
        connection = store.connection()
        try:
            with connection:
                connection.execute("UPDATE runs SET status='CLOSED' WHERE run_id=?", (run_id,))
        finally:
            connection.close()
        return _finish(args, mode="MANUAL", outcome="MANUAL_COMPLETE", emitted_files=emitted, checkpoint_before=store.verify()["checkpoint_sha"], checkpoint_after=store.verify()["checkpoint_sha"], run_id=run_id, report_id=report_id, report_sha256=rendered.sha256, quality=quality, ai_http_attempts=http_attempts)
    notification_id = str(uuid.uuid4())
    message_id = f"<{notification_id}@{config.smtp.message_id_domain}>"
    message = build_message(report, rendered, sender=config.smtp.sender, recipients=config.smtp.recipients, message_id=message_id, date=datetime.now(UTC), job_short_name=os.environ.get("JOB_NAME", "MANUAL"), current_build_number=os.environ.get("BUILD_NUMBER"), max_bytes=config.reports.mail_max_bytes)
    connection = store.connection()
    try:
        persist_notification(connection, notification_id=notification_id, report_id=report_id, generation=0, recipients=config.smtp.recipients, message_id=message_id, mime_bytes=message.mime_bytes, mime_sha256=message.sha256)

        def finalize_delivery(transaction, delivery_status: str) -> None:
            if delivery_status != "ACCEPTED":
                return
            if quality == "blocked":
                transaction.execute("UPDATE runs SET status='REVIEW_REQUIRED' WHERE run_id=?", (run_id,))
                return
            cursor = transaction.execute(
                "UPDATE scopes SET checkpoint_sha=? WHERE scope_hash=? AND epoch=? AND checkpoint_sha IS ?",
                (plan.target_sha, config.scope_hash, epoch, plan.base_sha),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("checkpoint compare-and-swap failed during delivery commit")
            transaction.execute("UPDATE runs SET status='COMMITTED' WHERE run_id=?", (run_id,))
            transaction.execute("INSERT INTO audit_events(scope_hash,run_id,action,actor,expected_base,target,created_at) VALUES (?,?,'CHECKPOINT_ADVANCED','system',?,?,?)", (config.scope_hash, run_id, plan.base_sha, plan.target_sha, _now()))

        outcome = send_persisted_notification(
            connection,
            notification_id,
            SmtpTransport(config.smtp),
            username=os.environ.get("DB_ANALYZER_SMTP_USERNAME"),
            password=os.environ.get("DB_ANALYZER_SMTP_PASSWORD"),
            on_accepted_transaction=finalize_delivery,
        )
    finally:
        connection.close()
    if outcome.status == "UNKNOWN":
        return _finish(args, mode="RUN", outcome="NOTIFICATION_UNKNOWN", exit_code=41, error_code=outcome.error_code, emitted_files=emitted, checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha, run_id=run_id, report_id=report_id, report_sha256=rendered.sha256, notification_status="UNKNOWN", quality=quality, ai_http_attempts=http_attempts, smtp_attempts=1)
    if outcome.status != "ACCEPTED":
        return _finish(args, mode="RUN", outcome="NOTIFICATION_FAILED_OR_HELD", exit_code=40, error_code=outcome.error_code or "RECIPIENT_NOT_ACCEPTED", emitted_files=emitted, checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha, run_id=run_id, report_id=report_id, report_sha256=rendered.sha256, notification_status=outcome.status, quality=quality, ai_http_attempts=http_attempts, smtp_attempts=1, smtp_accepted_transactions=outcome.accepted_transactions)
    if quality == "blocked":
        return _finish(args, mode="RUN", outcome="REVIEW_REQUIRED", exit_code=11, emitted_files=emitted, checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha, run_id=run_id, report_id=report_id, report_sha256=rendered.sha256, notification_status="ACCEPTED", quality=quality, ai_http_attempts=http_attempts, smtp_attempts=1, smtp_accepted_transactions=1)
    return _finish(args, mode="RUN", outcome="LIMITED_NOTIFIED" if quality == "limited" else "SUCCEEDED", exit_code=10 if quality == "limited" else 0, emitted_files=emitted, checkpoint_before=plan.base_sha, checkpoint_after=plan.target_sha, run_id=run_id, report_id=report_id, report_sha256=rendered.sha256, notification_status="ACCEPTED", quality=quality, ai_http_attempts=http_attempts, smtp_attempts=1, smtp_accepted_transactions=1)
