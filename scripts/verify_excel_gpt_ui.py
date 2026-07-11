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
    if len(e1) < 5:
        fail(f"enrich_1 should have many prompts, got {len(e1)}")
    ok(f"enrich_1 folder has {len(e1)} prompt files (shown in PromptFilesPanel)")

    if STEP_FOLDERS.get("enrich_1") != "05a_enrich_1":
        fail("enrich_1 folder mapping wrong")
    ok("STEP_FOLDERS enrich_1 → 05a_enrich_1")

    node_prompts = (ROOT / "web/src/lib/node-prompts.ts").read_text(encoding="utf-8")
    if "enrichFolderPromptSlots" in node_prompts or "expandExcelGptPromptSlots" in node_prompts:
        fail("must not expand 5 folder chips in V-menu")
    if 'id: "main"' not in node_prompts or "applyExcelGptNodeContext" not in node_prompts:
        fail("excel_gpt must use single main slot + enrich step mapping")
    ok("V-menu: excel + blocks + one Промт дополнения N per node")

    studio = (ROOT / "web/src/components/studio/node-studio.tsx").read_text(encoding="utf-8")
    if "excelGptEnrichStepCode" not in studio or "PromptFilesPanel" not in studio:
        fail("node-studio must open PromptFilesPanel with enrich folder stepCode")
    ok("studio PromptFilesPanel uses enrich_N folder for excel_gpt")

    vmenu = (ROOT / "web/src/components/canvas/node-v-menu.tsx").read_text(encoding="utf-8")
    if "resolvePromptSlots(nodeType, slots, nodeKey" not in vmenu:
        fail("V-menu must pass nodeKey to resolvePromptSlots")
    ok("V-menu passes nodeKey (stepCode not reset to excel_gpt)")

    if (ROOT / "web/src/lib/enrich-folder-slots.ts").exists():
        fail("enrich-folder-slots.ts should be removed")
    ok("no 5-folder V-menu chips module")

    print("=== ALL CHECKS PASSED ===")


if __name__ == "__main__":
    main()
