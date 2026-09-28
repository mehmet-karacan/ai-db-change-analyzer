from __future__ import annotations

import re


_SEQUENCE_START = re.compile(r"\bSTART\s+WITH\s+([+-]?\d+)\b", re.IGNORECASE)
_CREATE_SEQUENCE = re.compile(r"^\s*CREATE\s+SEQUENCE\b", re.IGNORECASE)


def sequence_start_value_only(old_sql: str, new_sql: str) -> tuple[str, str] | None:
    """Classify only an unambiguous numeric START WITH change.

    Everything else in both definitions must be byte-for-byte identical after
    replacing that one value. Ambiguous source stays on the normal AI path.
    """
    if not _CREATE_SEQUENCE.match(old_sql) or not _CREATE_SEQUENCE.match(new_sql):
        return None
    old_matches = list(_SEQUENCE_START.finditer(old_sql))
    new_matches = list(_SEQUENCE_START.finditer(new_sql))
    if len(old_matches) != 1 or len(new_matches) != 1:
        return None
    old_match, new_match = old_matches[0], new_matches[0]
    old_value, new_value = old_match.group(1), new_match.group(1)
    if old_value == new_value:
        return None
    old_masked = old_sql[: old_match.start(1)] + "<VALUE>" + old_sql[old_match.end(1) :]
    new_masked = new_sql[: new_match.start(1)] + "<VALUE>" + new_sql[new_match.end(1) :]
    if old_masked != new_masked:
        return None
    return old_value, new_value
