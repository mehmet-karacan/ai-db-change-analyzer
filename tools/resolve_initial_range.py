from __future__ import annotations

import os
import sys

from db_change_analyzer.config import load_config
from db_change_analyzer.git_client import GitClient
from db_change_analyzer.state import SqliteStateStore


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: resolve_initial_range.py CONFIG")
    config = load_config(sys.argv[1])
    store = SqliteStateStore(config)
    with store.lock():
        git = GitClient(store.paths.source, timeout_seconds=config.git.timeout_seconds)
        target = git.fetch(
            config.repository.url,
            config.repository.branch,
            username=os.environ.get("DB_ANALYZER_GIT_USERNAME"),
            password=os.environ.get("DB_ANALYZER_GIT_PASSWORD"),
        )
        parents = git.parents(target)
    print(parents[0] if parents else "ROOT", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
