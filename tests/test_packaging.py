from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_contract_checker() -> None:
    subprocess.run([sys.executable, str(ROOT / "tools" / "check_contracts.py")], cwd=ROOT, check=True)


def test_wheel_excludes_private_inputs(tmp_path: Path) -> None:
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)], cwd=ROOT, check=True)
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert any(name.endswith("prompts/system.tr.txt") for name in names)
    assert any(name.endswith("prompts/analysis_policy.tr.md") for name in names)
    assert any(name.endswith("prompts/report_language.tr.md") for name in names)
    assert any(name.endswith("templates/report_email.html.j2") for name in names)
    assert any(name.endswith("templates/innova_v1/report_email.html.j2") for name in names)
    assert any(name.endswith("templates/innova_v1/report_email.txt.j2") for name in names)
    assert any(name.endswith("assets/innova-logo-approved.png") for name in names)
    assert any(name.endswith("schemas/result.schema.json") for name in names)
    assert any(name.endswith("schemas/source-review-input.schema.json") for name in names)
    assert any(name.endswith("source_review.py") for name in names)
    assert not any(name.endswith(".sql.zip") or "AKTIF_GOREV" in name or ".ai-db-change-analyzer" in name for name in names)
