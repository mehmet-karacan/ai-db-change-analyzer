"""Conservative CREATE TABLE component extraction from Oracle lexer tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ddl_tokens import DdlToken, code_tokens, enclosed, identifier, source_span, top_level_items


@dataclass(frozen=True, slots=True)
class TableExtraction:
    columns: dict[str, dict[str, str | None]]
    constraints: dict[str, str]
    properties: dict[str, str | None]
    comments: dict[str, str]
    diagnostics: tuple[str, ...]
    partitions: dict[str, str] = field(default_factory=dict)
    unresolved_columns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TableContextStatement:
    target_parts: tuple[str, ...]
    text: str
    start_char: int
    end_char: int


_BOUNDARY = {"DEFAULT", "GENERATED", "CONSTRAINT", "PRIMARY", "UNIQUE", "REFERENCES", "CHECK", "COLLATE", "VISIBLE", "INVISIBLE", "NOT", "NULL"}
_NUMERIC = {"NUMBER", "NUMERIC", "DECIMAL", "DEC"}
_CHARACTER = {"CHAR", "NCHAR", "VARCHAR", "VARCHAR2", "NVARCHAR2", "RAW"}
_TABLE_PROPERTY_BOUNDARIES = {
    "ORGANIZATION", "ON", "TABLESPACE", "STORAGE", "LOB", "LOGGING", "NOLOGGING",
    "PARALLEL", "NOPARALLEL", "COMPRESS", "NOCOMPRESS", "ROW", "COLUMN",
}


def constraint_properties(definition: str) -> dict[str, str | None]:
    """Read only explicit, balanced clauses from a named table constraint."""
    result = {key: None for key in (
        "kind", "columns", "reference", "condition", "delete_rule", "enabled",
        "validation", "deferrable", "initially", "rely", "index_association",
    )}
    tokens = code_tokens(definition)
    if not tokens:
        return result
    start = 2 if tokens[0].upper == "CONSTRAINT" and len(tokens) > 2 else 0
    words = [token.upper for token in tokens]
    if words[start:start + 2] == ["PRIMARY", "KEY"]:
        result["kind"] = "PRIMARY KEY"
        column_start = start + 2
    elif words[start:start + 2] == ["FOREIGN", "KEY"]:
        result["kind"] = "FOREIGN KEY"
        column_start = start + 2
    elif words[start] in {"UNIQUE", "CHECK"}:
        result["kind"] = words[start]
        column_start = start + 1
    else:
        return result
    if column_start < len(tokens) and tokens[column_start].text == "(":
        group = enclosed(tokens, column_start)
        if group:
            if result["kind"] == "CHECK":
                result["condition"] = source_span(definition, group[0])
            else:
                columns = top_level_items(group[0])
                if columns and all(item and len(item) == 1 for item in columns):
                    result["columns"] = ", ".join(source_span(definition, item) for item in columns)
    depth = 0
    top: list[str | None] = []
    for token in tokens:
        if token.text == "(":
            depth += 1
        top.append(token.upper if depth == 0 else None)
        if token.text == ")":
            depth -= 1
    for index, word in enumerate(top):
        next_word = top[index + 1] if index + 1 < len(top) else None
        if word == "REFERENCES" and index + 1 < len(tokens):
            end = index + 1
            while end < len(tokens) and (top[end] not in {"ON", "ENABLE", "DISABLE", "VALIDATE", "NOVALIDATE", "DEFERRABLE", "NOT", "INITIALLY", "RELY", "NORELY", "USING"}):
                end += 1
            result["reference"] = source_span(definition, tokens[index + 1:end])
        elif word == "ON" and next_word == "DELETE" and index + 2 < len(tokens):
            last = index + 3 if index + 3 < len(tokens) and top[index + 2] == "SET" and top[index + 3] == "NULL" else index + 2
            result["delete_rule"] = source_span(definition, tokens[index:last + 1])
        elif word in {"ENABLE", "DISABLE"}:
            result["enabled"] = word
        elif word in {"VALIDATE", "NOVALIDATE"}:
            result["validation"] = word
        elif word == "NOT" and next_word == "DEFERRABLE":
            result["deferrable"] = "NOT DEFERRABLE"
        elif word == "DEFERRABLE" and (index == 0 or top[index - 1] != "NOT"):
            result["deferrable"] = "DEFERRABLE"
        elif word == "INITIALLY" and next_word in {"IMMEDIATE", "DEFERRED"}:
            result["initially"] = f"INITIALLY {next_word}"
        elif word in {"RELY", "NORELY"}:
            result["rely"] = word
        elif word == "USING" and next_word == "INDEX":
            result["index_association"] = source_span(definition, tokens[index:])
    return result


def _name_parts(tokens: tuple[DdlToken, ...]) -> tuple[str, ...]:
    return tuple(("Q:" if identifier(token)[1] else "U:") + identifier(token)[0] for token in tokens if token.text != ".")


def table_context_statements(source: str, default_schema: str | None) -> tuple[TableContextStatement, ...] | None:
    """Recognize a file made solely of ALTER TABLE / COMMENT ON statements."""
    tokens = code_tokens(source)
    if not tokens:
        return None
    statements = top_level_items(tokens, ";")
    if not statements:
        return None
    result: list[TableContextStatement] = []
    for statement in statements:
        if not statement:
            continue
        words = tuple(token.upper for token in statement)
        if words[:2] == ("ALTER", "TABLE"):
            boundary = next((i for i in range(2, len(words)) if words[i] in {"ADD", "MODIFY", "DROP", "PARALLEL", "NOPARALLEL", "LOGGING", "NOLOGGING"}), None)
            parts = _name_parts(statement[2:boundary]) if boundary is not None else ()
        elif words[:3] in {("COMMENT", "ON", "COLUMN"), ("COMMENT", "ON", "TABLE")}:
            boundary = next((i for i in range(3, len(words)) if words[i] == "IS"), None)
            parts = _name_parts(statement[3:boundary]) if boundary is not None else ()
            if words[2] == "COLUMN":
                parts = parts[:-1]
        else:
            return None
        if not parts or len(parts) > 2:
            return None
        if len(parts) == 1:
            if not default_schema:
                return None
            parts = ("U:" + default_schema.upper(), *parts)
        result.append(TableContextStatement(parts, source_span(source, statement), statement[0].start, statement[-1].end))
    return tuple(result) if result else None


def _constraint(source: str, item: tuple[DdlToken, ...], constraints: dict[str, str], diagnostics: list[str]) -> None:
    if item[0].upper == "CONSTRAINT" and len(item) > 2:
        name, quoted = identifier(item[1])
        key = ("Q:" if quoted else "U:") + name
        if key in constraints:
            diagnostics.append("DUPLICATE_CONSTRAINT")
        else:
            constraints[key] = source_span(source, item)
    else:
        diagnostics.append("UNNAMED_CONSTRAINT_UNRESOLVED")


def _constraint_uses_any_column(constraints: dict[str, str], keys: set[str]) -> bool:
    for definition in constraints.values():
        tokens = code_tokens(definition)
        if not tokens:
            return True
        for token in tokens:
            parts = _name_parts((token,))
            if parts and parts[0] in keys:
                return True
    return False


def _apply_context(
    source: str, context: tuple[DdlToken, ...], table_name: tuple[str, ...],
    columns: dict[str, dict[str, str | None]], constraints: dict[str, str],
    comments: dict[str, str], properties: dict[str, str | None],
    diagnostics: list[str], unresolved_columns: set[str],
) -> None:
    statements = top_level_items(context, ";")
    if statements is None:
        diagnostics.append("TABLE_CONTEXT_UNBALANCED")
        return
    for raw_statement in statements:
        statement = tuple(token for token in raw_statement if token.text != "/")
        if not statement:
            continue
        words = tuple(token.upper for token in statement)
        if words[:2] == ("ALTER", "TABLE"):
            action = next((i for i in range(2, len(words)) if words[i] in {"ADD", "MODIFY", "DROP", "PARALLEL", "NOPARALLEL", "LOGGING", "NOLOGGING"}), None)
            if action is None:
                name_end = 5 if len(table_name) == 2 else 3
                if _name_parts(statement[2:name_end]) == table_name:
                    diagnostics.append("ALTER_TABLE_UNSUPPORTED")
                continue
            target_parts = _name_parts(statement[2:action])
            if target_parts != table_name:
                if target_parts[:len(table_name)] == table_name:
                    diagnostics.append("ALTER_TABLE_UNSUPPORTED")
                continue
            payload = statement[action + 1 :]
            if words[action] in {"ADD", "MODIFY"}:
                if payload and payload[0].text == "(":
                    group = enclosed(payload, 0)
                    items = top_level_items(group[0]) if group else None
                else:
                    items = (payload,)
                if not items:
                    diagnostics.append("ALTER_TABLE_ITEMS_UNRESOLVED")
                    continue
                for item in items:
                    if not item:
                        diagnostics.append("ALTER_TABLE_ITEMS_UNRESOLVED")
                    elif item[0].upper in {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK"}:
                        if words[action] == "ADD":
                            _constraint(source, item, constraints, diagnostics)
                        else:
                            diagnostics.append("ALTER_CONSTRAINT_MODIFY_UNSUPPORTED")
                    else:
                        name, quoted = identifier(item[0])
                        key = ("Q:" if quoted else "U:") + name
                        if words[action] == "MODIFY" and len(item) >= 2:
                            modifier = tuple(token.upper for token in item[1:])
                            if modifier in {("NOT", "NULL"), ("NULL",)}:
                                if key in columns:
                                    columns[key]["nullable"] = "NOT NULL" if modifier[0] == "NOT" else "NULL"
                                else:
                                    diagnostics.append("ALTER_COLUMN_NOT_IN_CREATE")
                                continue
                            if modifier[0] == "DEFAULT":
                                begin = 2
                                on_null = len(modifier) >= 4 and modifier[1:3] == ("ON", "NULL")
                                if on_null:
                                    begin = 4
                                boundary = _top_level_position(item, _BOUNDARY - {"NULL"}, begin)
                                if begin < len(item) and boundary is None and key in columns:
                                    columns[key]["default"] = source_span(source, item[begin:])
                                    columns[key]["default_on_null"] = "DEFAULT ON NULL" if on_null else None
                                elif key not in columns:
                                    diagnostics.append("ALTER_COLUMN_NOT_IN_CREATE")
                                else:
                                    diagnostics.append("ALTER_COLUMN_PARTIAL_UNRESOLVED")
                                    unresolved_columns.add(key)
                                continue
                        parsed = _column(source, item)
                        if parsed is None or parsed[1]["data_type"] in {"NOT", "NULL", "DEFAULT"}:
                            diagnostics.append("ALTER_COLUMN_PARTIAL_UNRESOLVED")
                            unresolved_columns.add(key)
                        else:
                            key, detail = parsed
                            if words[action] == "ADD":
                                if key in columns:
                                    diagnostics.append("DUPLICATE_COLUMN")
                                else:
                                    detail["position"] = str(len(columns) + 1)
                                    columns[key] = detail
                            elif key in columns:
                                detail["position"] = columns[key]["position"]
                                columns[key] = {**columns[key], **{field: value for field, value in detail.items() if value is not None}}
                            else:
                                diagnostics.append("ALTER_COLUMN_NOT_IN_CREATE")
            elif words[action] == "DROP":
                if payload and payload[0].upper == "COLUMN" and len(payload) == 2:
                    key = _name_parts(payload[1:2])[0]
                    if key not in columns or _constraint_uses_any_column(constraints, {key}):
                        diagnostics.append("ALTER_TABLE_DROP_UNSUPPORTED")
                    else:
                        columns.pop(key)
                        comments.pop(key, None)
                elif payload and payload[0].text == "(":
                    group = enclosed(payload, 0)
                    items = top_level_items(group[0]) if group and group[1] == len(payload) else None
                    keys = tuple(_name_parts(item)[0] for item in items) if items and all(len(item) == 1 for item in items) else ()
                    if not keys or len(set(keys)) != len(keys) or any(key not in columns for key in keys) or _constraint_uses_any_column(constraints, set(keys)):
                        diagnostics.append("ALTER_TABLE_DROP_UNSUPPORTED")
                    else:
                        for key in keys:
                            columns.pop(key)
                            comments.pop(key, None)
                elif payload and payload[0].upper == "CONSTRAINT" and len(payload) == 2:
                    key = _name_parts(payload[1:2])[0]
                    if key in constraints:
                        constraints.pop(key)
                    else:
                        diagnostics.append("ALTER_TABLE_DROP_UNSUPPORTED")
                else:
                    diagnostics.append("ALTER_TABLE_DROP_UNSUPPORTED")
            elif words[action] in {"PARALLEL", "NOPARALLEL"}:
                if (words[action] == "NOPARALLEL" and not payload) or (words[action] == "PARALLEL" and (not payload or len(payload) == 1 and payload[0].text.isdecimal())):
                    properties["parallel"] = source_span(source, statement[action:])
                else:
                    diagnostics.append("ALTER_TABLE_UNSUPPORTED")
            elif words[action] in {"LOGGING", "NOLOGGING"}:
                if not payload:
                    properties["logging"] = words[action]
                else:
                    diagnostics.append("ALTER_TABLE_UNSUPPORTED")
        elif words[:3] in {("COMMENT", "ON", "COLUMN"), ("COMMENT", "ON", "TABLE")}:
            is_at = next((i for i in range(3, len(words)) if words[i] == "IS"), None)
            if is_at is None or is_at + 1 >= len(statement):
                diagnostics.append("COMMENT_UNRESOLVED")
                continue
            parts = _name_parts(statement[3:is_at])
            parent = parts[:-1] if words[2] == "COLUMN" else parts
            if parent != table_name:
                continue
            key = parts[-1] if words[2] == "COLUMN" else "TABLE"
            value_tokens = statement[is_at + 1 :]
            if len(value_tokens) == 1 and value_tokens[0].text == "''":
                comments.pop(key, None)
            else:
                comments[key] = source_span(source, value_tokens)
        else:
            diagnostics.append("TABLE_CONTEXT_UNSUPPORTED")


def _top_level_position(tokens: tuple[DdlToken, ...], candidates: set[str], start: int = 0) -> int | None:
    depth = 0
    for index in range(start, len(tokens)):
        token = tokens[index]
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
        elif depth == 0 and token.upper in candidates:
            return index
    return None


def _top_level_positions(tokens: tuple[DdlToken, ...]) -> tuple[int, ...]:
    depth = 0
    positions: list[int] = []
    for index, token in enumerate(tokens):
        if token.text == ")":
            depth -= 1
        if depth == 0:
            positions.append(index)
        if token.text == "(":
            depth += 1
    return tuple(positions)


def _column(source: str, item: tuple[DdlToken, ...]) -> tuple[str, dict[str, str | None]] | None:
    if len(item) < 2:
        return None
    name, quoted = identifier(item[0])
    key = ("Q:" if quoted else "U:") + name
    as_at = _top_level_position(item, {"AS"}, 1)
    virtual_group = enclosed(item, as_at + 1) if as_at is not None and as_at + 1 < len(item) and item[as_at + 1].text == "(" else None
    boundary = _top_level_position(item, _BOUNDARY | ({"AS"} if virtual_group else set()), 1)
    type_tokens = item[1:boundary] if boundary is not None else item[1:]
    if not type_tokens and not virtual_group:
        return None
    data_type = source_span(source, type_tokens) if type_tokens else None
    detail: dict[str, str | None] = {
        "definition": source_span(source, item), "data_type": data_type,
        "length": None, "length_semantics": None, "precision": None, "scale": None,
        "nullable": None, "default": None, "default_on_null": None,
        "identity": None, "virtual_expression": None, "visibility": None,
        "position": None, "collation": None,
    }
    if len(type_tokens) > 2 and type_tokens[1].text == "(":
        enclosed_type = enclosed(type_tokens, 1)
        if enclosed_type is not None:
            args = top_level_items(enclosed_type[0])
            if args and type_tokens[0].upper in _CHARACTER:
                first = args[0]
                if first and re.fullmatch(r"\+?\d+", first[0].text):
                    detail["length"] = first[0].text
                    if len(first) > 1 and first[1].upper in {"BYTE", "CHAR"}:
                        detail["length_semantics"] = first[1].upper
            elif args and type_tokens[0].upper in _NUMERIC:
                if args[0] and re.fullmatch(r"[+-]?\d+", source_span(source, args[0]).replace(" ", "")):
                    detail["precision"] = source_span(source, args[0]).replace(" ", "")
                if len(args) > 1 and args[1] and re.fullmatch(r"[+-]?\d+", source_span(source, args[1]).replace(" ", "")):
                    detail["scale"] = source_span(source, args[1]).replace(" ", "")
    suffix = item[boundary:] if boundary is not None else ()
    depth = 0
    for index, token in enumerate(suffix):
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
        if depth:
            continue
        if token.upper == "NOT" and index + 1 < len(suffix) and suffix[index + 1].upper == "NULL":
            detail["nullable"] = "NOT NULL"
        elif token.upper == "NULL" and (index == 0 or suffix[index - 1].upper not in {"ON", "NOT", "DEFAULT"}):
            detail["nullable"] = "NULL"
        elif token.upper == "DEFAULT":
            begin = index + 1
            if begin + 1 < len(suffix) and suffix[begin].upper == "ON" and suffix[begin + 1].upper == "NULL":
                detail["default_on_null"] = "DEFAULT ON NULL"
                begin += 2
            end = _top_level_position(suffix, _BOUNDARY - {"NULL"}, begin)
            detail["default"] = source_span(source, suffix[begin:end]) if end is not None else source_span(source, suffix[begin:])
        elif token.upper == "GENERATED":
            identity_at = _top_level_position(suffix, {"IDENTITY"}, index + 1)
            if identity_at is not None and suffix[identity_at - 1].upper == "AS":
                detail["identity"] = source_span(source, suffix[index:])
        elif token.upper == "AS" and index + 1 < len(suffix) and suffix[index + 1].text == "(":
            expression = enclosed(suffix, index + 1)
            if expression:
                detail["virtual_expression"] = source_span(source, expression[0])
        elif token.upper in {"VISIBLE", "INVISIBLE"}:
            detail["visibility"] = token.upper
        elif token.upper == "COLLATE" and index + 1 < len(suffix):
            detail["collation"] = suffix[index + 1].text
    return key, detail


def extract_table(source: str) -> TableExtraction:
    tokens = code_tokens(source)
    if tokens is None:
        return TableExtraction({}, {}, {}, {}, ("TABLE_LEXER_UNRESOLVED",))
    table_at = next((index for index, token in enumerate(tokens) if token.upper == "TABLE"), None)
    opening = next((index for index in range((table_at or 0) + 1, len(tokens)) if tokens[index].text == "("), None)
    if table_at is None or opening is None:
        return TableExtraction({}, {}, {}, {}, ("TABLE_DEFINITION_UNRESOLVED",))
    body = enclosed(tokens, opening)
    if body is None:
        return TableExtraction({}, {}, {}, {}, ("TABLE_BODY_UNBALANCED",))
    items = top_level_items(body[0])
    if items is None:
        return TableExtraction({}, {}, {}, {}, ("TABLE_ITEMS_UNBALANCED",))
    columns: dict[str, dict[str, str | None]] = {}
    constraints: dict[str, str] = {}
    diagnostics: list[str] = []
    unresolved_columns: set[str] = set()
    for position, item in enumerate(items, 1):
        if not item:
            diagnostics.append("EMPTY_TABLE_ITEM")
            continue
        if item[0].upper in {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK"}:
            _constraint(source, item, constraints, diagnostics)
            continue
        parsed = _column(source, item)
        if parsed is None:
            diagnostics.append("COLUMN_UNRESOLVED")
            continue
        key, detail = parsed
        if key in columns:
            diagnostics.append("DUPLICATE_COLUMN")
            continue
        detail["position"] = str(position)
        columns[key] = detail
    all_trailing = tokens[body[1]:]
    terminator = next((i for i, token in enumerate(all_trailing) if token.text == ";"), len(all_trailing))
    trailing = all_trailing[:terminator]
    properties: dict[str, str | None] = {
        "organization": None, "temporary": None, "on_commit": None,
        "tablespace": None, "storage": None, "logging": None,
        "compression": None, "parallel": None, "partitioning": None,
        "lob_storage": None, "external": None,
    }
    prefix_words = [token.upper for token in tokens[:table_at]]
    if "TEMPORARY" in prefix_words:
        properties["temporary"] = "PRIVATE TEMPORARY" if "PRIVATE" in prefix_words else "GLOBAL TEMPORARY" if "GLOBAL" in prefix_words else "TEMPORARY"
    top_level = _top_level_positions(trailing)
    for index in top_level:
        token = trailing[index]
        if token.upper == "ORGANIZATION" and index + 1 < len(trailing):
            properties["organization"] = trailing[index + 1].upper
            if trailing[index + 1].upper == "EXTERNAL":
                properties["external"] = "ORGANIZATION EXTERNAL"
        elif token.upper == "ON" and index + 2 < len(trailing) and trailing[index + 1].upper == "COMMIT":
            end = min(index + 4, len(trailing))
            properties["on_commit"] = source_span(source, trailing[index:end])
        elif token.upper == "TABLESPACE" and index + 1 < len(trailing):
            properties["tablespace"] = trailing[index + 1].text
        elif token.upper == "STORAGE" and index + 1 < len(trailing) and trailing[index + 1].text == "(":
            group = enclosed(trailing, index + 1)
            if group:
                properties["storage"] = source_span(source, trailing[index:group[1]])
        elif token.upper == "LOB" and index + 1 < len(trailing) and trailing[index + 1].text == "(":
            end = next((position for position in top_level if position > index and trailing[position].upper in _TABLE_PROPERTY_BOUNDARIES), len(trailing))
            properties["lob_storage"] = source_span(source, trailing[index:end])
        elif token.upper in {"LOGGING", "NOLOGGING"}:
            properties["logging"] = token.upper
        elif token.upper in {"PARALLEL", "NOPARALLEL"}:
            end = index + 2 if token.upper == "PARALLEL" and index + 1 < len(trailing) and trailing[index + 1].text.isdecimal() else index + 1
            properties["parallel"] = source_span(source, trailing[index:end])
        elif token.upper in {"ROW", "COLUMN"} and index + 2 < len(trailing) and trailing[index + 1].upper == "STORE" and trailing[index + 2].upper == "COMPRESS":
            end = index + 3
            if token.upper == "ROW" and end < len(trailing) and trailing[end].upper in {"BASIC", "ADVANCED"}:
                end += 1
            elif token.upper == "COLUMN" and end + 1 < len(trailing) and trailing[end].upper == "FOR" and trailing[end + 1].upper in {"QUERY", "ARCHIVE"}:
                end += 2
                if end < len(trailing) and trailing[end].upper in {"LOW", "HIGH"}:
                    end += 1
            properties["compression"] = source_span(source, trailing[index:end])
        elif token.upper in {"COMPRESS", "NOCOMPRESS"}:
            if index >= 2 and trailing[index - 1].upper == "STORE" and trailing[index - 2].upper in {"ROW", "COLUMN"}:
                continue
            end = index + 1
            if token.upper == "COMPRESS" and end < len(trailing) and trailing[end].upper in {"BASIC", "ADVANCED"}:
                end += 1
            elif token.upper == "COMPRESS" and end + 1 < len(trailing) and trailing[end].upper == "FOR" and trailing[end + 1].upper == "OLTP":
                end += 2
            properties["compression"] = source_span(source, trailing[index:end])
        elif token.upper == "PARTITION" and index + 1 < len(trailing) and trailing[index + 1].upper == "BY":
            end = next((position for position in top_level if position > index and trailing[position].upper in _TABLE_PROPERTY_BOUNDARIES), len(trailing))
            properties["partitioning"] = source_span(source, trailing[index:end])
    partitions: dict[str, str] = {}
    partition_at = next((i for i in top_level if i + 1 < len(trailing) and trailing[i].upper == "PARTITION" and trailing[i + 1].upper == "BY"), None)
    if partition_at is not None:
        for partition_opening in range(partition_at + 2, len(trailing)):
            if trailing[partition_opening].text != "(":
                continue
            group = enclosed(trailing, partition_opening)
            if group is None:
                diagnostics.append("TABLE_PARTITION_UNBALANCED")
                break
            items = top_level_items(group[0])
            if not items or not all(item and item[0].upper == "PARTITION" and len(item) > 1 for item in items):
                continue
            for item in items:
                name, quoted = identifier(item[1])
                key = ("Q:" if quoted else "U:") + name
                if key in partitions:
                    diagnostics.append("DUPLICATE_PARTITION")
                else:
                    partitions[key] = source_span(source, item)
            break
    comments: dict[str, str] = {}
    if terminator < len(all_trailing):
        table_name = _name_parts(tokens[table_at + 1 : opening])
        _apply_context(source, all_trailing[terminator + 1 :], table_name, columns, constraints, comments, properties, diagnostics, unresolved_columns)
    return TableExtraction(columns, constraints, properties, comments, tuple(diagnostics), partitions, tuple(sorted(unresolved_columns)))
