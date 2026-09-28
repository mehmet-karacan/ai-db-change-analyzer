from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass

from antlr4 import CommonTokenStream, InputStream
from antlr4.atn.PredictionMode import PredictionMode
from antlr4.error.ErrorListener import ErrorListener

from .generated.PlSqlLexer import PlSqlLexer
from .generated.PlSqlParser import PlSqlParser


class CollectingErrorListener(ErrorListener):
    def __init__(self) -> None:
        self.errors: list[dict[str, int | str]] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802 - ANTLR API
        self.errors.append({"line": int(line), "column": int(column), "code": "SYNTAX_ERROR"})


@dataclass(frozen=True, slots=True)
class ParseResult:
    ok: bool
    mode: str
    lexer_errors: tuple[dict[str, int | str], ...]
    parser_errors: tuple[dict[str, int | str], ...]
    token_count: int
    elapsed_ms: int


def _attempt(text: str, mode: PredictionMode) -> ParseResult:
    started = time.monotonic()
    lexer = PlSqlLexer(InputStream(text))
    lexer_listener = CollectingErrorListener()
    lexer.removeErrorListeners()
    lexer.addErrorListener(lexer_listener)
    tokens = CommonTokenStream(lexer)
    parser = PlSqlParser(tokens)
    parser_listener = CollectingErrorListener()
    parser.removeErrorListeners()
    parser.addErrorListener(parser_listener)
    parser._interp.predictionMode = mode
    parser.sql_script()
    tokens.fill()
    elapsed = int((time.monotonic() - started) * 1000)
    lexer_errors = tuple(lexer_listener.errors)
    parser_errors = tuple(parser_listener.errors)
    return ParseResult(not lexer_errors and not parser_errors, "SLL" if mode == PredictionMode.SLL else "LL", lexer_errors, parser_errors, len(tokens.tokens), elapsed)


def parse_text(text: str) -> ParseResult:
    first = _attempt(text, PredictionMode.SLL)
    if first.ok:
        return first
    return _attempt(text, PredictionMode.LL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument("--memory-mib", type=int, default=1024)
    args = parser.parse_args(argv)
    if os.name != "nt":
        import resource

        maximum = args.memory_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))
    raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        print(json.dumps({"ok": False, "code": "PARSE_UNIT_TOO_LARGE"}))
        return 2
    try:
        text = raw.decode(args.encoding, errors="strict")
    except (LookupError, UnicodeDecodeError):
        print(json.dumps({"ok": False, "code": "ENCODING_UNRESOLVED"}))
        return 2
    result = parse_text(text)
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
