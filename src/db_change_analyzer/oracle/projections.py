from __future__ import annotations

import re
from dataclasses import dataclass

from .scanner import ScanOccurrence


@dataclass(frozen=True, slots=True)
class Projection:
    object_key: str
    object_type: str
    properties: dict[str, str | list[str] | None]
    support: str


def project(occurrence: ScanOccurrence, fragment: str, *, parse_ok: bool) -> Projection:
    support = "structural" if parse_ok else "text_fallback"
    properties: dict[str, str | list[str] | None] = {
        "schema": occurrence.raw_schema,
        "name": occurrence.raw_name,
    }
    upper = fragment.upper()
    if occurrence.object_type == "SEQUENCE":
        for key, pattern in {
            "increment_by": r"\bINCREMENT\s+BY\s+(-?\d+)",
            "start_with": r"\bSTART\s+WITH\s+(-?\d+)",
            "cache": r"\bCACHE\s+(\d+)|\bNOCACHE\b",
        }.items():
            match = re.search(pattern, upper)
            properties[key] = match.group(1) if match and match.lastindex and match.group(1) else "NOCACHE" if match else None
        properties["cycle"] = "NOCYCLE" if "NOCYCLE" in upper else "CYCLE" if re.search(r"\bCYCLE\b", upper) else None
        properties["order"] = "NOORDER" if "NOORDER" in upper else "ORDER" if re.search(r"\bORDER\b", upper) else None
    elif occurrence.object_type == "TRIGGER":
        properties["enabled_state"] = occurrence.trailing_trigger_state
        target = re.search(r"(?is)\bON\s+(\"[^\"]+\"|[A-Z][A-Z0-9_$#.]*)", fragment)
        properties["target"] = target.group(1) if target else None
    elif occurrence.object_type == "INDEX":
        target = re.search(r"(?is)\bON\s+([^\s(]+)\s*\((.*?)\)", fragment)
        properties["target"] = target.group(1) if target else None
        properties["expression"] = target.group(2).strip() if target else None
        properties["unique"] = bool(re.search(r"(?is)\bCREATE\s+UNIQUE\s+INDEX\b", fragment))
    elif occurrence.object_type == "TABLE":
        open_index = fragment.find("(")
        close_index = fragment.rfind(")")
        properties["definition"] = fragment[open_index + 1 : close_index].strip() if 0 <= open_index < close_index else None
    elif occurrence.object_type in {"PACKAGE_SPEC", "PACKAGE_BODY", "PROCEDURE", "FUNCTION"}:
        properties["has_exception"] = bool(re.search(r"(?i)\bEXCEPTION\b", fragment))
        properties["has_commit"] = bool(re.search(r"(?i)\bCOMMIT\b", fragment))
        properties["has_rollback"] = bool(re.search(r"(?i)\bROLLBACK\b", fragment))
    else:
        properties["raw_clause"] = fragment.strip()
    return Projection(occurrence.object_key, occurrence.object_type, properties, support)
