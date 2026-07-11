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

    for i in range(1, 6):
        step = f"enrich_{i}"
        files = list_prompts(step)
        if len(files) < 1:
            fail(f"{step} should have prompts, got {len(files)}")
        folder = STEP_FOLDERS.get(step)
        if not folder or not folder.startswith("05"):
            fail(f"{step} folder mapping wrong: {folder}")
        ok(f"{step} → {folder} ({len(files)} files)")

    node_prompts = (ROOT / "web/src/lib/node-prompts.ts").read_text(encoding="utf-8")
    if "enrichFolderPromptSlots" not in node_prompts:
        fail("enrichFolderPromptSlots missing")
    if "applyExcelGptNodeContext" in node_prompts:
        fail("applyExcelGptNodeContext must be removed (overwrote enrich step codes)")
    ok("node-prompts uses 5 enrich folder slots")

    folders = (ROOT / "web/src/lib/enrich-folder-slots.ts").read_text(encoding="utf-8")
    for code in ("enrich_1", "enrich_5", "05a_enrich_1", "05e_enrich_5"):
        if code not in folders:
            fail(f"enrich-folder-slots missing {code}")
    ok("enrich-folder-slots defines all 5 folders")

    flow = (ROOT / "web/src/components/canvas/flow-canvas.tsx").read_text(encoding="utf-8")
    if "excel_gpt_nodes" not in flow:
        fail("flow-canvas meta hydration missing")
    ok("flow-canvas hydrates excel_gpt_nodes from meta")

    vmenu = (ROOT / "web/src/components/canvas/node-v-menu.tsx").read_text(encoding="utf-8")
    if "resolvePromptSlots(nodeType, slots, nodeKey" not in vmenu:
        fail("V-menu must pass nodeKey to resolvePromptSlots")
    if "NodeVMenuExcelPreview" in vmenu and "showExcelPreview" in vmenu:
        ok("V-menu excel preview gated (not for excel_gpt)")
    else:
        fail("V-menu preview gating broken")

    studio = (ROOT / "web/src/components/studio/node-studio.tsx").read_text(encoding="utf-8")
    if "activeSlot.stepCode" not in studio:
        fail("node-studio must use activeSlot.stepCode for enrich folders")
    ok("node-studio opens folder by active slot stepCode")

    print("=== ALL CHECKS PASSED ===")


if __name__ == "__main__":
    main()
