"""Ревью #352 r4 п.19: next не пропускает TAB/LF и percent-encoded управляющие.

Недозакрыто после c1177753: trim не видит \\t/\\n/\\r и %09 в середине пути.
"""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_safe_auth_next_rejects_control_characters() -> None:
    node = shutil.which("node")
    assert node, "node is required for safe-next"
    script = r"""
import { safeAuthNext } from "./web/src/lib/safe-next.ts";
const cases = [
  ["/\t/evil", "/profile"],
  ["/\n/evil", "/profile"],
  ["/\r/evil", "/profile"],
  ["/%09/evil", "/profile"],
  ["/%0a/evil.example", "/profile"],
  ["/%0d/evil", "/profile"],
  ["/%2509/evil", "/profile"],
  ["/studio\u0001", "/profile"],
  ["/studio\u007f", "/profile"],
  ["/%2f/evil.example", "/profile"],
  ["/.//evil.example", "/profile"],
  ["/..//evil.example", "/profile"],
  ["/%2e//evil.example", "/profile"],
  ["/.\\/evil.example", "/profile"],
  ["/.\\\\evil.example", "/profile"],
  ["/%5c%5cevil.example", "/profile"],
  ["/%2e%5c%5cevil.example", "/profile"],
  ["/.%5c%5cevil.example", "/profile"],
  ["/.%2f/evil.example", "/profile"],
  ["/%2e%2f/evil.example", "/profile"],
  ["/%2e%2f%2fevil.example", "/profile"],
  ["/..%2f/evil.example", "/profile"],
  ["/%2e%2e/%2f/evil.example", "/profile"],
  ["/%2e%2e//evil.example", "/profile"],
  ["/foo/%2e%2e/%2e%2e//evil.example", "/profile"],
  ["/ok/path", "/ok/path"],
  ["/foo/../bar", "/bar"],
  ["javascript:alert(1)", "/profile"],
  ["https://evil.example", "/profile"],
  ["//evil.example", "/profile"],
  ["/\\evil", "/profile"],
  ["/studio", "/studio"],
  ["/studio?x=1#h", "/studio?x=1#h"],
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
    assert proc.returncode == 0, proc.stderr or proc.stdout
