from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .git_client import GitClient, GitError, RawDelta


class HistoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CommitRecord:
    sha: str
    parents: tuple[str, ...]
    git_author_time: str
    git_committer_time: str
    position: int
    delta_kind: Literal["first_parent"] = "first_parent"


@dataclass(frozen=True, slots=True)
class CommitDelta:
    commit: CommitRecord
    parent_sha: str | None
    files: tuple[RawDelta, ...]


@dataclass(frozen=True, slots=True)
class RangePlan:
    outcome: str
    base_sha: str | None
    target_sha: str
    commits: tuple[CommitRecord, ...]
    event_deltas: tuple[CommitDelta, ...]
    net_deltas: tuple[RawDelta, ...]
    scope_deltas: tuple[RawDelta, ...]


def _in_scope(path: bytes, roots: list[str]) -> bool:
    normalized = path.replace(b"\\", b"/")
    return any(normalized == root.encode() or normalized.startswith(root.encode() + b"/") for root in roots)


class HistoryPlanner:
    def __init__(self, git: GitClient, roots: list[str], maximum_commits: int = 250) -> None:
        self.git = git
        self.roots = roots
        self.maximum_commits = maximum_commits

    def automatic(self, base: str | None, target: str) -> RangePlan:
        target = self.git.resolve_commit(target)
        if base is None:
            return RangePlan("BASELINED", None, target, (), (), (), ())
        base = self.git.resolve_commit(base)
        if base == target:
            return RangePlan("NO_CHANGE", base, target, (), (), (), ())
        if not self.git.is_ancestor(base, target):
            raise HistoryError("DIVERGED", "checkpoint is not an ancestor of the target")
        rows = self.git.rev_list(base, target, self.maximum_commits)
        if len(rows) > self.maximum_commits:
            raise HistoryError("BACKLOG_LIMIT", "new commit count exceeds configured limit")
        commits: list[CommitRecord] = []
        events: list[CommitDelta] = []
        scope_events: list[RawDelta] = []
        for position, (sha, parents) in enumerate(rows):
            author_time, committer_time = self.git.commit_times(sha)
            record = CommitRecord(sha, tuple(parents), author_time, committer_time, position)
            first_parent = parents[0] if parents else None
            files = tuple(self.git.diff(first_parent, sha))
            commits.append(record)
            events.append(CommitDelta(record, first_parent, files))
            scope_events.extend(item for item in files if _in_scope(item.path, self.roots))
        net = tuple(self.git.diff(base, target))
        scoped_net = tuple(item for item in net if _in_scope(item.path, self.roots))
        outcome = "OUT_OF_SCOPE_ONLY" if not scope_events and not scoped_net else "ANALYZE"
        return RangePlan(outcome, base, target, tuple(commits), tuple(events), net, tuple(scope_events or scoped_net))

    def manual_root(self, target: str) -> RangePlan:
        target = self.git.resolve_commit(target)
        if self.git.parents(target):
            raise HistoryError("ROOT_UNPROVEN", "manual ROOT target has a parent")
        author_time, committer_time = self.git.commit_times(target)
        record = CommitRecord(target, (), author_time, committer_time, 0)
        files = tuple(self.git.diff(None, target))
        scoped = tuple(item for item in files if _in_scope(item.path, self.roots))
        return RangePlan("ANALYZE" if scoped else "OUT_OF_SCOPE_ONLY", None, target, (record,), (CommitDelta(record, None, files),), files, scoped)
