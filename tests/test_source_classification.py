from db_change_analyzer.source_classification import sequence_start_value_only, whitespace_only_source_change


def test_sequence_start_only_change_is_deterministic() -> None:
    old = "CREATE SEQUENCE GPU_USER.SEQ_A START WITH 3058 INCREMENT BY 1 NOCACHE;"
    new = "CREATE SEQUENCE GPU_USER.SEQ_A START WITH 3101 INCREMENT BY 1 NOCACHE;"
    assert sequence_start_value_only(old, new) == ("3058", "3101")


def test_sequence_classifier_fails_closed_for_other_changes() -> None:
    old = "CREATE SEQUENCE GPU_USER.SEQ_A START WITH 3058 INCREMENT BY 1 NOCACHE;"
    assert sequence_start_value_only(old, old) is None
    assert sequence_start_value_only(old, old.replace("INCREMENT BY 1", "INCREMENT BY 2")) is None
    assert sequence_start_value_only(old, old.replace("SEQUENCE", "TABLE")) is None
    assert sequence_start_value_only(old + " -- START WITH 9", old.replace("3058", "3101") + " -- START WITH 9") == ("3058", "3101")


def test_sequence_start_ignores_literal_comment_and_quoted_identifier_tokens() -> None:
    old = '''CREATE SEQUENCE "START WITH 7" START WITH -999999999999999999999999999999 NOCACHE; -- START WITH 8'''
    new = old.replace("-999999999999999999999999999999", "+123456789012345678901234567890")
    assert sequence_start_value_only(old, new) == (
        "-999999999999999999999999999999", "+123456789012345678901234567890"
    )
    assert sequence_start_value_only(old, new.replace("NOCACHE", "CACHE 2")) is None
    assert sequence_start_value_only(old, new.replace("-- START WITH 8", "-- START WITH 9")) is None
    assert sequence_start_value_only(old, new.replace("START WITH +123", "START WITH 1.23")) is None


def test_sequence_start_does_not_read_q_quote_or_string_literal() -> None:
    old = "CREATE SEQUENCE S START WITH 3; -- q'[START WITH 4]'"
    new = old.replace("WITH 3", "WITH 2")
    assert sequence_start_value_only(old, new) == ("3", "2")
    assert sequence_start_value_only(
        "CREATE SEQUENCE S; -- START WITH 3", "CREATE SEQUENCE S; -- START WITH 2"
    ) is None


def test_sequence_start_handles_comment_markers_inside_quoted_name() -> None:
    old = 'CREATE SEQUENCE "S--/*START WITH 7" START WITH 3 NOCACHE;'
    new = old.replace("WITH 3", "WITH 2")
    assert sequence_start_value_only(old, new) == ("3", "2")


def test_whitespace_only_change_requires_exact_visible_tokens_and_no_hidden_source() -> None:
    old = "CREATE TABLE S.T (ID NUMBER, NAME VARCHAR2(20));"
    assert whitespace_only_source_change(old, "CREATE  TABLE S.T\n( ID NUMBER, NAME VARCHAR2(20) );")
    assert not whitespace_only_source_change(old, old.replace("20", "30"))
    assert not whitespace_only_source_change(old, old.replace("TABLE", "table"))
    assert not whitespace_only_source_change(old + " /*+ PARALLEL */", old + " /*+ NO_PARALLEL */")
    assert not whitespace_only_source_change(old + " -- note", old + " -- revised")
    assert not whitespace_only_source_change("CREATE VIEW V AS SELECT 'a b' X FROM T;", "CREATE VIEW V AS SELECT 'a  b' X FROM T;")
