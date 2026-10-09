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


def test_repaired_caller_remains_history_only_when_target_has_no_reference() -> None:
    target = obj("SCHEMA|APP|TABLE|T", "TABLE", "CREATE TABLE t(id NUMBER)")
    replacement = obj("SCHEMA|APP|TABLE|U", "TABLE", "CREATE TABLE u(id NUMBER)")
    old_caller = obj("SCHEMA|APP|VIEW|OLD_V", "VIEW", "CREATE VIEW old_v AS SELECT * FROM t")
    repaired_caller = obj("SCHEMA|APP|VIEW|NEW_V", "VIEW", "CREATE VIEW new_v AS SELECT * FROM u")
    old = build_dependency_graph("c" * 40, [target, old_caller])
    new = build_dependency_graph("d" * 40, [target, replacement, repaired_caller])
    selected = select_dependency_context(
        [target.object_key], old, new,
        {
            (old.revision, old_caller.object_key): {"evidence_id": "old"},
            (new.revision, repaired_caller.object_key): {"evidence_id": "new"},
        },
    )
    old_edges = [edge for edge in selected.dependency_edges if edge["from_object"] == old_caller.object_key]
    assert old_edges and {edge["context_side"] for edge in old_edges} == {"old_reverse"}
    assert not any(edge["context_side"] == "new_reverse" for edge in selected.dependency_edges)


def test_multiple_callers_keep_old_and_current_reverse_context_separate() -> None:
    target = obj("SCHEMA|APP|TABLE|T", "TABLE", "CREATE TABLE t(id NUMBER)")
    replacement = obj("SCHEMA|APP|TABLE|U", "TABLE", "CREATE TABLE u(id NUMBER)")
    repaired = obj("SCHEMA|APP|VIEW|REPAIRED", "VIEW", "CREATE VIEW repaired AS SELECT * FROM u")
    still_using = obj("SCHEMA|APP|VIEW|STILL_USING", "VIEW", "CREATE VIEW still_using AS SELECT * FROM t")
    old = build_dependency_graph("e" * 40, [target, obj("SCHEMA|APP|VIEW|REPAIRED", "VIEW", "CREATE VIEW repaired AS SELECT * FROM t"), still_using])
    new = build_dependency_graph("f" * 40, [target, replacement, repaired, still_using])
    selected = select_dependency_context(
        [target.object_key], old, new,
        {
            (old.revision, "SCHEMA|APP|VIEW|REPAIRED"): {"evidence_id": "repaired-old"},
            (old.revision, still_using.object_key): {"evidence_id": "still-old"},
            (new.revision, repaired.object_key): {"evidence_id": "repaired-new"},
            (new.revision, still_using.object_key): {"evidence_id": "still-new"},
        },
    )
    repaired_edges = [edge for edge in selected.dependency_edges if edge["from_object"].endswith("|REPAIRED")]
    still_edges = [edge for edge in selected.dependency_edges if edge["from_object"] == still_using.object_key]
    assert {edge["context_side"] for edge in repaired_edges} == {"old_reverse"}
    assert {edge["context_side"] for edge in still_edges} == {"old_reverse", "new_reverse"}


def test_qualified_package_calls_are_candidate_edges_without_overload_claim() -> None:
    package_spec = obj("SCHEMA|APP|PACKAGE_SPEC|P", "PACKAGE_SPEC", "CREATE PACKAGE P AS PROCEDURE RUN(P_ID NUMBER); END P;")
    package_body = obj("SCHEMA|APP|PACKAGE_BODY|P", "PACKAGE_BODY", "CREATE PACKAGE BODY P AS BEGIN NULL; END P;")
    caller = obj(
        "SCHEMA|APP|PROCEDURE|CALLER", "PROCEDURE",
        "CREATE PROCEDURE CALLER IS BEGIN APP.P.RUN(1); DBMS_OUTPUT.PUT_LINE('ignored'); END;",
    )
    graph = build_dependency_graph("4" * 40, [package_spec, package_body, caller])
    calls = [edge for edge in graph.edges if edge.relation == Relation.CALL]
    assert len(calls) == 2
    package_calls = [edge for edge in calls if edge.resolution == Resolution.CANDIDATE]
    assert len(package_calls) == 1
    assert package_spec.object_key in package_calls[0].to_candidate
    assert package_body.object_key in package_calls[0].to_candidate
    assert package_calls[0].reason.endswith("binding=arity_name_compatible_type_unresolved")
    assert any(edge.resolution == Resolution.UNRESOLVED and edge.to_candidate == "APP.DBMS_OUTPUT" for edge in calls)


def test_package_call_binding_keeps_named_defaults_and_incompatible_arity_distinct() -> None:
    package_spec = obj(
        "SCHEMA|APP|PACKAGE_SPEC|P", "PACKAGE_SPEC",
        "CREATE PACKAGE P AS PROCEDURE RUN(P_ID NUMBER, P_LABEL VARCHAR2 DEFAULT 'x'); END P;",
    )
    caller = obj(
        "SCHEMA|APP|PROCEDURE|CALLER", "PROCEDURE",
        "CREATE PROCEDURE CALLER IS BEGIN APP.P.RUN(P_ID => 1); APP.P.RUN(1, 2, 3); END;",
    )
    graph = build_dependency_graph("6" * 40, [package_spec, caller])
    reasons = [edge.reason for edge in graph.edges if edge.relation == Relation.CALL]
    assert any(reason.endswith("binding=arity_name_compatible_type_unresolved") for reason in reasons)
    assert any(reason.endswith("binding=incompatible_arity_or_names") for reason in reasons)


def test_overloaded_package_call_remains_an_overload_candidate() -> None:
    package_spec = obj(
        "SCHEMA|APP|PACKAGE_SPEC|P", "PACKAGE_SPEC",
        "CREATE PACKAGE P AS PROCEDURE RUN(P_ID NUMBER); PROCEDURE RUN(P_ID VARCHAR2); END P;",
    )
    caller = obj("SCHEMA|APP|PROCEDURE|CALLER", "PROCEDURE", "CREATE PROCEDURE CALLER IS BEGIN APP.P.RUN(1); END;")
    graph = build_dependency_graph("7" * 40, [package_spec, caller])
    calls = [edge for edge in graph.edges if edge.relation == Relation.CALL]
    assert len(calls) == 1
    assert calls[0].reason.endswith("binding=overload_candidate")


def test_candidate_package_call_is_available_in_reverse_impact_context() -> None:
    package_spec = obj("SCHEMA|APP|PACKAGE_SPEC|P", "PACKAGE_SPEC", "CREATE PACKAGE P AS PROCEDURE RUN; END P;")
    package_body = obj("SCHEMA|APP|PACKAGE_BODY|P", "PACKAGE_BODY", "CREATE PACKAGE BODY P AS BEGIN NULL; END P;")
    caller = obj("SCHEMA|APP|PROCEDURE|CALLER", "PROCEDURE", "CREATE PROCEDURE CALLER IS BEGIN APP.P.RUN(); END;")
    graph = build_dependency_graph("5" * 40, [package_spec, package_body, caller])
    selected = select_dependency_context(
        [package_spec.object_key], graph, graph,
        {(graph.revision, caller.object_key): {"evidence_id": "caller-evidence"}},
    )
    assert any(
        edge["relation"] == Relation.CALL.value
        and edge["resolution"] == Resolution.CANDIDATE.value
        and edge["from_object"] == caller.object_key
        and edge["context_side"] == "new_reverse"
        for edge in selected.dependency_edges
    )


def test_schema_qualified_standalone_routine_is_a_static_call_target() -> None:
    routine = obj(
        "SCHEMA|APP|PROCEDURE|RUN_JOB", "PROCEDURE",
        "CREATE PROCEDURE run_job(p_id NUMBER) IS BEGIN NULL; END;",
    )
    caller = obj(
        "SCHEMA|APP|PROCEDURE|CALLER", "PROCEDURE",
        "CREATE PROCEDURE caller IS BEGIN APP.RUN_JOB(1); END;",
    )
    graph = build_dependency_graph("8" * 40, [routine, caller])
    calls = [edge for edge in graph.edges if edge.relation == Relation.CALL]
    assert len(calls) == 1
    assert calls[0].to_candidate == routine.object_key
    assert calls[0].resolution == Resolution.RESOLVED_STATIC
    assert calls[0].reason == "standalone routine call candidate; binding=arity_name_compatible_type_unresolved"


def test_standalone_routine_binding_distinguishes_optional_and_incompatible_arguments() -> None:
    routine = obj(
        "SCHEMA|APP|PROCEDURE|RUN_JOB", "PROCEDURE",
        "CREATE PROCEDURE run_job(p_id NUMBER, p_label VARCHAR2 DEFAULT 'x') IS BEGIN NULL; END;",
    )
    caller = obj(
        "SCHEMA|APP|PROCEDURE|CALLER", "PROCEDURE",
        "CREATE PROCEDURE caller IS BEGIN APP.RUN_JOB(1); APP.RUN_JOB(1, 2, 3); END;",
    )
    graph = build_dependency_graph("9" * 40, [routine, caller])
    reasons = [edge.reason for edge in graph.edges if edge.relation == Relation.CALL]
    assert any(reason.endswith("binding=arity_name_compatible_type_unresolved") for reason in reasons)
    assert any(reason.endswith("binding=incompatible_arity_or_names") for reason in reasons)


def test_local_package_call_is_kept_as_a_package_candidate_without_builtin_noise() -> None:
    package_spec = obj(
        "SCHEMA|APP|PACKAGE_SPEC|P", "PACKAGE_SPEC",
        "CREATE PACKAGE P AS PROCEDURE HELPER; END P;",
    )
    package_body = obj(
        "SCHEMA|APP|PACKAGE_BODY|P", "PACKAGE_BODY",
        "CREATE PACKAGE BODY P AS PROCEDURE CALLER IS BEGIN HELPER; DBMS_OUTPUT.PUT_LINE('x'); END CALLER; END P;",
    )
    graph = build_dependency_graph("a" * 40, [package_spec, package_body])
    calls = [edge for edge in graph.edges if edge.relation == Relation.CALL and "local routine" in edge.reason]
    assert len(calls) == 1
    assert calls[0].resolution == Resolution.CANDIDATE
    assert package_spec.object_key in calls[0].to_candidate
    assert package_body.object_key in calls[0].to_candidate
    assert calls[0].reason.endswith("binding=arity_name_compatible_type_unresolved")


def test_unqualified_standalone_call_resolves_only_known_repository_routine() -> None:
    target = obj(
        "SCHEMA|APP|PROCEDURE|RUN_JOB", "PROCEDURE",
        "CREATE PROCEDURE RUN_JOB(P_ID NUMBER) IS BEGIN NULL; END RUN_JOB;",
    )
    caller = obj(
        "SCHEMA|APP|PROCEDURE|CALLER", "PROCEDURE",
        "CREATE PROCEDURE CALLER IS BEGIN RUN_JOB(1); DBMS_OUTPUT.PUT_LINE('x'); END CALLER;",
    )
    graph = build_dependency_graph("b" * 40, [target, caller])
    calls = [edge for edge in graph.edges if edge.relation == Relation.CALL and edge.to_candidate == target.object_key]
    assert len(calls) == 1
    assert calls[0].to_candidate == target.object_key
    assert calls[0].resolution == Resolution.RESOLVED_STATIC


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
