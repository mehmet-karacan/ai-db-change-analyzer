"""Evidence-bound property differences for the six approved Oracle types."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..taxonomy.catalog import change_catalog, supported_types
from .projections import Projection
from .table import constraint_properties


@dataclass(frozen=True, slots=True)
class Value:
    state: str
    value: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Change:
    fact_id: str
    taxonomy_id: str
    component_path: tuple[str, ...]
    action: str
    category: str
    before: Value
    after: Value
    context_only: bool
    verification: str


@dataclass(frozen=True, slots=True)
class ChangeSet:
    object_key: str
    object_type: str
    operation: str
    facts: tuple[Change, ...]
    unsupported_families: tuple[str, ...]
    diagnostics: tuple[str, ...]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list, dict)) and not value:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=lambda item: vars(item))


def _field(fields: dict[tuple[str, tuple[str, ...]], str | None], taxonomy_id: str, path: tuple[str, ...], value: Any) -> None:
    fields[(taxonomy_id, path)] = _text(value)


def _routine_groups(projection: Projection | None) -> dict[tuple[str, str], list[Any]]:
    groups: dict[tuple[str, str], list[Any]] = {}
    if projection is None or projection.object_type not in {"PACKAGE_SPEC", "PACKAGE_BODY"}:
        return groups
    for routine in projection.properties.get("routines", ()):
        name = routine.name if routine.name.startswith('"') else routine.name.upper()
        groups.setdefault((routine.kind, name), []).append(routine)
    return groups


def _flatten(projection: Projection) -> dict[tuple[str, tuple[str, ...]], str | None]:
    properties = projection.properties
    kind = projection.object_type
    fields: dict[tuple[str, tuple[str, ...]], str | None] = {}
    if kind in {"VIEW", "PACKAGE_SPEC", "PACKAGE_BODY"}:
        _field(fields, "common.object.editionable", (), properties.get("editionable"))
    if kind == "SEQUENCE":
        for key in ("start_with", "increment_by", "min_value", "max_value", "cache", "cycle", "order", "scale", "extend", "shard", "session", "keep"):
            _field(fields, f"sequence.sequence_property.{key}", (), properties.get(key))
    elif kind == "TABLE":
        for name, column in properties.get("columns", {}).items():
            for key, value in column.items():
                if key in {"definition", "data_type", "length", "length_semantics", "precision", "scale", "nullable", "default", "default_on_null", "identity", "virtual_expression", "visibility", "position", "collation"}:
                    _field(fields, f"table.column.{key}", (name,), value)
        for name, definition in properties.get("constraints", {}).items():
            _field(fields, "table.constraint.definition", (name,), definition)
            for key, value in constraint_properties(definition).items():
                _field(fields, f"table.constraint.{key}", (name,), value)
        for name, comment in properties.get("comments", {}).items():
            _field(fields, "table.comment.text", (name,), comment)
        for name, definition in properties.get("partitions", {}).items():
            _field(fields, "table.partition.definition", (name,), definition)
        for key, value in properties.get("table_properties", {}).items():
            taxonomy_id = f"table.table_property.{key}"
            if taxonomy_id in change_catalog():
                _field(fields, taxonomy_id, (), value)
    elif kind == "INDEX":
        for key in ("target", "unique", "kind", "keys", "directions", "visibility"):
            taxonomy_key = "direction" if key == "directions" else key
            _field(fields, f"index.index_property.{taxonomy_key}", (), properties.get(key))
        for key, value in properties.get("index_properties", {}).items():
            taxonomy_id = f"index.index_property.{key}"
            if taxonomy_id in change_catalog():
                _field(fields, taxonomy_id, (), value)
    elif kind == "VIEW":
        for position, value in enumerate(properties.get("declared_columns", ()), 1):
            _field(fields, "view.output_column.definition", (str(position),), value)
        _field(fields, "view.query_property.output_order", (), properties.get("declared_columns"))
        _field(fields, "view.query_property.projection", (), properties.get("projection"))
        for key, value in properties.get("query_clauses", {}).items():
            taxonomy_id = f"view.query_property.{key}"
            if taxonomy_id in change_catalog():
                _field(fields, taxonomy_id, (), value)
    elif kind in {"PACKAGE_SPEC", "PACKAGE_BODY"}:
        prefix = "package_spec" if kind == "PACKAGE_SPEC" else "package_body"
        if kind == "PACKAGE_SPEC":
            _field(fields, "package_spec.package_property.authid", (), properties.get("authid"))
        else:
            _field(fields, "package_body.body_property.initialization", (), properties.get("initialization"))
        for path, members in _routine_groups(projection).items():
            if len(members) > 1:
                _field(fields, f"{prefix}.routine.definition", path,
                       tuple(sorted(routine.source for routine in members)))
                _field(fields, f"{prefix}.routine.signature", path,
                       tuple(sorted(routine.signature for routine in members)))
                continue
            routine = members[0]
            _field(fields, f"{prefix}.routine.definition", path, routine.source)
            _field(fields, f"{prefix}.routine.signature", path, routine.signature)
            _field(fields, f"{prefix}.routine.return_type", path, routine.return_type)
            for parameter in routine.parameters:
                parameter_path = (*path, str(parameter.position))
                _field(fields, f"{prefix}.parameter.definition", parameter_path, parameter.source)
                for key in ("name", "position", "mode", "data_type", "default", "nocopy"):
                    _field(fields, f"{prefix}.parameter.{key}", parameter_path, getattr(parameter, key))
            if kind == "PACKAGE_BODY":
                _field(fields, "package_body.body_property.routine_body", path, routine.source)
                _field(fields, "package_body.body_property.transaction_statement", path, routine.transactions)
                _field(fields, "package_body.body_property.exception_handler", path, routine.exception_handlers)
                _field(fields, "package_body.body_property.dynamic_sql", path, routine.dynamic_sql)
                for taxonomy_key, values in (
                    ("sql_statement", routine.sql_statements),
                    ("condition", routine.conditions),
                    ("assignment", routine.assignments),
                    ("control_flow", routine.control_flow),
                    ("raise", routine.raises),
                    ("call_reference", routine.call_references),
                ):
                    _field(fields, f"package_body.body_property.{taxonomy_key}", path, values)
        declaration_family = {
            "type_declaration": "type_declaration",
            "constant": "constant",
            "variable_declaration": "variable",
            "cursor_declaration": "cursor",
            "exception_declaration": "exception_declaration",
            "pragma_declaration": "pragma",
        }
        for key, values in properties.get("declarations", {}).items():
            taxonomy_id = f"{prefix}.{declaration_family.get(key, key)}.definition"
            if taxonomy_id in change_catalog():
                for position, value in enumerate(values, 1):
                    _field(fields, taxonomy_id, (str(position),), value)
    return fields


def _value(value: str | None, evidence_ids: tuple[str, ...], *, missing_object: bool = False) -> Value:
    return Value(
        state="not_applicable" if missing_object else "not_in_source" if value is None else "present",
        value=None if missing_object or value is None else value,
        evidence_ids=evidence_ids,
    )


def compare_projections(
    old: Projection | None, new: Projection | None, *,
    old_evidence_ids: tuple[str, ...], new_evidence_ids: tuple[str, ...],
    old_scope_evidence_ids: tuple[str, ...] = (), new_scope_evidence_ids: tuple[str, ...] = (),
) -> ChangeSet:
    representative = new or old
    if representative is None or representative.object_type not in supported_types():
        raise ValueError("No approved Oracle object projection")
    if old and new and (old.object_key != new.object_key or old.object_type != new.object_type):
        raise ValueError("Cannot pair different Oracle identities")
    operation = "modified" if old and new else "added" if new else "removed"
    diagnostics: list[str] = list(old.diagnostics if old else ()) + list(new.diagnostics if new else ())
    if old is None and not old_scope_evidence_ids or new is None and not new_scope_evidence_ids:
        diagnostics.append("ABSENCE_SCOPE_UNVERIFIED")
    if old and old.support != "structural" or new and new.support != "structural":
        diagnostics.append("PARSER_NOT_VERIFIED")
    scoped_columns = (
        representative.object_type == "TABLE" and old is not None and new is not None
        and bool(diagnostics) and set(diagnostics) == {"ALTER_COLUMN_PARTIAL_UNRESOLVED"}
        and all(
            "ALTER_COLUMN_PARTIAL_UNRESOLVED" not in side.diagnostics
            or bool(side.properties.get("_unresolved_columns"))
            for side in (old, new)
        )
    )
    if diagnostics and not scoped_columns:
        return ChangeSet(representative.object_key, representative.object_type, "unknown" if "ABSENCE_SCOPE_UNVERIFIED" in diagnostics else operation, (), tuple(sorted(family.taxonomy_id for family in change_catalog().values() if representative.object_type in family.object_types)), tuple(dict.fromkeys(diagnostics)))
    old_fields, new_fields = _flatten(old) if old else {}, _flatten(new) if new else {}
    scoped_families: set[str] = set()
    if scoped_columns:
        unresolved = set(old.properties["_unresolved_columns"]) | set(new.properties["_unresolved_columns"])
        for fields in (old_fields, new_fields):
            for taxonomy_id, path in tuple(fields):
                if taxonomy_id.startswith("table.column.") and path and path[0] in unresolved:
                    del fields[(taxonomy_id, path)]
        scoped_families = {taxonomy_id for taxonomy_id in change_catalog() if taxonomy_id.startswith("table.column.")}
    overload_limited: set[str] = set()
    if representative.object_type in {"PACKAGE_SPEC", "PACKAGE_BODY"} and old and new:
        prefix = "package_spec" if representative.object_type == "PACKAGE_SPEC" else "package_body"
        old_groups, new_groups = _routine_groups(old), _routine_groups(new)
        for path in old_groups.keys() | new_groups.keys():
            if max(len(old_groups.get(path, ())), len(new_groups.get(path, ()))) < 2:
                continue
            retained = (f"{prefix}.routine.signature" if path in old_groups and path in new_groups
                        else f"{prefix}.routine.definition")
            for fields in (old_fields, new_fields):
                for taxonomy_id, field_path in tuple(fields):
                    if field_path[:2] == path and taxonomy_id.startswith(prefix + ".") and taxonomy_id != retained:
                        del fields[(taxonomy_id, field_path)]
            overload_limited.update(
                taxonomy_id for taxonomy_id in change_catalog()
                if taxonomy_id.startswith((f"{prefix}.routine.", f"{prefix}.parameter.", f"{prefix}.body_property."))
                and taxonomy_id not in {retained, f"{prefix}.body_property.initialization"}
            )
    old_ids = old_evidence_ids if old else old_scope_evidence_ids
    new_ids = new_evidence_ids if new else new_scope_evidence_ids
    if not old_ids or not new_ids:
        raise ValueError("Fact sides require source or scope evidence")
    catalog = change_catalog()
    facts: list[Change] = []
    covered: set[str] = set()
    if old is None or new is None:
        key = "common.object.presence"
        facts.append(Change(
            fact_id=_id(representative.object_key, key, ()), taxonomy_id=key, component_path=(),
            action=operation, category=catalog[key].category,
            before=Value("absent_in_snapshot", None, old_ids) if old is None else Value("present", "Tanım kaynakta mevcut", old_ids),
            after=Value("absent_in_snapshot", None, new_ids) if new is None else Value("present", "Tanım kaynakta mevcut", new_ids),
            context_only=False, verification="verified",
        ))
        covered.add(key)
    for taxonomy_id, path in sorted(old_fields.keys() | new_fields.keys()):
        before, after = old_fields.get((taxonomy_id, path)), new_fields.get((taxonomy_id, path))
        if before == after and old is not None and new is not None:
            covered.add(taxonomy_id)
            continue
        if before is None and after is None:
            continue
        family = catalog[taxonomy_id]
        context_only = old is None or new is None
        if not context_only and before is not None and after is not None and taxonomy_id in {
            "table.column.definition", "package_spec.routine.definition", "package_body.routine.definition",
        }:
            # A surviving component's raw definition changes when one of its
            # typed fields changes or whitespace moves. The typed field diff
            # carries the modification; definition is an add/remove family.
            covered.add(taxonomy_id)
            continue
        if not context_only and before is not None and after is not None and "modified" not in family.allowed_actions:
            for action, left, right in (
                ("removed", _value(before, old_ids), _value(None, new_ids)),
                ("added", _value(None, old_ids), _value(after, new_ids)),
            ):
                facts.append(Change(
                    fact_id=_id(representative.object_key, taxonomy_id, (*path, action)),
                    taxonomy_id=taxonomy_id, component_path=path, action=action,
                    category=family.category, before=left, after=right,
                    context_only=False, verification="verified",
                ))
            covered.add(taxonomy_id)
            continue
        action = operation if context_only else "added" if before is None and "added" in family.allowed_actions else "removed" if after is None and "removed" in family.allowed_actions else "modified"
        facts.append(Change(
            fact_id=_id(representative.object_key, taxonomy_id, path), taxonomy_id=taxonomy_id,
            component_path=path, action=action, category=family.category,
            before=_value(before, old_ids, missing_object=old is None),
            after=_value(after, new_ids, missing_object=new is None),
            context_only=context_only, verification="verified",
        ))
        covered.add(taxonomy_id)
    unsupported = tuple(sorted(family.taxonomy_id for family in catalog.values() if representative.object_type in family.object_types and (family.taxonomy_id not in covered or family.taxonomy_id in overload_limited or family.taxonomy_id in scoped_families)))
    return ChangeSet(representative.object_key, representative.object_type, operation, tuple(facts), unsupported, tuple(dict.fromkeys(diagnostics)))


def _id(object_key: str, taxonomy_id: str, path: tuple[str, ...]) -> str:
    digest = hashlib.sha256(json.dumps((object_key, taxonomy_id, path), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return "fact-" + digest[:32]
