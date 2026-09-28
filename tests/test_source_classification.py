from db_change_analyzer.source_classification import sequence_start_value_only


def test_sequence_start_only_change_is_deterministic() -> None:
    old = "CREATE SEQUENCE GPU_USER.SEQ_A START WITH 3058 INCREMENT BY 1 NOCACHE;"
    new = "CREATE SEQUENCE GPU_USER.SEQ_A START WITH 3101 INCREMENT BY 1 NOCACHE;"
    assert sequence_start_value_only(old, new) == ("3058", "3101")


def test_sequence_classifier_fails_closed_for_other_changes() -> None:
    old = "CREATE SEQUENCE GPU_USER.SEQ_A START WITH 3058 INCREMENT BY 1 NOCACHE;"
    assert sequence_start_value_only(old, old) is None
    assert sequence_start_value_only(old, old.replace("INCREMENT BY 1", "INCREMENT BY 2")) is None
    assert sequence_start_value_only(old, old.replace("SEQUENCE", "TABLE")) is None
    assert sequence_start_value_only(old + " -- START WITH 9", old.replace("3058", "3101") + " -- START WITH 9") is None
