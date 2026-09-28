from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class GitError(RuntimeError):
    def __init__(self, code: str, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.returncode = returncode


OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
REF_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


@dataclass(frozen=True, slots=True)
class TreeEntry:
    mode: str
    kind: str
    oid: str
    path: bytes

    @property
    def path_b64(self) -> str:
        return base64.b64encode(self.path).decode("ascii")

    @property
    def path_display(self) -> str:
        return self.path.decode("utf-8", errors="backslashreplace")


@dataclass(frozen=True, slots=True)
class RawDelta:
    old_mode: str
    new_mode: str
    old_oid: str
    new_oid: str
    status: str
    path: bytes

    @property
    def path_b64(self) -> str:
        return base64.b64encode(self.path).decode("ascii")

    @property
    def path_display(self) -> str:
        return self.path.decode("utf-8", errors="backslashreplace")


class GitClient:
    def __init__(self, cache: Path, *, timeout_seconds: int = 120, allow_file_protocol: bool = False) -> None:
        executable = shutil.which("git")
        if not executable:
            raise GitError("GIT_MISSING", "trusted Git executable is unavailable")
        self.executable = str(Path(executable).resolve())
        self.cache = cache.resolve()
        self.timeout_seconds = timeout_seconds
        self.allow_file_protocol = allow_file_protocol

    def _environment(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        allowed = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
        environment = {key: value for key in allowed if (value := os.environ.get(key))}
        environment.update(
            {
                "PATH": str(Path(self.executable).parent),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
            }
        )
        if self.allow_file_protocol:
            environment["GIT_ALLOW_PROTOCOL"] = "file"
        else:
            environment["GIT_ALLOW_PROTOCOL"] = "https"
        if extra:
            environment.update(extra)
        return environment

    def _run(
        self,
        args: list[str],
        *,
        input_bytes: bytes | None = None,
        check: bool = True,
        limit: int = 128 * 1024 * 1024,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [self.executable, *args]
        try:
            completed = subprocess.run(
                command,
                input=input_bytes,
                capture_output=True,
                timeout=self.timeout_seconds,
                shell=False,
                env=self._environment(environment),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitError("GIT_IO", "Git invocation failed") from exc
        if len(completed.stdout) > limit or len(completed.stderr) > 1024 * 1024:
            raise GitError("GIT_OUTPUT_LIMIT", "Git output exceeded a safety limit", completed.returncode)
        if check and completed.returncode != 0:
            raise GitError("GIT_COMMAND_FAILED", "Git command failed", completed.returncode)
        return completed

    def ensure_cache(self) -> None:
        if self.cache.exists():
            if self.cache.is_symlink() or not (self.cache / "HEAD").is_file():
                raise GitError("CACHE_INVALID", "Git cache is not an owned bare repository")
            bare = self._run(["-C", str(self.cache), "rev-parse", "--is-bare-repository"]).stdout.strip()
            if bare != b"true":
                raise GitError("CACHE_INVALID", "Git cache is not bare")
            return
        self.cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._run(["init", "--bare", str(self.cache)])
        self._run(["-C", str(self.cache), "config", "core.hooksPath", os.devnull])
        self._run(["-C", str(self.cache), "config", "credential.helper", ""])

    def object_format(self) -> str:
        value = self._run(["-C", str(self.cache), "rev-parse", "--show-object-format"]).stdout.decode().strip()
        if value not in {"sha1", "sha256"}:
            raise GitError("OBJECT_FORMAT", "unsupported Git object format")
        return value

    def validate_oid(self, oid: str) -> str:
        if not OID.fullmatch(oid):
            raise GitError("INVALID_OID", "revision must be a full object ID")
        expected = 40 if self.object_format() == "sha1" else 64
        if len(oid) != expected:
            raise GitError("INVALID_OID", "object ID length does not match repository format")
        return oid

    def fetch(self, url: str, branch: str, *, username: str | None = None, password: str | None = None) -> str:
        self.ensure_cache()
        if not REF_COMPONENT.fullmatch(branch) or ".." in branch:
            raise GitError("INVALID_BRANCH", "branch is not a simple safe ref component")
        if not self.allow_file_protocol and not url.startswith("https://"):
            raise GitError("PROTOCOL_BLOCKED", "production Git transport must use HTTPS")
        environment: dict[str, str] = {}
        helper: Path | None = None
        if username is not None or password is not None:
            if username is None or password is None:
                raise GitError("CREDENTIAL_INCOMPLETE", "both Git credential fields are required")
            helper = self.cache.parent / ".git-askpass"
            helper.write_text(
                "#!/bin/sh\ncase \"$1\" in *Username*) printf '%s\\n' \"$DB_ANALYZER_GIT_USERNAME\" ;; *) printf '%s\\n' \"$DB_ANALYZER_GIT_PASSWORD\" ;; esac\n",
                encoding="utf-8",
            )
            os.chmod(helper, 0o700)
            environment.update(
                {
                    "GIT_ASKPASS": str(helper),
                    "GIT_ASKPASS_REQUIRE": "force",
                    "DB_ANALYZER_GIT_USERNAME": username,
                    "DB_ANALYZER_GIT_PASSWORD": password,
                }
            )
        ref = f"refs/remotes/source/{branch}"
        refspec = f"+refs/heads/{branch}:{ref}"
        try:
            args = ["-C", str(self.cache), "fetch", "--no-tags", "--no-recurse-submodules", "--no-auto-maintenance", url, refspec]
            self._run(args, environment=environment)
        finally:
            if helper and helper.exists():
                helper.unlink()
        return self.resolve_commit(ref)

    def resolve_commit(self, revision: str) -> str:
        if not (OID.fullmatch(revision) or revision.startswith("refs/remotes/source/") or revision.startswith("refs/analyzer/pins/")):
            raise GitError("INVALID_REVISION", "revision expression is not allowlisted")
        result = self._run(["-C", str(self.cache), "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"])
        oid = result.stdout.decode("ascii").strip()
        return self.validate_oid(oid)

    def pin_target(self, run_id: str, target: str) -> str:
        if not REF_COMPONENT.fullmatch(run_id):
            raise GitError("INVALID_RUN_ID", "run ID cannot form a safe pin ref")
        target = self.resolve_commit(self.validate_oid(target))
        ref = f"refs/analyzer/pins/{run_id}/target"
        self._run(["-C", str(self.cache), "update-ref", ref, target])
        return ref

    def exists_commit(self, oid: str) -> bool:
        oid = self.validate_oid(oid)
        result = self._run(["-C", str(self.cache), "cat-file", "-e", f"{oid}^{{commit}}"], check=False)
        return result.returncode == 0

    def parents(self, oid: str) -> list[str]:
        oid = self.resolve_commit(self.validate_oid(oid))
        value = self._run(["-C", str(self.cache), "show", "-s", "--format=%P", oid]).stdout.decode("ascii").strip()
        return [] if not value else value.split()

    def is_ancestor(self, base: str, target: str) -> bool:
        base = self.validate_oid(base)
        target = self.validate_oid(target)
        result = self._run(["-C", str(self.cache), "merge-base", "--is-ancestor", base, target], check=False)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise GitError("ANCESTRY_UNPROVEN", "Git could not prove ancestry", result.returncode)

    def rev_list(self, base: str, target: str, maximum: int) -> list[tuple[str, list[str]]]:
        base = self.validate_oid(base)
        target = self.validate_oid(target)
        result = self._run(
            ["-C", str(self.cache), "rev-list", "--topo-order", "--reverse", "--parents", f"--max-count={maximum + 1}", target, f"^{base}"],
        )
        rows: list[tuple[str, list[str]]] = []
        for line in result.stdout.decode("ascii").splitlines():
            parts = line.split()
            rows.append((parts[0], parts[1:]))
        return rows

    def commit_times(self, oid: str) -> tuple[str, str]:
        value = self._run(["-C", str(self.cache), "show", "-s", "--format=%aI%x00%cI", self.validate_oid(oid)]).stdout
        author, committer = value.rstrip(b"\n").split(b"\x00", 1)
        return author.decode("ascii"), committer.decode("ascii")

    @staticmethod
    def _parse_raw_delta(data: bytes) -> list[RawDelta]:
        fields = data.split(b"\x00")
        if fields and fields[-1] == b"":
            fields.pop()
        deltas: list[RawDelta] = []
        index = 0
        while index < len(fields):
            header = fields[index]
            index += 1
            if index >= len(fields):
                raise GitError("DIFF_PARSE", "missing path in raw diff")
            path = fields[index]
            index += 1
            if not header.startswith(b":"):
                raise GitError("DIFF_PARSE", "unexpected raw diff header")
            try:
                modes_oids, status = header[1:].rsplit(b" ", 1)
                old_mode, new_mode, old_oid, new_oid = modes_oids.split(b" ")
            except ValueError as exc:
                raise GitError("DIFF_PARSE", "invalid raw diff header") from exc
            status_text = status.decode("ascii")
            if status_text.startswith(("R", "C")):
                if index >= len(fields):
                    raise GitError("DIFF_PARSE", "missing rename path")
                path = fields[index]
                index += 1
            deltas.append(
                RawDelta(
                    old_mode.decode("ascii"),
                    new_mode.decode("ascii"),
                    old_oid.decode("ascii"),
                    new_oid.decode("ascii"),
                    status_text[:1],
                    path,
                )
            )
        return deltas

    def diff(self, old: str | None, new: str) -> list[RawDelta]:
        new = self.validate_oid(new)
        common = ["--raw", "-r", "-z", "--full-index", "--no-renames", "--no-ext-diff"]
        if old is None:
            args = ["-C", str(self.cache), "diff-tree", "--root", "--no-commit-id", *common, new, "--"]
        else:
            args = ["-C", str(self.cache), "diff", *common, self.validate_oid(old), new, "--"]
        return self._parse_raw_delta(self._run(args).stdout)

    def list_tree(self, revision: str) -> list[TreeEntry]:
        revision = self.validate_oid(revision)
        data = self._run(["-C", str(self.cache), "ls-tree", "-r", "-z", "--full-tree", revision]).stdout
        entries: list[TreeEntry] = []
        for item in data.split(b"\x00"):
            if not item:
                continue
            metadata, path = item.split(b"\t", 1)
            mode, kind, oid = metadata.split(b" ")
            entries.append(TreeEntry(mode.decode(), kind.decode(), oid.decode(), path))
        return entries

    def read_blob(self, oid: str, maximum: int) -> bytes:
        if not OID.fullmatch(oid):
            raise GitError("INVALID_OID", "blob ID must be full")
        size = int(self._run(["-C", str(self.cache), "cat-file", "-s", oid], limit=1024).stdout)
        if size > maximum:
            raise GitError("BLOB_TOO_LARGE", "blob exceeds configured limit")
        data = self._run(["-C", str(self.cache), "cat-file", "blob", oid], limit=maximum + 1).stdout
        if len(data) != size:
            raise GitError("BLOB_SIZE_MISMATCH", "blob read was incomplete")
        return data
