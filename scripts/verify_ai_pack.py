#!/usr/bin/env python3
"""Проверка ai-pack: копии = источники, ключевые утверждения START_HERE правдивы."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "ai-pack"

# Должны совпадать с scripts/build_ai_pack.py
COPY_MAP: list[tuple[str, str]] = [
    ("AGENTS.md", "00_core/AGENTS.md"),
    ("docs/AGENT_MAP.md", "00_core/AGENT_MAP.md"),
    (".cursor/rules/video-pipeline-ops.mdc", "00_core/video-pipeline-ops.md"),
    (".cursor/rules/video-pipeline-map.mdc", "00_core/video-pipeline-map.md"),
    (".cursor/rules/scene-design-model.mdc", "00_core/scene-design-model.md"),
    ("docs/PROMPT_CONTRACT.md", "01_data_prompts/PROMPT_CONTRACT.md"),
    ("docs/DB_V2.md", "01_data_prompts/DB_V2.md"),
    ("docs/PROMPTS_BLOCKS.md", "01_data_prompts/PROMPTS_BLOCKS.md"),
    ("docs/NODE_SYSTEM.md", "01_data_prompts/NODE_SYSTEM.md"),
    ("docs/OPERATOR_BIBLE.md", "02_ops/OPERATOR_BIBLE.md"),
    ("docs/MASS_CREATION.md", "02_ops/MASS_CREATION.md"),
    (".env.example", "03_config/env.example"),
    ("pyproject.toml", "03_config/pyproject.toml"),
]


def _strip_frontmatter_mdc(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[2].lstrip("\n")
    return text


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def check_copies() -> list[str]:
    errors: list[str] = []
    for src_rel, dst_rel in COPY_MAP:
        src = ROOT / src_rel
        dst = PACK / dst_rel
        if not src.is_file():
            errors.append(f"MISSING SOURCE: {src_rel}")
            continue
        if not dst.is_file():
            errors.append(f"MISSING PACK: {dst_rel}")
            continue
        raw = src.read_text(encoding="utf-8")
        if src.suffix == ".mdc":
            raw = _strip_frontmatter_mdc(raw)
        pack = dst.read_text(encoding="utf-8")
        if _norm(raw) != _norm(pack):
            errors.append(f"STALE COPY: {dst_rel} != {src_rel} (run build_ai_pack.py)")
    return errors


def check_start_here() -> list[str]:
    errors: list[str] = []
    start = PACK / "START_HERE.md"
    if not start.is_file():
        return ["MISSING: ai-pack/START_HERE.md"]
    text = start.read_text(encoding="utf-8")

    # Файлы, на которые ссылается START_HERE, должны существовать в паке.
    for rel in (
        "00_core/AGENTS.md",
        "00_core/AGENT_MAP.md",
        "00_core/video-pipeline-ops.md",
        "01_data_prompts/PROMPT_CONTRACT.md",
        "01_data_prompts/DB_V2.md",
        "01_data_prompts/NODE_SYSTEM.md",
        "01_data_prompts/PROMPTS_BLOCKS.md",
        "02_ops/OPERATOR_BIBLE.md",
        "02_ops/MASS_CREATION.md",
        "03_config/env.example",
    ):
        if not (PACK / rel).is_file():
            errors.append(f"START_HERE links to missing {rel}")

    # Код, на который ссылаемся, должен существовать.
    for rel in (
        "app/services/gpt_text_builder.py",
        "app/services/chatgpt_xlsx.py",
        "app/orchestrator/node_registry.py",
        "STUDIO.cmd",
        "scripts/build_ai_pack.py",
        "tests/test_status_sequencing_gates.py",
        "tests/test_animation_prompt_gpt.py",
        "tests/test_chatgpt_attachment_guard.py",
    ):
        if not (ROOT / rel).is_file():
            errors.append(f"START_HERE references missing code/test: {rel}")

    # Контрактные утверждения — сверка с реальным кодом.
    from app.services.gpt_text_builder import ENRICH_DEFAULT_ACCOMPANYING_TEXT
    from app.services.chatgpt_xlsx import PLAN_XLSX_OUTPUT_FOOTER

    if "xlsx" in ENRICH_DEFAULT_ACCOMPANYING_TEXT.lower() and "без xlsx" not in ENRICH_DEFAULT_ACCOMPANYING_TEXT.lower():
        # Допускаем слово xlsx в запрете («без xlsx»).
        if "прилож" in ENRICH_DEFAULT_ACCOMPANYING_TEXT.lower():
            errors.append("ENRICH footer still asks to attach xlsx")
    if "apply-ops" not in ENRICH_DEFAULT_ACCOMPANYING_TEXT.lower() and "apply-ops" not in ENRICH_DEFAULT_ACCOMPANYING_TEXT:
        errors.append("ENRICH footer missing apply-ops")
    if "# Лист:" in PLAN_XLSX_OUTPUT_FOOTER and "Не прикладывай" not in PLAN_XLSX_OUTPUT_FOOTER:
        errors.append("PLAN footer still requires TSV # Лист as primary output")

    # video data-guard must require anim prompts
    guard = (ROOT / "app/services/step_data_guard.py").read_text(encoding="utf-8")
    if "scan_missing_animation_prompts" not in guard:
        errors.append("step_data_guard no longer requires anim prompts for videos")

    # linear media preference in auto_advance
    aa = (ROOT / "app/orchestrator/auto_advance.py").read_text(encoding="utf-8")
    if "_LINEAR_MEDIA_READY" not in aa:
        errors.append("auto_advance missing _LINEAR_MEDIA_READY gate")

    # sidecar sync NodeRun
    side = (ROOT / "app/services/anim_pr_sidecar.py").read_text(encoding="utf-8")
    if "sync_anim_pr_noderun_done" not in side:
        errors.append("anim_pr_sidecar missing NodeRun sync")

    # START_HERE не должен утверждать фиксированную ветку housepc как факт этого ПК,
    # если .env говорит иначе — только список веток.
    env_branch = None
    env_path = ROOT / ".env"
    if env_path.is_file():
        m = re.search(r"^ORCHESTRATOR_GIT_BRANCH\s*=\s*(\S+)", env_path.read_text(encoding="utf-8"), re.M)
        if m:
            env_branch = m.group(1).strip()
    if "этот ПК = `housepc`" in text or "этот ПК: housepc" in text.lower():
        if env_branch and env_branch != "housepc":
            errors.append(
                f"START_HERE claims housepc but .env ORCHESTRATOR_GIT_BRANCH={env_branch}"
            )

    ops = (PACK / "00_core/video-pipeline-ops.md").read_text(encoding="utf-8")
    if "этот ПК = `housepc`" in ops or "ORCHESTRATOR_GIT_BRANCH=housepc`" in ops:
        errors.append("ops still hardcodes this PC as housepc — read .env instead")
    if "колонка R48 в project.xlsx`, не stale DB" in ops or (
        "колонка R48" in ops and "не stale DB" in ops
    ):
        errors.append("ops still claims R48/xlsx is SoT for anim_pr (DB is SoT)")
    if "Источник правды" in ops and "DB" not in ops.split("anim_pr", 1)[-1][:400]:
        # soft check: anim_pr section should mention DB
        anim_sec = ops.split("## anim_pr", 1)[-1][:500]
        if "DB" not in anim_sec and "Frame.animation_prompt" not in anim_sec:
            errors.append("ops anim_pr section must state DB SoT")

    bible = (PACK / "02_ops/OPERATOR_BIBLE.md").read_text(encoding="utf-8")
    if "→ **[2]** (`origin/main`)" in bible or "→ **[2]** (origin/main)" in bible:
        errors.append("OPERATOR_BIBLE still says [2]=git update (actually stop)")
    if "**[4]**" not in bible and "пункт **[4]**" not in bible:
        # update path should mention [4]
        if "Обновление" in bible and "[4]" not in bible:
            errors.append("OPERATOR_BIBLE update path must mention Studio [4]")

    return errors


def check_no_secrets() -> list[str]:
    errors: list[str] = []
    secretish = re.compile(
        r"(sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._\-]{20,}|"
        r"api[_-]?key\s*=\s*[A-Za-z0-9]{16,})",
        re.I,
    )
    for path in PACK.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".png", ".jpg", ".mp4", ".db"}:
            errors.append(f"BINARY/DATA in pack: {path.relative_to(PACK)}")
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"NON_TEXT in pack: {path.relative_to(PACK)}")
            continue
        if secretish.search(body):
            errors.append(f"POSSIBLE_SECRET in {path.relative_to(PACK)}")
        # filled .env pattern
        if path.name == ".env" or path.name.endswith(".env"):
            errors.append(f"ENV FILE in pack: {path.relative_to(PACK)}")
    return errors


def main() -> int:
    # Ensure pack is built fresh first (caller may rebuild).
    errors: list[str] = []
    errors.extend(check_copies())
    errors.extend(check_start_here())
    errors.extend(check_no_secrets())

    warns = [e for e in errors if e.startswith("WARN_")]
    hard = [e for e in errors if not e.startswith("WARN_")]

    if hard:
        print("ai-pack VERIFY FAIL:")
        for e in hard:
            print(f"  - {e}")
        for e in warns:
            print(f"  ! {e}")
        return 1
    if warns:
        print("ai-pack VERIFY OK with warnings:")
        for e in warns:
            print(f"  ! {e}")
    else:
        print("ai-pack VERIFY OK")
    head = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
    ).strip()
    print(f"  git HEAD: {head}")
    print(f"  pack files checked: {len(COPY_MAP)} copies + START_HERE")
    return 0


if __name__ == "__main__":
    # Allow importing app.*
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
