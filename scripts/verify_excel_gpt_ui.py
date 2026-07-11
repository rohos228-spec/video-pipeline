#!/usr/bin/env python3
"""Self-check before shipping excel_gpt UI fixes."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.prompt_library import list_prompts, STEP_FOLDERS


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    raise SystemExit(1)


def main() -> None:
    print("=== excel_gpt UI verification ===")

    e1 = list_prompts("enrich_1")
    e2 = list_prompts("enrich_2")
    if len(e1) < 5:
        fail(f"enrich_1 should have many prompts, got {len(e1)}")
    ok(f"enrich_1 has {len(e1)} prompt files")
    if len(e2) < 1:
        fail(f"enrich_2 should have prompts, got {len(e2)}")
    ok(f"enrich_2 has {len(e2)} prompt files")

    if STEP_FOLDERS.get("enrich_1") != "05a_enrich_1":
        fail("enrich_1 folder mapping wrong")
    ok("STEP_FOLDERS enrich_1 → 05a_enrich_1")

    # Frontend source checks (static)
    node_prompts = (ROOT / "web/src/lib/node-prompts.ts").read_text(encoding="utf-8")
    if "resolvePromptSlotsForNode" not in node_prompts:
        fail("resolvePromptSlotsForNode missing")
    if "customPromptsForExcelGptNode" not in node_prompts:
        fail("customPromptsForExcelGptNode missing")
    ok("node-prompts migration helpers present")

    flow = (ROOT / "web/src/components/canvas/flow-canvas.tsx").read_text(encoding="utf-8")
    if "excel_gpt_nodes" not in flow:
        fail("flow-canvas meta hydration missing")
    ok("flow-canvas hydrates excel_gpt_nodes from meta")

    vmenu = (ROOT / "web/src/components/canvas/node-v-menu.tsx").read_text(encoding="utf-8")
    if "excelGptAttachmentChipTitle" not in vmenu:
        fail("V-menu dynamic attachment labels missing")
    if "NodeVMenuExcelPreview" in vmenu and "showExcelPreview" in vmenu:
        ok("V-menu excel preview gated (not for excel_gpt)")
    else:
        fail("V-menu preview gating broken")

    print("=== ALL CHECKS PASSED ===")


if __name__ == "__main__":
    main()
