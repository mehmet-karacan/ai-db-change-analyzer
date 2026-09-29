from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..identities import canonical_identifier, object_key


IDENT = r'(?P<{name}>"(?:[^"]|"")*"|[A-Za-z][A-Za-z0-9_$#]*)'
TYPE_PATTERN = (
    r"MATERIALIZED\s+VIEW\s+LOG|MATERIALIZED\s+VIEW|PACKAGE\s+BODY|TYPE\s+BODY|"
    r"PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TABLE|SEQUENCE|INDEX|VIEW|TYPE|SYNONYM|DIRECTORY|DATABASE\s+LINK|USER|ROLE"
)
HEADER = re.compile(
    r"(?is)\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NO\s+)?FORCE\s+)?(?:(?:NON)?EDITIONABLE\s+)?(?:PUBLIC\s+)?"
    r"(?:(?:UNIQUE|BITMAP)\s+)?"
    rf"(?P<object_type>{TYPE_PATTERN})\s+"
    + IDENT.format(name="first")
    + rf"(?:\s*\.\s*{IDENT.format(name='second')})?"
)
ALTER_TRIGGER = re.compile(
    r"(?is)\bALTER\s+TRIGGER\s+"
    + IDENT.format(name="first")
    + rf"(?:\s*\.\s*{IDENT.format(name='second')})?\s+(?P<state>ENABLE|DISABLE)\b"
)


@dataclass(frozen=True, slots=True)
class ScanOccurrence:
    object_key: str
    object_type: str
    raw_schema: str | None
    raw_name: str
    schema_quoted: bool
    name_quoted: bool
    start_char: int
    end_char: int
    start_byte: int
    end_byte_exclusive: int
    start_line: int
    end_line: int
    fragment_sha256: str
    trailing_trigger_state: str | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    occurrences: tuple[ScanOccurrence, ...]
    unresolved_non_whitespace_bytes: int
    diagnostics: tuple[str, ...]


def _mask_sql(text: str) -> tuple[str, list[str]]:
    chars = list(text)
    masked = list(text)
    diagnostics: list[str] = []
    index = 0
    state = "normal"
    q_close = ""
    while index < len(chars):
        char = chars[index]
        following = chars[index + 1] if index + 1 < len(chars) else ""
        if state == "normal":
            prefix = text[index : index + 3].lower()
            nq_prefix = text[index : index + 4].lower()
            if char == '"':
                state = "double"
                index += 1
                continue
            if char == "-" and following == "-":
                masked[index] = masked[index + 1] = " "
                state = "line_comment"
                index += 2
                continue
            if char == "/" and following == "*":
                masked[index] = masked[index + 1] = " "
                state = "block_comment"
                index += 2
                continue
            if char == "'":
                masked[index] = " "
                state = "single"
                index += 1
                continue
            q_offset = 2 if prefix.startswith("q'") else 3 if nq_prefix.startswith("nq'") else None
            if q_offset is not None and index + q_offset < len(chars):
                delimiter = chars[index + q_offset]
                pairs = {"[": "]", "(": ")", "{": "}", "<": ">"}
                q_close = pairs.get(delimiter, delimiter)
                for position in range(index, index + q_offset + 1):
                    masked[position] = " "
                index += q_offset + 1
                state = "qquote"
                continue
            index += 1
            continue
        if state == "line_comment":
            if char in "\r\n":
                state = "normal"
            else:
                masked[index] = " "
            index += 1
            continue
        if state == "double":
            if char == '"' and following == '"':
                index += 2
            elif char == '"':
                state = "normal"
                index += 1
            else:
                index += 1
            continue
        if state == "block_comment":
            if char == "*" and following == "/":
                masked[index] = masked[index + 1] = " "
                state = "normal"
                index += 2
            else:
                if char not in "\r\n":
                    masked[index] = " "
                index += 1
            continue
        if state == "single":
            masked[index] = " " if char not in "\r\n" else char
            if char == "'" and following == "'":
                masked[index + 1] = " "
                index += 2
            elif char == "'":
                state = "normal"
                index += 1
            else:
                index += 1
            continue
        if state == "qquote":
            masked[index] = " " if char not in "\r\n" else char
            if char == q_close and following == "'":
                masked[index + 1] = " "
                state = "normal"
                index += 2
            else:
                index += 1
    if state not in {"normal", "line_comment"}:
        diagnostics.append(f"UNTERMINATED_{state.upper()}")
    return "".join(masked), diagnostics


def mask_sql_code(text: str) -> str:
    """Preserve character offsets while hiding non-code and quoted names.

    This is for finding SQL keywords only. It does not parse Oracle syntax.
    Empty output indicates an unterminated literal, comment or identifier.
    """
    masked, diagnostics = _mask_sql(text)
    if diagnostics:
        return ""
    chars = list(masked)
    index = 0
    while index < len(chars):
        if chars[index] != '"':
            index += 1
            continue
        start = index
        index += 1
        while index < len(chars):
            if chars[index] == '"':
                if index + 1 < len(chars) and chars[index + 1] == '"':
                    index += 2
                    continue
                index += 1
                break
            index += 1
        else:
            return ""
        for position in range(start, index):
            chars[position] = " "
    return "".join(chars)


def _identifier(value: str) -> tuple[str, bool]:
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('""', '"'), True
    return value, False


def _byte_offsets(text: str) -> list[int]:
    offsets = [0]
    total = 0
    for char in text:
        total += len(char.encode("utf-8"))
        offsets.append(total)
    return offsets


def scan(raw: bytes, *, default_schema: str | None) -> ScanResult:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return ScanResult((), len(raw), ("ENCODING_UNRESOLVED",))
    masked, diagnostics = _mask_sql(text)
    headers = list(HEADER.finditer(masked))
    offsets = _byte_offsets(text)
    trigger_states: dict[tuple[str | None, str], str] = {}
    for match in ALTER_TRIGGER.finditer(masked):
        first, first_quoted = _identifier(match.group("first"))
        second = match.group("second")
        if second:
            name, _ = _identifier(second)
            schema = first
        else:
            name = first
            schema = default_schema
        trigger_states[(schema, name)] = match.group("state").upper()
    occurrences: list[ScanOccurrence] = []
    for position, match in enumerate(headers):
        start = match.start()
        end = headers[position + 1].start() if position + 1 < len(headers) else len(text)
        raw_type = re.sub(r"\s+", "_", match.group("object_type").upper())
        object_type = {"PACKAGE": "PACKAGE_SPEC"}.get(raw_type, raw_type)
        first, first_quoted = _identifier(match.group("first"))
        second_raw = match.group("second")
        if second_raw:
            second, second_quoted = _identifier(second_raw)
            raw_schema, schema_quoted = first, first_quoted
            raw_name, name_quoted = second, second_quoted
        else:
            raw_schema, schema_quoted = default_schema, False
            raw_name, name_quoted = first, first_quoted
        schema_identifier = canonical_identifier(raw_schema, quoted=schema_quoted) if raw_schema else None
        name_identifier = canonical_identifier(raw_name, quoted=name_quoted)
        key = object_key("PUBLIC" if raw_type == "SYNONYM" and "PUBLIC" in match.group(0).upper() else "SCHEMA", schema_identifier, object_type, name_identifier)
        fragment = raw[offsets[start] : offsets[end]]
        start_line = text.count("\n", 0, start) + 1
        end_line = text.count("\n", 0, end) + (0 if end > 0 and text[end - 1] == "\n" else 1)
        state = trigger_states.get((raw_schema, raw_name)) if object_type == "TRIGGER" else None
        occurrences.append(
            ScanOccurrence(
                key,
                object_type,
                raw_schema,
                raw_name,
                schema_quoted,
                name_quoted,
                start,
                end,
                offsets[start],
                offsets[end],
                start_line,
                max(start_line, end_line),
                hashlib.sha256(fragment).hexdigest(),
                state,
            )
        )
    if headers:
        unresolved = len(raw[: offsets[headers[0].start()]].strip())
    else:
        unresolved = len(raw.strip())
    if unresolved:
        diagnostics.append("UNRESOLVED_PREFIX_OR_ARTIFACT")
    return ScanResult(tuple(occurrences), unresolved, tuple(diagnostics))
