from __future__ import annotations

import re

from .scanner import mask_sql_code


_INTEGER = r"[+-]?\d+(?![\w.$#])"
_OPTIONS: dict[str, str] = {
    "start_with": rf"\bSTART\s+WITH\s+({_INTEGER})",
    "increment_by": rf"\bINCREMENT\s+BY\s+({_INTEGER})",
    "min_value": rf"\b(MINVALUE\s+{_INTEGER}|NOMINVALUE)\b",
    "max_value": rf"\b(MAXVALUE\s+{_INTEGER}|NOMAXVALUE)\b",
    "cache": rf"\b(CACHE\s+{_INTEGER}|NOCACHE)\b",
    "cycle": r"\b(NOCYCLE|CYCLE)\b",
    "order": r"\b(NOORDER|ORDER)\b",
    "scale": r"\b(NOSCALE|SCALE)\b",
    "extend": r"\b(NOEXTEND|EXTEND)\b",
    "shard": r"\b(NOSHARD|SHARD)\b",
    "session": r"\b(SESSION|GLOBAL)\b",
    "keep": r"\b(NOKEEP|KEEP)\b",
}


def extract_sequence_options(fragment: str) -> dict[str, str | None]:
    """Read explicitly present sequence options from parser checked SQL.

    An absent or ambiguous option remains unknown. Values are kept as source
    strings so large Oracle integers never pass through floating point.
    """
    code = mask_sql_code(fragment)
    options: dict[str, str | None] = {}
    for key, pattern in _OPTIONS.items():
        matches = list(re.finditer(pattern, code, re.IGNORECASE)) if code else []
        if len(matches) != 1:
            options[key] = None
            continue
        match = matches[0]
        options[key] = fragment[match.start(1) : match.end(1)].upper()
    return options
