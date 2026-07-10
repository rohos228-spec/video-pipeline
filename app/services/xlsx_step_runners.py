"""Полная логика xlsx-шагов ChatGPT — единый источник для TG-бота и воркера.

Telegram `_run_*_xlsx` и orchestrator steps вызывают одни и те же функции.
GPT-сессия (browser → new_conversation → ask_with_files → download) — в
`xlsx_gpt_flow`; здесь — подготовка файлов, валидация, backup/replace, sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services import xlsx_gpt_flow as xgf
from app.services.xlsx_versioning import (
    backup_to_old,
    normalize_xlsx_to_reference_layout,
    replace_with,
    validate_xlsx,
)
from app.services.voiceover_split_local import (
    parse_dash_separated_blocks,
    split_voiceover_locally,
    write_voiceover_blocks_to_xlsx,
)
from app.storage import for_project as _sheet_for_project

# Должен совпадать со строкой 4 в web/STUDIO_VERSION. Если в логе make_plan
# нет «xlsx_step_runners» — на диске старый make_plan.py (текст 30k в ask).
XLSX_STEP_RUNNERS_ID = "xlsx_step_runners-v74-normalize"


def _apply_split_fallback(
    xlsx_path: Path,
    voiceover_path: Path,
    *,
    gpt_reply: str,
) -> int:
    """GPT часто не пишет R49 — пробуем блоки из ответа или voiceover.txt."""
    blocks = parse_dash_separated_blocks(gpt_reply)
    if len(blocks) < 2 and voiceover_path.exists():
        blocks = split_voiceover_locally(
            voiceover_path.read_text(encoding="utf-8")
        )
    if len(blocks) < 2:
        return _count_v8_voiceover_blocks(xlsx_path)
    write_voiceover_blocks_to_xlsx(xlsx_path, blocks)
    return _count_v8_voiceover_blocks(xlsx_path)


def diagnose_split_xlsx(xlsx_path: Path) -> str:
    """Краткая диагностика для ошибок split: листы + блоки R49."""
    from openpyxl import load_workbook

    from app.services.xlsx_v8_import import (
        ROW_VOICEOVER_V8,
        _read_voiceover_blocks,
        has_v8_plan_sheet,
    )

    if not xlsx_path.exists():
        return f"файл не найден: {xlsx_path}"
    try:
        wb = load_workbook(filename=str(xlsx_path), data_only=True)
        try:
            sheets = list(wb.sheetnames)
            if not has_v8_plan_sheet(wb):
                return (
                    f"листы={sheets!r} — нет листа «план» (v8); "
                    f"voiceover должен быть в строке {ROW_VOICEOVER_V8}"
                )
            blocks = len(_read_voiceover_blocks(wb))
            return f"листы={sheets!r}, voiceover-блоков (R{ROW_VOICEOVER_V8})={blocks}"
        finally:
            wb.close()
    except Exception as e:  # noqa: BLE001
        return f"не удалось прочитать {xlsx_path.name}: {e}"


def _count_v8_voiceover_blocks(xlsx_path: Path) -> int:
    """Сколько voiceover-блоков уже записано в v8-xlsx (после разбивки)."""
    from openpyxl import load_workbook

    from app.services.xlsx_v8_import import _read_voiceover_blocks, has_v8_plan_sheet

    if not xlsx_path.exists():
        return 0
    try:
        wb = load_workbook(filename=str(xlsx_path), data_only=True)
        try:
            if not has_v8_plan_sheet(wb):
                return 0
            return len(_read_voiceover_blocks(wb))
        finally:
            wb.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("split_xlsx: cannot count voiceover blocks in {}: {}", xlsx_path, e)
        return 0


def _try_reuse_split_download(
    tmp_dir: Path, proj_xlsx: Path, *, min_blocks: int = 2
) -> XlsxRoundtripResult | None:
    """Если GPT уже отдал xlsx в tmp_gpt, не дергаем ChatGPT повторно.

    Только если скачанный файл новее project.xlsx (иначе это устаревший кэш).
    """
    if not proj_xlsx.exists():
        return None
    proj_mtime = proj_xlsx.stat().st_mtime
    candidates = sorted(
        tmp_dir.glob("split_*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates[:5]:
        if candidate.stat().st_size < 1024:
            continue
        if candidate.stat().st_mtime <= proj_mtime:
            continue
        if validate_xlsx(candidate) is not None:
            continue
        blocks = _count_v8_voiceover_blocks(candidate)
        if blocks < min_blocks:
            continue
        backup = backup_to_old(proj_xlsx)
        replace_with(proj_xlsx, candidate)
        logger.info(
            "split_xlsx: reuse downloaded {} ({} voiceover blocks) — skip GPT",
            candidate.name,
            blocks,
        )
        return XlsxRoundtripResult(
            reply_text="",
            downloaded_path=candidate,
            project_xlsx=proj_xlsx,
            backup_path=backup,
        )
    return None


@dataclass
class XlsxRoundtripResult:
    """Результат GPT round-trip с xlsx."""

    reply_text: str
    downloaded_path: Path
    project_xlsx: Path
    backup_path: Path | None = None


def _ts() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _ensure_project_xlsx(project: Project) -> Path:
    proj_xlsx = project.data_dir / "project.xlsx"
    if proj_xlsx.exists():
        return proj_xlsx
    sheet = _sheet_for_project(project)
    proj_xlsx = sheet.ensure_initialized(
        project_id=project.id, slug=project.slug
    )
    if not proj_xlsx.exists():
        raise FileNotFoundError(f"project.xlsx не найден: {proj_xlsx}")
    return proj_xlsx


async def run_plan_xlsx(
    project: Project,
    *,
    topic: str | None = None,
    project_id: int | None = None,
) -> XlsxRoundtripResult:
    """Шаг «План»: prompt.md + project.xlsx → обновлённый xlsx."""
    proj_xlsx = _ensure_project_xlsx(project)
    actual_topic = topic if topic is not None else (project.topic or "")

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_plan_prompt_file(
        project, tmp_dir, topic=actual_topic, ts=ts
    )
    chat_msg = cx.chat_message(
        project, "plan", topic=actual_topic, prompt_file_name=prompt_file.name
    )
    downloaded = tmp_dir / f"plan_{ts}.xlsx"

    logger.info(
        "plan_xlsx: prompt_file={} ({} байт), xlsx={}, chat_len={}",
        prompt_file.name,
        prompt_file.stat().st_size,
        proj_xlsx,
        len(chat_msg),
    )

    async def _gpt() -> str:
        return await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx],
            downloaded,
            project_id=project_id or project.id,
            validate_xlsx_download=True,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "plan", _gpt)

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        if normalize_xlsx_to_reference_layout(downloaded, proj_xlsx):
            validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        raise RuntimeError(f"скачанный xlsx невалиден: {validation_err}")

    backup = backup_to_old(proj_xlsx)
    replace_with(proj_xlsx, downloaded)
    return XlsxRoundtripResult(
        reply_text=reply,
        downloaded_path=downloaded,
        project_xlsx=proj_xlsx,
        backup_path=backup,
    )


async def run_script_xlsx(
    project: Project,
    *,
    project_id: int | None = None,
) -> tuple[XlsxRoundtripResult, str]:
    """Шаг «Закадровый текст»: prompt.txt + project.xlsx → voiceover.txt."""
    proj_xlsx = _ensure_project_xlsx(project)
    voiceover_path = proj_xlsx.parent / "voiceover.txt"

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_script_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project, "script", prompt_file_name=prompt_file.name
    )
    downloaded = tmp_dir / f"voiceover_{ts}.txt"

    logger.info(
        "script_xlsx: prompt_file={} ({} байт), xlsx={}, chat_len={}",
        prompt_file.name,
        prompt_file.stat().st_size,
        proj_xlsx,
        len(chat_msg),
    )

    async def _gpt() -> str:
        return await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx],
            downloaded,
            project_id=project_id or project.id,
            ask_timeout=1800.0,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "script", _gpt)

    if not downloaded.exists() or downloaded.stat().st_size < 10:
        raise RuntimeError(
            f"скачанный txt пустой или повреждён: {downloaded}"
        )

    voiceover_text = downloaded.read_text(encoding="utf-8").strip()
    cx.save_voiceover_text(project, voiceover_path, voiceover_text)

    return (
        XlsxRoundtripResult(
            reply_text=reply,
            downloaded_path=downloaded,
            project_xlsx=proj_xlsx,
        ),
        voiceover_text,
    )


async def run_split_xlsx(
    project: Project,
    *,
    project_id: int | None = None,
) -> XlsxRoundtripResult:
    """Шаг «Разбивка»: prompt + project.xlsx + voiceover.txt → xlsx."""
    proj_xlsx = _ensure_project_xlsx(project)
    voiceover = proj_xlsx.parent / "voiceover.txt"
    if not voiceover.exists() and project.script_text:
        voiceover.write_text(project.script_text.strip(), encoding="utf-8")
    if not voiceover.exists():
        raise FileNotFoundError(
            "voiceover.txt не найден — сначала пройди шаг «Закадровый текст»"
        )

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_split_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project, "split", prompt_file_name=prompt_file.name
    )
    downloaded = tmp_dir / f"split_{ts}.xlsx"

    reused = _try_reuse_split_download(tmp_dir, proj_xlsx)
    if reused is not None:
        return reused

    logger.info(
        "split_xlsx: prompt={}, xlsx={}, voiceover={}, chat_len={}",
        prompt_file.name,
        proj_xlsx.name,
        voiceover.name,
        len(chat_msg),
    )

    async def _gpt() -> str:
        return await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx, voiceover],
            downloaded,
            project_id=project_id or project.id,
            validate_xlsx_download=True,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "split", _gpt)

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        raise RuntimeError(f"скачанный xlsx невалиден: {validation_err}")

    downloaded_blocks = _count_v8_voiceover_blocks(downloaded)
    if downloaded_blocks < 2:
        downloaded_blocks = _apply_split_fallback(
            downloaded, voiceover, gpt_reply=reply or ""
        )

    on_disk_blocks = _count_v8_voiceover_blocks(proj_xlsx)
    logger.info(
        "split_xlsx: voiceover blocks — downloaded={}, project.xlsx={}",
        downloaded_blocks,
        on_disk_blocks,
    )

    backup: Path | None = None
    if downloaded_blocks >= 2:
        backup = backup_to_old(proj_xlsx)
        replace_with(proj_xlsx, downloaded)
    elif on_disk_blocks >= 2:
        logger.warning(
            "split_xlsx: GPT xlsx has {} blocks (<2) — keeping project.xlsx "
            "({} blocks), skip replace",
            downloaded_blocks,
            on_disk_blocks,
        )
        downloaded = proj_xlsx
    else:
        on_disk_blocks = _apply_split_fallback(
            proj_xlsx, voiceover, gpt_reply=reply or ""
        )
        if on_disk_blocks >= 2:
            logger.warning(
                "split_xlsx: applied local fallback — {} blocks in project.xlsx",
                on_disk_blocks,
            )
            downloaded = proj_xlsx
        else:
            diag = diagnose_split_xlsx(downloaded)
            raise RuntimeError(
                "разбивка не найдена в xlsx после GPT — "
                f"downloaded={downloaded_blocks} блоков, "
                f"project.xlsx={on_disk_blocks} блоков. {diag}"
            )

    return XlsxRoundtripResult(
        reply_text=reply,
        downloaded_path=downloaded,
        project_xlsx=proj_xlsx,
        backup_path=backup,
    )


async def run_img_pr_xlsx(
    project: Project,
    *,
    n_frames: int | None = None,
    project_id: int | None = None,
) -> XlsxRoundtripResult:
    """Шаг «Промты картинок»: prompt.md + project.xlsx → xlsx с image_prompt."""
    proj_xlsx = _ensure_project_xlsx(project)

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_img_pr_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project,
        "img_pr",
        prompt_file_name=prompt_file.name,
        n_frames=n_frames or 0,
    )
    downloaded = tmp_dir / f"img_pr_{ts}.xlsx"

    logger.info(
        "img_pr_xlsx: prompt_file={} ({} байт), xlsx={}, chat_len={}",
        prompt_file.name,
        prompt_file.stat().st_size,
        proj_xlsx,
        len(chat_msg),
    )

    async def _gpt() -> str:
        return await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx],
            downloaded,
            project_id=project_id or project.id,
            validate_xlsx_download=True,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "img_pr", _gpt)

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        raise RuntimeError(f"скачанный xlsx невалиден: {validation_err}")

    backup = backup_to_old(proj_xlsx)
    from app.storage.plan_sheet_v8 import merge_gpt_image_prompt_rows_into_project

    n45, n46 = merge_gpt_image_prompt_rows_into_project(proj_xlsx, downloaded)
    if n45 == 0:
        raise RuntimeError(
            "GPT не заполнил строку 45 (промты картинок) — project.xlsx не изменён"
        )
    logger.info(
        "img_pr_xlsx: в project.xlsx слиты R45={} R46={} (enrich сохранён)",
        n45,
        n46,
    )
    return XlsxRoundtripResult(
        reply_text=reply,
        downloaded_path=downloaded,
        project_xlsx=proj_xlsx,
        backup_path=backup,
    )


async def sync_after_plan(
    session: AsyncSession, project: Project, xlsx_path: Path
) -> None:
    await cx.sync_project_xlsx(session, project, xlsx_path, keep_fields=False)
    from app.services.plan_validation import is_meaningful_general_plan

    plan_text = (project.general_plan or "").strip()
    if not is_meaningful_general_plan(plan_text):
        raise RuntimeError(
            "ChatGPT вернул пустой/слишком короткий план после xlsx-sync"
        )


async def sync_after_split(
    session: AsyncSession, project: Project, xlsx_path: Path
) -> dict | None:
    return await cx.sync_project_xlsx(
        session,
        project,
        xlsx_path,
        keep_fields=False,
        update_frames_voiceover=True,
    )


async def sync_after_img_pr(
    session: AsyncSession, project: Project, xlsx_path: Path
) -> None:
    await cx.sync_project_xlsx(session, project, xlsx_path, keep_fields=False)
    from app.services.xlsx_v8_import import apply_v8_image_prompts_from_xlsx

    applied = await apply_v8_image_prompts_from_xlsx(session, project, xlsx_path)
    if applied:
        logger.info(
            "[#{}] sync_after_img_pr: image_prompt из xlsx для кадров {}",
            project.id,
            applied,
        )
    from app.services.plan_shot2 import (
        SHOT2_PROMPT_ATTR,
        SHOT2_STATUS_ATTR,
        read_shot2_columns,
    )

    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    by_num = read_shot2_columns(xlsx_path)
    shot2_n = 0
    for fr in frames:
        info = by_num.get(fr.number)
        if info is None or not info.has_shot2:
            continue
        attrs = dict(fr.attrs or {})
        attrs[SHOT2_PROMPT_ATTR] = info.prompt
        if SHOT2_STATUS_ATTR not in attrs:
            attrs[SHOT2_STATUS_ATTR] = "image_prompt_ready"
        fr.attrs = attrs
        shot2_n += 1
    if shot2_n:
        await session.flush()
        logger.info(
            "[#{}] sync_after_img_pr: shot_02 промты для {} кадров",
            project.id,
            shot2_n,
        )


def set_status_if_behind(
    project: Project, target: ProjectStatus
) -> None:
    """Ставит статус, если текущий «ниже» target (как в bot после xlsx)."""
    from app.telegram.menu import status_order as _ord

    if _ord(project.status) < _ord(target):
        project.status = target
