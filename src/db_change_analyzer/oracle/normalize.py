from __future__ import annotations

from .scanner import _mask_sql


def token_normalized(text: str) -> str:
    masked, _ = _mask_sql(text)
    output: list[str] = []
    whitespace = False
    for original, visible in zip(text, masked):
        if original.isspace() and visible.isspace():
            whitespace = True
            continue
        if whitespace and output:
            output.append(" ")
        output.append(original)
        whitespace = False
    return "".join(output).strip()


def classify_text_change(before: str, after: str) -> str:
    if before == after:
        return "unchanged"
    if token_normalized(before) == token_normalized(after):
        return "format_only"
    return "structural_or_logic"
