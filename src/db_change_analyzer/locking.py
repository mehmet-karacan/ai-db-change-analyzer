from __future__ import annotations

import os
import time
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType


class LockBusyError(RuntimeError):
    pass


class ScopeLock(AbstractContextManager["ScopeLock"]):
    """Advisory OS lock whose inode is never unlinked."""

    def __init__(self, path: Path, wait_seconds: int = 0) -> None:
        self.path = path
        self.wait_seconds = wait_seconds
        self._handle = None

    def __enter__(self) -> "ScopeLock":
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._handle = self.path.open("a+b", buffering=0)
        if self._handle.tell() == 0:
            self._handle.write(b"0")
            self._handle.flush()
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                self._lock_nonblocking()
                return self
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise LockBusyError("scope lock is held") from exc
                time.sleep(0.05)

    def _lock_nonblocking(self) -> None:
        assert self._handle is not None
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
