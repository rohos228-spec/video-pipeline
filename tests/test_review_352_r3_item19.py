"""Ревью #352 п.19: next — только относительный путь."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_safe_auth_next_rejects_open_redirect() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = r"""
import { safeAuthNext } from "./web/src/lib/safe-next.ts";
const cases = [
  ["javascript:alert(1)", "/profile"],
  ["https://evil.example", "/profile"],
  ["//evil.example", "/profile"],
  ["/\\evil", "/profile"],
  ["/studio", "/studio"],
  [null, "/profile"],
];
for (const [raw, expect] of cases) {
  const got = safeAuthNext(raw);
  if (got !== expect) {
    console.error(JSON.stringify({ raw, got, expect }));
    process.exit(1);
  }
}
"""
    proc = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
