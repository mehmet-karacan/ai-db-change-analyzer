from db_change_analyzer.oracle.package import extract_package


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
    assert routine.start_line < routine.end_line


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
