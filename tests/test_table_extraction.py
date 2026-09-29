from db_change_analyzer.oracle.table import constraint_properties, extract_table, table_context_statements


def test_table_columns_keep_types_semantics_defaults_and_constraints() -> None:
    source = '''CREATE TABLE "Case".T (
      ID NUMBER(12, -2) NOT NULL,
      "Display,Name" VARCHAR2(20 CHAR) DEFAULT q'[a,b]',
      CREATED_AT TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
      ACTIVE NUMBER DEFAULT NULL,
      CONSTRAINT PK_T PRIMARY KEY (ID)
    ) TABLESPACE TBS NOLOGGING;'''
    result = extract_table(source)
    assert result.diagnostics == ()
    assert result.columns["U:ID"]["precision"] == "12"
    assert result.columns["U:ID"]["scale"] == "-2"
    assert result.columns["U:ID"]["nullable"] == "NOT NULL"
    assert result.columns["Q:Display,Name"]["length"] == "20"
    assert result.columns["Q:Display,Name"]["length_semantics"] == "CHAR"
    assert result.columns["Q:Display,Name"]["default"] == "q'[a,b]'"
    assert result.columns["U:CREATED_AT"]["data_type"] == "TIMESTAMP WITH TIME ZONE"
    assert result.columns["U:ACTIVE"]["nullable"] is None
    assert result.columns["U:ACTIVE"]["default"] == "NULL"
    assert result.constraints["U:PK_T"] == "CONSTRAINT PK_T PRIMARY KEY (ID)"
    assert result.properties["tablespace"] == "TBS"


def test_table_duplicate_and_unbalanced_source_remain_limited() -> None:
    duplicated = extract_table("CREATE TABLE T (ID NUMBER, ID VARCHAR2(20));")
    assert "DUPLICATE_COLUMN" in duplicated.diagnostics
    assert len(duplicated.columns) == 1
    unbalanced = extract_table("CREATE TABLE T (ID NUMBER(10);")
    assert unbalanced.diagnostics


def test_virtual_column_expression_is_distinct_from_data_type_and_identity() -> None:
    table = extract_table(
        "CREATE TABLE S.T (A NUMBER, B AS (A + 1) VIRTUAL, "
        "C NUMBER GENERATED ALWAYS AS (A + 2) VIRTUAL, "
        "ID NUMBER GENERATED ALWAYS AS IDENTITY);"
    )
    assert table.diagnostics == ()
    assert table.columns["U:B"]["data_type"] is None
    assert table.columns["U:B"]["virtual_expression"] == "A + 1"
    assert table.columns["U:C"]["data_type"] == "NUMBER"
    assert table.columns["U:C"]["virtual_expression"] == "A + 2"
    assert table.columns["U:C"]["identity"] is None
    assert table.columns["U:ID"]["identity"] == "GENERATED ALWAYS AS IDENTITY"
    assert table.columns["U:ID"]["virtual_expression"] is None


def test_related_alter_and_comment_context_updates_parent_table() -> None:
    source = """CREATE TABLE S.T (ID NUMBER NOT NULL, NAME VARCHAR2(20));
    ALTER TABLE S.T ADD (EXTRA NUMBER);
    ALTER TABLE S.T MODIFY (NAME VARCHAR2(30));
    ALTER TABLE S.T DROP COLUMN EXTRA;
    COMMENT ON COLUMN S.T.NAME IS 'public label';
    ALTER TABLE S.OTHER ADD (BAD NUMBER);
    """
    table = extract_table(source)
    assert table.diagnostics == ()
    assert set(table.columns) == {"U:ID", "U:NAME"}
    assert table.columns["U:NAME"]["length"] == "30"
    assert table.columns["U:ID"]["nullable"] == "NOT NULL"
    assert table.comments["U:NAME"] == "'public label'"


def test_empty_comment_literal_removes_table_and_column_comments() -> None:
    table = extract_table(
        "CREATE TABLE S.T (ID NUMBER); "
        "COMMENT ON TABLE S.T IS 'table label'; "
        "COMMENT ON COLUMN S.T.ID IS 'column label'; "
        "COMMENT ON TABLE S.T IS ''; "
        "COMMENT ON COLUMN S.T.ID IS '';"
    )
    assert table.diagnostics == ()
    assert table.comments == {}


def test_partial_alter_updates_only_explicit_column_properties() -> None:
    table = extract_table(
        "CREATE TABLE S.T (ID NUMBER, NAME VARCHAR2(20) DEFAULT 'old'); "
        "ALTER TABLE S.T MODIFY (ID NOT NULL, NAME DEFAULT 'new');"
    )
    assert table.diagnostics == ()
    assert table.columns["U:ID"]["data_type"] == "NUMBER"
    assert table.columns["U:ID"]["nullable"] == "NOT NULL"
    assert table.columns["U:ID"]["definition"] == "ID NUMBER"
    assert table.columns["U:NAME"]["data_type"] == "VARCHAR2(20)"
    assert table.columns["U:NAME"]["default"] == "'new'"


def test_partial_alter_keeps_unsupported_modifier_diagnostic() -> None:
    table = extract_table("CREATE TABLE S.T (ID NUMBER); ALTER TABLE S.T MODIFY (ID GENERATED ALWAYS);")
    assert "ALTER_COLUMN_PARTIAL_UNRESOLVED" in table.diagnostics
    mixed = extract_table("CREATE TABLE S.T (ID NUMBER); ALTER TABLE S.T MODIFY (ID DEFAULT 1 NOT NULL);")
    assert "ALTER_COLUMN_PARTIAL_UNRESOLVED" in mixed.diagnostics


def test_named_constraint_properties_are_structured_without_checking_literals() -> None:
    detail = constraint_properties(
        "CONSTRAINT FK_T FOREIGN KEY (A, B) REFERENCES PARENT(ID, CODE) "
        "ON DELETE SET NULL ENABLE NOVALIDATE NOT DEFERRABLE RELY"
    )
    assert detail["kind"] == "FOREIGN KEY"
    assert detail["columns"] == "A, B"
    assert detail["reference"] == "PARENT(ID, CODE)"
    assert detail["delete_rule"] == "ON DELETE SET NULL"
    assert detail["enabled"] == "ENABLE"
    assert detail["validation"] == "NOVALIDATE"
    assert detail["deferrable"] == "NOT DEFERRABLE"
    assert detail["rely"] == "RELY"


def test_partitioned_table_keeps_parent_name_for_following_alter() -> None:
    table = extract_table(
        "CREATE TABLE S.T (ID NUMBER) PARTITION BY RANGE (ID) "
        "(PARTITION P1 VALUES LESS THAN (10)); ALTER TABLE S.T ADD (X NUMBER);"
    )
    assert table.diagnostics == ()
    assert "U:X" in table.columns
    assert "U:P1" in table.partitions


def test_partition_storage_does_not_become_table_storage_or_parallel_clause() -> None:
    table = extract_table(
        "CREATE TABLE S.T (ID NUMBER) PARTITION BY RANGE (ID) "
        "(PARTITION P1 VALUES LESS THAN (10) TABLESPACE TS1, "
        "PARTITION P2 VALUES LESS THAN (20) TABLESPACE TS2) PARALLEL 4;"
    )
    assert table.diagnostics == ()
    assert table.properties["tablespace"] is None
    assert table.properties["parallel"] == "PARALLEL 4"
    assert table.properties["partitioning"].endswith("PARTITION P2 VALUES LESS THAN (20) TABLESPACE TS2)")


def test_table_compression_clause_retains_explicit_mode() -> None:
    advanced = extract_table("CREATE TABLE S.T (ID NUMBER) ROW STORE COMPRESS ADVANCED;")
    basic = extract_table("CREATE TABLE S.T (ID NUMBER) COMPRESS BASIC;")
    assert advanced.diagnostics == basic.diagnostics == ()
    assert advanced.properties["compression"] == "ROW STORE COMPRESS ADVANCED"
    assert basic.properties["compression"] == "COMPRESS BASIC"


def test_alter_table_parallel_and_logging_context_update_physical_properties() -> None:
    table = extract_table(
        "CREATE TABLE S.T (ID NUMBER) PARALLEL 4 LOGGING; "
        "ALTER TABLE S.T NOPARALLEL; ALTER TABLE S.T NOLOGGING;"
    )
    assert table.diagnostics == ()
    assert table.properties["parallel"] == "NOPARALLEL"
    assert table.properties["logging"] == "NOLOGGING"

    invalid = extract_table("CREATE TABLE S.T (ID NUMBER); ALTER TABLE S.T PARALLEL 4 NOPARALLEL;")
    assert invalid.diagnostics == ("ALTER_TABLE_UNSUPPORTED",)
    move = extract_table("CREATE TABLE S.T (ID NUMBER); ALTER TABLE S.T MOVE PARALLEL 4;")
    assert move.diagnostics == ("ALTER_TABLE_UNSUPPORTED",)
    assert move.properties["parallel"] is None
    unrelated = extract_table("CREATE TABLE S.T (ID NUMBER); ALTER TABLE S.OTHER RENAME COLUMN X TO Y;")
    assert unrelated.diagnostics == ()


def test_alter_drop_column_list_is_atomic_and_removes_column_comments() -> None:
    table = extract_table(
        "CREATE TABLE S.T (A NUMBER, B NUMBER, C NUMBER); "
        "COMMENT ON COLUMN S.T.A IS 'old'; ALTER TABLE S.T DROP (A, B);"
    )
    assert table.diagnostics == ()
    assert set(table.columns) == {"U:C"}
    assert "U:A" not in table.comments

    unresolved = extract_table(
        "CREATE TABLE S.T (A NUMBER, B NUMBER); ALTER TABLE S.T DROP (A, MISSING);"
    )
    assert unresolved.diagnostics == ("ALTER_TABLE_DROP_UNSUPPORTED",)
    assert set(unresolved.columns) == {"U:A", "U:B"}

    dependent = extract_table(
        "CREATE TABLE S.T (A NUMBER, B NUMBER, CONSTRAINT PK_T PRIMARY KEY (A)); "
        "ALTER TABLE S.T DROP (A);"
    )
    assert dependent.diagnostics == ("ALTER_TABLE_DROP_UNSUPPORTED",)
    assert "U:A" in dependent.columns

    unrelated = extract_table(
        "CREATE TABLE S.T (A NUMBER, B NUMBER, CONSTRAINT PK_T PRIMARY KEY (B)); "
        "ALTER TABLE S.T DROP (A);"
    )
    assert unrelated.diagnostics == ()
    assert set(unrelated.columns) == {"U:B"}
    assert "U:PK_T" in unrelated.constraints


def test_temporary_table_on_commit_and_storage_properties() -> None:
    result = extract_table(
        "CREATE GLOBAL TEMPORARY TABLE S.T (ID NUMBER) "
        "ON COMMIT PRESERVE ROWS TABLESPACE TEMP_TBS STORAGE (INITIAL 64K);"
    )
    assert result.properties["temporary"] == "GLOBAL TEMPORARY"
    assert result.properties["on_commit"] == "ON COMMIT PRESERVE ROWS"
    assert result.properties["tablespace"] == "TEMP_TBS"
    assert result.properties["storage"] == "STORAGE (INITIAL 64K)"


def test_context_only_file_targets_parent_table_without_guessing_other_sql() -> None:
    context = table_context_statements(
        "ALTER TABLE T ADD (X NUMBER); COMMENT ON COLUMN S.T.X IS 'x';", "S"
    )
    assert context is not None and len(context) == 2
    assert all(item.target_parts == ("U:S", "U:T") for item in context)
    assert table_context_statements("ALTER TABLE T ADD (X NUMBER); DROP TABLE T;", "S") is None


def test_named_partitions_keep_individual_source_definitions() -> None:
    source = ("CREATE TABLE S.T (ID NUMBER) PARTITION BY RANGE (ID) "
              "(PARTITION P1 VALUES LESS THAN (10), PARTITION P2 VALUES LESS THAN (MAXVALUE));")
    table = extract_table(source)
    assert table.diagnostics == ()
    assert table.partitions == {
        "U:P1": "PARTITION P1 VALUES LESS THAN (10)",
        "U:P2": "PARTITION P2 VALUES LESS THAN (MAXVALUE)",
    }
