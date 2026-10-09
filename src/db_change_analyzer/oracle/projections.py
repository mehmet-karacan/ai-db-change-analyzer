from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .ddl_tokens import code_tokens
from .index import extract_index
from .package import extract_package, extract_standalone_routine
from .scanner import ScanOccurrence
from .sequence import extract_sequence_options
from .table import extract_table
from .view import extract_view


@dataclass(frozen=True, slots=True)
class Projection:
    object_key: str
    object_type: str
    properties: dict[str, Any]
    support: str
    diagnostics: tuple[str, ...] = ()


def project(occurrence: ScanOccurrence, fragment: str, *, parse_ok: bool) -> Projection:
    support = "structural" if parse_ok else "text_fallback"
    properties: dict[str, Any] = {
        "schema": occurrence.raw_schema,
        "name": occurrence.raw_name,
    }
    if not parse_ok:
        properties["raw_clause"] = fragment.strip()
        return Projection(occurrence.object_key, occurrence.object_type, properties, support, ("PARSER_NOT_VERIFIED",))
    if occurrence.object_type in {"VIEW", "PACKAGE_SPEC", "PACKAGE_BODY"}:
        tokens = code_tokens(fragment)
        before_type = []
        for token in tokens:
            if token.upper in {"VIEW", "PACKAGE"}:
                break
            before_type.append(token.upper)
        properties["editionable"] = "NONEDITIONABLE" if "NONEDITIONABLE" in before_type else "EDITIONABLE" if "EDITIONABLE" in before_type else None
    upper = fragment.upper()
    if occurrence.object_type == "SEQUENCE":
        properties.update(extract_sequence_options(fragment))
    elif occurrence.object_type == "TABLE":
        extracted = extract_table(fragment)
        properties.update({"columns": extracted.columns, "constraints": extracted.constraints, "table_properties": extracted.properties, "comments": extracted.comments, "partitions": extracted.partitions, "_unresolved_columns": extracted.unresolved_columns})
        return Projection(occurrence.object_key, occurrence.object_type, properties, support, extracted.diagnostics)
    elif occurrence.object_type == "INDEX":
        extracted = extract_index(fragment)
        properties.update({
            "target": extracted.target, "unique": extracted.unique, "kind": extracted.kind,
            "keys": extracted.keys, "directions": extracted.directions, "visibility": extracted.visibility,
            "index_properties": extracted.properties,
        })
        return Projection(occurrence.object_key, occurrence.object_type, properties, support, extracted.diagnostics)
    elif occurrence.object_type == "VIEW":
        extracted = extract_view(fragment)
        properties.update({"declared_columns": extracted.declared_columns, "projection": extracted.projection, "query_clauses": extracted.clauses})
        return Projection(occurrence.object_key, occurrence.object_type, properties, support, extracted.diagnostics)
    elif occurrence.object_type in {"PACKAGE_SPEC", "PACKAGE_BODY"}:
        extracted = extract_package(fragment, occurrence.object_type)
        properties.update({"authid": extracted.authid, "routines": extracted.routines, "declarations": extracted.declarations, "initialization": extracted.initialization})
        # Overload membership is known even when individual old/new overloads
        # cannot be paired. Keep that bounded group fact available downstream.
        diagnostics = tuple(code for code in extracted.diagnostics if code != "OVERLOAD_PAIRING_REQUIRES_REVIEW")
        return Projection(occurrence.object_key, occurrence.object_type, properties, support, diagnostics)
    elif occurrence.object_type == "TRIGGER":
        properties["enabled_state"] = occurrence.trailing_trigger_state
        target = re.search(r"(?is)\bON\s+(\"[^\"]+\"|[A-Z][A-Z0-9_$#.]*)", fragment)
        properties["target"] = target.group(1) if target else None
    elif occurrence.object_type in {"PROCEDURE", "FUNCTION"}:
        extracted = extract_standalone_routine(fragment, occurrence.object_type)
        if extracted.routines:
            routine = extracted.routines[0]
            properties["routine_signature"] = (routine.signature,)
            properties["transaction_statements"] = routine.transactions
            properties["exception_handlers"] = routine.exception_handlers
            properties["has_exception"] = bool(routine.exception_handlers)
            properties["has_commit"] = any(value.upper().startswith("COMMIT") for value in routine.transactions)
            properties["has_rollback"] = any(value.upper().startswith("ROLLBACK") for value in routine.transactions)
        else:
            properties["has_exception"] = bool(re.search(r"(?i)\bEXCEPTION\b", fragment))
            properties["has_commit"] = bool(re.search(r"(?i)\bCOMMIT\b", fragment))
            properties["has_rollback"] = bool(re.search(r"(?i)\bROLLBACK\b", fragment))
            properties["transaction_statements"] = ()
            properties["exception_handlers"] = ()
    else:
        properties["raw_clause"] = fragment.strip()
    return Projection(occurrence.object_key, occurrence.object_type, properties, support)
