"""Conservative CREATE INDEX extraction from Oracle lexer tokens."""

from __future__ import annotations

from dataclasses import dataclass

from .ddl_tokens import code_tokens, enclosed, identifier, source_span, top_level_items


_PARTITION_BOUNDARIES = {
    "TABLESPACE", "STORAGE", "COMPRESS", "NOCOMPRESS", "LOGGING", "NOLOGGING",
    "PARALLEL", "NOPARALLEL", "VISIBLE", "INVISIBLE", "INDEXTYPE", "PARAMETERS",
    "LOCAL", "GLOBAL",
}


def _top_level_positions(tokens) -> tuple[int, ...]:
    depth = 0
    positions: list[int] = []
    for position, token in enumerate(tokens):
        if token.text == ")":
            depth -= 1
        if depth == 0:
            positions.append(position)
        if token.text == "(":
            depth += 1
    return tuple(positions)


def _physical_option(source: str, tokens, index: int) -> str:
    upper = tokens[index].upper
    end = index + 1
    if upper in {"PARALLEL", "COMPRESS"} and end < len(tokens) and tokens[end].text.isdecimal():
        end += 1
    elif upper == "COMPRESS" and end < len(tokens) and tokens[end].upper == "ADVANCED":
        end += 1
        if end < len(tokens) and tokens[end].upper in {"LOW", "HIGH"}:
            end += 1
    return source_span(source, tokens[index:end])


@dataclass(frozen=True, slots=True)
class IndexExtraction:
    target: str | None
    unique: str | None
    kind: str | None
    keys: tuple[str, ...]
    directions: tuple[str | None, ...]
    visibility: str | None
    properties: dict[str, str | None]
    diagnostics: tuple[str, ...]


def extract_index(source: str) -> IndexExtraction:
    tokens = code_tokens(source)
    empty = IndexExtraction(None, None, None, (), (), None, {}, ("INDEX_DEFINITION_UNRESOLVED",))
    if not tokens:
        return empty
    index_at = next((i for i, token in enumerate(tokens) if token.upper == "INDEX"), None)
    if index_at is None or index_at + 2 >= len(tokens):
        return empty
    before = {token.upper for token in tokens[:index_at]}
    unique = "UNIQUE" if "UNIQUE" in before else "NONUNIQUE"
    kind = "BITMAP" if "BITMAP" in before else "NORMAL"
    on_at = next((i for i in range(index_at + 1, len(tokens)) if tokens[i].upper == "ON"), None)
    if on_at is None:
        return empty
    opening = next((i for i in range(on_at + 1, len(tokens)) if tokens[i].text == "("), None)
    if opening is None or opening == on_at + 1:
        return empty
    target = source_span(source, tokens[on_at + 1 : opening])
    body = enclosed(tokens, opening)
    if body is None:
        return IndexExtraction(target, unique, kind, (), (), None, {}, ("INDEX_KEYS_UNBALANCED",))
    items = top_level_items(body[0])
    if items is None or not all(items):
        return IndexExtraction(target, unique, kind, (), (), None, {}, ("INDEX_KEYS_UNRESOLVED",))
    keys: list[str] = []
    directions: list[str | None] = []
    for item in items:
        direction = item[-1].upper if item[-1].upper in {"ASC", "DESC"} else None
        expression = item[:-1] if direction else item
        keys.append(source_span(source, expression))
        directions.append(direction)
    all_tail = tokens[body[1] :]
    terminator = next((i for i, token in enumerate(all_tail) if token.text == ";"), len(all_tail))
    tail = all_tail[:terminator]
    top_level = _top_level_positions(tail)
    visibility = next((tail[i].upper for i in top_level if tail[i].upper in {"VISIBLE", "INVISIBLE"}), None)
    properties: dict[str, str | None] = {
        "tablespace": None, "storage": None, "compression": None,
        "logging": None, "parallel": None, "partitioning": None,
        "domain_parameters": None,
    }
    for i in top_level:
        token = tail[i]
        if token.upper == "TABLESPACE" and i + 1 < len(tail):
            properties["tablespace"] = tail[i + 1].text
        elif token.upper == "STORAGE" and i + 1 < len(tail) and tail[i + 1].text == "(":
            group = enclosed(tail, i + 1)
            if group:
                properties["storage"] = source_span(source, tail[i:group[1]])
        elif token.upper in {"LOGGING", "NOLOGGING"}:
            properties["logging"] = token.upper
        elif token.upper in {"PARALLEL", "NOPARALLEL"}:
            properties["parallel"] = _physical_option(source, tail, i)
        elif token.upper in {"COMPRESS", "NOCOMPRESS"}:
            properties["compression"] = _physical_option(source, tail, i)
        elif token.upper in {"LOCAL", "GLOBAL"}:
            end = next((position for position in top_level if position > i and tail[position].upper in _PARTITION_BOUNDARIES), len(tail))
            properties["partitioning"] = source_span(source, tail[i:end])
        elif token.upper == "INDEXTYPE" and i + 1 < len(tail) and tail[i + 1].upper == "IS":
            kind = "DOMAIN"
        elif token.upper == "PARAMETERS" and i + 1 < len(tail) and tail[i + 1].text == "(":
            parameter = enclosed(tail, i + 1)
            if parameter:
                properties["domain_parameters"] = source_span(source, parameter[0])
    diagnostics: list[str] = []
    if terminator < len(all_tail):
        index_name = tuple(identifier(token) for token in tokens[index_at + 1 : on_at] if token.text != ".")
        statements = top_level_items(all_tail[terminator + 1 :], ";")
        if statements is None:
            diagnostics.append("INDEX_CONTEXT_UNBALANCED")
        else:
            for raw_statement in statements:
                statement = tuple(token for token in raw_statement if token.text != "/")
                if not statement:
                    continue
                words = tuple(token.upper for token in statement)
                if words[:2] != ("ALTER", "INDEX"):
                    diagnostics.append("INDEX_CONTEXT_UNSUPPORTED")
                    continue
                option = next((i for i in range(2, len(words)) if words[i] in {"VISIBLE", "INVISIBLE", "TABLESPACE", "LOGGING", "NOLOGGING", "PARALLEL", "NOPARALLEL", "COMPRESS", "NOCOMPRESS", "REBUILD"}), None)
                if option is None:
                    diagnostics.append("ALTER_INDEX_UNSUPPORTED")
                    continue
                altered_name = tuple(identifier(token) for token in statement[2:option] if token.text != ".")
                if altered_name != index_name:
                    continue
                for i, token in enumerate(statement[option:], option):
                    if token.upper in {"VISIBLE", "INVISIBLE"}:
                        visibility = token.upper
                    elif token.upper == "TABLESPACE" and i + 1 < len(statement):
                        properties["tablespace"] = statement[i + 1].text
                    elif token.upper in {"LOGGING", "NOLOGGING"}:
                        properties["logging"] = token.upper
                    elif token.upper in {"PARALLEL", "NOPARALLEL"}:
                        properties["parallel"] = _physical_option(source, statement, i)
                    elif token.upper in {"COMPRESS", "NOCOMPRESS"}:
                        properties["compression"] = _physical_option(source, statement, i)
    return IndexExtraction(target, unique, kind, tuple(keys), tuple(directions), visibility, properties, tuple(diagnostics))
