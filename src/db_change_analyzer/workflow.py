from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import read_secret
from .git_client import GitClient, RawDelta
from .history import RangePlan
from .identities import stable_id
from .inventory import FileInventory, _parse_in_worker, inventory_revision
from .litellm_http import LiteLLMClient, ModelTransportError
from .mail_commentary import accept_commentary, build_source_unit_input
from .notification import finalize_auto_delivery, insert_notification, send_persisted_notification
from .oracle.changes import Change, ChangeSet, Value, compare_projections
from .oracle.projections import Projection, project
from .oracle.table import TableContextStatement, extract_table, table_context_statements
from .reporting import ReportError, render_report
from .security import scan_secret
from .source_classification import sequence_start_value_only, whitespace_only_source_change
from .smtp_transport import SmtpTransport
from .taxonomy.catalog import supported_types
from .validation import ResponseValidationError
from .v5_adapter import build_mail_view
from .v5_rendering import V5RenderError, build_render_manifest, render_v5_view


@dataclass(frozen=True, slots=True)
class OccurrenceRef:
    inventory: FileInventory
    occurrence: Any
    raw: bytes
    projection: Projection
    context_evidence: tuple[dict[str, Any], ...] = ()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _index(git: GitClient, revision: str, inventories: list[FileInventory], maximum: int) -> dict[str, list[OccurrenceRef]]:
    result: dict[str, list[OccurrenceRef]] = defaultdict(list)
    for inventory in inventories:
        if not inventory.occurrences:
            continue
        raw = git.read_blob(inventory.entry.oid, maximum)
        for occurrence, projection in zip(inventory.occurrences, inventory.projections, strict=True):
            result[occurrence.object_key].append(OccurrenceRef(inventory, occurrence, raw, projection))
    return result


def _signature(items: list[OccurrenceRef]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((item.inventory.entry.path_b64, item.occurrence.fragment_sha256 + ":" + ":".join(evidence["path_b64"] + ":" + evidence["fragment_sha256"] for evidence in item.context_evidence)) for item in items))


def _reordered_keys(old_inventories: list[FileInventory], new_inventories: list[FileInventory]) -> set[str]:
    """Detect changed definition order only with a complete, unique file inventory."""
    old_by_path = {item.entry.path: item for item in old_inventories}
    new_by_path = {item.entry.path: item for item in new_inventories}
    changed: set[str] = set()
    for path in old_by_path.keys() & new_by_path.keys():
        old, new = old_by_path[path], new_by_path[path]
        if old.parse_status != "parsed" or new.parse_status != "parsed" or old.diagnostics or new.diagnostics:
            continue
        before = tuple(item.object_key for item in old.occurrences)
        after = tuple(item.object_key for item in new.occurrences)
        if len(before) < 2 or len(set(before)) != len(before) or set(before) != set(after):
            continue
        changed.update(key for index, key in enumerate(before) if after.index(key) != index)
    return changed


def _table_parts(ref: OccurrenceRef) -> tuple[str, str]:
    occurrence = ref.occurrence
    return (
        ("Q:" if occurrence.schema_quoted else "U:") + (occurrence.raw_schema if occurrence.schema_quoted else occurrence.raw_schema.upper()),
        ("Q:" if occurrence.name_quoted else "U:") + (occurrence.raw_name if occurrence.name_quoted else occurrence.raw_name.upper()),
    )


def _context_evidence(inventory: FileInventory, raw: bytes, source: str,
                      statement: TableContextStatement, revision: str) -> dict[str, Any]:
    start = len(source[:statement.start_char].encode("utf-8"))
    end = len(source[:statement.end_char].encode("utf-8"))
    fragment = raw[start:end]
    digest = hashlib.sha256(fragment).hexdigest()
    return {
        "kind": "source", "evidence_id": stable_id("ev", {"revision": revision, "path": inventory.entry.path_b64,
                                                       "start": start, "hash": digest})[:100],
        "revision": revision, "path_display": inventory.entry.path_display,
        "path_b64": inventory.entry.path_b64, "blob_oid": inventory.entry.oid,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "start_line": source.count("\n", 0, statement.start_char) + 1,
        "end_line": source.count("\n", 0, statement.end_char) + 1,
        "start_byte": start, "end_byte_exclusive": end,
        "fragment_sha256": digest, "snippet": fragment.decode("utf-8"), "redacted": False,
    }


def _attach_table_contexts(
    git: GitClient, revision: str, roots: dict[str, str], inventories: list[FileInventory],
    index: dict[str, list[OccurrenceRef]], maximum: int, timeout_seconds: int,
) -> tuple[dict[str, list[OccurrenceRef]], set[bytes]]:
    """Join standalone ALTER/COMMENT files only when every target is known."""
    targets: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for key, refs in index.items():
        for position, ref in enumerate(refs):
            if ref.occurrence.object_type == "TABLE":
                targets[_table_parts(ref)].append((key, position))
    additions: dict[tuple[str, int], list[tuple[TableContextStatement, dict[str, Any]]]] = defaultdict(list)
    matched_paths: set[bytes] = set()
    for inventory in inventories:
        if inventory.occurrences or not inventory.entry.path.lower().endswith(b".sql"):
            continue
        if inventory.bytes is None or inventory.bytes > min(maximum, 128_000):
            continue
        if "UNRESOLVED_PREFIX_OR_ARTIFACT" not in inventory.diagnostics:
            continue
        raw = git.read_blob(inventory.entry.oid, maximum)
        source = raw.decode("utf-8")
        matching = [(root, schema) for root, schema in roots.items()
                    if inventory.entry.path == root.encode() or inventory.entry.path.startswith(root.encode() + b"/")]
        default_schema = max(matching, key=lambda pair: len(pair[0]))[1] if matching else None
        statements = table_context_statements(source, default_schema)
        if not statements or any(len(targets.get(statement.target_parts, ())) != 1 for statement in statements):
            continue
        if inventory.parse_status != "parsed" and not _parse_in_worker(raw, timeout_seconds)[0]:
            continue
        per_location: dict[tuple[str, int], int] = defaultdict(int)
        for statement in statements:
            per_location[targets[statement.target_parts][0]] += 1
        if any(len(additions[location]) + count > 12 for location, count in per_location.items()):
            continue
        if any(index[key][position].projection.support != "structural" and not _parse_in_worker(index[key][position].raw, timeout_seconds)[0]
               for key, position in per_location):
            continue
        for statement in statements:
            evidence = _context_evidence(inventory, raw, source, statement, revision)
            for location in targets[statement.target_parts]:
                additions[location].append((statement, evidence))
        matched_paths.add(inventory.entry.path)
    if not additions:
        return index, matched_paths
    updated = {key: list(refs) for key, refs in index.items()}
    for (key, position), items in additions.items():
        ref = updated[key][position]
        fragment = ref.raw[ref.occurrence.start_byte:ref.occurrence.end_byte_exclusive].decode(ref.inventory.encoding or "utf-8")
        combined = fragment.rstrip() + "\n" + ";\n".join(statement.text for statement, _ in items) + ";"
        extracted = extract_table(combined)
        base_projection = ref.projection if ref.projection.support == "structural" else project(ref.occurrence, fragment, parse_ok=True)
        properties = {**base_projection.properties, "columns": extracted.columns,
                      "constraints": extracted.constraints, "table_properties": extracted.properties,
                      "comments": extracted.comments, "_unresolved_columns": extracted.unresolved_columns}
        projection = Projection(base_projection.object_key, base_projection.object_type, properties,
                                base_projection.support, tuple(dict.fromkeys((*base_projection.diagnostics, *extracted.diagnostics))))
        updated[key][position] = replace(ref, projection=projection,
                                         context_evidence=(*ref.context_evidence, *(evidence for _, evidence in items)))
    return updated, matched_paths


def _verify_changed_refs(index: dict[str, list[OccurrenceRef]], keys: list[str], timeout_seconds: int) -> None:
    """Verify unchanged CREATE files when related context makes an object changed."""
    verified_blobs: dict[str, bool] = {}
    for key in keys:
        for position, ref in enumerate(index.get(key, ())):
            if ref.projection.support == "structural" or ref.context_evidence:
                continue
            oid = ref.inventory.entry.oid
            if oid not in verified_blobs:
                verified_blobs[oid] = _parse_in_worker(ref.raw, timeout_seconds)[0]
            if not verified_blobs[oid]:
                continue
            fragment = ref.raw[ref.occurrence.start_byte:ref.occurrence.end_byte_exclusive].decode(ref.inventory.encoding or "utf-8")
            index[key][position] = replace(ref, projection=project(ref.occurrence, fragment, parse_ok=True))


def _conflicting_definitions(items: list[OccurrenceRef]) -> bool:
    return len({(
        item.occurrence.fragment_sha256,
        tuple(evidence["fragment_sha256"] for evidence in item.context_evidence),
        item.projection.support,
        item.projection.diagnostics,
    ) for item in items}) > 1


def _incomplete_changed_paths(inventories: list[FileInventory], changed_paths: set[bytes]) -> set[bytes]:
    return {
        item.entry.path
        for item in inventories
        if item.entry.path in changed_paths
        and (
            item.parse_status != "parsed" or "UNRESOLVED_PREFIX_OR_ARTIFACT" in item.diagnostics
            or any(projection.diagnostics for projection in item.projections)
        )
    }


def _identity_incomplete_paths(inventories: list[FileInventory]) -> set[bytes]:
    limit_codes = {"UNRESOLVED_PREFIX_OR_ARTIFACT", "ENCODING_UNRESOLVED", "UNSUPPORTED_GIT_MODE"}
    return {item.entry.path for item in inventories if limit_codes.intersection(item.diagnostics)}


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


def _canonical_projection_facts(
    old: OccurrenceRef, new: OccurrenceRef, old_evidence_ids: tuple[str, ...], new_evidence_ids: tuple[str, ...],
    *, run_id: str, base_sha: str, target_sha: str, changes: ChangeSet | None = None,
) -> list[dict[str, Any]]:
    changes = changes or compare_projections(
        old.projection, new.projection,
        old_evidence_ids=old_evidence_ids, new_evidence_ids=new_evidence_ids,
    )
    categories = {
        "observed_value": "sequence_observed_value",
        "presentation": "format_only",
        "configuration": "physical_configuration",
        "presence": "structural",
        "source_text": "unknown",
    }
    facts: list[dict[str, Any]] = []
    for change in changes.facts:
        if change.before.state not in {"present", "not_in_source"} or change.after.state not in {"present", "not_in_source"}:
            continue
        before, after = change.before.value, change.after.value
        if (before is not None and len(before) > 6000) or (after is not None and len(after) > 6000):
            continue
        label = "START WITH" if change.taxonomy_id == "sequence.sequence_property.start_with" else change.taxonomy_id
        if change.component_path:
            label += " [" + "/".join(change.component_path) + "]"
        if len(label) > 1000:
            continue
        facts.append({
            "fact_id": stable_id("fact", {"run": run_id, "object": changes.object_key, "taxonomy": change.taxonomy_id, "path": change.component_path})[:100],
            "property": label, "before": before, "after": after,
            "category": categories.get(change.category, change.category),
            "evidence_ids": [*old_evidence_ids, *new_evidence_ids],
            "source_pair": {"old_revision": base_sha, "new_revision": target_sha},
            "event_ids": [], "view_tags": ["net"],
        })
    return facts


def _with_source_text_change(
    change_set: ChangeSet, old: OccurrenceRef, new: OccurrenceRef,
    old_evidence: dict[str, Any], new_evidence: dict[str, Any],
) -> ChangeSet:
    if change_set.diagnostics or change_set.facts or old.occurrence.fragment_sha256 == new.occurrence.fragment_sha256:
        return change_set
    format_only = whitespace_only_source_change(old_evidence["snippet"], new_evidence["snippet"])
    taxonomy_id = "common.source.format" if format_only else "common.source.text"
    category = "presentation" if format_only else "source_text"
    fact = Change(
        fact_id=stable_id("fact", {"key": change_set.object_key, "taxonomy": taxonomy_id})[:100],
        taxonomy_id=taxonomy_id, component_path=(), action="modified", category=category,
        before=Value("present", "sha256:" + old.occurrence.fragment_sha256, (old_evidence["evidence_id"],)),
        after=Value("present", "sha256:" + new.occurrence.fragment_sha256, (new_evidence["evidence_id"],)),
        context_only=False, verification="verified",
    )
    return ChangeSet(
        change_set.object_key, change_set.object_type, change_set.operation,
        (*change_set.facts, fact),
        tuple(item for item in change_set.unsupported_families if item != taxonomy_id),
        change_set.diagnostics,
    )


def _with_format_override(
    change_set: ChangeSet, old: OccurrenceRef, new: OccurrenceRef,
    old_snippet: str, new_snippet: str,
) -> ChangeSet:
    """Discard raw-span property diffs when the full definition has equal tokens."""
    if (old.context_evidence or new.context_evidence
            or old.projection.support != "structural" or new.projection.support != "structural"
            or old.projection.diagnostics or new.projection.diagnostics or change_set.diagnostics):
        return change_set
    if whitespace_only_source_change(old_snippet, new_snippet):
        return replace(change_set, facts=())
    return change_set


def _with_occurrence_order_change(
    change_set: ChangeSet, old: OccurrenceRef, new: OccurrenceRef,
    old_evidence_id: str, new_evidence_id: str,
) -> ChangeSet:
    if old.inventory.entry.path_b64 != new.inventory.entry.path_b64 or change_set.diagnostics:
        return change_set
    old_order = tuple(item.object_key for item in old.inventory.occurrences)
    new_order = tuple(item.object_key for item in new.inventory.occurrences)
    if (len(old_order) < 2 or len(set(old_order)) != len(old_order)
            or set(old_order) != set(new_order) or old_order == new_order):
        return change_set
    before, after = old_order.index(change_set.object_key) + 1, new_order.index(change_set.object_key) + 1
    if before == after:
        return change_set
    fact = Change(
        fact_id=stable_id("fact", {"key": change_set.object_key, "taxonomy": "common.source.occurrence_order"})[:100],
        taxonomy_id="common.source.occurrence_order", component_path=(), action="modified", category="presentation",
        before=Value("present", str(before), (old_evidence_id,)),
        after=Value("present", str(after), (new_evidence_id,)),
        context_only=False, verification="verified",
    )
    return ChangeSet(change_set.object_key, change_set.object_type, change_set.operation,
                     (*change_set.facts, fact),
                     tuple(item for item in change_set.unsupported_families if item != "common.source.occurrence_order"),
                     change_set.diagnostics)


def _with_source_path_change(change_set: ChangeSet, old: OccurrenceRef, new: OccurrenceRef,
                             old_evidence_id: str, new_evidence_id: str) -> ChangeSet:
    if old.inventory.entry.path_b64 == new.inventory.entry.path_b64 or change_set.diagnostics:
        return change_set
    path_fact = Change(
        fact_id=stable_id("fact", {"key": change_set.object_key, "taxonomy": "common.source.path"})[:100],
        taxonomy_id="common.source.path", component_path=(), action="modified",
        category="relocation",
        before=Value("present", old.inventory.entry.path_display, (old_evidence_id,)),
        after=Value("present", new.inventory.entry.path_display, (new_evidence_id,)),
        context_only=False, verification="verified",
    )
    return ChangeSet(
        change_set.object_key, change_set.object_type, change_set.operation,
        (*change_set.facts, path_fact),
        tuple(item for item in change_set.unsupported_families if item != "common.source.path"),
        change_set.diagnostics,
    )


def _operation(delta: RawDelta) -> str:
    return {"A": "added", "D": "removed", "M": "modified", "R": "relocated", "C": "relocated"}.get(delta.status[:1], "unknown")


def _commit_rows(plan: RangePlan) -> list[dict[str, Any]]:
    by_sha = {item.sha: item for item in plan.commits}
    first_parent_chain: set[str] = set()
    cursor = plan.target_sha
    while cursor in by_sha and cursor not in first_parent_chain:
        first_parent_chain.add(cursor)
        parents = by_sha[cursor].parents
        cursor = parents[0] if parents else ""
    return [{
        "sha": item.sha, "parents": list(item.parents),
        "git_author_time": item.git_author_time, "git_committer_time": item.git_committer_time,
        "position": item.position, "delta_kind": "first_parent" if item.parents else "root",
        "integration_role": "target_first_parent_chain" if item.sha in first_parent_chain else "other_reachable",
    } for item in plan.commits]


def _history_events(plan: RangePlan) -> list[dict[str, Any]]:
    scoped_paths = {item.path for item in plan.scope_deltas}
    events: list[dict[str, Any]] = []
    for transition in plan.event_deltas:
        for position, delta in enumerate(transition.files):
            if delta.path not in scoped_paths:
                continue
            events.append({
                "event_id": stable_id("event", {"commit": transition.commit.sha, "path": delta.path_b64,
                                                 "position": position, "old_blob": delta.old_oid,
                                                 "new_blob": delta.new_oid})[:100],
                "commit_sha": transition.commit.sha, "parent_sha": transition.parent_sha,
                "object_key": None, "artifact_paths_b64": [delta.path_b64],
                "operation": _operation(delta), "categories": ["unknown"],
                "before_evidence_ids": [], "after_evidence_ids": [],
            })
    return events


def _stage_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("xb") as handle:
        os.chmod(temporary, 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def execute_analysis(args, config, store, plan: RangePlan, git: GitClient, *, mode: str = "AUTO", resume_run_id: str | None = None) -> int:
    from .cli import _finish

    run_id = resume_run_id or str(uuid.uuid4())
    planned_at = _now()
    first_analysis_started_at = _now()
    previous_ai_phase_started_at: str | None = None
    epoch = store.verify()["epoch"]
    git.pin_target(run_id, plan.target_sha)
    connection = store.connection()
    try:
        with connection:
            if resume_run_id:
                row = connection.execute("SELECT planned_at,analysis_started_at,ai_phase_started_at FROM runs WHERE run_id=? AND status='ANALYZING'", (run_id,)).fetchone()
                if row is None:
                    raise RuntimeError("pinned analysis run is no longer resumable")
                planned_at = row["planned_at"]
                first_analysis_started_at = row["analysis_started_at"] or planned_at
                previous_ai_phase_started_at = row["ai_phase_started_at"]
                connection.execute("UPDATE runs SET last_attempt_at=? WHERE run_id=?", (_now(), run_id))
            else:
                connection.execute(
                    "INSERT INTO runs(run_id,scope_hash,mode,base_sha,target_sha,epoch,analysis_generation,status,config_digest,fingerprint_json,originating_build_json,planned_at,last_attempt_at,analysis_started_at) VALUES (?,?,?,?,?,?,0,'ANALYZING',?,?,?,?,?,?)",
                    (run_id, config.scope_hash, mode, plan.base_sha, plan.target_sha, epoch, config.config_digest, json.dumps({"model": config.model.id, "prompt": "mail-commentary-tr-1.1"}, separators=(",", ":")), None, planned_at, planned_at, first_analysis_started_at),
                )
    finally:
        connection.close()

    analysis_started = first_analysis_started_at
    analysis_started_mono = time.monotonic()
    roots = {root.path: root.default_schema for root in config.scope.roots}
    changed_paths = {delta.path for delta in plan.scope_deltas}
    old_inventories = [] if plan.base_sha is None else inventory_revision(git, plan.base_sha, roots, max_file_bytes=config.parser.max_file_bytes, timeout_seconds=config.parser.worker_timeout_seconds, parse_paths=changed_paths)
    new_inventories = inventory_revision(git, plan.target_sha, roots, max_file_bytes=config.parser.max_file_bytes, timeout_seconds=config.parser.worker_timeout_seconds, parse_paths=changed_paths)
    old_index = _index(git, plan.base_sha, old_inventories, config.parser.max_file_bytes) if plan.base_sha else {}
    new_index = _index(git, plan.target_sha, new_inventories, config.parser.max_file_bytes)
    old_index, old_context_paths = _attach_table_contexts(git, plan.base_sha, roots, old_inventories, old_index, config.parser.max_file_bytes, config.parser.worker_timeout_seconds) if plan.base_sha else ({}, set())
    new_index, new_context_paths = _attach_table_contexts(git, plan.target_sha, roots, new_inventories, new_index, config.parser.max_file_bytes, config.parser.worker_timeout_seconds)
    incomplete_paths = _incomplete_changed_paths(old_inventories, changed_paths) | _incomplete_changed_paths(new_inventories, changed_paths)
    incomplete_paths |= _identity_incomplete_paths(old_inventories) | _identity_incomplete_paths(new_inventories)
    old_incomplete = (_incomplete_changed_paths(old_inventories, changed_paths)
                      | _identity_incomplete_paths(old_inventories)) - old_context_paths
    new_incomplete = (_incomplete_changed_paths(new_inventories, changed_paths)
                      | _identity_incomplete_paths(new_inventories)) - new_context_paths
    incomplete_paths = old_incomplete | new_incomplete
    scope_incomplete = bool(incomplete_paths)
    reordered_keys = _reordered_keys(old_inventories, new_inventories)
    changed_keys = sorted({key for key in old_index.keys() | new_index.keys() if _signature(old_index.get(key, [])) != _signature(new_index.get(key, []))} | reordered_keys)
    _verify_changed_refs(old_index, changed_keys, config.parser.worker_timeout_seconds)
    _verify_changed_refs(new_index, changed_keys, config.parser.worker_timeout_seconds)
    if len(changed_keys) > config.analysis.max_new_units_per_invocation:
        return _finish(args, mode="RUN", outcome="RETRY_PENDING", exit_code=11, error_code="UNIT_BUDGET", checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)

    system_prompt = (Path(__file__).parent / "prompts" / "mail_commentary.tr.txt").read_text(encoding="utf-8")
    response_schema = json.loads((Path(__file__).parent / "schemas" / "v5" / "ai-mail-commentary.schema.json").read_text(encoding="utf-8"))
    report_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"db-change-analyzer/v5/report/{run_id}"))
    evidence_registry: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    changes_by_key: dict[str, Any] = {}
    ai_comments_by_key: dict[str, list[dict[str, Any]]] = {}
    ai_unit_ids_by_key: dict[str, str] = {}
    ai_status_by_key: dict[str, str] = {}
    http_attempts = 0
    ai_phase_started_at: str | None = previous_ai_phase_started_at
    ai_phase_started_mono: float | None = None
    ai_phase_completed_at: str | None = None
    ai_phase_elapsed_ms: int | None = None
    returned_models_by_unit: dict[str, set[str]] = {}
    try:
        with LiteLLMClient(config.model) as model:
            for key in changed_keys:
                old_refs, new_refs = old_index.get(key, []), new_index.get(key, [])
                all_refs = [*old_refs, *new_refs]
                representative = (new_refs or old_refs)[0]
                old_evidence = [evidence for item in old_refs for evidence in (_evidence(item, plan.base_sha), *item.context_evidence)]
                new_evidence = [evidence for item in new_refs for evidence in (_evidence(item, plan.target_sha), *item.context_evidence)]
                local_evidence = [*old_evidence, *new_evidence]
                evidence_registry.extend(local_evidence)
                conflict = _conflicting_definitions(old_refs) or _conflicting_definitions(new_refs)
                secret = any(scan_secret(item["snippet"], config.security.extra_secret_patterns).blocked for item in local_evidence)
                mandatory_bytes = sum(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in local_evidence)
                diagnostics: list[str] = []
                assessments: list[dict[str, Any]] = []
                facts: list[dict[str, Any]] = []
                categories = ["unknown"]
                status = "limited" if any(item.projection.support != "structural" for item in all_refs) else "analyzed"
                sequence_change = (
                    sequence_start_value_only(old_evidence[0]["snippet"], new_evidence[0]["snippet"])
                    if old_refs and new_refs and not conflict
                    and old_refs[0].occurrence.object_type == new_refs[0].occurrence.object_type == "SEQUENCE"
                    and old_refs[0].projection.support == new_refs[0].projection.support == "structural"
                    and not old_refs[0].projection.diagnostics and not new_refs[0].projection.diagnostics
                    else None
                )
                if scope_incomplete:
                    status, diagnostics = "unresolved", ["INCOMPLETE_SOURCE_INVENTORY"]
                elif conflict:
                    status, diagnostics = "unresolved", ["CONFLICTING_DEFINITIONS"]
                elif secret:
                    status, diagnostics = "unresolved", ["SECRET_IN_MANDATORY_SOURCE"]
                elif mandatory_bytes > config.analysis.max_request_utf8_bytes:
                    status, diagnostics = "unresolved", ["MANDATORY_CONTEXT_EXCEEDS_BUDGET"]
                if not diagnostics and all_refs and representative.occurrence.object_type in supported_types():
                    old_ref = old_refs[0] if old_refs else None
                    new_ref = new_refs[0] if new_refs else None
                    old_scope = (stable_id("scope", {"revision": plan.base_sha, "key": key})[:100],) if not old_ref and plan.base_sha else ()
                    new_scope = (stable_id("scope", {"revision": plan.target_sha, "key": key})[:100],) if not new_ref else ()
                    if (old_ref and new_ref) or (not scope_incomplete and (old_scope or new_scope)):
                        change_set = compare_projections(
                            old_ref.projection if old_ref else None,
                            new_ref.projection if new_ref else None,
                            old_evidence_ids=tuple(item["evidence_id"] for item in old_evidence),
                            new_evidence_ids=tuple(item["evidence_id"] for item in new_evidence),
                            old_scope_evidence_ids=old_scope,
                            new_scope_evidence_ids=new_scope,
                        )
                        if old_ref and new_ref:
                            change_set = _with_format_override(
                                change_set, old_ref, new_ref,
                                old_evidence[0]["snippet"], new_evidence[0]["snippet"],
                            )
                            change_set = _with_source_text_change(
                                change_set, old_ref, new_ref, old_evidence[0], new_evidence[0],
                            )
                            if key in reordered_keys:
                                change_set = _with_occurrence_order_change(
                                    change_set, old_ref, new_ref,
                                    old_evidence[0]["evidence_id"], new_evidence[0]["evidence_id"],
                                )
                            if len(old_refs) == len(new_refs) == 1:
                                change_set = _with_source_path_change(
                                    change_set, old_ref, new_ref,
                                    old_evidence[0]["evidence_id"], new_evidence[0]["evidence_id"],
                                )
                        changes_by_key[key] = change_set
                if not diagnostics and old_refs and new_refs and old_refs[0].projection.support == new_refs[0].projection.support == "structural" and representative.occurrence.object_type in supported_types():
                    facts.extend(_canonical_projection_facts(
                        old_refs[0], new_refs[0], tuple(item["evidence_id"] for item in old_evidence),
                        tuple(item["evidence_id"] for item in new_evidence),
                        run_id=run_id, base_sha=plan.base_sha, target_sha=plan.target_sha,
                        changes=changes_by_key.get(key),
                    ))
                    if facts:
                        categories = sorted({item["category"] for item in facts})
                if not diagnostics and sequence_change:
                    before, after = sequence_change
                    categories = ["sequence_observed_value"]
                    if not any(item["property"] == "START WITH" for item in facts):
                        facts.append({
                        "fact_id": stable_id("fact", {"key": key, "base": plan.base_sha, "target": plan.target_sha, "property": "START WITH"})[:100],
                        "property": "START WITH", "before": before, "after": after,
                        "category": "sequence_observed_value",
                        "evidence_ids": [item["evidence_id"] for item in local_evidence],
                        "source_pair": {"old_revision": plan.base_sha, "new_revision": plan.target_sha},
                        "event_ids": [], "view_tags": ["net"],
                        })
                elif (not diagnostics and key in changes_by_key and old_refs and new_refs
                      and any(change.taxonomy_id not in {"common.source.format", "common.source.occurrence_order", "common.source.path", "common.source.text"}
                              for change in changes_by_key[key].facts)):
                    unit_id = stable_id("unit", {"run": run_id, "key": key})[:100]
                    unit_input = build_source_unit_input(
                        report_id=report_id, unit_id=unit_id, changes=changes_by_key[key],
                        old_evidence=old_evidence, new_evidence=new_evidence,
                        base_sha=plan.base_sha, target_sha=plan.target_sha,
                        extra_secret_patterns=config.security.extra_secret_patterns,
                    )
                    if unit_input["facts"]:
                        ai_unit_ids_by_key[key] = unit_id
                        response: dict[str, Any] | None = None
                        comments: list[dict[str, Any]] = []
                        gate_errors: list[str] = []
                        attempts_used = 0
                        stored_unit = None
                        if resume_run_id:
                            existing_connection = store.connection()
                            try:
                                stored_unit = existing_connection.execute(
                                    "SELECT request_digest,status,result_json,returned_models_json FROM units WHERE run_id=? AND analysis_generation=0 AND unit_id=?",
                                    (run_id, unit_id),
                                ).fetchone()
                            finally:
                                existing_connection.close()
                        if stored_unit is not None and stored_unit["request_digest"] == unit_input["input_digest"]:
                            returned_models_by_unit[unit_id] = set(json.loads(stored_unit["returned_models_json"]))
                            if stored_unit["status"] == "VALIDATED":
                                response, comments = accept_commentary(
                                    stored_unit["result_json"], unit_input,
                                    prompt_json=config.model.output_mode == "prompt_json",
                                    extra_secret_patterns=config.security.extra_secret_patterns,
                                )
                        for attempt in range(0 if response is not None else 2):
                            if ai_phase_started_mono is None:
                                if ai_phase_started_at is None:
                                    ai_phase_started_at = _now()
                                    phase_connection = store.connection()
                                    try:
                                        with phase_connection:
                                            phase_connection.execute("UPDATE runs SET ai_phase_started_at=? WHERE run_id=? AND ai_phase_started_at IS NULL", (ai_phase_started_at, run_id))
                                    finally:
                                        phase_connection.close()
                                ai_phase_started_mono = time.monotonic()
                            attempts_used += 1
                            http_attempts += 1
                            reply = model.complete(
                                api_key=read_secret("LITELLM_API_KEY").get_secret_value(),
                                system_message=system_prompt if attempt == 0 else system_prompt + "\nÖnceki yanıt geçersizdi; yalnız sözleşmeye uygun JSON döndür.",
                                user_payload=unit_input, response_schema=response_schema,
                                output_tokens=config.analysis.output_tokens,
                            )
                            if reply.returned_model:
                                returned_models_by_unit.setdefault(unit_id, set()).add(reply.returned_model)
                            try:
                                response, comments = accept_commentary(
                                    reply.content, unit_input,
                                    prompt_json=config.model.output_mode == "prompt_json",
                                    extra_secret_patterns=config.security.extra_secret_patterns,
                                )
                                break
                            except ResponseValidationError as exc:
                                gate_errors.extend(exc.codes)
                        if ai_phase_started_mono is not None:
                            ai_phase_completed_at = _now()
                            ai_phase_elapsed_ms = int((datetime.fromisoformat(ai_phase_completed_at) - datetime.fromisoformat(ai_phase_started_at)).total_seconds() * 1000) if previous_ai_phase_started_at else int((time.monotonic() - ai_phase_started_mono) * 1000)
                        ai_comments_by_key[key] = comments
                        ai_status_by_key[key] = "displayed" if comments else "withheld"
                        connection = store.connection()
                        try:
                            with connection:
                                connection.execute(
                                    "INSERT INTO units(run_id,analysis_generation,unit_id,canonical_sources_json,alias_events_json,context_digest,request_digest,status,result_json,diagnostics_json,http_attempts,returned_models_json) VALUES (?,0,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,analysis_generation,unit_id) DO UPDATE SET canonical_sources_json=excluded.canonical_sources_json,context_digest=excluded.context_digest,request_digest=excluded.request_digest,status=excluded.status,result_json=excluded.result_json,diagnostics_json=excluded.diagnostics_json,http_attempts=units.http_attempts+excluded.http_attempts,returned_models_json=excluded.returned_models_json",
                                    (run_id, unit_id, json.dumps(local_evidence), "[]", unit_input["input_digest"],
                                     unit_input["input_digest"], "VALIDATED" if response is not None else "INVALID",
                                     json.dumps(response, ensure_ascii=False) if response is not None else None,
                                     json.dumps(sorted(set(gate_errors))), attempts_used,
                                     json.dumps(sorted(returned_models_by_unit.get(unit_id, set())))),
                                )
                        finally:
                            connection.close()
                        if response is None:
                            raise ResponseValidationError(*gate_errors)
                objects.append({
                    "identity": {
                        "object_key": key, "namespace": key.split("|", 1)[0], "schema_name": representative.occurrence.raw_schema,
                        "name": representative.occurrence.raw_name.upper() if not representative.occurrence.name_quoted else representative.occurrence.raw_name,
                        "object_type": representative.occurrence.object_type, "raw_schema": representative.occurrence.raw_schema,
                        "raw_name": representative.occurrence.raw_name, "schema_quoted": representative.occurrence.schema_quoted,
                        "name_quoted": representative.occurrence.name_quoted, "identity_confidence": "known", "parent_key": None, "routine_signature": None,
                    },
                    "net_operation": "unknown" if scope_incomplete and (not old_refs or not new_refs) else "added" if not old_refs else "removed" if not new_refs else "modified",
                    "categories": categories, "status": status,
                    "parser_level": "structural" if status == "analyzed" else "limited" if status == "limited" else "unresolved",
                    "old_evidence_ids": [item["evidence_id"] for item in old_evidence],
                    "new_evidence_ids": [item["evidence_id"] for item in new_evidence],
                    "facts": facts, "assessments": assessments, "diagnostics": diagnostics,
                })
    except ModelTransportError as exc:
        return _finish(args, mode="RUN", outcome="AI_TRANSPORT_OR_AUTH", exit_code=30, error_code=exc.code, checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)
    except ResponseValidationError as exc:
        return _finish(args, mode="RUN", outcome="AI_CONTEXT_INVALID", exit_code=31, error_code=exc.codes[0], checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)

    connection = store.connection()
    try:
        total_http_attempts = connection.execute(
            "SELECT COALESCE(SUM(http_attempts),0) FROM units WHERE run_id=? AND analysis_generation=0",
            (run_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    if previous_ai_phase_started_at and ai_phase_completed_at is None:
        ai_phase_completed_at = _now()
        ai_phase_elapsed_ms = int((datetime.fromisoformat(ai_phase_completed_at) - datetime.fromisoformat(previous_ai_phase_started_at)).total_seconds() * 1000)
    quality = "blocked" if scope_incomplete or any(item["status"] == "unresolved" for item in objects) else "limited" if any(item["status"] == "limited" for item in objects) else "complete"
    commits = _commit_rows(plan)
    unique_deltas = {item.path: item for item in plan.scope_deltas}
    scoped_net_deltas = {item.path: item for item in plan.net_deltas if item.path in unique_deltas}
    inventory_by_path: dict[bytes, list[FileInventory]] = defaultdict(list)
    for inventory in (*old_inventories, *new_inventories):
        inventory_by_path[inventory.entry.path].append(inventory)
    artifacts = []
    for item in unique_deltas.values():
        net = scoped_net_deltas.get(item.path)
        related = inventory_by_path[item.path]
        diagnostics = sorted({code for inventory in related for code in inventory.diagnostics} | {code for inventory in related for projection in inventory.projections for code in projection.diagnostics})
        parse_status = "unresolved" if item.path in incomplete_paths else "parsed" if related else "metadata_only"
        if parse_status == "parsed" and item.path in old_context_paths | new_context_paths:
            diagnostics = [code for code in diagnostics if code != "UNRESOLVED_PREFIX_OR_ARTIFACT"]
        artifacts.append({"path_display": item.path_display, "path_b64": item.path_b64,
                          "net_operation": _operation(net) if net else "unchanged",
                          "old_mode": net.old_mode or None if net else None,
                          "new_mode": net.new_mode or None if net else None,
                          "old_blob": net.old_oid if net and set(net.old_oid) != {"0"} else None,
                          "new_blob": net.new_oid if net and set(net.new_oid) != {"0"} else None,
                          "known_objects": None, "parse_status": parse_status,
                          "diagnostic_codes": diagnostics, "assessments": []})
    for path in sorted(incomplete_paths - set(unique_deltas)):
        related = inventory_by_path[path]
        if not related:
            continue
        entry = related[-1].entry
        diagnostics = sorted({code for inventory in related for code in inventory.diagnostics})
        artifacts.append({
            "path_display": entry.path_display, "path_b64": entry.path_b64,
            "net_operation": "unchanged", "old_mode": entry.mode,
            "new_mode": entry.mode, "old_blob": entry.oid if entry.kind == "blob" else None,
            "new_blob": entry.oid if entry.kind == "blob" else None,
            "known_objects": None, "parse_status": "unresolved",
            "diagnostic_codes": diagnostics, "assessments": [],
        })
    events = _history_events(plan)
    counts = {"net_files": len(scoped_net_deltas), "history_files": len(unique_deltas), "net_known_objects": len(changed_keys), "history_known_objects": len(changed_keys), "change_events": len(events), "ai_units": len(ai_unit_ids_by_key), "ai_http_attempts": total_http_attempts, "analyzed_objects": sum(item["status"] == "analyzed" for item in objects), "limited_objects": sum(item["status"] == "limited" for item in objects), "unresolved_objects": sum(item["status"] == "unresolved" for item in objects), "unknown_artifacts": len(incomplete_paths), "has_unknown_object_count": scope_incomplete}
    sequence_only = bool(objects) and all(item["categories"] == ["sequence_observed_value"] for item in objects)
    summary = (
        "Git kaynak kapsamı tam ayrıştırılamadı; nesne sayısı ve ekleme/çıkarma işlemleri doğrulanamadı."
        if scope_incomplete else
        f"Git kaynağında {len(objects)} sequence için yalnız START WITH değeri değişti; diğer tanım metni aynı. Canlı veritabanı etkisi doğrulanmadı."
        if sequence_only else
        f"Git snapshot'ında {len(changed_keys)} nesne kaynak geçişi incelendi."
    )
    limitations = ["Analiz Git snapshot kaynaklarıyla sınırlıdır; canlı veritabanı doğrulaması değildir."]
    if scope_incomplete:
        limitations.append("Değişen kaynaklardan bazıları tam ayrıştırılamadı; bilinmeyen nesneler ve işlemler olabilir.")
    if len(plan.commits) > 1 or len(events) > len(unique_deltas):
        limitations.append(
            f"İzlenen aralıkta {len(plan.commits)} Git commit ve {len(events)} dosya geçişi kaydedildi; "
            "nesne karşılaştırması başlangıç ve hedef snapshot arasındaki net farkı gösterir."
        )
    if sequence_only:
        limitations.append("START WITH kaynak farkı, mevcut NEXTVAL veya dağıtım sonucu hakkında tek başına kanıt değildir.")
    analysis_completed = _now()
    analysis_elapsed_ms = int((datetime.fromisoformat(analysis_completed) - datetime.fromisoformat(analysis_started)).total_seconds() * 1000) if resume_run_id else int((time.monotonic() - analysis_started_mono) * 1000)
    returned_models = sorted({label for labels in returned_models_by_unit.values() for label in labels})
    report = {
        "schema_version": "1.0", "synthetic": False, "report_id": report_id, "supersedes_report_id": None,
        "run": {"run_id": run_id, "mode": mode, "repository_id": config.repository.id, "branch": config.repository.branch, "scope_hash": config.scope_hash, "epoch": epoch, "base_sha": plan.base_sha, "target_sha": plan.target_sha, "planned_at": planned_at, "analysis_started_at": analysis_started, "analysis_completed_at": analysis_completed, "analysis_generation": 0, "observed_git_at": _now(), "snapshot_captured_at": None, "actual_db_change_at": None, "originating_analyzer_build": None, "sync_build": None},
        "quality": quality, "automatic_commit_eligible": quality != "blocked", "summary_tr": summary, "overall_ai_risk": "unknown",
        "counts": counts, "versions": {"analyzer": "0.1.0", "grammar_commit": config.parser.grammar_commit, "parser_adapter": "1.0", "prompt": "mail-commentary-tr-1.1", "unit_response_schema": "1.0", "report_schema": "1.0", "configured_model": config.model.id, "returned_model": returned_models[0] if len(returned_models) == 1 else None, "resolved_model_version": None, "config_digest": config.config_digest},
        "commits": commits, "events": events, "artifacts": artifacts, "evidence_registry": evidence_registry, "objects": objects,
        "limitations": limitations,
    }
    try:
        rendered = render_report(report, max_object_details=config.reports.mail_max_object_details)
        view = build_mail_view(
            report, changes_by_key, analysis_elapsed_ms=analysis_elapsed_ms,
            analysis_duration_basis="timestamp_difference" if resume_run_id else "monotonic",
            ai_phase_started_at=ai_phase_started_at, ai_phase_completed_at=ai_phase_completed_at,
            ai_phase_elapsed_ms=ai_phase_elapsed_ms, returned_models_by_unit=returned_models_by_unit,
            extra_secret_patterns=config.security.extra_secret_patterns,
            ai_comments_by_key=ai_comments_by_key, ai_unit_ids_by_key=ai_unit_ids_by_key,
            ai_status_by_key=ai_status_by_key,
        )
        notification_id = str(uuid.uuid4())
        message_id = f"<{notification_id}@{config.smtp.message_id_domain}>"
        v5 = render_v5_view(
            view, sender=config.smtp.sender, recipients=config.smtp.recipients,
            message_id=message_id, date=datetime.now(UTC),
            mime_limit=config.reports.mail_max_bytes,
        )
        manifest = build_render_manifest(view, v5, source_report_sha256=rendered.sha256)
        view_json = json.dumps(view, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        manifest_json = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (ReportError, V5RenderError, ValueError) as exc:
        return _finish(args, mode="RUN", outcome="SAFETY_POLICY_VIOLATION", exit_code=50, error_code=str(exc), checkpoint_before=plan.base_sha, checkpoint_after=plan.base_sha)
    emit_dir = Path(args.emit_dir or config.reports.emit_dir).resolve()
    outputs = {
        emit_dir / "report.json": rendered.canonical_json + b"\n",
        emit_dir / "report.html": v5.html,
        emit_dir / "report.txt": v5.text,
        emit_dir / "mail-view.json": view_json + b"\n",
        emit_dir / "render-manifest.json": manifest_json + b"\n",
    }
    staged: list[tuple[Path, Path]] = []
    try:
        for destination, data in outputs.items():
            staged.append((_stage_file(destination, data), destination))
    except Exception:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
        raise
    connection = store.connection()
    try:
        with connection:
            connection.execute("INSERT INTO reports(report_id,run_id,canonical_json,content_sha256,html,text,rendered_version,quality,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (report_id, run_id, rendered.canonical_json, rendered.sha256, v5.html, v5.text, "v5.0", quality, _now()))
            connection.execute("INSERT INTO report_render_sidecars(report_id,render_generation,source_report_sha256,mail_view_json,mail_view_sha256,manifest_json,manifest_sha256,html,text,mime,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (report_id, 0, rendered.sha256, view_json, v5.view_sha256, manifest_json, hashlib.sha256(manifest_json).hexdigest(), v5.html, v5.text, v5.mime, _now()))
            if mode != "MANUAL":
                insert_notification(connection, notification_id=notification_id, report_id=report_id, generation=0,
                                    recipients=config.smtp.recipients, message_id=message_id,
                                    mime_bytes=v5.mime, mime_sha256=manifest["mime_sha256"])
            connection.execute("UPDATE runs SET report_id=?,status='REPORTED',quality=? WHERE run_id=?", (report_id, quality, run_id))
    except Exception:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    emitted = []
    for temporary, destination in staged:
        os.replace(temporary, destination)
        emitted.append(str(destination))
    if mode == "MANUAL":
        connection = store.connection()
        try:
            with connection:
                connection.execute("UPDATE runs SET status='CLOSED' WHERE run_id=?", (run_id,))
        finally:
            connection.close()
        return _finish(args, mode="MANUAL", outcome="MANUAL_COMPLETE", emitted_files=emitted, checkpoint_before=store.verify()["checkpoint_sha"], checkpoint_after=store.verify()["checkpoint_sha"], run_id=run_id, report_id=report_id, report_sha256=rendered.sha256, quality=quality, ai_http_attempts=http_attempts)
    connection = store.connection()
    try:
        def finalize_delivery(transaction, delivery_status: str) -> None:
            if delivery_status == "ACCEPTED":
                finalize_auto_delivery(transaction, notification_id, config.scope_hash)

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
