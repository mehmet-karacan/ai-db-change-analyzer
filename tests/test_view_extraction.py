from db_change_analyzer.oracle.view import extract_view


def test_view_declared_columns_projection_and_query_clauses() -> None:
    source = """CREATE OR REPLACE FORCE VIEW S.V (ID, NAME) AS
      SELECT DISTINCT A.ID, NVL(A.NAME, 'x,y') AS NAME
      FROM S.A A JOIN S.B B ON B.ID = A.ID
      WHERE A.ACTIVE = 1 GROUP BY A.ID, A.NAME
      HAVING COUNT(*) > 0 ORDER BY A.ID WITH READ ONLY;"""
    view = extract_view(source)
    assert view.diagnostics == ()
    assert view.declared_columns == ("ID", "NAME")
    assert view.projection == ("A.ID", "NVL(A.NAME, 'x,y') AS NAME")
    assert view.clauses["distinct"] == "DISTINCT"
    assert view.clauses["source_references"].startswith("FROM S.A A JOIN")
    assert view.clauses["where"] == "WHERE A.ACTIVE = 1"
    assert view.clauses["group_by"] == "GROUP BY A.ID, A.NAME"
    assert view.clauses["having"] == "HAVING COUNT(*) > 0"
    assert view.clauses["read_only"] == "WITH READ ONLY"
    assert view.clauses["force"] == "FORCE"


def test_view_star_does_not_invent_output_columns() -> None:
    view = extract_view("CREATE VIEW V AS SELECT * FROM T;")
    assert view.declared_columns == ()
    assert view.projection == ("*",)


def test_join_type_and_each_join_condition_have_separate_bounded_spans() -> None:
    source = ("CREATE VIEW S.V AS SELECT A.ID FROM A LEFT OUTER JOIN B ON B.ID = A.ID "
              "RIGHT JOIN C ON C.ID = B.ID WHERE A.ID > 0;")
    view = extract_view(source)
    assert view.diagnostics == ()
    assert view.clauses["join"] == ("LEFT OUTER JOIN B ON B.ID = A.ID | "
                                    "RIGHT JOIN C ON C.ID = B.ID")
    assert "WHERE" not in view.clauses["join"]


def test_analytic_windows_are_bounded_and_quoted_text_is_ignored() -> None:
    source = (
        "CREATE VIEW S.V AS SELECT ROW_NUMBER() OVER (PARTITION BY D ORDER BY X) RN, "
        "SUM(X) OVER (PARTITION BY D) SX, 'OVER (FAKE)' TXT FROM S.T;"
    )
    view = extract_view(source)
    assert view.diagnostics == ()
    assert view.clauses["analytic"] == (
        "OVER (PARTITION BY D ORDER BY X) | OVER (PARTITION BY D)"
    )
