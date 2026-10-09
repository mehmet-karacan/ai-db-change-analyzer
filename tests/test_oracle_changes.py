from __future__ import annotations

import pytest
from functools import cache

from db_change_analyzer.inventory import inventory_bytes
from db_change_analyzer.oracle.changes import compare_projections


SOURCES = {
    "TABLE": (
        "CREATE TABLE S.T (ID NUMBER(10) NOT NULL);",
        "CREATE TABLE S.T (ID NUMBER(12) NOT NULL);",
        "table.column.precision",
    ),
    "INDEX": (
        "CREATE INDEX S.I ON S.T (ID ASC);",
        "CREATE INDEX S.I ON S.T (ID DESC);",
        "index.index_property.direction",
    ),
    "SEQUENCE": (
        "CREATE SEQUENCE S.Q START WITH 1 INCREMENT BY 1;",
        "CREATE SEQUENCE S.Q START WITH 2 INCREMENT BY 1;",
        "sequence.sequence_property.start_with",
    ),
    "VIEW": (
        "CREATE VIEW S.V AS SELECT ID FROM S.T;",
        "CREATE VIEW S.V AS SELECT ID, NAME FROM S.T;",
        "view.query_property.projection",
    ),
    "PACKAGE_SPEC": (
        "CREATE PACKAGE S.P AS PROCEDURE X(P_ID NUMBER); END P; /",
        "CREATE PACKAGE S.P AS PROCEDURE X(P_ID VARCHAR2); END P; /",
        "package_spec.parameter.data_type",
    ),
    "PACKAGE_BODY": (
        "CREATE PACKAGE BODY S.P AS PROCEDURE X IS BEGIN NULL; END X; END P; /",
        "CREATE PACKAGE BODY S.P AS PROCEDURE X IS BEGIN COMMIT; END X; END P; /",
        "package_body.body_property.transaction_statement",
    ),
}


@cache
def _projection(source: str):
    scan_result, projections, status, diagnostics = inventory_bytes(source.encode(), default_schema="S")
    assert status == "parsed", diagnostics
    assert len(scan_result.occurrences) == len(projections) == 1
    assert projections[0].diagnostics == ()
    return projections[0]


@pytest.mark.parametrize("kind", SOURCES)
@pytest.mark.parametrize("operation", ("added", "modified", "removed"))
def test_six_types_three_operations_have_taxonomy_and_correct_evidence(kind: str, operation: str) -> None:
    old_source, new_source, expected_taxonomy = SOURCES[kind]
    old = _projection(old_source) if operation != "added" else None
    new = _projection(new_source) if operation != "removed" else None
    result = compare_projections(
        old, new, old_evidence_ids=("ev-old",) if old else (),
        new_evidence_ids=("ev-new",) if new else (),
        old_scope_evidence_ids=("scope-old",) if old is None else (),
        new_scope_evidence_ids=("scope-new",) if new is None else (),
    )
    assert result.object_type == kind
    assert result.operation == operation
    assert result.facts
    assert all(fact.before.evidence_ids == (("ev-old",) if old else ("scope-old",)) for fact in result.facts)
    assert all(fact.after.evidence_ids == (("ev-new",) if new else ("scope-new",)) for fact in result.facts)
    if operation == "modified":
        assert expected_taxonomy in {fact.taxonomy_id for fact in result.facts}
    else:
        assert result.facts[0].taxonomy_id == "common.object.presence"


def test_absence_without_complete_scope_is_not_called_added() -> None:
    _, new_source, _ = SOURCES["TABLE"]
    result = compare_projections(None, _projection(new_source), old_evidence_ids=(), new_evidence_ids=("ev-new",))
    assert result.operation == "unknown"
    assert result.facts == ()
    assert "ABSENCE_SCOPE_UNVERIFIED" in result.diagnostics


def test_sequence_change_is_not_limited_by_workflow_source_families() -> None:
    old = _projection("CREATE SEQUENCE S.Q START WITH 1 INCREMENT BY 1;")
    new = _projection("CREATE SEQUENCE S.Q START WITH 2 INCREMENT BY 1;")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    assert {fact.taxonomy_id for fact in result.facts} == {"sequence.sequence_property.start_with"}
    assert "common.object.presence" not in result.unsupported_families
    assert not any(item.startswith("common.source.") for item in result.unsupported_families)


def test_table_constraint_enable_change_has_its_own_taxonomy_fact() -> None:
    old = _projection("CREATE TABLE S.T (ID NUMBER, CONSTRAINT PK_T PRIMARY KEY (ID) ENABLE);")
    new = _projection("CREATE TABLE S.T (ID NUMBER, CONSTRAINT PK_T PRIMARY KEY (ID) DISABLE);")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    enabled = next(fact for fact in result.facts if fact.taxonomy_id == "table.constraint.enabled")
    assert enabled.before.value == "ENABLE"
    assert enabled.after.value == "DISABLE"


def test_empty_comment_literal_emits_removed_comment_fact() -> None:
    old_source = "CREATE TABLE S.T (ID NUMBER); COMMENT ON COLUMN S.T.ID IS 'label';"
    old = _projection(old_source)
    new = _projection(old_source + " COMMENT ON COLUMN S.T.ID IS '';")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    comment = next(fact for fact in result.facts if fact.taxonomy_id == "table.comment.text")
    assert result.operation == "modified"
    assert comment.before.value == "'label'"
    assert comment.after.value is None
    assert comment.before.evidence_ids == ("ev-old",)
    assert comment.after.evidence_ids == ("ev-new",)


def test_table_partial_modifiers_emit_nullable_and_default_facts() -> None:
    old = _projection("CREATE TABLE S.T (ID NUMBER, NAME VARCHAR2(20) DEFAULT 'old');")
    new = _projection(
        "CREATE TABLE S.T (ID NUMBER, NAME VARCHAR2(20) DEFAULT 'old'); "
        "ALTER TABLE S.T MODIFY (ID NOT NULL, NAME DEFAULT 'new');"
    )
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    by_taxonomy = {fact.taxonomy_id: fact for fact in result.facts}
    assert by_taxonomy["table.column.nullable"].after.value == "NOT NULL"
    assert by_taxonomy["table.column.default"].after.value == "'new'"
    assert "table.column.data_type" not in by_taxonomy
    assert "table.column.definition" not in by_taxonomy


def test_table_virtual_expression_change_has_its_own_fact() -> None:
    old = _projection("CREATE TABLE S.T (A NUMBER, B AS (A + 1) VIRTUAL);")
    new = _projection("CREATE TABLE S.T (A NUMBER, B AS (A + 2) VIRTUAL);")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    by_taxonomy = {fact.taxonomy_id: fact for fact in result.facts}
    expression = by_taxonomy["table.column.virtual_expression"]
    assert expression.before.value == "A + 1"
    assert expression.after.value == "A + 2"
    assert expression.before.evidence_ids == ("ev-old",)
    assert expression.after.evidence_ids == ("ev-new",)
    assert "table.column.identity" not in by_taxonomy
    assert "table.column.data_type" not in by_taxonomy


def test_unresolved_alter_column_does_not_hide_verified_sibling_change() -> None:
    old_source = "CREATE TABLE S.T (ID NUMBER, SAFE NUMBER);"
    new_source = (
        "CREATE TABLE S.T (ID NUMBER, SAFE VARCHAR2(20)); "
        "ALTER TABLE S.T MODIFY (ID DEFAULT 1 NOT NULL);"
    )
    old = inventory_bytes(old_source.encode(), default_schema="S")[1][0]
    new = inventory_bytes(new_source.encode(), default_schema="S")[1][0]
    assert new.diagnostics == ("ALTER_COLUMN_PARTIAL_UNRESOLVED",)
    assert new.properties["_unresolved_columns"] == ("U:ID",)
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    assert result.diagnostics == ("ALTER_COLUMN_PARTIAL_UNRESOLVED",)
    assert any(fact.taxonomy_id == "table.column.data_type" and fact.component_path == ("U:SAFE",)
               and fact.before.value == "NUMBER" and fact.after.value == "VARCHAR2(20)"
               for fact in result.facts)
    assert all(fact.component_path != ("U:ID",) for fact in result.facts)
    assert "table.column.default" in result.unsupported_families


def test_unscoped_table_diagnostic_still_blocks_all_field_facts() -> None:
    old = _projection("CREATE TABLE S.T (ID NUMBER, SAFE NUMBER);")
    new_source = "CREATE TABLE S.T (ID NUMBER, SAFE VARCHAR2(20)); ALTER TABLE S.T DROP COLUMN ID CASCADE CONSTRAINTS;"
    new = inventory_bytes(new_source.encode(), default_schema="S")[1][0]
    assert new.diagnostics == ("ALTER_TABLE_DROP_UNSUPPORTED",)
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    assert result.facts == ()
    assert result.diagnostics == ("ALTER_TABLE_DROP_UNSUPPORTED",)


def test_alter_drop_column_list_produces_removed_column_facts() -> None:
    old = _projection("CREATE TABLE S.T (A NUMBER, B NUMBER, C NUMBER);")
    new = _projection(
        "CREATE TABLE S.T (A NUMBER, B NUMBER, C NUMBER); ALTER TABLE S.T DROP (A, B);"
    )
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    removed = [fact for fact in result.facts if fact.taxonomy_id == "table.column.definition"]
    assert {fact.component_path for fact in removed} == {("U:A",), ("U:B",)}
    assert all(fact.action == "removed" and fact.before.evidence_ids == ("ev-old",)
               and fact.after.evidence_ids == ("ev-new",) for fact in removed)


def test_package_body_sql_statement_change_has_parser_backed_fact() -> None:
    old = _projection("CREATE PACKAGE BODY S.P AS PROCEDURE X IS BEGIN UPDATE T SET X = 1; END X; END P; /")
    new = _projection("CREATE PACKAGE BODY S.P AS PROCEDURE X IS BEGIN UPDATE T SET X = 2; END X; END P; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    statement = next(fact for fact in result.facts if fact.taxonomy_id == "package_body.body_property.sql_statement")
    assert statement.before.value == '["UPDATE T SET X = 1"]'
    assert statement.after.value == '["UPDATE T SET X = 2"]'
    assert statement.before.evidence_ids == ("ev-old",)
    assert statement.after.evidence_ids == ("ev-new",)


def test_package_body_dynamic_sql_profile_is_bounded_and_safe_to_display() -> None:
    old = _projection("CREATE PACKAGE BODY S.P AS PROCEDURE X IS BEGIN EXECUTE IMMEDIATE 'UPDATE S.T SET X = 1'; END X; END P; /")
    new = _projection("CREATE PACKAGE BODY S.P AS PROCEDURE X IS BEGIN EXECUTE IMMEDIATE 'UPDATE ' || V_TABLE || ' SET X = :1' USING V_ID; END X; END P; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    profile = next(fact for fact in result.facts if fact.taxonomy_id == "package_body.body_property.dynamic_sql_profile")

    assert profile.before.value == '["mode=literal,target=static,bind=no,concat=no,validation=not_applicable,loop=no"]'
    assert profile.after.value == '["mode=expression,target=runtime,bind=yes,concat=yes,validation=not_observed,loop=no"]'
    assert "EXECUTE" not in (profile.before.value or "")
    assert "EXECUTE" not in (profile.after.value or "")


def test_package_declaration_families_use_catalog_names() -> None:
    old = _projection("CREATE PACKAGE S.P AS C_LIMIT CONSTANT NUMBER := 10; V_COUNT NUMBER; END P; /")
    new = _projection("CREATE PACKAGE S.P AS C_LIMIT CONSTANT NUMBER := 20; V_COUNT VARCHAR2(30); END P; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    ids = {fact.taxonomy_id for fact in result.facts}
    assert "package_spec.constant.definition" in ids
    assert "package_spec.variable.definition" in ids


def test_package_parameter_definition_retains_source_span() -> None:
    old = _projection("CREATE PACKAGE S.P AS PROCEDURE X(P_ID NUMBER); END P; /")
    new = _projection("CREATE PACKAGE S.P AS PROCEDURE X(P_ID VARCHAR2); END P; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    definitions = [fact for fact in result.facts if fact.taxonomy_id == "package_spec.parameter.definition"]
    assert definitions
    assert {fact.before.value for fact in definitions if fact.before.value} == {"P_ID NUMBER"}
    assert {fact.after.value for fact in definitions if fact.after.value} == {"P_ID VARCHAR2"}


def test_standalone_procedure_signature_change_has_contract_fact() -> None:
    old = _projection("CREATE PROCEDURE S.RUN_JOB(P_ID NUMBER) IS BEGIN NULL; END RUN_JOB; /")
    new = _projection("CREATE PROCEDURE S.RUN_JOB(P_ID NUMBER, P_LABEL VARCHAR2 DEFAULT 'x') IS BEGIN NULL; END RUN_JOB; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    signature = next(fact for fact in result.facts if fact.taxonomy_id == "procedure.procedure_property.signature")
    assert signature.action == "modified"
    assert "P_ID NUMBER" in (signature.before.value or "")
    assert "P_LABEL VARCHAR2" in (signature.after.value or "")
    assert signature.before.evidence_ids == ("ev-old",)
    assert signature.after.evidence_ids == ("ev-new",)


def test_standalone_procedure_exception_and_transaction_facts_stay_source_bound() -> None:
    old = _projection("CREATE PROCEDURE S.RUN_JOB IS BEGIN NULL; END RUN_JOB; /")
    new = _projection("CREATE PROCEDURE S.RUN_JOB IS BEGIN COMMIT; EXCEPTION WHEN OTHERS THEN ROLLBACK; END RUN_JOB; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    by_taxonomy = {fact.taxonomy_id: fact for fact in result.facts}
    assert by_taxonomy["procedure.procedure_property.transaction_statement"].after.value == '["COMMIT","ROLLBACK"]'
    assert by_taxonomy["procedure.procedure_property.exception_handler"].after.value is not None
    assert by_taxonomy["procedure.procedure_property.transaction_statement"].after.evidence_ids == ("ev-new",)
    assert by_taxonomy["procedure.procedure_property.exception_handler"].after.evidence_ids == ("ev-new",)


def test_standalone_function_return_signature_change_has_contract_fact() -> None:
    old = _projection("CREATE FUNCTION S.GET_VALUE(P_ID NUMBER) RETURN NUMBER IS BEGIN RETURN P_ID; END GET_VALUE; /")
    new = _projection("CREATE FUNCTION S.GET_VALUE(P_ID NUMBER) RETURN VARCHAR2 IS BEGIN RETURN TO_CHAR(P_ID); END GET_VALUE; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    signature = next(fact for fact in result.facts if fact.taxonomy_id == "function.function_property.signature")
    assert signature.action == "modified"
    assert "RETURN NUMBER" in (signature.before.value or "")
    assert "RETURN VARCHAR2" in (signature.after.value or "")


def test_overload_signature_set_changes_without_inventing_member_pairings() -> None:
    old = _projection("CREATE PACKAGE S.P AS PROCEDURE X(P NUMBER); PROCEDURE X(P VARCHAR2); END P; /")
    new = _projection("CREATE PACKAGE S.P AS PROCEDURE X(P NUMBER); PROCEDURE X(P DATE); END P; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    assert result.diagnostics == ()
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.taxonomy_id == "package_spec.routine.signature"
    assert fact.component_path == ("PROCEDURE", "X")
    assert fact.action == "modified"
    assert "VARCHAR2" in fact.before.value and "DATE" in fact.after.value
    assert fact.before.evidence_ids == ("ev-old",) and fact.after.evidence_ids == ("ev-new",)
    assert "package_spec.parameter.data_type" in result.unsupported_families


def test_overload_group_added_does_not_claim_individual_parameter_deletions() -> None:
    old = _projection("CREATE PACKAGE S.P AS PROCEDURE X(P NUMBER); END P; /")
    new = _projection("CREATE PACKAGE S.P AS PROCEDURE X(P NUMBER); PROCEDURE X(P VARCHAR2); END P; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    assert [fact.taxonomy_id for fact in result.facts] == ["package_spec.routine.signature"]
    assert result.facts[0].action == "modified"
    assert not any(fact.taxonomy_id.startswith("package_spec.parameter.") for fact in result.facts)


def test_package_body_overloads_keep_signature_evidence_and_bound_body_details() -> None:
    old = _projection("CREATE PACKAGE BODY S.P AS PROCEDURE X(P NUMBER) IS BEGIN NULL; END X; PROCEDURE X(P VARCHAR2) IS BEGIN NULL; END X; END P; /")
    new = _projection("CREATE PACKAGE BODY S.P AS PROCEDURE X(P NUMBER) IS BEGIN NULL; END X; PROCEDURE X(P DATE) IS BEGIN NULL; END X; END P; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    assert [fact.taxonomy_id for fact in result.facts] == ["package_body.routine.signature"]
    assert "package_body.body_property.routine_body" in result.unsupported_families
    assert not any(fact.action in {"added", "removed"} for fact in result.facts)


def test_view_editionable_change_is_explicit_source_fact() -> None:
    old = _projection("CREATE EDITIONABLE VIEW S.V AS SELECT ID FROM S.T;")
    new = _projection("CREATE NONEDITIONABLE VIEW S.V AS SELECT ID FROM S.T;")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    fact = next(item for item in result.facts if item.taxonomy_id == "common.object.editionable")
    assert fact.before.value == "EDITIONABLE"
    assert fact.after.value == "NONEDITIONABLE"


def test_view_join_type_change_has_its_own_taxonomy_fact() -> None:
    old = _projection("CREATE VIEW S.V AS SELECT A.ID FROM A LEFT OUTER JOIN B ON B.ID = A.ID;")
    new = _projection("CREATE VIEW S.V AS SELECT A.ID FROM A RIGHT OUTER JOIN B ON B.ID = A.ID;")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    join = next(fact for fact in result.facts if fact.taxonomy_id == "view.query_property.join")
    assert join.before.value == "LEFT OUTER JOIN B ON B.ID = A.ID"
    assert join.after.value == "RIGHT OUTER JOIN B ON B.ID = A.ID"
    assert join.before.evidence_ids == ("ev-old",) and join.after.evidence_ids == ("ev-new",)


def test_view_analytic_window_change_has_its_own_taxonomy_fact() -> None:
    old = _projection("CREATE VIEW S.V AS SELECT SUM(X) OVER (PARTITION BY D) SX FROM S.T;")
    new = _projection("CREATE VIEW S.V AS SELECT SUM(X) OVER (PARTITION BY E) SX FROM S.T;")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    window = next(fact for fact in result.facts if fact.taxonomy_id == "view.query_property.analytic")
    assert window.before.value == "OVER (PARTITION BY D)"
    assert window.after.value == "OVER (PARTITION BY E)"
    assert window.before.evidence_ids == ("ev-old",)
    assert window.after.evidence_ids == ("ev-new",)


def test_package_body_initialization_is_separate_from_routines() -> None:
    old = _projection("CREATE PACKAGE BODY S.P AS V NUMBER; BEGIN V := 1; END P; /")
    new = _projection("CREATE PACKAGE BODY S.P AS V NUMBER; BEGIN V := 2; END P; /")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    initialization = next(fact for fact in result.facts if fact.taxonomy_id == "package_body.body_property.initialization")
    assert initialization.before.value == "V := 1;"
    assert initialization.after.value == "V := 2;"


def test_named_table_partition_change_has_component_fact() -> None:
    prefix = "CREATE TABLE S.T (ID NUMBER) PARTITION BY RANGE (ID) ("
    suffix = ", PARTITION P2 VALUES LESS THAN (MAXVALUE));"
    old = _projection(prefix + "PARTITION P1 VALUES LESS THAN (10)" + suffix)
    new = _projection(prefix + "PARTITION P1 VALUES LESS THAN (20)" + suffix)
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    partition = next(fact for fact in result.facts if fact.taxonomy_id == "table.partition.definition")
    assert partition.component_path == ("U:P1",)
    assert partition.before.value == "PARTITION P1 VALUES LESS THAN (10)"
    assert partition.after.value == "PARTITION P1 VALUES LESS THAN (20)"


def test_index_partition_detail_change_is_bounded_to_partitioning_family() -> None:
    old = _projection("CREATE INDEX S.I ON S.T (ID) LOCAL (PARTITION P1 TABLESPACE TS1); ")
    new = _projection("CREATE INDEX S.I ON S.T (ID) LOCAL (PARTITION P1 TABLESPACE TS2); ")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    assert [fact.taxonomy_id for fact in result.facts] == ["index.index_property.partitioning"]
    assert result.facts[0].before.value == "LOCAL (PARTITION P1 TABLESPACE TS1)"
    assert result.facts[0].after.value == "LOCAL (PARTITION P1 TABLESPACE TS2)"


def test_index_parallel_degree_change_has_physical_configuration_fact() -> None:
    old = _projection("CREATE INDEX S.I ON S.T (ID) PARALLEL 4;")
    new = _projection("CREATE INDEX S.I ON S.T (ID) PARALLEL 8;")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    fact = next(item for item in result.facts if item.taxonomy_id == "index.index_property.parallel")
    assert fact.before.value == "PARALLEL 4"
    assert fact.after.value == "PARALLEL 8"
    assert fact.before.evidence_ids == ("ev-old",)
    assert fact.after.evidence_ids == ("ev-new",)


def test_table_parallel_degree_change_has_physical_configuration_fact() -> None:
    old = _projection("CREATE TABLE S.T (ID NUMBER) PARALLEL 4;")
    new = _projection("CREATE TABLE S.T (ID NUMBER) PARALLEL 8;")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    fact = next(item for item in result.facts if item.taxonomy_id == "table.table_property.parallel")
    assert fact.before.value == "PARALLEL 4"
    assert fact.after.value == "PARALLEL 8"
    assert fact.before.evidence_ids == ("ev-old",)
    assert fact.after.evidence_ids == ("ev-new",)


def test_alter_table_parallel_context_has_physical_configuration_fact() -> None:
    old = _projection("CREATE TABLE S.T (ID NUMBER) PARALLEL 4;")
    new = _projection("CREATE TABLE S.T (ID NUMBER) PARALLEL 4; ALTER TABLE S.T NOPARALLEL;")
    result = compare_projections(old, new, old_evidence_ids=("ev-old",), new_evidence_ids=("ev-new",))
    fact = next(item for item in result.facts if item.taxonomy_id == "table.table_property.parallel")
    assert fact.before.value == "PARALLEL 4"
    assert fact.after.value == "NOPARALLEL"
