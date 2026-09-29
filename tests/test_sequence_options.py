from db_change_analyzer.oracle.sequence import extract_sequence_options


def test_sequence_options_keep_explicit_values_and_large_signed_integers() -> None:
    source = (
        "CREATE SEQUENCE S START WITH -999999999999999999999999 "
        "INCREMENT BY +2 MINVALUE -999 MAXVALUE 999 CACHE 20 "
        "NOCYCLE NOORDER SCALE NOEXTEND SHARD SESSION KEEP;"
    )
    assert extract_sequence_options(source) == {
        "start_with": "-999999999999999999999999",
        "increment_by": "+2",
        "min_value": "MINVALUE -999",
        "max_value": "MAXVALUE 999",
        "cache": "CACHE 20",
        "cycle": "NOCYCLE",
        "order": "NOORDER",
        "scale": "SCALE",
        "extend": "NOEXTEND",
        "shard": "SHARD",
        "session": "SESSION",
        "keep": "KEEP",
    }


def test_sequence_options_ignore_misleading_tokens_and_ambiguity() -> None:
    source = '''CREATE SEQUENCE "CACHE 12" START WITH 4 NOCACHE; -- INCREMENT BY 9
    /* CYCLE */'''
    options = extract_sequence_options(source)
    assert options["start_with"] == "4"
    assert options["cache"] == "NOCACHE"
    assert options["increment_by"] is None
    assert options["cycle"] is None
    assert extract_sequence_options("CREATE SEQUENCE S CACHE 2 NOCACHE;")["cache"] is None
    assert extract_sequence_options("CREATE SEQUENCE S START WITH 1.2;")["start_with"] is None
