"""№19: python -m app.monitor.report не падает NameError на print_report."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_print_report_is_defined_before_main_guard() -> None:
    src = Path("app/monitor/report.py").read_text(encoding="utf-8")
    assert src.rfind("def print_report") < src.rfind('if __name__ == "__main__"')


def test_report_cli_missing_dir_is_not_nameerror(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "app.monitor.report", "--dir", str(tmp_path)],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    blob = proc.stdout + proc.stderr
    assert "NameError" not in blob
    assert proc.returncode == 1
