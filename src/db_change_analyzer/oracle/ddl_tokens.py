"""Offset preserving Oracle lexer helpers for conservative DDL extraction."""

from __future__ import annotations

from dataclasses import dataclass

from antlr4 import InputStream
from antlr4.error.ErrorListener import ErrorListener

from .generated.PlSqlLexer import PlSqlLexer


@dataclass(frozen=True, slots=True)
class DdlToken:
    text: str
    start: int
    end: int

    @property
    def upper(self) -> str:
        return self.text.upper()


class _Errors(ErrorListener):
    def __init__(self) -> None:
        self.count = 0

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802 - ANTLR API
        self.count += 1


def code_tokens(source: str) -> tuple[DdlToken, ...] | None:
    lexer = PlSqlLexer(InputStream(source))
    errors = _Errors()
    lexer.removeErrorListeners()
    lexer.addErrorListener(errors)
    tokens: list[DdlToken] = []
    while True:
        token = lexer.nextToken()
        if token.type == -1:
            break
        if token.channel == 0:
            tokens.append(DdlToken(token.text, token.start, token.stop + 1))
    return tuple(tokens) if not errors.count else None


def top_level_items(tokens: tuple[DdlToken, ...], separator: str = ",") -> tuple[tuple[DdlToken, ...], ...] | None:
    """Split only outside parentheses; reject unbalanced source."""
    items: list[tuple[DdlToken, ...]] = []
    current: list[DdlToken] = []
    depth = 0
    for token in tokens:
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
            if depth < 0:
                return None
        if token.text == separator and depth == 0:
            items.append(tuple(current))
            current = []
        else:
            current.append(token)
    if depth:
        return None
    items.append(tuple(current))
    return tuple(items)


def enclosed(tokens: tuple[DdlToken, ...], opening: int) -> tuple[tuple[DdlToken, ...], int] | None:
    if opening >= len(tokens) or tokens[opening].text != "(":
        return None
    depth = 0
    for index in range(opening, len(tokens)):
        if tokens[index].text == "(":
            depth += 1
        elif tokens[index].text == ")":
            depth -= 1
            if depth == 0:
                return tokens[opening + 1 : index], index + 1
    return None


def source_span(source: str, tokens: tuple[DdlToken, ...]) -> str:
    return source[tokens[0].start : tokens[-1].end].strip() if tokens else ""


def identifier(token: DdlToken) -> tuple[str, bool]:
    if token.text.startswith('"') and token.text.endswith('"'):
        return token.text[1:-1].replace('""', '"'), True
    return token.text.upper(), False
