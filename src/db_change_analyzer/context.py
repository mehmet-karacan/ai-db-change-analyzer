from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from .dependencies import DependencyEdge, DependencyGraph, Relation


_RELATION_PRIORITY = {
    Relation.TYPE_REF: 0,
    Relation.SPEC_BODY: 0,
    Relation.TRIGGER_ON: 1,
    Relation.FK_REF: 1,
    Relation.WRITE: 2,
    Relation.READ: 3,
    Relation.CALL: 4,
    Relation.SYNONYM_TO: 5,
}


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    object_key: str
    evidence: dict[str, Any]
    relation: Relation


@dataclass(frozen=True, slots=True)
class ContextSelection:
    evidence_registry: tuple[dict[str, Any], ...]
    dependency_edges: tuple[dict[str, object], ...]
    candidate_count: int
    provided_count: int
    omissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SerializedRequest:
    payload: bytes
    sha256: str
    utf8_bytes: int
    estimated_input_tokens: int
    fits_byte_budget: bool
    fits_context_window: bool | None


def select_dependency_context(
    changed_keys: Iterable[str],
    old_graph: DependencyGraph,
    new_graph: DependencyGraph,
    evidence_by_revision_and_key: dict[tuple[str, str], dict[str, Any]],
    *,
    depth: int = 1,
    max_neighbors_per_side: int = 8,
) -> ContextSelection:
    keys = tuple(sorted(set(changed_keys)))
    # Removed consumers exist only in the old reverse graph; new forward/reverse
    # references describe the target snapshot. Keep both evidence planes.
    tagged = [("old_reverse", edge, old_graph.revision) for edge in old_graph.neighbors(keys, depth=depth, reverse=True)]
    tagged += [("new_outgoing", edge, new_graph.revision) for edge in new_graph.neighbors(keys, depth=depth)]
    tagged += [("new_reverse", edge, new_graph.revision) for edge in new_graph.neighbors(keys, depth=depth, reverse=True)]
    tagged.sort(key=lambda item: (_RELATION_PRIORITY[item[1].relation], item[1].to_candidate, item[1].from_object, item[0]))
    candidate_count = len(tagged)
    selected: list[tuple[str, DependencyEdge, str]] = []
    side_counts: dict[str, int] = {"old_reverse": 0, "new_outgoing": 0, "new_reverse": 0}
    for item in tagged:
        side = item[0]
        if side_counts[side] >= max_neighbors_per_side:
            continue
        side_counts[side] += 1
        selected.append(item)

    registry: list[dict[str, Any]] = []
    edge_records: list[dict[str, object]] = []
    seen_evidence: set[str] = set()
    for side, edge, revision in selected:
        related = edge.from_object if side.endswith("reverse") else edge.to_candidate
        evidence = evidence_by_revision_and_key.get((revision, related))
        if evidence is not None:
            identity = json.dumps(evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if identity not in seen_evidence:
                seen_evidence.add(identity)
                registry.append(evidence)
        record = edge.as_dict()
        record["context_side"] = side
        edge_records.append(record)
    omissions: list[str] = []
    if candidate_count > len(selected):
        omissions.append(f"DEPENDENCY_NEIGHBOR_LIMIT:{candidate_count - len(selected)}")
    return ContextSelection(tuple(registry), tuple(edge_records), candidate_count, len(selected), tuple(omissions))


def serialize_request(
    *,
    system_message: str,
    user_payload: dict[str, Any],
    response_format: dict[str, Any],
    max_request_utf8_bytes: int,
    output_tokens: int,
    safety_tokens: int,
    verified_context_window_tokens: int,
) -> SerializedRequest:
    body = {
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": json.dumps(user_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))},
        ],
        "response_format": response_format,
    }
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    byte_count = len(payload)
    fits_context = None if verified_context_window_tokens <= 0 else byte_count + output_tokens + safety_tokens <= verified_context_window_tokens
    return SerializedRequest(
        payload,
        hashlib.sha256(payload).hexdigest(),
        byte_count,
        byte_count,
        byte_count <= max_request_utf8_bytes,
        fits_context,
    )


def prune_optional_evidence(
    mandatory: Iterable[dict[str, Any]],
    optional: Iterable[dict[str, Any]],
    request_factory,
    *,
    max_request_utf8_bytes: int,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    required = list(mandatory)
    extras = list(optional)
    while extras and len(request_factory([*required, *extras])) > max_request_utf8_bytes:
        extras.pop()
    payload_size = len(request_factory(required))
    if payload_size > max_request_utf8_bytes:
        raise ValueError("MANDATORY_CONTEXT_EXCEEDS_BUDGET")
    omitted = len(list(optional)) - len(extras)
    return [*required, *extras], ((f"OPTIONAL_CONTEXT_BYTE_LIMIT:{omitted}",) if omitted else ())
