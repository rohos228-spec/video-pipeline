"""GPT-проверки в формате «Вердикт: …» (Studio + auto_mode).

См. docs/SPEC-RELIABILITY-QUEUE-GPT-MUSIC.md §4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, HITLKind, Project
from app.services.auto_review import CHECK_FOLDER_BY_KIND, PROMPTS_ROOT, load_check_prompt

_APPROVED = re.compile(
    r"вердикт\s*:\s*одобрено",
    re.IGNORECASE,
)
_REJECT = re.compile(
    r"вердикт\s*:\s*не\s+одобрено\s*:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

VERDICT_SUFFIX = """

ФОРМАТ ОТВЕТА (строго, без markdown):
Вердикт: Одобрено (все хорошо)
или
Вердикт: Не одобрено: <конкретные правки>
"""

MAX_VERDICT_ROUNDS = 3

STEP_TO_HITL: dict[str, HITLKind] = {
    "plan": HITLKind.approve_plan,
    "script": HITLKind.approve_script,
    "split": HITLKind.approve_script,
    "hero": HITLKind.approve_hero,
    "items": HITLKind.approve_hero,
    "img_pr": HITLKind.approve_images,
    "images": HITLKind.approve_images,
    "anim_pr": HITLKind.approve_videos,
    "video": HITLKind.approve_videos,
}

FIX_TARGET_BY_STEP: dict[str, str] = {
    "plan": "excel",
    "script": "voiceover",
    "split": "excel",
    "hero": "prompt",
    "items": "prompt",
    "img_pr": "excel",
    "images": "prompt",
    "anim_pr": "excel",
    "video": "prompt",
}

VERDICT_STUDIO_STEPS: frozenset[str] = frozenset(
    {"plan", "script", "split", "hero", "items", "img_pr", "images"}
)


@dataclass
class VerdictResult:
    approved: bool
    fix_text: str = ""
    raw: str = ""


@dataclass
class VerdictRunResult:
    approved: bool
    rounds: int
    last_raw: str = ""
    fix_text: str = ""
    history: list[str] = field(default_factory=list)


def parse_gpt_verdict(raw: str) -> VerdictResult:
    if not raw or not raw.strip():
        return VerdictResult(approved=False, fix_text="пустой ответ GPT", raw=raw or "")
    if _APPROVED.search(raw):
        return VerdictResult(approved=True, raw=raw)
    m = _REJECT.search(raw)
    if m:
        return VerdictResult(approved=False, fix_text=m.group(1).strip(), raw=raw)
    return VerdictResult(approved=False, fix_text=raw.strip()[:2000], raw=raw)


def build_fix_user_message(fix_text: str, *, target: str = "excel") -> str:
    if target == "excel":
        return (
            f"исправь Excel согласно требованиям и пришли обновленный файл: {fix_text}"
        )
    if target == "voiceover":
        return (
            f"исправь файл согласно требованиям и пришли обновленный файл: {fix_text}"
        )
    return f"исправь согласно требованиям: {fix_text}"


def load_verdict_check_prompt(step_code: str) -> str:
    kind = STEP_TO_HITL.get(step_code)
    if kind is None:
        raise ValueError(f"нет GPT-проверки для шага {step_code!r}")
    base = load_check_prompt(kind)
    return base.rstrip() + VERDICT_SUFFIX


async def attachments_for_step(
    session: AsyncSession, project: Project, step_code: str
) -> list[Path]:
    paths: list[Path] = []
    xlsx = project.data_dir / "project.xlsx"
    if step_code in ("plan", "script", "split", "img_pr", "anim_pr", "hero", "items"):
        if xlsx.is_file():
            paths.append(xlsx)
    voice = project.data_dir / "voiceover.txt"
    if step_code in ("script", "music") and voice.is_file():
        paths.append(voice)
    if step_code in ("hero", "items", "images"):
        kinds = [ArtifactKind.hero_reference, ArtifactKind.scene_image]
        arts = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.kind.in_(kinds),
                )
                .order_by(Artifact.id.asc())
                .limit(12)
            )
        ).scalars().all()
        for a in arts:
            if a.path and Path(a.path).is_file():
                paths.append(Path(a.path))
    return paths


def artifact_text_for_step(project: Project, step_code: str) -> str:
    if step_code == "plan":
        return (project.general_plan or "").strip()
    if step_code == "script":
        return (project.script_text or "").strip()
    return ""


async def run_verdict_review(
    session: AsyncSession,
    project: Project,
    step_code: str,
    chatgpt_bot: Any,
    *,
    user_prompt: str | None = None,
    max_rounds: int = MAX_VERDICT_ROUNDS,
) -> VerdictRunResult:
    """Цикл check → fix → recheck до одобрения или лимита раундов."""
    if step_code not in VERDICT_STUDIO_STEPS:
        raise ValueError(f"шаг {step_code!r} без GPT-проверки «Вердикт»")

    check_prompt = user_prompt or load_verdict_check_prompt(step_code)
    fix_target = FIX_TARGET_BY_STEP.get(step_code, "excel")
    artifact = artifact_text_for_step(project, step_code)
    files = await attachments_for_step(session, project, step_code)

    history: list[str] = []
    last_raw = ""
    fix_text = ""

    for round_idx in range(1, max_rounds + 1):
        parts = [check_prompt.rstrip()]
        if artifact:
            parts.append("\n\n---\n\n" + artifact)
        full_prompt = "\n".join(parts)

        await chatgpt_bot.new_conversation()
        if files:
            last_raw = await chatgpt_bot.ask_with_files(
                full_prompt,
                files,
                timeout=900,
                project_id=project.id,
            )
        else:
            last_raw = await chatgpt_bot.ask_fresh(full_prompt, timeout=900)

        verdict = parse_gpt_verdict(last_raw or "")
        history.append(f"round {round_idx}: {'ok' if verdict.approved else 'reject'}")
        logger.info(
            "[#{}] gpt_verdict {} round {}: approved={}",
            project.id,
            step_code,
            round_idx,
            verdict.approved,
        )
        if verdict.approved:
            return VerdictRunResult(
                approved=True,
                rounds=round_idx,
                last_raw=last_raw or "",
                history=history,
            )

        fix_text = verdict.fix_text
        if round_idx >= max_rounds:
            break

        fix_msg = build_fix_user_message(fix_text, target=fix_target)
        await chatgpt_bot.new_conversation()
        if files:
            await chatgpt_bot.ask_with_files(
                fix_msg,
                files,
                timeout=900,
                project_id=project.id,
            )
        else:
            await chatgpt_bot.ask_fresh(fix_msg, timeout=900)

    return VerdictRunResult(
        approved=False,
        rounds=max_rounds,
        last_raw=last_raw or "",
        fix_text=fix_text,
        history=history,
    )


def verdict_prompt_template_path(step_code: str) -> Path | None:
    kind = STEP_TO_HITL.get(step_code)
    if kind is None:
        return None
    folder = CHECK_FOLDER_BY_KIND.get(kind)
    if not folder:
        return None
    return PROMPTS_ROOT / folder / "default.md"
