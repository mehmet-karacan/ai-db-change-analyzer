from types import SimpleNamespace

import pytest

from db_change_analyzer.git_client import TreeEntry
from db_change_analyzer.inventory import FileInventory, inventory_bytes
from db_change_analyzer.oracle.projections import project
from db_change_analyzer.oracle.changes import ChangeSet, compare_projections
from db_change_analyzer.oracle.scanner import scan
from db_change_analyzer.workflow import _attach_table_contexts, _canonical_projection_facts, _conflicting_definitions, _identity_incomplete_paths, _incomplete_changed_paths, _index, _reordered_keys, _with_format_override, _with_occurrence_order_change, _with_source_path_change, _with_source_text_change


def test_duplicate_definitions_are_checked_on_each_revision() -> None:
    def ref(fragment: str, *contexts: str) -> SimpleNamespace:
        return SimpleNamespace(
            occurrence=SimpleNamespace(fragment_sha256=fragment),
            context_evidence=tuple({"fragment_sha256": digest} for digest in contexts),
            projection=SimpleNamespace(support="structural", diagnostics=()),
        )

    first = ref("a", "context-a")
    same = ref("a", "context-a")
    other = ref("b", "context-a")
    assert not _conflicting_definitions([first, same])
    assert _conflicting_definitions([first, other])
    assert _conflicting_definitions([first, ref("a", "context-b")])
    assert _conflicting_definitions([first, ref("a")])


def test_changed_partial_inventory_cannot_prove_absence() -> None:
    def inventory(path: bytes, status: str, *diagnostics: str) -> SimpleNamespace:
        return SimpleNamespace(entry=SimpleNamespace(path=path), parse_status=status, diagnostics=diagnostics)

    inventories = [
        inventory(b"scope/changed.sql", "text_fallback"),
        inventory(b"scope/unknown.sql", "parsed", "UNRESOLVED_PREFIX_OR_ARTIFACT"),
        inventory(b"scope/unchanged.sql", "unsupported"),
    ]
    assert _incomplete_changed_paths(
        inventories, {b"scope/changed.sql", b"scope/unknown.sql"}
    ) == {b"scope/changed.sql", b"scope/unknown.sql"}
    assert _identity_incomplete_paths(inventories) == {b"scope/unknown.sql"}


def test_structural_projection_facts_keep_taxonomy_and_both_source_sides() -> None:
    def ref(sql: str) -> SimpleNamespace:
        occurrence = scan(sql.encode(), default_schema="S").occurrences[0]
        return SimpleNamespace(projection=project(occurrence, sql, parse_ok=True))

    facts = _canonical_projection_facts(
        ref("CREATE TABLE S.T (ID NUMBER(10));"),
        ref("CREATE TABLE S.T (ID NUMBER(12));"),
        ("ev-old",), ("ev-new",), run_id="run-1", base_sha="a" * 40, target_sha="b" * 40,
    )
    precision = next(fact for fact in facts if fact["property"].startswith("table.column.precision"))
    assert precision["before"] == "10"
    assert precision["after"] == "12"
    assert precision["evidence_ids"] == ["ev-old", "ev-new"]


def test_same_identity_moved_between_paths_is_a_separate_source_fact() -> None:
    old = SimpleNamespace(inventory=SimpleNamespace(entry=SimpleNamespace(path_b64="b2xk", path_display="schema/old.sql")))
    new = SimpleNamespace(inventory=SimpleNamespace(entry=SimpleNamespace(path_b64="bmV3", path_display="schema/new.sql")))
    base = ChangeSet("key", "TABLE", "modified", (), ("common.source.path",), ())
    result = _with_source_path_change(base, old, new, "ev-old", "ev-new")
    assert result.facts[0].taxonomy_id == "common.source.path"
    assert result.facts[0].before.value == "schema/old.sql"
    assert result.facts[0].after.value == "schema/new.sql"
    assert result.unsupported_families == ()


def test_source_format_and_unstructured_text_have_distinct_taxonomy_facts() -> None:
    def ref(sql: str) -> SimpleNamespace:
        occurrence = scan(sql.encode(), default_schema="S").occurrences[0]
        return SimpleNamespace(occurrence=occurrence)

    old_sql = "CREATE VIEW S.V AS SELECT 1 AS X FROM DUAL;"
    for new_sql, expected in (
        ("CREATE VIEW S.V AS\n SELECT 1 AS X FROM DUAL;", "common.source.format"),
        ("CREATE VIEW S.V AS SELECT 2 AS X FROM DUAL;", "common.source.text"),
    ):
        old, new = ref(old_sql), ref(new_sql)
        change = _with_source_text_change(
            ChangeSet(old.occurrence.object_key, "VIEW", "modified", (), (expected,), ()),
            old, new,
            {"snippet": old_sql, "evidence_id": "ev-old"},
            {"snippet": new_sql, "evidence_id": "ev-new"},
        )
        assert [fact.taxonomy_id for fact in change.facts] == [expected]
        assert change.facts[0].before.value.startswith("sha256:")
        assert change.unsupported_families == ()


@pytest.mark.parametrize("old_sql,new_sql", [
    ("CREATE INDEX S.I ON S.T (ID + 1);", "CREATE INDEX S.I ON S.T (ID  + 1);"),
    ("CREATE PACKAGE S.P AS PROCEDURE X(P NUMBER); END P; /",
     "CREATE PACKAGE S.P AS PROCEDURE X(P  NUMBER); END P; /"),
    ("CREATE PACKAGE BODY S.P AS PROCEDURE X IS BEGIN NULL; END X; END P; /",
     "CREATE PACKAGE BODY S.P AS PROCEDURE X IS BEGIN  NULL; END X; END P; /"),
])
def test_parser_raw_spans_do_not_turn_whitespace_into_structural_changes(old_sql: str, new_sql: str) -> None:
    def ref(sql: str) -> SimpleNamespace:
        scanned, projections, status, diagnostics = inventory_bytes(sql.encode(), default_schema="S")
        assert status == "parsed" and not diagnostics
        return SimpleNamespace(occurrence=scanned.occurrences[0], projection=projections[0], context_evidence=())

    old, new = ref(old_sql), ref(new_sql)
    structural = compare_projections(old.projection, new.projection,
                                     old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    assert structural.facts  # The extractor contains source spans with whitespace.
    bounded = _with_format_override(structural, old, new, old_sql, new_sql)
    final = _with_source_text_change(bounded, old, new,
                                     {"snippet": old_sql, "evidence_id": "ev-old"},
                                     {"snippet": new_sql, "evidence_id": "ev-new"})
    assert [fact.taxonomy_id for fact in final.facts] == ["common.source.format"]


def test_format_override_does_not_hide_a_bound_context_change() -> None:
    old_sql = "CREATE INDEX S.I ON S.T (ID + 1);"
    new_sql = "CREATE INDEX S.I ON S.T (ID  + 1);"
    old_scan, old_projections, _, _ = inventory_bytes(old_sql.encode(), default_schema="S")
    new_scan, new_projections, _, _ = inventory_bytes(new_sql.encode(), default_schema="S")
    old = SimpleNamespace(occurrence=old_scan.occurrences[0], projection=old_projections[0],
                          context_evidence=({"fragment_sha256": "a"},))
    new = SimpleNamespace(occurrence=new_scan.occurrences[0], projection=new_projections[0],
                          context_evidence=({"fragment_sha256": "b"},))
    structural = compare_projections(old.projection, new.projection,
                                     old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    assert structural.facts
    assert _with_format_override(structural, old, new, old_sql, new_sql) is structural


def test_reordered_definitions_are_detected_even_with_identical_fragments() -> None:
    def inventory(sql: bytes) -> FileInventory:
        scanned, projections, status, diagnostics = inventory_bytes(sql, default_schema="S", parse=True)
        return FileInventory(TreeEntry("100644", "blob", "a" * 40, b"s/tables.sql"), len(sql), "utf-8", "LF",
                             status, diagnostics, scanned.occurrences, projections)

    old = inventory(b"CREATE TABLE S.A (ID NUMBER);\nCREATE TABLE S.B (ID NUMBER);")
    new = inventory(b"CREATE TABLE S.B (ID NUMBER);\nCREATE TABLE S.A (ID NUMBER);")
    keys = _reordered_keys([old], [new])
    assert keys == {item.object_key for item in old.occurrences}
    old_ref = SimpleNamespace(inventory=old)
    new_ref = SimpleNamespace(inventory=new)
    key = old.occurrences[0].object_key
    result = _with_occurrence_order_change(ChangeSet(key, "TABLE", "modified", (), (), ()),
                                           old_ref, new_ref, "ev-old", "ev-new")
    assert result.facts[0].taxonomy_id == "common.source.occurrence_order"
    assert (result.facts[0].before.value, result.facts[0].after.value) == ("1", "2")


def test_standalone_alter_and_comment_join_only_their_table_with_source_evidence() -> None:
    create = b"CREATE TABLE T (ID NUMBER);"
    context = b"ALTER TABLE T ADD (X NUMBER); COMMENT ON COLUMN T.X IS 'label'; ALTER TABLE T PARALLEL 4;"
    blobs = {"a" * 40: create, "b" * 40: context}

    class FakeGit:
        def read_blob(self, oid, _maximum):
            return blobs[oid]

    inventories = []
    for path, oid, raw in ((b"s/t.sql", "a" * 40, create), (b"s/t_context.sql", "b" * 40, context)):
        scan_result, projections, status, diagnostics = inventory_bytes(raw, default_schema="S", parse=path == b"s/t.sql")
        inventories.append(FileInventory(TreeEntry("100644", "blob", oid, path), len(raw), "utf-8", "LF",
                                         status, diagnostics, scan_result.occurrences, projections))
    original = _index(FakeGit(), "c" * 40, inventories, 1000)
    updated, matched = _attach_table_contexts(FakeGit(), "c" * 40, {"s": "S"}, inventories, original, 1000, 20)
    assert matched == {b"s/t_context.sql"}
    ref = next(iter(updated.values()))[0]
    assert "U:X" in ref.projection.properties["columns"]
    assert ref.projection.properties["comments"]["U:X"] == "'label'"
    assert ref.projection.properties["table_properties"]["parallel"] == "PARALLEL 4"
    assert len(ref.context_evidence) == 3
    assert all(evidence["path_display"] == "s/t_context.sql" for evidence in ref.context_evidence)
