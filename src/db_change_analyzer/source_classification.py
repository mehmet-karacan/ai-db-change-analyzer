from __future__ import annotations

import re

from .oracle.ddl_tokens import code_tokens
from .oracle.scanner import mask_sql_code


_SEQUENCE_START = re.compile(r"\bSTART\s+WITH\s+([+-]?\d+)(?![\w.$#])", re.IGNORECASE)
_CREATE_SEQUENCE = re.compile(r"^\s*CREATE\s+SEQUENCE\b", re.IGNORECASE)


def whitespace_only_source_change(old_sql: str, new_sql: str) -> bool:
    """Prove a presentation-only edit without discarding hints or literals."""
    if old_sql == new_sql:
        return False
    old_tokens, new_tokens = code_tokens(old_sql), code_tokens(new_sql)
    if not old_tokens or not new_tokens or tuple(token.text for token in old_tokens) != tuple(token.text for token in new_tokens):
        return False
    for source, tokens in ((old_sql, old_tokens), (new_sql, new_tokens)):
        cursor = 0
        for token in tokens:
            if source[cursor:token.start].strip():
                return False
            cursor = token.end
        if source[cursor:].strip():
            return False
    return True


def sequence_start_value_only(old_sql: str, new_sql: str) -> tuple[str, str] | None:
    """Classify only an unambiguous numeric START WITH change.

    Everything else in both definitions must be byte-for-byte identical after
    replacing that one value. Ambiguous source stays on the normal AI path.
    """
    old_code, new_code = mask_sql_code(old_sql), mask_sql_code(new_sql)
    if not old_code or not new_code or not _CREATE_SEQUENCE.match(old_code) or not _CREATE_SEQUENCE.match(new_code):
        return None
    old_matches = list(_SEQUENCE_START.finditer(old_code))
    new_matches = list(_SEQUENCE_START.finditer(new_code))
    if len(old_matches) != 1 or len(new_matches) != 1:
        return None
    old_match, new_match = old_matches[0], new_matches[0]
    old_value = old_sql[old_match.start(1) : old_match.end(1)]
    new_value = new_sql[new_match.start(1) : new_match.end(1)]
    if old_value == new_value:
        return None
    old_masked = old_sql[: old_match.start(1)] + "<VALUE>" + old_sql[old_match.end(1) :]
    new_masked = new_sql[: new_match.start(1)] + "<VALUE>" + new_sql[new_match.end(1) :]
    if old_masked != new_masked:
        return None
    return old_value, new_value
