from __future__ import annotations

import hashlib

from db_change_analyzer.inventory import inventory_bytes
from db_change_analyzer.oracle.normalize import classify_text_change
from db_change_analyzer.oracle.scanner import scan


def test_p01_p02_literals_comments_and_q_quotes_do_not_create_false_boundaries() -> None:
    raw = b"""CREATE OR REPLACE PACKAGE BODY GPU_USER.P AS\r\nPROCEDURE X IS\r\nBEGIN\r\n  V := q'[; CREATE TABLE BAD.X (A NUMBER); END;]';\r\n  V2 := 'it''s; CREATE VIEW BAD.V AS SELECT 1 X FROM DUAL';\r\nEND;\r\nEND P;\r\n/\r\n"""
    result = scan(raw, default_schema="GPU_USER")
    assert [item.object_type for item in result.occurrences] == ["PACKAGE_BODY"]
    assert result.occurrences[0].fragment_sha256 == hashlib.sha256(raw).hexdigest()


def test_p03_combined_spec_body_and_trailing_trigger_alter_are_preserved() -> None:
    raw = b"""CREATE OR REPLACE PACKAGE GPU_USER.P AS\nPROCEDURE X;\nEND P;\n/\nCREATE OR REPLACE PACKAGE BODY GPU_USER.P AS\nPROCEDURE X IS BEGIN NULL; END;\nEND P;\n/\nCREATE OR REPLACE TRIGGER GPU_USER.T BEFORE INSERT ON GPU_USER.X BEGIN NULL; END;\n/\nALTER TRIGGER GPU_USER.T DISABLE;\n"""
    result = scan(raw, default_schema="GPU_USER")
    assert [item.object_type for item in result.occurrences] == ["PACKAGE_SPEC", "PACKAGE_BODY", "TRIGGER"]
    assert result.occurrences[-1].trailing_trigger_state == "DISABLE"


def test_parser_generation_runs_real_antlr_for_table_and_sequence() -> None:
    raw = b"CREATE TABLE GPU_USER.T (ID NUMBER NOT NULL);\nCREATE SEQUENCE GPU_USER.S START WITH 3 INCREMENT BY 2 NOCACHE;\n"
    scanned, projections, status, diagnostics = inventory_bytes(raw, default_schema="GPU_USER", parse_timeout_seconds=20)
    assert status == "parsed", diagnostics
    assert len(scanned.occurrences) == 2
    assert projections[1].properties["start_with"] == "3"
    assert projections[1].properties["increment_by"] == "2"


def test_p08_readable_unknown_ddl_is_visible_fallback() -> None:
    raw = b"CREATE FOOBAR GPU_USER.X WITH UNSUPPORTED CLAUSE;\n"
    scanned, projections, status, diagnostics = inventory_bytes(raw, default_schema="GPU_USER", parse_timeout_seconds=20)
    assert status == "unresolved"
    assert scanned.unresolved_non_whitespace_bytes == len(raw.strip())


def test_table_create_alter_and_comment_are_linked_after_real_parser_check() -> None:
    raw = b"CREATE TABLE S.T (ID NUMBER);\nALTER TABLE S.T ADD (NAME VARCHAR2(20));\nCOMMENT ON COLUMN S.T.NAME IS 'label';"
    scanned, projections, status, diagnostics = inventory_bytes(raw, default_schema="S")
    assert status == "parsed", diagnostics
    assert len(scanned.occurrences) == 1
    table = projections[0].properties
    assert table["columns"]["U:NAME"]["length"] == "20"
    assert table["comments"]["U:NAME"] == "'label'"


def test_n01_whitespace_only_vs_n02_literal_change() -> None:
    assert classify_text_change("SELECT 1 FROM DUAL", "SELECT   1\nFROM DUAL") == "format_only"
    assert classify_text_change("SELECT 'a b' FROM DUAL", "SELECT 'ab' FROM DUAL") == "structural_or_logic"
