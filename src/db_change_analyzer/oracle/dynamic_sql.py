"""Bounded, source-only classification for EXECUTE IMMEDIATE expressions."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DynamicSqlProfile:
    """A display-safe summary; it never contains the dynamic SQL text."""

    mode: str
    target: str
    bind: bool
    concat: bool
    loop: bool = False
    validation: str = "unknown"

    def as_text(self) -> str:
        return (
            f"mode={self.mode},target={self.target},bind={'yes' if self.bind else 'no'},"
            f"concat={'yes' if self.concat else 'no'},validation={self.validation},"
            f"loop={'yes' if self.loop else 'no'}"
        )


def _outside_expression(text: str) -> str:
    """Return the expression before a top-level USING clause."""
    quote = False
    index = 0
    upper = text.upper()
    while index < len(text):
        char = text[index]
        if char == "'":
            if quote and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            quote = not quote
        elif not quote and re.match(r"\bUSING\b", upper[index:]):
            return text[:index].rstrip()
        index += 1
    return text.strip()


def _literal_value(expression: str) -> str | None:
    match = re.fullmatch(r"'((?:''|[^'])*)'", expression.strip(), re.S)
    if not match:
        return None
    return match.group(1).replace("''", "'")


def _has_static_target(statement: str) -> bool:
    statement = statement.strip()
    patterns = (
        r"\b(?:FROM|JOIN)\s+[\"A-Za-z][\w$#]*(?:\s*\.\s*[\"A-Za-z][\w$#]*)?",
        r"\b(?:INTO|UPDATE|TABLE|VIEW|INDEX|SEQUENCE)\s+[\"A-Za-z][\w$#]*(?:\s*\.\s*[\"A-Za-z][\w$#]*)?",
        r"\bDELETE\s+FROM\s+[\"A-Za-z][\w$#]*(?:\s*\.\s*[\"A-Za-z][\w$#]*)?",
    )
    return any(re.search(pattern, statement, re.I) for pattern in patterns)


def classify_dynamic_sql(statement: str, *, loop: bool = False) -> DynamicSqlProfile:
    """Classify only what is visible in one bounded source statement.

    The classifier deliberately reports a runtime candidate for concatenated
    expressions. It does not execute SQL, resolve runtime variables, or make
    an injection finding from syntax alone.
    """
    if not isinstance(statement, str):
        return DynamicSqlProfile("unknown", "unknown", False, False, loop, "unknown")
    match = re.match(r"(?is)^\s*EXECUTE\s+IMMEDIATE\s+(?P<expression>.*?);?\s*$", statement)
    if not match:
        return DynamicSqlProfile("unknown", "unknown", False, False, loop, "unknown")
    expression = _outside_expression(match.group("expression"))
    bind = bool(re.search(r"(?is)\bUSING\b", match.group("expression")))
    concat = "||" in expression
    literal = _literal_value(expression)
    if literal is not None:
        return DynamicSqlProfile("literal", "static" if _has_static_target(literal) else "unknown", bind, concat, loop, "not_applicable")
    if concat:
        validation = "visible" if re.search(r"(?is)\bDBMS_ASSERT\s*\.", expression) else "not_observed"
        return DynamicSqlProfile("expression", "runtime", bind, True, loop, validation)
    return DynamicSqlProfile("expression", "unknown", bind, False, loop, "not_observed")
