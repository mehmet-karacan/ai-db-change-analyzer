from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an approved offline wheelhouse using the hashed lock files")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--include-dev", action="store_true")
    args = parser.parse_args()
    destination = Path(args.destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    locks = [ROOT / "requirements.lock"]
    if args.include_dev:
        locks.append(ROOT / "requirements-dev.lock")
    for lock in locks:
        subprocess.run([sys.executable, "-m", "pip", "download", "--require-hashes", "--only-binary=:all:", "--dest", str(destination), "-r", str(lock)], check=True, shell=False)
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(destination), str(ROOT)], check=True, shell=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
