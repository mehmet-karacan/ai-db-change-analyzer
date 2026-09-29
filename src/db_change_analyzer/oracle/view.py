"""Source-level VIEW query clauses; no inferred output types or expansion of *."""

from __future__ import annotations

from dataclasses import dataclass

from .ddl_tokens import DdlToken, code_tokens, enclosed, source_span, top_level_items


@dataclass(frozen=True, slots=True)
class ViewExtraction:
    declared_columns: tuple[str, ...]
    projection: tuple[str, ...]
    clauses: dict[str, str | None]
    diagnostics: tuple[str, ...]


_CLAUSES = {
    "FROM": "source_references", "WHERE": "where", "HAVING": "having",
    "UNION": "set_operators", "INTERSECT": "set_operators", "MINUS": "set_operators",
}
_JOIN_MODIFIERS = {"NATURAL", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS"}


def _top_level(tokens: tuple[DdlToken, ...], start: int = 0) -> list[tuple[int, str]]:
    depth = 0
    result: list[tuple[int, str]] = []
    for index in range(start, len(tokens)):
        token = tokens[index]
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
        elif depth == 0:
            result.append((index, token.upper))
    return result


def extract_view(source: str) -> ViewExtraction:
    tokens = code_tokens(source)
    if not tokens:
        return ViewExtraction((), (), {}, ("VIEW_LEXER_UNRESOLVED",))
    view_at = next((i for i, token in enumerate(tokens) if token.upper == "VIEW"), None)
    if view_at is None:
        return ViewExtraction((), (), {}, ("VIEW_DEFINITION_UNRESOLVED",))
    as_at = next((i for i in range(view_at + 1, len(tokens)) if tokens[i].upper == "AS"), None)
    if as_at is None:
        return ViewExtraction((), (), {}, ("VIEW_QUERY_UNRESOLVED",))
    declared: tuple[str, ...] = ()
    before_as = tokens[view_at + 1 : as_at]
    opening = next((i for i, token in enumerate(before_as) if token.text == "("), None)
    if opening is not None:
        body = enclosed(before_as, opening)
        if body is None:
            return ViewExtraction((), (), {}, ("VIEW_COLUMNS_UNBALANCED",))
        items = top_level_items(body[0])
        if items is None:
            return ViewExtraction((), (), {}, ("VIEW_COLUMNS_UNRESOLVED",))
        declared = tuple(source_span(source, item) for item in items)
    full_query = tokens[as_at + 1 :]
    terminator = next((i for i, token in enumerate(full_query) if token.text == ";"), len(full_query))
    query = full_query[:terminator]
    diagnostics = ("VIEW_CONTEXT_UNSUPPORTED",) if any(token.text != "/" for token in full_query[terminator + 1 :]) else ()
    tops = _top_level(query)
    first_select = next((i for i, upper in tops if upper == "SELECT"), None)
    if first_select is None:
        return ViewExtraction(declared, (), {}, ("VIEW_SELECT_UNRESOLVED",))
    clauses: dict[str, str | None] = {
        "source_references": None, "join": None, "where": None,
        "group_by": None, "having": None, "set_operators": None,
        "distinct": None, "with_clause": None, "analytic": None,
        "ordering": None, "row_limit": None, "check_option": None,
        "read_only": None, "force": None, "editioning": None,
    }
    preamble = {token.upper for token in tokens[:view_at]}
    clauses["force"] = "FORCE" if "FORCE" in preamble else "NO FORCE" if "NOFORCE" in preamble else None
    clauses["editioning"] = "EDITIONING" if "EDITIONING" in preamble else None
    if first_select > 0:
        clauses["with_clause"] = source_span(source, query[:first_select])
    clause_starts: list[tuple[int, str]] = []
    for position, (index, upper) in enumerate(tops):
        following = tops[position + 1][1] if position + 1 < len(tops) else ""
        if upper in _CLAUSES:
            clause_starts.append((index, _CLAUSES[upper]))
        elif upper == "GROUP" and following == "BY":
            clause_starts.append((index, "group_by"))
        elif upper == "ORDER" and following == "BY":
            clause_starts.append((index, "ordering"))
        elif upper in {"FETCH", "OFFSET"}:
            clause_starts.append((index, "row_limit"))
        elif upper == "WITH" and following in {"CHECK", "READ"} and index > first_select:
            clause_starts.append((index, "check_option" if following == "CHECK" else "read_only"))
    first_clause = min((i for i, _ in clause_starts if i > first_select), default=len(query))
    select_tokens = query[first_select + 1 : first_clause]
    if select_tokens and select_tokens[0].upper in {"DISTINCT", "UNIQUE"}:
        clauses["distinct"] = select_tokens[0].upper
        select_tokens = select_tokens[1:]
    projection_items = top_level_items(select_tokens)
    projection = tuple(source_span(source, item) for item in projection_items) if projection_items is not None else ()
    for number, (begin, key) in enumerate(clause_starts):
        end = clause_starts[number + 1][0] if number + 1 < len(clause_starts) else len(query)
        if end > begin and query[end - 1].text == ";":
            end -= 1
        value = source_span(source, query[begin:end])
        clauses[key] = (clauses[key] + " | " + value) if clauses[key] else value
    joins: list[tuple[int, int]] = []
    for position, (index, upper) in enumerate(tops):
        if upper != "JOIN":
            continue
        begin = position
        while begin > 0 and position - begin < 3 and tops[begin - 1][1] in _JOIN_MODIFIERS:
            begin -= 1
        joins.append((tops[begin][0], index))
    if joins:
        segments = []
        for position, (begin, join_at) in enumerate(joins):
            next_join = joins[position + 1][0] if position + 1 < len(joins) else len(query)
            next_clause = min((start for start, _ in clause_starts if start > join_at), default=len(query))
            segments.append(source_span(source, query[begin:min(next_join, next_clause)]))
        clauses["join"] = " | ".join(segments)
    analytic = []
    for index in range(1, len(query) - 1):
        if query[index].upper != "OVER" or query[index - 1].text != ")" or query[index + 1].text != "(":
            continue
        group = enclosed(query, index + 1)
        if group is not None:
            analytic.append(source_span(source, query[index:group[1]]))
    if analytic:
        clauses["analytic"] = " | ".join(analytic)
    return ViewExtraction(declared, projection, clauses, diagnostics)
