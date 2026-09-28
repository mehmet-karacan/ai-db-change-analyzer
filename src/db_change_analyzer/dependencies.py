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


def build_dependency_graph(revision: str, objects: Iterable[SnapshotObject]) -> DependencyGraph:
    snapshot = tuple(objects)
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
