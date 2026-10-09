from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .oracle.scanner import _mask_sql


class Relation(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    CALL = "CALL"
    TYPE_REF = "TYPE_REF"
    TRIGGER_ON = "TRIGGER_ON"
    FK_REF = "FK_REF"
    SYNONYM_TO = "SYNONYM_TO"
    SPEC_BODY = "SPEC_BODY"


class Resolution(StrEnum):
    RESOLVED_STATIC = "resolved_static"
    CANDIDATE = "candidate"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class SnapshotObject:
    object_key: str
    schema: str | None
    name: str
    object_type: str
    source: str
    evidence_ids: tuple[str, ...]
    structural: bool = True


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    from_object: str
    to_candidate: str
    relation: Relation
    resolution: Resolution
    revision: str
    evidence_ids: tuple[str, ...]
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "from_object": self.from_object,
            "to_candidate": self.to_candidate,
            "relation": self.relation.value,
            "resolution": self.resolution.value,
            "revision": self.revision,
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
        }


_IDENT = r'(?:(?:"(?:[^"]|"")*")|(?:[A-Za-z][A-Za-z0-9_$#]*))'
_QUALIFIED = rf"(?P<target>{_IDENT}(?:\s*\.\s*{_IDENT})?)"
_RULES: tuple[tuple[Relation, re.Pattern[str], str], ...] = (
    (Relation.WRITE, re.compile(rf"(?is)\b(?:INSERT\s+INTO|MERGE\s+INTO|UPDATE|DELETE\s+FROM)\s+{_QUALIFIED}"), "DML target"),
    (Relation.FK_REF, re.compile(rf"(?is)\bREFERENCES\s+{_QUALIFIED}"), "foreign key target"),
    (Relation.TRIGGER_ON, re.compile(rf"(?is)\bTRIGGER\b[\s\S]*?\bON\s+{_QUALIFIED}"), "trigger target"),
    (Relation.SYNONYM_TO, re.compile(rf"(?is)\bSYNONYM\s+{_IDENT}(?:\s*\.\s*{_IDENT})?\s+FOR\s+{_QUALIFIED}"), "synonym target"),
    (Relation.TYPE_REF, re.compile(rf"(?is)\b{_QUALIFIED}\s*%(?:TYPE|ROWTYPE)\b"), "percent type reference"),
    (Relation.READ, re.compile(rf"(?is)\b(?:FROM|JOIN)\s+{_QUALIFIED}"), "query source"),
    (Relation.READ, re.compile(rf"(?is)\b{_QUALIFIED}\s*\.\s*(?:NEXTVAL|CURRVAL)\b"), "sequence value reference"),
)
_CALL_RULE = re.compile(
    rf"(?is)\b(?P<package>{_IDENT}(?:\s*\.\s*{_IDENT})?)\s*\.\s*(?P<routine>{_IDENT})\s*\("
)
_CALL_NO_ARGS_RULE = re.compile(
    rf"(?is)\b(?P<package>{_IDENT}(?:\s*\.\s*{_IDENT})?)\s*\.\s*(?P<routine>{_IDENT})\s*(?=;)"
)
_CALL_LOCAL_RULE = re.compile(
    rf"(?is)(?P<routine>{_IDENT})(?:\s*\(|\s*(?=;))"
)
_LOCAL_CALL_BLOCKERS = {
    "BEGIN", "CASE", "COMMIT", "CREATE", "DECLARE", "DELETE", "END", "EXCEPTION", "EXECUTE",
    "FOR", "FUNCTION", "IF", "INSERT", "LOOP", "MERGE", "NULL", "PROCEDURE", "RAISE", "RETURN",
    "ROLLBACK", "SELECT", "THEN", "UPDATE", "WHEN", "WHILE",
}


@dataclass(frozen=True, slots=True)
class _RoutineShape:
    name: str
    parameter_names: tuple[str, ...]
    parameter_types: tuple[str | None, ...]
    required_count: int


def _routine_shapes(objects: tuple[SnapshotObject, ...]) -> dict[tuple[str | None, str], tuple[_RoutineShape, ...]]:
    """Extract bounded package and standalone routine signatures."""
    from .oracle.package import extract_package, extract_standalone_routine

    result: dict[tuple[str | None, str], list[_RoutineShape]] = defaultdict(list)
    for item in objects:
        if item.object_type in {"PACKAGE_SPEC", "PACKAGE_BODY"}:
            extraction = extract_package(item.source, item.object_type)
        elif item.object_type in {"PROCEDURE", "FUNCTION"}:
            extraction = extract_standalone_routine(item.source, item.object_type)
        else:
            continue
        if not extraction.routines or any(code in {
            "PACKAGE_EXTRACTION_SIZE_LIMIT", "WRAPPED_SOURCE_OPAQUE",
            "PACKAGE_PARSE_UNRESOLVED", "PACKAGE_IDENTITY_UNRESOLVED",
        } for code in extraction.diagnostics):
            continue
        key = (item.schema, item.name)
        for routine in extraction.routines:
            shape = _RoutineShape(
                name=item.name.upper() if item.object_type in {"PROCEDURE", "FUNCTION"} else routine.name.upper(),
                parameter_names=tuple((parameter.name or "").strip('"').upper() for parameter in routine.parameters),
                parameter_types=tuple((parameter.data_type or "").strip().upper() or None for parameter in routine.parameters),
                required_count=sum(parameter.default is None for parameter in routine.parameters),
            )
            if shape not in result[key]:
                result[key].append(shape)
    return {key: tuple(value) for key, value in result.items()}


def _split_call_arguments(text: str) -> tuple[tuple[str | None, str], ...] | None:
    """Split one call argument list without interpreting expressions or types."""
    if not text.strip():
        return ()
    parts: list[str] = []
    start = 0
    depth = 0
    quote = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            if quote and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            quote = not quote
        elif not quote:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return None
            elif char == "," and depth == 0:
                parts.append(text[start:index].strip())
                start = index + 1
        index += 1
    if quote or depth != 0:
        return None
    parts.append(text[start:].strip())
    result: list[tuple[str | None, str]] = []
    for part in parts:
        if not part:
            return None
        named = re.match(r"(?is)^\s*([A-Za-z][A-Za-z0-9_$#]*)\s*=>\s*(.+)$", part, re.S)
        result.append((named.group(1).upper(), named.group(2).strip()) if named else (None, part))
    return tuple(result)


def _call_arguments(masked: str, opening_parenthesis: int) -> str | None:
    depth = 1
    quote = False
    index = opening_parenthesis + 1
    while index < len(masked):
        char = masked[index]
        if char == "'":
            if quote and index + 1 < len(masked) and masked[index + 1] == "'":
                index += 2
                continue
            quote = not quote
        elif not quote:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return masked[opening_parenthesis + 1:index]
        index += 1
    return None


def _call_binding_reason(
    shapes: dict[tuple[str | None, str], tuple[_RoutineShape, ...]],
    schema: str | None, package: str, routine: str, arguments: tuple[tuple[str | None, str], ...] | None,
    *, kind: str = "qualified package/routine",
) -> str:
    candidates = [shape for shape in shapes.get((schema, package), ()) if shape.name == routine.upper()]
    if arguments is None:
        return f"{kind} call candidate; binding=arguments_unparsed"
    compatible: list[_RoutineShape] = []
    for shape in candidates:
        positional_count = 0
        named: list[str] = []
        seen_named = False
        order_valid = True
        for name, _value in arguments:
            if name is None:
                if seen_named:
                    order_valid = False
                positional_count += 1
            else:
                seen_named = True
                named.append(name)
        if not order_valid or positional_count > len(shape.parameter_names) or len(set(named)) != len(named):
            continue
        if named and any(name not in shape.parameter_names for name in named):
            continue
        supplied = set(named) | set(shape.parameter_names[:positional_count])
        if len(supplied) < shape.required_count:
            continue
        compatible.append(shape)
    if not candidates:
        suffix = "definition_unresolved"
    elif not compatible:
        suffix = "incompatible_arity_or_names"
    elif len(compatible) > 1:
        suffix = "overload_candidate"
    else:
        suffix = "arity_name_compatible_type_unresolved"
    return f"{kind} call candidate; binding={suffix}"


def _local_call_allowed(masked: str, match: re.Match[str]) -> bool:
    """Reject declaration/control keywords and qualified calls already handled elsewhere."""
    routine = _unquote(match.group("routine"))[0]
    if routine in _LOCAL_CALL_BLOCKERS:
        return False
    prefix = masked[max(0, match.start() - 32):match.start()]
    if re.search(r"\.\s*$", prefix):
        return False
    previous = re.findall(r"[A-Za-z][A-Za-z0-9_$#]*", prefix.upper())
    return not previous or previous[-1] not in {"PROCEDURE", "FUNCTION", "END"}


def _unquote(value: str) -> tuple[str, bool]:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('""', '"'), True
    return value.upper(), False


def _parts(value: str, default_schema: str | None) -> tuple[str | None, str]:
    raw = re.split(r"\s*\.\s*", value.strip(), maxsplit=1)
    if len(raw) == 2:
        schema, _ = _unquote(raw[0])
        name, _ = _unquote(raw[1])
        return schema, name
    name, _ = _unquote(raw[0])
    return default_schema.upper() if default_schema else None, name


class DependencyGraph:
    def __init__(self, revision: str, objects: Iterable[SnapshotObject], edges: Iterable[DependencyEdge]) -> None:
        self.revision = revision
        self.objects = {item.object_key: item for item in objects}
        self.edges = tuple(sorted(set(edges), key=lambda edge: (edge.from_object, edge.relation, edge.to_candidate, edge.reason)))
        outgoing: dict[str, list[DependencyEdge]] = defaultdict(list)
        incoming: dict[str, list[DependencyEdge]] = defaultdict(list)
        for edge in self.edges:
            outgoing[edge.from_object].append(edge)
            if edge.resolution == Resolution.RESOLVED_STATIC:
                incoming[edge.to_candidate].append(edge)
            elif edge.resolution == Resolution.CANDIDATE:
                # Candidate targets are still useful for reverse impact
                # context.  Keep the edge's candidate resolution so callers
                # cannot be presented as uniquely bound.
                for candidate in edge.to_candidate.split(","):
                    if candidate in self.objects:
                        incoming[candidate].append(edge)
        self.outgoing = {key: tuple(value) for key, value in outgoing.items()}
        self.incoming = {key: tuple(value) for key, value in incoming.items()}

    def neighbors(self, seeds: Iterable[str], *, depth: int, reverse: bool = False) -> tuple[DependencyEdge, ...]:
        if depth < 0 or depth > 2:
            raise ValueError("dependency depth must be between 0 and 2")
        adjacency = self.incoming if reverse else self.outgoing
        queue = deque((seed, 0) for seed in sorted(set(seeds)))
        seen_nodes = set(seed for seed, _ in queue)
        chosen: list[DependencyEdge] = []
        seen_edges: set[DependencyEdge] = set()
        while queue:
            node, level = queue.popleft()
            if level >= depth:
                continue
            for edge in adjacency.get(node, ()):
                if edge in seen_edges:
                    continue
                seen_edges.add(edge)
                chosen.append(edge)
                next_node = edge.from_object if reverse else edge.to_candidate
                if edge.resolution == Resolution.RESOLVED_STATIC and next_node not in seen_nodes:
                    seen_nodes.add(next_node)
                    queue.append((next_node, level + 1))
        return tuple(chosen)


def _resolve(schema: str | None, name: str, objects: Iterable[SnapshotObject]) -> tuple[str, Resolution]:
    exact = sorted(item.object_key for item in objects if item.schema == schema and item.name == name)
    if len(exact) == 1:
        return exact[0], Resolution.RESOLVED_STATIC
    if exact:
        return ",".join(exact), Resolution.CANDIDATE
    by_name = sorted(item.object_key for item in objects if item.name == name)
    return (",".join(by_name), Resolution.CANDIDATE) if by_name else ((f"{schema}." if schema else "") + name, Resolution.UNRESOLVED)


def _resolve_call(
    package_ref: str, routine: str, default_schema: str | None,
    objects: Iterable[SnapshotObject],
) -> tuple[str, Resolution, str, str | None, str]:
    """Resolve package calls, with a conservative standalone fallback.

    A package specification and body intentionally remain a candidate pair;
    this graph does not claim overload or runtime binding from a call spelling.
    """
    snapshot = tuple(objects)
    schema, package = _parts(package_ref, default_schema)
    packages = tuple(item for item in snapshot if item.object_type in {"PACKAGE_SPEC", "PACKAGE_BODY"})
    package_target, package_resolution = _resolve(schema, package, packages)
    if package_resolution != Resolution.UNRESOLVED:
        return package_target, package_resolution, "qualified package/routine", schema, package

    raw_parts = re.split(r"\s*\.\s*", package_ref.strip())
    if len(raw_parts) in {1, 2}:
        # APP.RUN_JOB is captured as package_ref=APP, routine=RUN_JOB;
        # APP.P.RUN is captured as package_ref=APP.P.  In both cases only
        # an existing standalone object can activate this fallback.
        standalone_schema, _ = _unquote(raw_parts[0] if len(raw_parts) == 2 else package_ref)
        standalone = tuple(
            item for item in snapshot
            if item.schema == standalone_schema
            and item.name == routine
            and item.object_type in {"PROCEDURE", "FUNCTION"}
        )
        if standalone:
            target, resolution = _resolve(standalone_schema, routine, standalone)
            return target, resolution, "standalone routine", standalone_schema, routine
    return package_target, package_resolution, "qualified package/routine", schema, package


def build_dependency_graph(revision: str, objects: Iterable[SnapshotObject]) -> DependencyGraph:
    snapshot = tuple(objects)
    routine_shapes = _routine_shapes(snapshot)
    edges: list[DependencyEdge] = []
    for item in snapshot:
        if not item.structural:
            continue
        masked, diagnostics = _mask_sql(item.source)
        if diagnostics:
            continue
        # Avoid treating common-table-expression names as repository objects.
        ctes = {match.group(1).upper() for match in re.finditer(rf"(?is)(?:\bWITH|,)\s*({_IDENT})\s+AS\s*\(", masked)}
        for relation, pattern, reason in _RULES:
            for match in pattern.finditer(masked):
                schema, name = _parts(match.group("target"), item.schema)
                if name.upper() in ctes:
                    continue
                target, resolution = _resolve(schema, name, snapshot)
                edges.append(DependencyEdge(item.object_key, target, relation, resolution, revision, item.evidence_ids, reason))
        call_matches: list[tuple[re.Match[str], tuple[tuple[str | None, str], ...] | None, bool]] = []
        for match in _CALL_RULE.finditer(masked):
            arguments_text = _call_arguments(masked, match.end() - 1)
            call_matches.append((match, _split_call_arguments(arguments_text) if arguments_text is not None else None, False))
        call_matches.extend((match, (), False) for match in _CALL_NO_ARGS_RULE.finditer(masked))
        if item.object_type in {"PACKAGE_BODY", "PROCEDURE", "FUNCTION"}:
            for match in _CALL_LOCAL_RULE.finditer(masked):
                if not _local_call_allowed(masked, match):
                    continue
                arguments = _call_arguments(masked, match.end() - 1) if masked[match.end() - 1] == "(" else ""
                call_matches.append((match, _split_call_arguments(arguments), True))
        seen_calls: set[tuple[int, int]] = set()
        for match, arguments, local in call_matches:
            marker = (match.start(), match.end())
            if marker in seen_calls:
                continue
            seen_calls.add(marker)
            routine = _unquote(match.group("routine"))[0]
            if local:
                package_ref = item.name if item.object_type == "PACKAGE_BODY" else item.schema or item.name
            else:
                package_ref = match.group("package")
            target, resolution, kind, shape_schema, shape_name = _resolve_call(
                package_ref, routine, item.schema, snapshot,
            )
            if local and resolution == Resolution.UNRESOLVED:
                continue
            if local:
                kind = "local routine"
            reason = _call_binding_reason(
                routine_shapes, shape_schema, shape_name or "", routine, arguments, kind=kind,
            )
            edges.append(DependencyEdge(
                item.object_key, target, Relation.CALL, resolution, revision,
                item.evidence_ids, reason,
            ))

    grouped: dict[tuple[str | None, str], dict[str, SnapshotObject]] = defaultdict(dict)
    for item in snapshot:
        if item.object_type in {"PACKAGE_SPEC", "PACKAGE_BODY"}:
            grouped[(item.schema, item.name)][item.object_type] = item
    for pair in grouped.values():
        if pair.keys() >= {"PACKAGE_SPEC", "PACKAGE_BODY"}:
            for left, right in ((pair["PACKAGE_SPEC"], pair["PACKAGE_BODY"]), (pair["PACKAGE_BODY"], pair["PACKAGE_SPEC"])):
                edges.append(DependencyEdge(left.object_key, right.object_key, Relation.SPEC_BODY, Resolution.RESOLVED_STATIC, revision, left.evidence_ids, "package specification/body pair"))
    return DependencyGraph(revision, snapshot, edges)


def resolve_synonym(graph: DependencyGraph, object_key: str, *, max_hops: int = 5) -> tuple[str | None, tuple[str, ...]]:
    current = object_key
    visited: list[str] = []
    for _ in range(max_hops):
        if current in visited:
            return None, tuple((*visited, "SYNONYM_CYCLE"))
        visited.append(current)
        candidates = [edge for edge in graph.outgoing.get(current, ()) if edge.relation == Relation.SYNONYM_TO]
        if not candidates:
            return current, tuple(visited)
        edge = candidates[0]
        if edge.resolution != Resolution.RESOLVED_STATIC:
            return None, tuple((*visited, "SYNONYM_UNRESOLVED"))
        current = edge.to_candidate
    return None, tuple((*visited, "SYNONYM_HOP_LIMIT"))
