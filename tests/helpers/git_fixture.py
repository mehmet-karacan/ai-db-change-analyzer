from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
        self.run("config", "user.name", "Fixture")
        self.run("config", "user.email", "fixture@example.invalid")

    def run(self, *args: str) -> str:
        environment = os.environ.copy()
        environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
        completed = subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True, env=environment)
        return completed.stdout.strip()

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    def commit(self, message: str) -> str:
        self.run("add", "-A")
        self.run("commit", "-m", message)
        return self.run("rev-parse", "HEAD")

    def branch(self, name: str, start: str) -> None:
        self.run("switch", "-c", name, start)

    def switch(self, name: str) -> None:
        self.run("switch", name)
