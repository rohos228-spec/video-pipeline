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

# Legacy: раньше дописывался в UI/отправку — больше не используем.
_LEGACY_VERDICT_SUFFIX = """

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
    "enrich_1": HITLKind.approve_hero,
    "enrich_2": HITLKind.approve_hero,
    "enrich_3": HITLKind.approve_hero,
    "enrich_4": HITLKind.approve_hero,
    "enrich_5": HITLKind.approve_hero,
    "excel_gpt": HITLKind.approve_hero,
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
    "enrich_1": "excel",
    "enrich_2": "excel",
    "enrich_3": "excel",
    "enrich_4": "excel",
    "enrich_5": "excel",
    "excel_gpt": "excel",
}

# Шаги с GPT-проверкой «Вердикт» и скачиванием файла при «Не одобрено».
FILE_FIX_STEPS: frozenset[str] = frozenset(
    {
        "plan",
        "script",
        "split",
        "enrich_1",
        "enrich_2",
        "enrich_3",
        "enrich_4",
        "enrich_5",
        "excel_gpt",
        "img_pr",
        "anim_pr",
    }
)

STEP_VERDICT_FOLDER: dict[str, str] = {
    "plan": "check_plan",
    "script": "check_script",
    "split": "check_script",
    "hero": "check_hero",
    "items": "check_hero",
    "img_pr": "check_images",
    "images": "check_images",
    "anim_pr": "check_videos",
    "enrich_1": "check_plan",
    "enrich_2": "check_plan",
    "enrich_3": "check_plan",
    "enrich_4": "check_plan",
    "enrich_5": "check_plan",
    "excel_gpt": "check_plan",
}

VERDICT_STUDIO_STEPS: frozenset[str] = frozenset(
    {
        "plan",
        "script",
        "split",
        "hero",
        "items",
        "img_pr",
        "images",
        "anim_pr",
        "enrich_1",
        "enrich_2",
        "enrich_3",
        "enrich_4",
        "enrich_5",
        "excel_gpt",
    }
)


def verdict_template_for_project(project: Project, step_code: str) -> str:
    """Шаблон проверки из Studio: meta.gpt_verdict_templates[step_code]."""
    meta = getattr(project, "meta", None) or {}
    raw = meta.get("gpt_verdict_templates")
    if isinstance(raw, dict):
        name = str(raw.get(step_code) or "").strip()
        if name:
            return name
    return "default"


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
    fix_applied: bool = False
    fix_path: str = ""


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


def _strip_legacy_verdict_suffix(text: str) -> str:
    """Убрать старый автодописанный блок формата ответа из файла/черновика."""
    body = text.rstrip()
    suffix = _LEGACY_VERDICT_SUFFIX.strip()
    if suffix and suffix in body:
        body = body.split(suffix)[0].rstrip()
    return body


def load_verdict_check_prompt(step_code: str, *, template: str = "default") -> str:
    kind = STEP_TO_HITL.get(step_code)
    if kind is None:
        raise ValueError(f"нет GPT-проверки для шага {step_code!r}")
    path = verdict_template_path(step_code, template)
    if path is None or not path.is_file():
        base = load_check_prompt(kind)
    else:
        base = path.read_text(encoding="utf-8")
    return _strip_legacy_verdict_suffix(base)


def verdict_template_dir(step_code: str) -> Path | None:
    folder = STEP_VERDICT_FOLDER.get(step_code)
    if folder is None:
        kind = STEP_TO_HITL.get(step_code)
        if kind is None:
            return None
        folder = CHECK_FOLDER_BY_KIND.get(kind)
    if not folder:
        return None
    return PROMPTS_ROOT / folder


def verdict_template_path(step_code: str, name: str) -> Path | None:
    from app.services.prompt_library import is_valid_prompt_name

    if not is_valid_prompt_name(name):
        return None
    root = verdict_template_dir(step_code)
    if root is None:
        return None
    return root / f"{name}.md"


def list_verdict_templates(step_code: str) -> list[str]:
    root = verdict_template_dir(step_code)
    if root is None or not root.is_dir():
        return []
    names = sorted(p.stem for p in root.glob("*.md") if p.is_file())
    return names or ["default"]


def save_verdict_template(step_code: str, name: str, content: str) -> Path:
    from app.services.prompt_library import is_valid_prompt_name

    if not is_valid_prompt_name(name):
        raise ValueError(f"invalid template name: {name!r}")
    path = verdict_template_path(step_code, name)
    if path is None:
        raise ValueError(f"no template folder for step {step_code!r}")
    body = content.rstrip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n", encoding="utf-8")
    return path


def delete_verdict_template(step_code: str, name: str) -> bool:
    from app.services.prompt_library import DEFAULT_NAME, is_valid_prompt_name

    if name == DEFAULT_NAME:
        raise ValueError("шаблон default удалять нельзя")
    if not is_valid_prompt_name(name):
        raise ValueError(f"invalid template name: {name!r}")
    path = verdict_template_path(step_code, name)
    if path is None:
        raise ValueError(f"no template folder for step {step_code!r}")
    if not path.is_file():
        return False
    path.unlink()
    return True


async def attachments_for_step(
    session: AsyncSession, project: Project, step_code: str, *, node_key: str | None = None
) -> list[Path]:
    from app.services.excel_gpt_node import (
        EXCEL_GPT_STEP_CODE,
        attachment_paths,
        display_attachment_name,
    )

    paths: list[Path] = []
    if step_code == "topic":
        return paths
    if step_code in (EXCEL_GPT_STEP_CODE, "enrich_1", "enrich_2", "enrich_3", "enrich_4", "enrich_5"):
        return attachment_paths(project, node_key)
    xlsx = project.data_dir / "project.xlsx"
    excel_steps = (
        "plan",
        "script",
        "split",
        "img_pr",
        "anim_pr",
        "hero",
        "items",
    )
    if step_code in excel_steps:
        if xlsx.is_file():
            paths.append(xlsx)
    if step_code in ("script", "music", "split"):
        from app.services import chatgpt_xlsx as cx

        voice = cx.ensure_source_voiceover(project)
        if voice is not None:
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


async def _sync_verdict_excel_fix(
    session: AsyncSession, project: Project, step_code: str, xlsx_path: Path
) -> None:
    from app.services import chatgpt_xlsx as cx
    from app.services import xlsx_step_runners as xsr

    if step_code == "plan":
        await xsr.sync_after_plan(session, project, xlsx_path)
    elif step_code == "split":
        await xsr.sync_after_split(session, project, xlsx_path)
    elif step_code == "img_pr":
        await xsr.sync_after_img_pr(session, project, xlsx_path)
    else:
        await cx.sync_project_xlsx(session, project, xlsx_path, keep_fields=False)


async def _download_and_apply_verdict_fix(
    session: AsyncSession,
    project: Project,
    step_code: str,
    chatgpt_bot: Any,
    *,
    fix_text: str,
    fix_target: str,
    files: list[Path],
    last_raw: str,
) -> Path:
    """Скачать исправленный файл из ответа GPT (или после fix-сообщения) и применить."""
    from datetime import datetime

    from app.services import chatgpt_xlsx as cx
    from app.services.xlsx_versioning import backup_to_old, replace_with, validate_xlsx

    tmp_dir = cx.tmp_gpt_dir(project)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    data_dir = project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    async def _download_to(path: Path, fallback: str) -> bool:
        try:
            await chatgpt_bot.download_attachment_from_last_reply(
                path,
                timeout=600,
                fallback_text=fallback,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] gpt_verdict {} fix download failed: {}",
                project.id,
                step_code,
                e,
            )
            return False
        return path.is_file() and path.stat().st_size >= 10

    if fix_target == "voiceover":
        tmp_path = tmp_dir / f"verdict_{step_code}_{ts}.txt"
        target = data_dir / "voiceover.txt"
        if not await _download_to(tmp_path, last_raw):
            fix_msg = build_fix_user_message(fix_text, target=fix_target)
            if files:
                fix_raw = await chatgpt_bot.ask_with_files(
                    fix_msg,
                    files,
                    timeout=900,
                    project_id=project.id,
                )
            else:
                fix_raw = await chatgpt_bot.ask_fresh(fix_msg, timeout=900)
            if not await _download_to(tmp_path, fix_raw or ""):
                raise RuntimeError("GPT не вернул исправленный voiceover.txt")
        text = tmp_path.read_text(encoding="utf-8").strip()
        if len(text) < 10:
            raise RuntimeError("скачанный voiceover.txt пустой")
        cx.save_voiceover_text(project, target, text)
        project.script_text = text
        await session.flush()
        logger.info(
            "[#{}] gpt_verdict {} fix applied → voiceover.txt ({} симв.)",
            project.id,
            step_code,
            len(text),
        )
        return target

    tmp_path = tmp_dir / f"verdict_{step_code}_{ts}.xlsx"
    target = data_dir / "project.xlsx"
    if not await _download_to(tmp_path, last_raw):
        fix_msg = build_fix_user_message(fix_text, target=fix_target)
        if files:
            fix_raw = await chatgpt_bot.ask_with_files(
                fix_msg,
                files,
                timeout=900,
                project_id=project.id,
            )
        else:
            fix_raw = await chatgpt_bot.ask_fresh(fix_msg, timeout=900)
        if not await _download_to(tmp_path, fix_raw or ""):
            raise RuntimeError("GPT не вернул исправленный xlsx")
    validation_err = validate_xlsx(tmp_path)
    if validation_err is not None:
        raise RuntimeError(f"скачанный xlsx невалиден: {validation_err}")
    if target.is_file():
        backup_to_old(target)
    replace_with(target, tmp_path)
    await _sync_verdict_excel_fix(session, project, step_code, target)
    await session.flush()
    logger.info(
        "[#{}] gpt_verdict {} fix applied → project.xlsx",
        project.id,
        step_code,
    )
    return target


async def run_verdict_review(
    session: AsyncSession,
    project: Project,
    step_code: str,
    chatgpt_bot: Any,
    *,
    user_prompt: str | None = None,
    max_rounds: int = MAX_VERDICT_ROUNDS,
) -> VerdictRunResult:
    """Проверка «Вердикт»: одобрено → готово; не одобрено + файл → скачать и завершить."""
    if step_code not in VERDICT_STUDIO_STEPS:
        raise ValueError(f"шаг {step_code!r} без GPT-проверки «Вердикт»")

    if user_prompt is not None:
        check_prompt = _strip_legacy_verdict_suffix(user_prompt)
    else:
        template = verdict_template_for_project(project, step_code)
        check_prompt = load_verdict_check_prompt(step_code, template=template)
        logger.info(
            "[#{}] gpt_verdict {} template={!r} prompt_len={}",
            project.id,
            step_code,
            template,
            len(check_prompt),
        )
    fix_target = FIX_TARGET_BY_STEP.get(step_code, "excel")
    files = await attachments_for_step(session, project, step_code)

    history: list[str] = []
    last_raw = ""
    fix_text = ""

    for round_idx in range(1, max_rounds + 1):
        full_prompt = check_prompt.rstrip()

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
        if step_code in FILE_FIX_STEPS and fix_target in ("excel", "voiceover"):
            try:
                fix_path = await _download_and_apply_verdict_fix(
                    session,
                    project,
                    step_code,
                    chatgpt_bot,
                    fix_text=fix_text,
                    fix_target=fix_target,
                    files=files,
                    last_raw=last_raw or "",
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "[#{}] gpt_verdict {} fix file failed",
                    project.id,
                    step_code,
                )
                history.append(f"fix_file: error: {e}")
                return VerdictRunResult(
                    approved=False,
                    rounds=round_idx,
                    last_raw=last_raw or "",
                    fix_text=fix_text or str(e),
                    history=history,
                )
            history.append(f"fix_file: {fix_path.name}")
            return VerdictRunResult(
                approved=False,
                rounds=round_idx,
                last_raw=last_raw or "",
                fix_text=fix_text,
                history=history,
                fix_applied=True,
                fix_path=str(fix_path),
            )

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
    return verdict_template_path(step_code, "default")
