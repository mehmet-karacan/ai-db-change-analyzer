from __future__ import annotations

import json

import pytest

from db_change_analyzer.context import prune_optional_evidence, select_dependency_context, serialize_request
from db_change_analyzer.dependencies import Relation, Resolution, SnapshotObject, build_dependency_graph, resolve_synonym


def obj(key: str, kind: str, source: str, *, schema: str = "APP") -> SnapshotObject:
    return SnapshotObject(key, schema, key.rsplit("|", 1)[-1], kind, source, (f"ev-{key}",))


def test_static_graph_masks_literals_and_ctes_and_resolves_repository_symbols() -> None:
    table = obj("SCHEMA|APP|TABLE|CUSTOMER", "TABLE", "CREATE TABLE customer(id NUMBER)")
    view = obj(
        "SCHEMA|APP|VIEW|V_CUSTOMER",
        "VIEW",
        "CREATE VIEW v_customer AS WITH fake AS (SELECT 'from SECRET' x FROM customer) SELECT * FROM fake",
    )
    graph = build_dependency_graph("a" * 40, [table, view])
    reads = [edge for edge in graph.edges if edge.relation == Relation.READ]
    assert [(edge.to_candidate, edge.resolution) for edge in reads] == [(table.object_key, Resolution.RESOLVED_STATIC)]


def test_old_reverse_consumers_and_new_neighbors_are_both_selected() -> None:
    target = obj("SCHEMA|APP|TABLE|T", "TABLE", "CREATE TABLE t(id NUMBER)")
    old_consumer = obj("SCHEMA|APP|VIEW|OLD_V", "VIEW", "CREATE VIEW old_v AS SELECT * FROM t")
    new_consumer = obj("SCHEMA|APP|VIEW|NEW_V", "VIEW", "CREATE VIEW new_v AS SELECT * FROM t")
    old = build_dependency_graph("1" * 40, [target, old_consumer])
    new = build_dependency_graph("2" * 40, [target, new_consumer])
    evidence = {
        (old.revision, old_consumer.object_key): {"evidence_id": "old"},
        (new.revision, new_consumer.object_key): {"evidence_id": "new"},
    }
    selected = select_dependency_context([target.object_key], old, new, evidence)
    assert {item["evidence_id"] for item in selected.evidence_registry} == {"old", "new"}
    assert {edge["context_side"] for edge in selected.dependency_edges} >= {"old_reverse", "new_reverse"}


def test_synonym_resolution_has_cycle_and_hop_boundaries() -> None:
    a = obj("SCHEMA|APP|SYNONYM|A", "SYNONYM", "CREATE SYNONYM a FOR app.b")
    b = obj("SCHEMA|APP|SYNONYM|B", "SYNONYM", "CREATE SYNONYM b FOR app.a")
    graph = build_dependency_graph("3" * 40, [a, b])
    resolved, trace = resolve_synonym(graph, a.object_key)
    assert resolved is None
    assert trace[-1] == "SYNONYM_CYCLE"


def test_serialized_budget_counts_system_user_and_schema_bytes() -> None:
    request = serialize_request(
        system_message="Türkçe güvenli sistem",
        user_payload={"unit_id": "u1", "source": "ş" * 20},
        response_format={"type": "json_schema", "schema": {"type": "object"}},
        max_request_utf8_bytes=1000,
        output_tokens=100,
        safety_tokens=50,
        verified_context_window_tokens=2000,
    )
    decoded = json.loads(request.payload)
    assert decoded["messages"][0]["content"] == "Türkçe güvenli sistem"
    assert request.utf8_bytes == len(request.payload)
    assert request.fits_byte_budget is True
    assert request.fits_context_window is True


def test_optional_context_is_removed_but_mandatory_is_never_truncated() -> None:
    mandatory = [{"evidence_id": "m", "snippet": "changed hunk"}]
    optional = [{"evidence_id": str(index), "snippet": "x" * 100} for index in range(5)]
    factory = lambda registry: json.dumps({"evidence_registry": registry}, separators=(",", ":")).encode()
    selected, omissions = prune_optional_evidence(mandatory, optional, factory, max_request_utf8_bytes=180)
    assert selected[0] == mandatory[0]
    assert omissions and omissions[0].startswith("OPTIONAL_CONTEXT_BYTE_LIMIT")
    with pytest.raises(ValueError, match="MANDATORY_CONTEXT_EXCEEDS_BUDGET"):
        prune_optional_evidence([{"snippet": "z" * 500}], [], factory, max_request_utf8_bytes=100)
