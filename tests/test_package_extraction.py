from db_change_analyzer.oracle.package import extract_package, extract_standalone_routine


def test_package_spec_public_signature_and_overload_limit() -> None:
    source = """CREATE OR REPLACE PACKAGE S.P AUTHID CURRENT_USER AS
      PROCEDURE RUN_JOB(P_ID IN NUMBER, P_LABEL OUT NOCOPY VARCHAR2 DEFAULT 'a,b');
      FUNCTION GET_JOB(P_ID NUMBER) RETURN VARCHAR2;
      PROCEDURE RUN_JOB(P_ID IN VARCHAR2);
    END P;
    /"""
    package = extract_package(source, "PACKAGE_SPEC")
    assert package.authid == "AUTHID CURRENT_USER"
    assert [routine.name for routine in package.routines] == ["RUN_JOB", "GET_JOB", "RUN_JOB"]
    assert package.routines[0].parameters[1].mode == "OUT"
    assert package.routines[0].parameters[1].nocopy
    assert package.routines[1].return_type == "VARCHAR2"
    assert "OVERLOAD_PAIRING_REQUIRES_REVIEW" in package.diagnostics


def test_standalone_routine_reuses_bounded_signature_and_body_facts() -> None:
    source = """CREATE OR REPLACE PROCEDURE S.RUN_JOB(P_ID NUMBER, P_LABEL VARCHAR2 DEFAULT 'x') AUTHID DEFINER IS
    BEGIN
      COMMIT;
    EXCEPTION WHEN OTHERS THEN
      ROLLBACK;
    END RUN_JOB;
    /"""
    routine = extract_standalone_routine(source, "PROCEDURE")
    assert routine.diagnostics == ()
    assert len(routine.routines) == 1
    assert [parameter.name for parameter in routine.routines[0].parameters] == ["P_ID", "P_LABEL"]
    assert routine.routines[0].parameters[1].default == "DEFAULT 'x'"
    assert routine.routines[0].transactions == ("COMMIT", "ROLLBACK")
    assert len(routine.routines[0].exception_handlers) == 1


def test_standalone_function_signature_is_extracted() -> None:
    routine = extract_standalone_routine(
        "CREATE OR REPLACE FUNCTION S.GET_VALUE(P_ID NUMBER) RETURN NUMBER IS BEGIN RETURN P_ID; END GET_VALUE; /",
        "FUNCTION",
    )
    assert routine.diagnostics == ()
    assert routine.routines[0].kind == "FUNCTION"
    assert routine.routines[0].return_type == "NUMBER"
    assert routine.routines[0].parameters[0].data_type == "NUMBER"


def test_package_body_counts_real_transaction_and_exception_nodes() -> None:
    source = """CREATE OR REPLACE PACKAGE BODY S.P AS
      PROCEDURE RUN_JOB IS
        V_TXT VARCHAR2(100) := 'COMMIT; ROLLBACK;';
      BEGIN
        EXECUTE IMMEDIATE 'UPDATE T SET X = 1';
        COMMIT;
      EXCEPTION WHEN OTHERS THEN
        ROLLBACK;
      END RUN_JOB;
    END P;
    /"""
    package = extract_package(source, "PACKAGE_BODY")
    assert package.diagnostics == ()
    assert len(package.routines) == 1
    routine = package.routines[0]
    assert len(routine.transactions) == 2
    assert len(routine.exception_handlers) == 1
    assert len(routine.dynamic_sql) == 1
    assert routine.dynamic_sql_profiles[0].as_text() == "mode=literal,target=static,bind=no,concat=no,validation=not_applicable,loop=no"
    assert routine.start_line < routine.end_line


def test_dynamic_sql_profiles_distinguish_static_bind_and_runtime_targets() -> None:
    source = """CREATE OR REPLACE PACKAGE BODY S.P AS
      PROCEDURE RUN_JOB(P_ID NUMBER, P_TABLE VARCHAR2) IS
      BEGIN
        EXECUTE IMMEDIATE 'UPDATE S.T SET X = 1';
        EXECUTE IMMEDIATE 'UPDATE S.T SET X = :1' USING P_ID;
        EXECUTE IMMEDIATE 'UPDATE ' || P_TABLE || ' SET X = :1' USING P_ID;
      END RUN_JOB;
    END P;
    /"""
    package = extract_package(source, "PACKAGE_BODY")
    profiles = [profile.as_text() for profile in package.routines[0].dynamic_sql_profiles]

    assert profiles == [
        "mode=literal,target=static,bind=no,concat=no,validation=not_applicable,loop=no",
        "mode=literal,target=static,bind=yes,concat=no,validation=not_applicable,loop=no",
        "mode=expression,target=runtime,bind=yes,concat=yes,validation=not_observed,loop=no",
    ]


def test_dynamic_sql_profile_marks_visible_identifier_validation_without_claiming_safety() -> None:
    source = """CREATE OR REPLACE PACKAGE BODY S.P AS
      PROCEDURE RUN_JOB(P_TABLE VARCHAR2) IS
      BEGIN
        EXECUTE IMMEDIATE 'UPDATE ' || DBMS_ASSERT.SQL_OBJECT_NAME(P_TABLE) || ' SET X = :1' USING 1;
      END RUN_JOB;
    END P;
    /"""
    package = extract_package(source, "PACKAGE_BODY")
    assert [profile.as_text() for profile in package.routines[0].dynamic_sql_profiles] == [
        "mode=expression,target=runtime,bind=yes,concat=yes,validation=visible,loop=no",
    ]


def test_dynamic_sql_profile_marks_loop_context() -> None:
    source = """CREATE OR REPLACE PACKAGE BODY S.P AS
      PROCEDURE RUN_JOB IS
      BEGIN
        FOR I IN 1..2 LOOP
          EXECUTE IMMEDIATE 'UPDATE S.T SET X = 1';
        END LOOP;
      END RUN_JOB;
    END P;
    /"""
    package = extract_package(source, "PACKAGE_BODY")
    assert [profile.as_text() for profile in package.routines[0].dynamic_sql_profiles] == [
        "mode=literal,target=static,bind=no,concat=no,validation=not_applicable,loop=yes",
    ]


def test_wrapped_package_remains_opaque() -> None:
    package = extract_package("CREATE PACKAGE BODY P WRAPPED abc123", "PACKAGE_BODY")
    assert package.routines == ()
    assert package.diagnostics == ("WRAPPED_SOURCE_OPAQUE",)


def test_large_package_second_parse_is_limited_before_parent_process_work() -> None:
    package = extract_package("CREATE PACKAGE P AS " + (" " * 128_001), "PACKAGE_SPEC")
    assert package.routines == ()
    assert package.diagnostics == ("PACKAGE_EXTRACTION_SIZE_LIMIT",)


def test_package_constant_is_not_grouped_as_mutable_variable() -> None:
    package = extract_package("CREATE PACKAGE S.P AS C_LIMIT CONSTANT NUMBER := 10; V_COUNT NUMBER; END P; /", "PACKAGE_SPEC")
    assert len(package.declarations["constant"]) == 1
    assert len(package.declarations["variable_declaration"]) == 1


def test_package_body_structural_statements_ignore_keywords_in_literals() -> None:
    source = """CREATE PACKAGE BODY S.P AS
      PROCEDURE X IS V NUMBER;
      BEGIN
        V := 1;
        IF V > 0 THEN UPDATE T SET X = 1; END IF;
        WHILE V < 2 LOOP V := V + 1; END LOOP;
        HELPER;
        RAISE;
        V := LENGTH('INSERT DELETE CALL COMMIT RAISE');
      END X;
    END P; /"""
    package = extract_package(source, "PACKAGE_BODY")
    assert package.diagnostics == ()
    routine = package.routines[0]
    assert routine.sql_statements == ("UPDATE T SET X = 1",)
    assert routine.conditions == ("V > 0", "V < 2")
    assert len(routine.assignments) == 3
    assert len(routine.control_flow) == 2
    assert routine.raises == ("RAISE",)
    assert routine.call_references == ("HELPER",)
    assert routine.transactions == ()


def test_nested_routine_statement_is_not_assigned_to_parent() -> None:
    source = """CREATE PACKAGE BODY S.P AS
      PROCEDURE X IS
        PROCEDURE INNER_X IS BEGIN COMMIT; END INNER_X;
      BEGIN NULL; END X;
    END P; /"""
    package = extract_package(source, "PACKAGE_BODY")
    assert len(package.routines) == 1
    assert package.routines[0].transactions == ()
    assert "NESTED_ROUTINE_LIMITED" in package.diagnostics
