from db_change_analyzer.oracle.index import extract_index


def test_index_target_key_order_expression_direction_and_physical_options() -> None:
    source = '''CREATE UNIQUE INDEX S.I ON S.T (A DESC, NVL(B, 'x,y') ASC)
      INVISIBLE TABLESPACE IDX_TBS NOLOGGING PARALLEL 4;'''
    index = extract_index(source)
    assert index.diagnostics == ()
    assert index.target == "S.T"
    assert index.unique == "UNIQUE"
    assert index.keys == ("A", "NVL(B, 'x,y')")
    assert index.directions == ("DESC", "ASC")
    assert index.visibility == "INVISIBLE"
    assert index.properties["tablespace"] == "IDX_TBS"
    assert index.properties["logging"] == "NOLOGGING"


def test_index_unbalanced_expression_is_not_claimed() -> None:
    index = extract_index("CREATE INDEX I ON T (A, NVL(B, 1);")
    assert index.diagnostics
    assert index.keys == ()


def test_related_alter_index_updates_visibility_without_touching_other_indexes() -> None:
    source = """CREATE INDEX S.I ON S.T (ID);
    ALTER INDEX S.I INVISIBLE;
    ALTER INDEX S.OTHER VISIBLE;
    """
    index = extract_index(source)
    assert index.diagnostics == ()
    assert index.visibility == "INVISIBLE"


def test_index_storage_clause_is_bounded_and_recorded() -> None:
    index = extract_index("CREATE INDEX S.I ON S.T (ID) STORAGE (INITIAL 64K NEXT 128K);")
    assert index.diagnostics == ()
    assert index.properties["storage"] == "STORAGE (INITIAL 64K NEXT 128K)"


def test_local_partition_clause_retains_partition_detail_without_claiming_global_tablespace() -> None:
    source = "CREATE INDEX S.I ON S.T (ID) LOCAL (PARTITION P1 TABLESPACE TS1, PARTITION P2 TABLESPACE TS2);"
    index = extract_index(source)
    assert index.diagnostics == ()
    assert index.properties["partitioning"] == "LOCAL (PARTITION P1 TABLESPACE TS1, PARTITION P2 TABLESPACE TS2)"
    assert index.properties["tablespace"] is None


def test_global_partition_clause_stops_before_independent_tablespace() -> None:
    source = ("CREATE INDEX S.I ON S.T (ID) GLOBAL PARTITION BY RANGE (ID) "
              "(PARTITION P1 VALUES LESS THAN (10), PARTITION P2 VALUES LESS THAN (MAXVALUE)) TABLESPACE IDX_TBS;")
    index = extract_index(source)
    assert index.diagnostics == ()
    assert index.properties["partitioning"].endswith("PARTITION P2 VALUES LESS THAN (MAXVALUE))")
    assert index.properties["tablespace"] == "IDX_TBS"


def test_index_parallel_degree_and_compression_prefix_are_preserved() -> None:
    index = extract_index("CREATE INDEX S.I ON S.T (A, B) COMPRESS 1 PARALLEL 4;")
    assert index.diagnostics == ()
    assert index.properties["compression"] == "COMPRESS 1"
    assert index.properties["parallel"] == "PARALLEL 4"

    altered = extract_index(
        "CREATE INDEX S.I ON S.T (A, B) COMPRESS 1 PARALLEL 4; "
        "ALTER INDEX S.I PARALLEL 8; ALTER INDEX S.I REBUILD COMPRESS 2;"
    )
    assert altered.diagnostics == ()
    assert altered.properties["parallel"] == "PARALLEL 8"
    assert altered.properties["compression"] == "COMPRESS 2"

    advanced = extract_index("CREATE INDEX S.I ON S.T (A, B) COMPRESS ADVANCED LOW;")
    assert advanced.diagnostics == ()
    assert advanced.properties["compression"] == "COMPRESS ADVANCED LOW"
