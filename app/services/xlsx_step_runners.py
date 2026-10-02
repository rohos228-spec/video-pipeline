"""Полная логика xlsx-шагов ChatGPT — единый источник для TG-бота и воркера.

Telegram `_run_*_xlsx` и orchestrator steps вызывают одни и те же функции.
GPT-сессия (browser → new_conversation → ask_with_files → download) — в
`xlsx_gpt_flow`; здесь — подготовка файлов, валидация, backup/replace, sync.
"""

from __future__ import annotations

import re

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
XLSX_STEP_RUNNERS_ID = "xlsx_step_runners-v98-nii67-hg-ban"
_EMPTY_OPS_BACKOFF_S = (5.0, 10.0, 15.0)


def img_pr_live_streams(project: Project | None) -> int:
    """Параллель GPT для img_pr.

    check_streams справа — верхняя граница; низ 8, иначе 102 кадра
    при 2 потоках висят полчаса (волна gather ждёт всех).
    """
    from app.services.check_streams import get_check_streams

    n = get_check_streams(project)
    if n <= 0:
        return 1
    return max(8, n)


def _plan_empty_error(xlsx_path: Path, *, plan_len: int) -> RuntimeError:
    """Понятная ошибка: импортёр читает «Общий план», GPT часто пишет в «план»."""
    sheets: list[str] = []
    try:
        from openpyxl import load_workbook

        wb = load_workbook(filename=str(xlsx_path), read_only=True, data_only=True)
        sheets = list(wb.sheetnames)
        wb.close()
    except Exception:  # noqa: BLE001
        pass
    sheets_s = ", ".join(sheets) if sheets else "?"
    return RuntimeError(
        "лист «Общий план» пуст/шаблон после GPT "
        f"(прочитано {plan_len} симв., нужно ≥200); "
        "GPT заполнил не тот лист (часто «план» вместо «Общий план»). "
        f"Листы файла: [{sheets_s}]"
    )


def _assert_downloaded_plan_meaningful(xlsx_path: Path) -> None:
    """До replace project.xlsx — отказать, если «Общий план» не заполнен."""
    from openpyxl import load_workbook

    from app.services.plan_validation import is_meaningful_general_plan
    from app.services.xlsx_v8_import import _read_general_plan

    try:
        wb = load_workbook(filename=str(xlsx_path), data_only=True)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"скачанный xlsx не читается: {e}") from e
    try:
        plan_text = (_read_general_plan(wb) or "").strip()
    finally:
        wb.close()
    if not is_meaningful_general_plan(plan_text):
        raise _plan_empty_error(xlsx_path, plan_len=len(plan_text))


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
        from app.storage.plan_sheet_v8 import merge_gpt_voiceover_row_into_project

        backup = backup_to_old(proj_xlsx)
        merged = merge_gpt_voiceover_row_into_project(proj_xlsx, candidate)
        if merged < min_blocks:
            # Нет листа «план» в шаблоне / не v8 — полный replace как раньше.
            replace_with(proj_xlsx, candidate)
            merged = _count_v8_voiceover_blocks(proj_xlsx)
        logger.info(
            "split_xlsx: reuse downloaded {} (R49 merged={}, blocks={}) — skip GPT",
            candidate.name,
            merged,
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
    """Результат GPT round-trip (DB-first; xlsx только экспорт)."""

    reply_text: str
    downloaded_path: Path
    project_xlsx: Path
    backup_path: Path | None = None
    # DB-first plan: текст общего плана из apply-ops (без скачивания xlsx).
    plan_text: str | None = None
    # split: список {закадр, длительность?}
    frames_spec: list[dict] | None = None
    # img_pr / др.: готовые ops для apply_ops
    apply_ops: list[dict] | None = None
    # True = ops уже записаны в DB по батчам внутри runner (не apply повторно).
    ops_applied_inline: bool = False


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


_PLAN_DB_HINT = (
    "\n\n# ЗАПИСЬ СЦЕНАРИЯ — ИСТОЧНИК ПРАВДЫ БАЗА (НЕ Excel)\n"
    "Верни ТОЛЬКО JSON apply-ops. Без TSV, без `# Лист:`, без `@row=`, "
    "без ссылок на скачивание .xlsx:\n"
    '{"ops":[{"target":"project","fields":{"общий_план":"<полный текст сценария>"}}]}\n'
    "Текст в общем_плане — полный содержательный сценарий/план "
    "(минимум ~200 символов). Никакой прозы вокруг JSON.\n"
)


def extract_general_plan_from_gpt_reply(reply: str) -> str:
    """Достать общий_план из apply-ops JSON ответа модели."""
    from app.services import db_apply

    data = db_apply.extract_apply_ops_json(reply or "")
    if isinstance(data, dict):
        for op in data.get("ops") or []:
            if not isinstance(op, dict):
                continue
            fields = op.get("fields") or {}
            if not isinstance(fields, dict):
                continue
            for key in ("общий_план", "general_plan", "план", "сценарий"):
                val = fields.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            if str(op.get("target") or "").strip().lower() == "project":
                for val in fields.values():
                    if isinstance(val, str) and len(val.strip()) >= 80:
                        return val.strip()
    return ""


async def run_plan_xlsx(
    project: Project,
    *,
    topic: str | None = None,
    project_id: int | None = None,
) -> XlsxRoundtripResult:
    """Шаг «План»: GPT → apply-ops (общий_план) → DB; Excel только экспорт позже.

    Больше не скачиваем .xlsx / TSV writeback (HTML-страницы ломали шаг).
    """
    from app.services.plan_validation import is_meaningful_general_plan

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
    chat_msg = f"{chat_msg}{_PLAN_DB_HINT}"

    logger.info(
        "plan_db: prompt_file={} ({} байт), chat_len={} (без xlsx-download)",
        prompt_file.name,
        prompt_file.stat().st_size,
        len(chat_msg),
    )

    async def _gpt() -> str:
        # Промт + xlsx только как контекст; запись — JSON apply-ops, не файл.
        return await xgf.telegram_style_ask_with_files(
            chat_msg,
            [prompt_file, proj_xlsx],
            project_id=project_id or project.id,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "plan", _gpt)
    plan_text = extract_general_plan_from_gpt_reply(reply)
    if not is_meaningful_general_plan(plan_text):
        raise RuntimeError(
            "GPT не вернул общий_план в apply-ops JSON "
            f"(досталось {len(plan_text)} символов). Нужен формат: "
            '{"ops":[{"target":"project","fields":{"общий_план":"…"}}]}'
        )

    logger.info("plan_db: общий_план len={}", len(plan_text))
    return XlsxRoundtripResult(
        reply_text=reply,
        downloaded_path=proj_xlsx,
        project_xlsx=proj_xlsx,
        backup_path=None,
        plan_text=plan_text,
    )


_SCRIPT_DB_HINT = (
    "\n\n# ЗАПИСЬ ЗАКАДРА — ИСТОЧНИК ПРАВДЫ БАЗА (НЕ Excel)\n"
    "Верни ТОЛЬКО JSON apply-ops ИЛИ блок <<<VOICEOVER>>>…<<<END>>>:\n"
    '{"ops":[{"target":"project","fields":{"закадровый_текст":"<полный закадр>"}}]}\n'
    "Без TSV, без `# Лист:`, без скачивания файлов.\n"
)


_NII67_BANNED_PLACE_RE = re.compile(
    r"(?i)(?:"
    r"прачечн\w*"
    r"|laundry"
    r"|архивохранилищ\w*"
    r"|(?<![\wА-Яа-я])архив(?![\wА-Яа-я])"
    r"|шахт\w*"
    r"|штольн\w*"
    r"|mine\s*shaft"
    r"|туннел\w*"
    r"|тоннел\w*"
    r"|подземн\w*\s+коридор\w*"
    r"|каменоломн\w*"
    r")"
)
_NII67_LAZY_TROPE_RE = re.compile(
    r"(?i)(?:"
    r"маятник\w*.{0,40}тен"
    r"|тен\w*.{0,40}маятник"
    r"|тени\s+без\s+люд"
    r"|человеческ\w*\s+тени\s+без"
    r"|тени\s+на\s+стенах?\s+без"
    r")"
)

_NII67_STEP_CENTRIC_RE = re.compile(
    r"(?i)(?:"
    r"каждый\s+(?:мой\s+)?шаг"
    r"|кажд\w*\s+мо[йи]\s+шаг"
    r"|шаг\w*\s+геро"
    r"|отпечаток\w*.{0,20}шаг"
    r"|шаг\w*.{0,20}отпечаток"
    r"|след\w*\s+(?:шаг|ног|ступн)"
    r"|след\w*\s+на\s+(?:паркет|пол|потолк|кафел)"
    r"|шаг\w*.{0,40}(?:трещин|оставл|появля|оста[её]т)"
    r"|трещин\w*.{0,40}шаг"
    r"|пол\w*.{0,40}помн\w*.{0,20}шаг"
    r"|циферблат\w*.{0,40}шаг"
    r"|шаг\w*.{0,40}(?:циферблат|час)"
    r"|час\w*.{0,40}шаг\w*"
    r")"
)
_NII67_VAGUE_DISAPPEAR_RE = re.compile(
    r"(?i)(?:"
    r"слива(?:ет|ют|ется|ются|ясь)\w*"
    r"|раствор(?:яет|яют|яется|яются|яясь)\w*"
    r"|стира(?:ет|ют|ется|ются|я)\w*"
    r"|исчеза(?:ет|ют)\w*"
    r"|из\s+отражен\w*"
    r"|стира\w*.{0,40}отраж"
    r"|исчеза\w*.{0,40}отраж"
    r"|затуха\w*"
    r"|бледне\w*.{0,24}исчеза"
    r")"
)

_NII67_FLOOR_TILE_TRAP_RE = re.compile(
    r"(?i)(?:"
    r"плитк\w*.{0,48}(?:разъезж|смыка|вста\w*\s+вертикал|зажима|раскрыв|складыва)"
    r"|плитка\s+под\s+.{0,40}раскрыв"
    r"|(?:соседн\w*\s+)?плитк\w*.{0,40}(?:вста\w*|стенк)"
    r"|(?<![\wА-Яа-яЁё])пол(?:ом|у|а|е|ы)?(?![\wА-Яа-яЁё]).{0,48}(?:складыва|запечатыва|смыка|разъезж|раскрыв)"
    r"|керамическ\w*\s+кокон"
    r"|фарфоров\w*\s+(?:кокон|плит)"
    r"|кокон\w*.{0,36}(?:плит|пол|керами|фарфор)"
    r"|кафел\w*.{0,48}(?:разъезж|смыка|раскрыв|зажима|складыва)"
    r"|из\s+пола.{0,24}(?:запечат|смыка|кокон|подним)"
    r"|провалива\w*.{0,24}в\s+пол"
    r"|глина\s+запечат\w*.{0,24}(?:в\s+)?пол"
    r")"
)
_NII67_FLOOR_TILE_CENTER_RE = re.compile(
    r"(?i)(?:"
    r"плитк\w*"
    r"|кафел\w*"
    r"|керамическ\w*\s+(?:зал|пол|кокон|панел)"
    r"|фарфоров\w*\s+(?:плит|кокон|зал)"
    r"|(?<![\wА-Яа-яЁё])пол(?:ом|у|а|е|ы)?(?![\wА-Яа-яЁё])\s+(?:складыва|запечат|смыка|разъезж|раскрыв)"
    r")"
)

_NII67_DOOR_HANDLE_FACE_RE = re.compile(
    r"(?i)(?:"
    r"ручк\w*.{0,40}(?:лиц\w*|вместо\s+лица)"
    r"|дверн\w*\s*ручк\w*.{0,40}лиц\w*"
    r"|вместо\s+лица.{0,40}ручк\w*"
    r")"
)

_NII67_BORING_INTERIOR_RE = re.compile(
    r"(?i)(?:"
    r"(?<![\wА-Яа-я])(?:зал|кабинет|столовая|музей|комната|бассейн|оранжерея|теплица|"
    r"примерочн\w*|кухн\w*|класс|спортзал|буфет|гардероб|гостиная|детская|"
    r"читальн\w*|типограф\w*|радиоузел|аптека|мастерск\w*|приёмн\w*|приемн\w*)"
    r"(?![\wА-Яа-я])"
    r")"
)

# Гуманоидный дефолт: 4/6 рук + лицо/грудь/череп как у человека.
_NII67_HUMANOID_CREATURE_RE = re.compile(
    r"(?i)(?:"
    r"(?:четыр(?:е|ёх|ех)|шесть|4|6)\s*рук\w*.{0,80}(?:лиц\w*|груд\w*|череп\w*)"
    r"|(?:лиц\w*|груд\w*|череп\w*).{0,80}(?:четыр(?:е|ёх|ех)|шесть|4|6)\s*рук\w*"
    r"|рук\w*.{0,40}(?:на\s+)?груд\w*"
    r"|паст\w*\s+на\s+груд\w*"
    r"|лиц\w*.{0,24}(?:внутри|внутренн\w*)\s*(?:череп|груд)"
    r"|демон\w*.{0,40}(?:четыр|шесть)\s*рук"
    r"|ангел\w*.{0,40}(?:четыр|шесть)\s*рук"
    r"|человекоподоб\w*"
    r"|гуманоид\w*"
    r")"
)

# Неснимаемое: колонны/столбы/мебель из пара/дыма/тумана.
_NII67_STEAM_PILLAR_RE = re.compile(
    r"(?i)(?:"
    r"колонн\w*.{0,24}(?:пар\w*|дым\w*|туман\w*)"
    r"|(?:пар\w*|дым\w*|туман\w*).{0,24}колонн\w*"
    r"|столб\w*.{0,24}(?:пар\w*|дым\w*|туман\w*)"
    r"|(?:пар\w*|дым\w*|туман\w*).{0,24}столб\w*"
    r"|облак\w*[-\s]*столб\w*"
    r"|столб\w*[-\s]*(?:из\s+)?(?:льд\w*\s+и\s+)?пар\w*"
    r"|мебел\w*.{0,24}(?:пар\w*|дым\w*|туман\w*)"
    r"|(?:пар\w*|дым\w*|туман\w*).{0,24}мебел\w*"
    r")"
)

# Копипаст-рецепт собор/камера/коридор+витрина как концепт места (голова двери).
_NII67_COPYPASTE_PLACE_RE = re.compile(
    r"(?i)(?:"
    r"(?<![\wА-Яа-я])(?:собор|камера|коридор|музей|витрина|улица\s+с\s+фасад)"
    r"(?![\wА-Яа-я])"
    r")"
)

# Выдуманные «материалы» — брак (место может быть космос/горизонт; вещество — нет).
_NII67_NONSENSE_MATERIAL_RE = re.compile(
    r"(?i)(?:"
    r"вакуумн\w*\s+стекл\w*"
    r"|кож\w*\s+горизонт\w*"
    r"|застывш\w*\s+свет\w*"
    r"|сжат\w*\s+свет\w*"
    r"|застывш\w*\s+фотон\w*"
    r"|жидк\w*\s+свет\w*"
    r"|кров\w*\s+света"
    r"|застывш\w*\s+эфир\w*"
    r"|лавов\w*\s+стекл\w*"
    r"|звёздн\w*\s+пепел\w*"
    r"|звездн\w*\s+пепел\w*"
    r"|ангельск\w*\s+кост\w*"
    r"|демонск\w*\s+кост\w*"
    r"|демонск\w*\s+чешу\w*"
    r"|кристалл\w*\s+корон\w*"
    r")"
)

_NII67_NONSENSE_PLACE_RE = re.compile(
    r"(?i)(?:"
    r"яма\s+глаз"
    r"|глаз\w*\s+в\s+песке"
    r"|тихая\s+лента"
    r"|лента\s+застывш\w*\s+стекл"
    r"|потолок\s+из\s+мышц"
    r"|потолок\w*\s+из\s+сухожил"
    r"|мышц\w*\s+и\s+сухожил"
    r"|костян\w*\s+р[её]бр\w*"
    r"|р[её]бр\w*\s+(?:как\s+)?(?:мост|свод|арк|декор|над\s+лент)"
    r"|между\s+костян\w*\s+р[её]бр"
    r"|пустын\w*\s+зуб"
    r"|лес\w*\s+жив\w*\s+плот"
    r"|жив\w*\s+планет\w*\s+ткан"
    r"|ткань\s+жив\w*\s+планет"
    r"|болот\w*\s+из\s+печен"
    r"|болот\w*\s+из\s+желч"
    r"|тень-масса"
    r"|живая\s+тень-масса"
    r"|смоляная\s+волна"
    r"|дюн\w*\s+из\s+эмал"
    r"|каньон\w*\s+из\s+р[её]бер"
    r"|р[её]бер\w*\s+левиафан"
    r")"
)

_NII67_MAGIC_ENGULF_RE = re.compile(
    r"(?i)(?:"
    # магма/лава поднимается вокруг / с выступа / и затягивает
    r"(?:магм|лав)\w*.{0,48}поднима\w*.{0,32}(?:вокруг|с\s+(?:выступ|скал|остров|террас)|и\s+затягив)"
    r"|(?:магм|лав)\w*.{0,48}затягива\w*.{0,40}(?:топит|тонет|утопа)"
    r"|поднима\w*.{0,24}вокруг.{0,40}(?:магм|лав|смол|ртут|кислот)"
    r"|затягива\w*\s+его\s+и\s+топит"
    r"|(?:смол|ртут|кислот)\w*.{0,40}поднима\w*.{0,32}вокруг"
    r"|(?:магм|лав|смол|ртут)\w*.{0,32}(?:внезапно\s+)?(?:появля|телепорт|возник)\w*"
    r")"
)

# v98-nii67-hg-ban: ртуть / mercury как материал мира или агент смерти — брак.
# Физика: плотность ~13.5 г/см³ (человек плавает); t_пл ≈ −38.83°C (жидкая при комнатной).
_NII67_MERCURY_BAN_RE = re.compile(
    r"(?:"
    r"ртут"
    r"|mercury"
    r"|ртутн\w*\s+(?:океан|мор|волн|озёр|луж|глуб|поверхност)"
    r"|океан\w*\s+ртут"
    r"|жидк\w*\s+ртут"
    r"|ртут\w*.{0,32}(?:замор[оа]ж|тонет|тон\w*|ид[её]т\w*\s+по|ходит\w*\s+по|провал)"
    r"|(?:замор[оа]ж\w*).{0,32}ртут"
    r")",
    re.I | re.U,
)

_NII67_CLEAR_DEATH_RE = re.compile(
    r"(?i)(?:"
    r"сжига|сгора\w*|обгора\w*|обжига|обугл\w*|дотла"
    r"|протуберан\w*.{0,40}(?:сжига|обжига|поглощ)"
    r"|(?:стру[яи]|фонтан)\w*.{0,40}(?:сжига|обжига|накрыва|попада)"
    r"|(?:сопл|жерл|вентил)\w*.{0,48}(?:сжига|обжига|бь[её]т|удар)"
    r"|пада\w*.{0,48}(?:в\s+(?:лав|магм|пропасть|бездну|воронк|чёрн|дыр|ртут|кислот)|вниз)"
    r"|провалива\w*.{0,40}(?:в\s+)?(?:лав|магм|пропасть|бездну|воронк)"
    r"|(?:террас|выступ|мост|опор|камен)\w*.{0,40}(?:треска|обруш|рушит).{0,40}(?:пада|провал)"
    r"|обвал\w*.{0,30}(?:пада|провал)"
    r"|раздавл\w*|расплющ\w*|смина\w*|перемалыва\w*"
    r"|сжима\w*.{0,30}(?:насмерть|до\s+смерти|хруст)"
    r"|зажима\w*.{0,40}(?:раздавл|смина|насмерть|убива|погиб)"
    r"|смыка\w*.{0,40}(?:раздавл|смина|зажима\w*.{0,20}раздавл)"
    r"|заморажива\w*|обледен\w*|выморажива\w*|льдом\s+насмерть"
    r"|вакуум\w*.{0,48}(?:без\s+воздуха|разрыва|задыха|раздува|декомпресс)"
    r"|без\s+воздуха.{0,40}(?:задыха|разрыва|гибн|погиб)"
    r"|декомпресс\w*|люк\w*.{0,40}вакуум"
    r"|(?:чёрн\w*\s+дыр|горизонт\w*\s+событий|воронк\w*).{0,48}(?:затягива|поглощ|провалива)"
    r"|кислот\w*.{0,40}(?:разъеда|раствор|попада|залива\w*.{0,20}разъед|брызг)"
    r"|разъеда\w*.{0,30}кислот"
    r"|(?:пада\w*|провалива\w*|фонтан\w*|жерл\w*).{0,48}(?:утопа|тонет|топит\w*.{0,20}(?:лав|магм|ртут))"
    r"|(?:утопа|тонет)\w*.{0,40}(?:в\s+)?(?:лав|магм|ртут).{0,20}(?:внизу|ниже|под)"
    r")"
)

_NII67_VAGUE_SEAL_ONLY_RE = re.compile(
    r"(?i)(?:"
    r"запечат\w*"
    r"|накрыва\w*"
    r"|обматыва\w*"
    r"|тень-масса"
    r"|смоляная\s+волна"
    r")"
)

_NII67_EXPERIMENT_RE = re.compile(
    r"(?i)эксперимент\s+номер\s+([а-яё\-]+(?:\s+[а-яё\-]+)?)"
)


def _nii67_place_token(place: str) -> str:
    from app.services.nii67_card import _place_token

    return _place_token(place)


def _nii67_extract_experiment(text: str) -> str:
    m = _NII67_EXPERIMENT_RE.search(text or "")
    return (m.group(1).strip().casefold() if m else "")


# Ключевые мотивы МЕСТА по полному тексту (нормализованные ярлыки партии).
_NII67_PLACE_MOTIF_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (key, re.compile(pat, re.I | re.U))
    for key, pat in (
        ("flesh-forest", r"лес\w*\s+жив\w*\s+плот|жив\w*\s+плот\w*"),
        ("asteroid", r"астероидн"),
        ("concrete-plateau", r"бетонн\w*\s+плато"),
        ("mercury-ocean", r"ртутн\w*\s+(?:океан|мор)"),
        ("lava-ocean", r"океан\w*\s+лав|лав\w*\s+океан"),
        ("mirror-void", r"зеркальн\w*\s+пустот|хромированн\w*\s+глад"),
        ("mech-throat", r"механическ\w*\s+горл|горл\w*\s+из\s+шестер"),
        ("photosphere", r"фотосфер"),
        ("tooth-desert", r"пустын\w*\s+зуб"),
        ("accretion", r"аккрецион"),
        ("rib-canyon", r"каньон\w*\s+из\s+р[её]бер|р[её]бер\w*\s+левиафан"),
        ("gravity-slope", r"гравитац\w*\s+теч|склон\w*\s+где\s+гравитац"),
        ("tar-marsh", r"болот\w*\s+ч[её]рн\w*\s+смол|тень-болот"),
        ("obsidian-cube", r"куб-лабиринт|вращающ\w*\s+куб"),
        ("volcano-maw", r"жерло\w*\s+вулкан"),
        ("needle-field", r"каменн\w*\s+игл|рой\s+каменн\w*\s+игл"),
        ("planet-flesh", r"жива\w*\s+планетарн\w*\s+ткан|ткань\s+жив\w*\s+планет"),
        ("hell-forge", r"кузниц\w*\s+на\s+открыт"),
        ("blade-spiral", r"спираль\w*\s+геометрическ\w*\s+лезв"),
        ("charred-forest", r"лес\w*\s+обуглен"),
        ("eye-pit", r"яма\s+глаз"),
        ("slag-quarry", r"шлаков\w*\s+карьер"),
        ("planet-crack", r"трещин\w*\s+в\s+коре\s+планет"),
        ("jaw-grotto", r"грот\w*\s+из\s+сросш\w*\s+челюст"),
        ("blood-field", r"застывш\w*\s+кров"),
        ("mercury-gorge", r"река\s+ртут"),
        ("maw-world", r"н[её]бо\s+пасти|пасти-мира"),
        ("parasite-grid", r"реш[её]тк\w*\s+геометрическ"),
        ("slag-yard", r"двор\w*\s+из\s+шлака"),
        ("ice-ridge", r"хрустально-ледян|ледян\w*\s+шельф|гряда\w*\s+в\s+вакуум"),
        ("salt-blocks", r"парящ\w*\s+глыб\w*\s+соли"),
        ("ice-rings", r"кольца\s+льда\s+вокруг"),
        ("white-dwarf", r"белого\s+карлика"),
        ("comet-belt", r"пояс\w*\s+кометн"),
        ("neutron-rim", r"нейтронн\w*\s+звезд"),
        ("plasma-plain", r"облако\s+плазмы\s+как\s+равнин"),
        ("station-hulk", r"орбитальн\w*\s+обломок"),
        ("ash-plain", r"пепельн\w*\s+равнин"),
    )
)

# Мотивы существа/агента по полному тексту (не только строки «Существо:»).
_NII67_CREATURE_MOTIF_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (key, re.compile(pat, re.I | re.U))
    for key, pat in (
        ("eye-swarm", r"рой\s+глаз|глаз\w*\s+без\s+тела|масса\s+глаз"),
        ("grab-threads", r"(?:мясн\w*|смолян\w*|плот\w*|чёрн\w*)\s+нит\w*|нит\w*.{0,28}(?:обвив|хват|тян|сд[её]рг)"),
        ("tar-mass", r"смолян\w*\s+масс|жидк\w*\s+смолян"),
        ("geo-parasite", r"геометрическ\w*\s+паразит|стальн\w*\s+куб.{0,20}врез"),
        ("living-shadow", r"жива\w*\s+тень|тень-масс"),
        ("chrome-worm", r"хромированн\w*\s+черв"),
        ("tooth-wave", r"масса\s+зуб\w*|дюн\w*\s+эмал\w*.{0,24}хребт"),
        ("blade-claw", r"реш[её]тк\w*\s+из\s+лезв|лезв\w*.{0,20}клешн"),
        ("ice-needle-cloud", r"облако\s+инея.{0,40}игл|ине[яй].{0,24}металлическ\w*\s+игл"),
        ("reflection-pull", r"отражен\w*\s+без\s+тела|вытягива\w*.{0,24}(?:стекл|рам)"),
        ("gear-jaw", r"шестерн\w*\s+горл|челюсть\s+машин"),
        ("plasma-ribbon", r"лента\s+плазмы"),
        ("mercury-claw", r"ртутн\w*\s+волн\w*.{0,24}клешн"),
        ("bubble-eyes", r"пузыр\w*-глаз|глаз\w*-пузыр"),
    )
)

# Класс механики, видимый в тексте (кольца-тиски нельзя дважды в партии).
_NII67_MECHANIC_MOTIF_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (key, re.compile(pat, re.I | re.U))
    for key, pat in (
        (
            "ring-clamp",
            r"(?:стальн\w*|металлическ\w*|железн\w*|медн\w*)\s+кольц\w*"
            r"|кольц\w*.{0,48}(?:сжима|запечат|зажима|смыка|катят|раскрыв|прижим|обод)"
            r"|(?:сжима|запечат|зажима|прижим)\w*.{0,32}кольц",
        ),
        (
            "wall-clamp",
            r"стен\w*.{0,40}(?:смыка|зажима|сжима)|(?:смыка|зажима)\w*.{0,40}стен",
        ),
        (
            "ceiling-pull",
            r"потолк\w*.{0,40}(?:тян|запечат|хвата)|(?:тян|запечат)\w*.{0,40}потолк",
        ),
        (
            "liquid-seal",
            r"(?:смол|ртут|чернил|лав|магм)\w*.{0,40}(?:залив|запечат|обтека|тверде)"
            r"|(?:залив|запечат)\w*.{0,40}(?:смол|ртут|чернил|жидк)",
        ),
        (
            "fabric-wrap",
            r"(?:ткан|штор|бархат|нит)\w*.{0,40}(?:обвив|затягив|душ)"
            r"|(?:обвив|затягив)\w*.{0,40}(?:ткан|штор|нит)",
        ),
        (
            "frame-pull",
            r"(?:зеркал|рам|портрет|карт)\w*.{0,40}(?:вытягив|затягив|втягив)"
            r"|(?:вытягив|втягив)\w*.{0,40}(?:зеркал|рам)",
        ),
    )
)


def _nii67_extract_place_motifs(text: str) -> set[str]:
    """Ярлыки ключевых мест/миров по полному тексту сценария."""
    blob = text or ""
    return {key for key, rx in _NII67_PLACE_MOTIF_RES if rx.search(blob)}


def _nii67_extract_mechanic_motifs(text: str) -> set[str]:
    """Ярлыки класса механики (кольца-тиски, стены, жидкость…) по тексту."""
    blob = text or ""
    return {key for key, rx in _NII67_MECHANIC_MOTIF_RES if rx.search(blob)}


def _nii67_extract_creature_hints(text: str) -> set[str]:
    """Метки существа/агента: строки «Существо …» + мотивы по полному тексту."""
    out: set[str] = set()
    for m in re.finditer(
        r"(?im)^\s*существо[:\s]+(.+?)\s*$",
        text or "",
    ):
        chunk = m.group(1).strip().casefold()
        if chunk and "нет" not in chunk[:12]:
            out.add(chunk[:80])
    for key, rx in _NII67_CREATURE_MOTIF_RES:
        if rx.search(text or ""):
            out.add(key)
    return out


_NII67_DOOR_PLACE_RE = re.compile(
    r"(?im)^\s*дверь\s*[12]\s*[.:]\s*(.+)$"
)
_NII67_FRAMES_HEADER_RE = re.compile(r"(?im)^\s*кадры\.?\s*$")
_NII67_TIMED_LINE_RE = re.compile(
    r"(?m)^\s*\d+\s*[–—\-]\s*\d+\s*(?:сек(?:унд\w*)?)?\s*\|[^\n]*\|[^\n]+"
)


def _nii67_extract_door_places(scenario: str) -> list[str]:
    """Места из строк «Дверь 1/2. …» — голова до первой точки-описания."""
    out: list[str] = []
    for m in _NII67_DOOR_PLACE_RE.finditer(scenario or ""):
        line = m.group(1).strip()
        # «Шахта с полом из …. Существо…» → до первого предложения-факта слишком длинно;
        # берём до первой точки, но не короче 3 символов.
        head = line.split(".")[0].strip()
        if len(head) >= 3:
            out.append(head)
    return out


def _nii67_scenario_structure_ok(scenario: str) -> str | None:
    """None = ok. Иначе причина: нет блока Кадры. или мало тайм-кодовых строк VO."""
    text = scenario or ""
    if not _NII67_FRAMES_HEADER_RE.search(text):
        return "нет блока «Кадры.»"
    timed = _NII67_TIMED_LINE_RE.findall(text)
    if len(timed) < 4:
        return f"мало тайм-кодовых строк закадра в Кадрах ({len(timed)}<4)"
    return None



_NII67_BLACKHOLE_CUE_RE = re.compile(
    r"(?i)(?:"
    r"чёрн\w*\s+дыр"
    r"|черн\w*\s+дыр"
    r"|аккрецион\w*"
    r"|горизонт\w*\s+событий"
    r"|воронк\w*\s+(?:горизонт|дыр|событ)"
    r"|event\s*horizon"
    r")"
)

_NII67_HELL_GRAFT_RE = re.compile(
    r"(?i)(?:"
    r"лав\w*"
    r"|магм\w*"
    r"|шлак\w*"
    r"|раскалённ\w*\s+(?:стал|мост|желез|камен)"
    r"|раскаленн\w*\s+(?:стал|мост|желез|камен)"
    r"|мост\w*\s+из\s+раскалён"
    r"|мост\w*\s+из\s+раскален"
    r"|мост\w*\s+из\s+(?:лав|магм|шлак)"
    r"|адск\w*"
    r"|море\s+огня"
    r")"
)

_NII67_CREATURE_NONE_RE = re.compile(
    r"(?i)существ\w*\s+нет"
)


def _creature_hint_is_env_only_safe(hint: str) -> bool:
    h = (hint or "").casefold()
    return h.startswith("существа нет") or "существа нет —" in h[:48]


def _nii67_door_blocks(scenario: str) -> list[tuple[str, str]]:
    text = scenario or ""
    matches = list(
        re.finditer(
            r"(?is)(Дверь\s+([12])\.(.*?))(?=Дверь\s+[12]\.|Финал:|Кадры\.|$)",
            text,
        )
    )
    out: list[tuple[str, str]] = []
    for m in matches:
        out.append((m.group(2), m.group(1)))
    return out


def _nii67_door_has_creature(door_block: str) -> bool:
    if _NII67_CREATURE_NONE_RE.search(door_block or ""):
        return False
    if re.search(
        r"(?i)(?:существ\w+|черв\w+|глыб\w+-|лента\s+плазм|шестерн\w*\s+горл|"
        r"сгусток|иглобрюх|краб|пастью|без\s+лица|негуманоид)",
        door_block or "",
    ):
        return True
    m = re.search(r"(?i)существ\w*\s*[:\-–—]\s*([^\n.]+)", door_block or "")
    if m and "нет" not in m.group(1).casefold()[:12]:
        return True
    return False


def _nii67_blackhole_hell_graft_reason(scenario: str) -> str | None:
    for num, block in _nii67_door_blocks(scenario):
        if _NII67_BLACKHOLE_CUE_RE.search(block) and _NII67_HELL_GRAFT_RE.search(block):
            return (
                f"дверь {num}: прививка ада к чёрной дыре/диску "
                "(лава/магма/шлак/раскалённый мост) — нужна космическая физика"
            )
    blob = scenario or ""
    for m in _NII67_BLACKHOLE_CUE_RE.finditer(blob):
        window = blob[max(0, m.start() - 80) : m.end() + 120]
        if _NII67_HELL_GRAFT_RE.search(window):
            return (
                "чёрная дыра/аккреционный диск рядом с лавой/магмой/шлаком/"
                "раскалённым мостом — запрещённый троп"
            )
    return None




def _nii67_variant_reject_reason(
    scenario: str,
    voiceover: str,
    *,
    used_experiments: set[str],
    used_places: set[str],
    used_creatures: set[str],
    seed_places: list[str],
    floor_trap_hits: int = 0,
    mundane_batch_hits: int = 0,
    used_place_motifs: set[str] | None = None,
    used_mechanic_motifs: set[str] | None = None,
    used_world_kinds: set[str] | None = None,
    seed_world_kinds: set[str] | None = None,
) -> str | None:
    """None = ok. Иначе короткая причина для retry/лога."""
    from app.services.nii67_card import place_kinds, places_collide, normalize_place_head

    struct = _nii67_scenario_structure_ok(scenario)
    if struct:
        return struct

    blob = f"{scenario}\n{voiceover}"
    low = blob.casefold()

    if _NII67_DOOR_HANDLE_FACE_RE.search(blob):
        return "дверная ручка вместо лица"
    if _NII67_MERCURY_BAN_RE.search(blob):
        return (
            "ртуть/mercury как материал мира или агент смерти (ртутный океан / "
            "тонуть в ртути / ртуть замораживает) — запрещено: плотность ~13.5, "
            "заморозка только от холодной среды (лёд/LN2), не от ртути"
        )
    if _NII67_HUMANOID_CREATURE_RE.search(blob):
        return (
            "гуманоидное существо (четыре/шесть рук + лицо/грудь/череп) — "
            "нужна иная форма или угроза среды"
        )
    if _NII67_STEAM_PILLAR_RE.search(blob):
        return "неснимаемый материал (колонны/столбы/мебель из пара/дыма/тумана)"
    # Оба мира — скучный интерьер без шока: брак. Один инженерный шок — ок.
    door_places = _nii67_extract_door_places(scenario)
    boring_hits = [
        p for p in door_places
        if _NII67_BORING_INTERIOR_RE.search((p or "").split(",")[0])
    ]
    if len(boring_hits) >= 2:
        return (
            "оба мира — скучный интерьер (зал/кабинет/музей/столовая/комната) — "
            "смешай оси: Солнце/ад/космос/чёрная дыра/зеркало/лёд/криоген/лава/кислота"
        )
    # Копипаст собор/камера/коридор как голова места — брак, если обе двери такие
    paste_hits = [
        p for p in door_places
        if _NII67_COPYPASTE_PLACE_RE.search((p or "").split(",")[0])
    ]
    if len(paste_hits) >= 2:
        return "копипаст мест (собор/камера/коридор/музей/витрина/улица с фасадами) на обе двери"
    if _NII67_BANNED_PLACE_RE.search(blob):
        return "запрещённая локация (архив/прачечная/шахта/туннель)"
    if _NII67_LAZY_TROPE_RE.search(blob):
        return "стоковый троп (маятник/тени на стене)"
    if _NII67_STEP_CENTRIC_RE.search(blob):
        return "шаг-как-механика (каждый шаг/следы/трещины от шагов/часы-шаги)"
    if _NII67_VAGUE_DISAPPEAR_RE.search(blob):
        return "размытое исчезновение/стирание/слияние (нужен видимый глагол)"
    if _NII67_NONSENSE_MATERIAL_RE.search(blob):
        return (
            "выдуманный материал (вакуумное стекло / кожа горизонта / застывший свет / "
            "звёздный пепел-мебель / ангельская кость-купол) — нужны реальные вещества"
        )
    if _NII67_NONSENSE_PLACE_RE.search(blob):
        return (
            "сюр-коллаж места (яма глаз / тихая лента / потолок из мышц / "
            "костяные рёбра-архитектура / пустыня зубов / тень-масса) — нужен читаемый мир"
        )
    if _NII67_MAGIC_ENGULF_RE.search(blob):
        return (
            "магическое поднятие/затягивание среды без источника "
            "(магма/лава «поднимается вокруг» / «затягивает и топит» без падения вниз "
            "или видимого жерла/фонтана) — нужна физика места"
        )
    graft = _nii67_blackhole_hell_graft_reason(scenario)
    if graft:
        return graft

    # Смертельная ветка: обязателен ясный физический механизм смерти
    fatal_m = re.search(
        r"(?im)^Смертельная\s+дверь:\s*([12])",
        scenario or "",
    )
    if fatal_m:
        fatal_door = fatal_m.group(1)
        door_block = ""
        dm = re.search(
            rf"(?is)Дверь\s+{fatal_door}\.(.*?)(?=Дверь\s+[12]\.|Финал:|Кадры\.|$)",
            scenario or "",
        )
        if dm:
            door_block = dm.group(1)
        else:
            door_block = scenario or ""
        if not _NII67_CLEAR_DEATH_RE.search(door_block):
            return (
                "смертельная дверь без ясного физического механизма "
                "(сжечь/упасть в лаву/фонтан из жерла/раздавить/заморозить/"
                "вакуум/дыра/кислота попадает) — не «магма поднялась и топит»"
            )
        # если есть только запечатка/накрытие без clear-death — уже отсечено выше
    if _NII67_FLOOR_TILE_TRAP_RE.search(blob):
        return (
            "пол/плитка-ловушка (плитки разъезжаются/смыкаются, пол складывается, "
            "керамический кокон из пола) — запрещено; пол только материал"
        )
    # ≥2 вариантов партии с центром на пол/плитку — брак даже без жёсткого trap-паттерна
    if floor_trap_hits >= 1 and _NII67_FLOOR_TILE_CENTER_RE.search(blob):
        return (
            "повтор пола/плитки как центра угрозы в партии "
            f"(уже {floor_trap_hits} вариант(ов) с плиткой/полом)"
        )
    if mundane_batch_hits >= 1:
        door_places_m = _nii67_extract_door_places(scenario)
        mund_now = [
            p for p in door_places_m
            if _NII67_BORING_INTERIOR_RE.search((p or "").split(",")[0])
        ]
        if mund_now:
            return (
                "повтор скучных интерьеров в партии "
                f"(уже {mundane_batch_hits} вариант(ов) с зал/кабинет/музей) — нужна другая ось мира"
            )

    exp = _nii67_extract_experiment(blob)
    if exp and exp in used_experiments:
        return f"повтор номера эксперимента: {exp}"

    text_places = _nii67_extract_door_places(scenario)
    check_places = list(seed_places) + text_places

    for seed in check_places:
        tok = _nii67_place_token(seed)
        if tok and tok in used_places:
            return f"повтор вида места: {tok} ({normalize_place_head(seed) or seed})"

    # пересечение видов с уже занятыми токенами (g:/s:) и сырыми головами
    for place in check_places:
        head = normalize_place_head(place)
        if not head:
            continue
        for prev in used_places:
            if not prev:
                continue
            # prev — канонический токен (g:underground) или старая голова
            if prev.startswith(("g:", "s:", "h:")):
                if prev in place_kinds(place) or prev == _nii67_place_token(place):
                    return f"повтор вида места: {prev} (~{head})"
            else:
                # полные головы: равенство/подстрока; широкий g: уже в used_places как g:*
                if len(prev) >= 6 and (prev == head or prev in head or head in prev):
                    return f"повтор места/синонима: {prev} (~{head})"
                pa = prev.split()[0] if prev.split() else prev
                pb = head.split()[0] if head.split() else head
                if (
                    len(pa) >= 5
                    and len(pb) >= 5
                    and (pa == pb or pa.startswith(pb[:5]) or pb.startswith(pa[:5]))
                ):
                    return f"повтор места/синонима: {prev} (~{head})"

    # если модель явно повторила чужую ПОЛНУЮ голову места в тексте
    import re as _re
    for tok in used_places:
        if not tok or tok.startswith(("g:", "s:", "h:")):
            continue
        if len(tok) < 6:
            continue
        if tok in low:
            return f"повтор места в тексте: {tok}"

    creatures = _nii67_extract_creature_hints(f"{scenario}\n{voiceover}")
    for c in creatures:
        if c in used_creatures:
            return f"повтор существа/мотива: {c}"

    # Мотивы места по полному тексту (лес живой плоти, астероидн, ртутн океан…)
    used_pm = used_place_motifs if used_place_motifs is not None else set()
    place_motifs = _nii67_extract_place_motifs(f"{scenario}\n{voiceover}")
    for seed in seed_places:
        place_motifs |= _nii67_extract_place_motifs(seed or "")
    for m in place_motifs:
        if m in used_pm:
            return f"повтор мотива места в партии: {m}"

    # Механика: стальные кольца-тиски и др. классы — не чаще одного раза
    used_mm = used_mechanic_motifs if used_mechanic_motifs is not None else set()
    mech_motifs = _nii67_extract_mechanic_motifs(f"{scenario}\n{voiceover}")
    for m in mech_motifs:
        if m in used_mm:
            return f"повтор класса механики в партии: {m}"

    # Soft: world_kind group from seeds (g:cosmic / g:organic …) unique in batch
    used_wk = used_world_kinds if used_world_kinds is not None else set()
    seed_wk = seed_world_kinds if seed_world_kinds is not None else set()
    overlap_wk = used_wk & seed_wk
    if overlap_wk:
        return f"повтор оси мира (world_kind) в партии: {', '.join(sorted(overlap_wk))}"

    return None


_SCRIPT_NII67_HINT = (
    "\n\n# НИИ 67 — ОБА БЛОКА ОБЯЗАТЕЛЬНЫ\n"
    "Сначала <<<SCENARIO>>>…<<<END_SCENARIO>>> (что видно, без камеры и зерна; "
    "внутри обязателен блок «Кадры.» со строками «сек | видно | закадр»; каждый бит — конкретное видимое действие, без «каждый шаг» и без исчезает/стирается/сливается как главного бита), "
    "потом <<<VOICEOVER>>>…<<<END>>> (только озвучка). "
    "Не JSON apply-ops. Без сценария или без «Кадры.» ответ — брак.\n"
)


def _voiceover_from_script_reply(reply: str) -> str:
    from app.services import db_apply
    from app.services.voiceover_sanitize import extract_voiceover_block

    voiceover_text = ""
    data = db_apply.extract_apply_ops_json(reply or "")
    if isinstance(data, dict):
        for op in data.get("ops") or []:
            if not isinstance(op, dict):
                continue
            fields = op.get("fields") or {}
            if not isinstance(fields, dict):
                continue
            for key in (
                "закадровый_текст",
                "script_text",
                "сценарий",
                "voiceover",
                "общий_план",
            ):
                if key == "общий_план":
                    continue
                val = fields.get(key)
                if isinstance(val, str) and len(val.strip()) >= 80:
                    voiceover_text = val.strip()
                    break
            if voiceover_text:
                break
    if not voiceover_text:
        voiceover_text = (extract_voiceover_block(reply or "") or "").strip()
    if not voiceover_text:
        raw = (reply or "").strip()
        if (
            raw
            and '"ops"' not in raw[:40]
            and "<<<SCENARIO>>>" not in raw
            and len(raw) >= 200
        ):
            voiceover_text = raw
    return voiceover_text


async def _ask_script_files(
    project: Project,
    chat_msg: str,
    attach_files: list[Path],
    *,
    project_id: int | None,
) -> str:
    async def _gpt() -> str:
        return await xgf.telegram_style_ask_with_files(
            chat_msg,
            attach_files,
            timeout=1800.0,
            project_id=project_id or project.id,
        )

    return await xgf.run_under_xlsx_lock(project.id, "script", _gpt)


async def _run_nii67_script_variants(
    project: Project,
    *,
    project_id: int | None,
    specs: list[dict],
    prompt_file: Path,
    chat_base: str,
    source_voiceover: Path | None,
    proj_xlsx: Path,
    tmp_dir: Path,
    ts: str,
) -> tuple[XlsxRoundtripResult, str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.nii67_card import (
        apply_variant,
        format_nii67_card,
        parse_nii67_card,
        strip_nii67_report_boilerplate,
    )
    from app.services.voiceover_sanitize import extract_scenario_or_preamble

    _ = source_voiceover

    card = parse_nii67_card(project.general_plan or "")
    outputs: list[dict[str, str]] = []
    last_reply = ""
    voiceover_text = ""
    out_root = proj_xlsx.parent / "nii67_scripts"
    out_root.mkdir(parents=True, exist_ok=True)
    n = len(specs)
    used_places_hint: list[str] = []
    used_experiments: set[str] = set()
    used_place_tokens: set[str] = set()
    used_creatures: set[str] = set()
    used_place_motifs: set[str] = set()
    used_mechanic_motifs: set[str] = set()
    used_world_kinds: set[str] = set()
    floor_trap_hits = 0
    mundane_batch_hits = 0
    creature_doors_ok = 0
    creatureless_doors = 0
    variants_all_creatureless = 0
    for i, spec in enumerate(specs, start=1):
        filled = format_nii67_card(apply_variant(card, spec))
        creepy = str(spec.get("creepy_door") or "")
        fatal = str(spec.get("fatal_door") or "")
        assignment = (
            "\n\n## Назначение варианта\n"
            f"Номер эксперимента словами: {spec.get('experiment_words') or ''}\n"
            f"Жуткая дверь: {creepy}\n"
            f"Смертельная дверь: {fatal}\n"
            f"Жуткое место: {spec.get('creepy_place') or ''}\n"
            f"Тихое место: {spec.get('quiet_place') or ''}\n"
            f"Класс механики смерти/угрозы: {spec.get('mechanic_key') or ''}\n"
            f"Механика (обязательно соблюсти, НЕ пол/плитка): "
            f"{spec.get('mechanic_hint') or ''}\n"
            f"Материалы мира (не плитка как крючок): {spec.get('material_hint') or ''}\n"
            f"Форма существа/угрозы (НЕ гуманоид с руками+лицом): {spec.get('creature_hint') or ''}\n"
            "РАЗНООБРАЗИЕ: штамп мест/существа UNIQUE в партии — соблюди ТОЧНО эти места и эту форму существа; ЗАПРЕТ сюр-коллажа: яма глаз, тихая лента, потолок из мышц, костяные рёбра-архитектура, тень-масса. Смерть: ясный глагол (сжечь/утопить в лаве/раздавить/заморозить/вакуум/дыра), не одна запечатка. Чередуй оси; среда может убивать без существа.\n"
            "НЕ делай смерть/угрозу через пол/плитку/кокон из пола. "
            "Пол — только материал мира, не ловушка. "
            "Запрет: колонны/столбы из пара; 4/6 рук + лицо/грудь.\n"
        )
        filled = filled.rstrip() + assignment
        card_path = tmp_dir / f"nii67_card_{spec['id']}_{ts}.md"
        card_path.write_text(filled, encoding="utf-8")
        gp = tmp_dir / f"general_plan_{spec['id']}_{ts}.txt"
        gp.write_text(filled, encoding="utf-8")
        attach = [prompt_file, gp, card_path]
        banned = ""
        if used_places_hint:
            motif_ban = ""
            if used_place_motifs or used_creatures or used_mechanic_motifs:
                bits = []
                if used_place_motifs:
                    bits.append("места=" + ",".join(sorted(used_place_motifs)))
                if used_creatures:
                    bits.append("существа=" + ",".join(sorted(used_creatures)))
                if used_mechanic_motifs:
                    bits.append("механики=" + ",".join(sorted(used_mechanic_motifs)))
                motif_ban = " Мотивы партии уже заняты (" + "; ".join(bits) + ")."
            banned = (
                " Уже занято другими вариантами, повторять нельзя: "
                + "; ".join(used_places_hint)
                + "."
                + motif_ban
            )
        same = "Жуткая дверь и смертельная совпадают." if creepy and creepy == fatal else (
            "Смерть в тихом месте. Жуткое место героя отпускает, дверь обратно после него."
        )
        base_hint = (
            f"\n\nЭто вариант {i} из {n}. "
            f"Жуткая дверь: {creepy}. Смертельная дверь: {fatal}. {same} "
            f"Жуткое место: {spec.get('creepy_place') or ''}. "
            f"Тихое место: {spec.get('quiet_place') or ''}. "
            f"Номер эксперимента: {spec.get('experiment_words') or ''}. "
            f"Класс механики: {spec.get('mechanic_key') or ''} — {spec.get('mechanic_hint') or ''}. "
            f"Материалы: {spec.get('material_hint') or ''}. "
            "НЕ делай смерть/угрозу через пол/плитку/кокон из пола. Пол — только материал, не ловушка. Не подмешивай орбиту/космос/вакуум к штампу, если штамп не космический (иначе g:cosmic коллизия). ФИЗИКА СРЕДЫ: запрещено магма/лава/смола «поднимается вокруг» / «затягивает с выступа и топит» без падения вниз или видимого жерла/фонтана/сопла; смерть = тепло/падение вниз/струя из источника/вакуум/механика/химия с видимой причиной; ЗАПРЕТ РТУТИ: нельзя ртутный океан / тонуть в ртути / «ртуть замораживает» (плотность ~13.5 — человек плавает; заморозка только от льда/LN2/криогенного воздуха); "
            "Существо минимум в одном мире, в жутком оно пугает. "
            "У выжившего дверь обратно в свой НИИ. "
            "Закадр — прямая речь героя. Камеру, зерно и чб не пиши. "
            "Название проекта не пиши. Только то, что видно. "
            "Служебные пояснения в текст не копируй. "
            "Запрещено: архив/прачечная/шахта/туннель/каменоломня как локации; стоковые тропы (маятник-тени, тени на стене без людей); акцент на ШАГАХ как механике (каждый шаг / следы / трещины от шагов / часы считают шаги); СМЕРТЬ/УГРОЗА ЧЕРЕЗ ПОЛ/ПЛИТКУ (плитки разъезжаются/смыкаются, пол складывается/запечатывает, керамический/фарфоровый кокон из пола, плитка раскрывается под ногами) — пол только материал; размытое исчезает/стирается/сливается/растворяется как главный бит (особенно «из отражений»); "
            "существа с дверной ручкой (ручкой) вместо лица; "
            "повтор номера эксперимента, вида места (шахта/туннель/подземный "
            "коридор = один вид; оранжерея/теплица = один вид) или существа "
            "из других вариантов. Миры РАЗНЫХ осей в партии (космос/ад-пустошь/зеркало/органика/физика/шоковое инженерное) — НЕ копипаст собор+демон / камера+ангел / коридор+витрина и НЕ оба мира скучный зал/кабинет/музей. Существо НЕ гуманоид с 4/6 руками и лицом/грудью; либо угроза среды. Материалы — снимаемые (лава, обсидиан, металл, стекло, камень, лёд, пепел, кровь, плоть); ЗАПРЕТ: колонны из пара, вакуумное стекло, кожа горизонта, застывший свет. Каждый ключевой бит — яркое видимое действие (хватает, бросает, тянет, запечатывает, вытягивает сквозь зеркало); если зритель не видит сразу ЧТО случилось — перепиши. Обязателен полный сценарий с блоком «Кадры.» и строками "
            "«сек | видно | закадр» как у эталона — без Кадров ответ брак."
            f"{banned}"
        )
        seed_places = [
            str(spec.get("creepy_place") or ""),
            str(spec.get("quiet_place") or ""),
        ]
        # Если модель раньше заняла мотив, совпадающий с сидом — перештампуем места.
        from app.services.nii67_card import (
            place_kinds as _pk_kinds,
            _pick_unique_place_pair as _re_pick_places,
            _pick_unique_creature as _re_pick_creature,
        )
        seed_motifs = set()
        for sp in seed_places:
            seed_motifs |= _nii67_extract_place_motifs(sp)
        seed_wk = set()
        for sp in seed_places:
            seed_wk |= {k for k in _pk_kinds(sp) if k.startswith("g:")}
        if (seed_motifs & used_place_motifs) or (seed_wk & used_world_kinds):
            # COPY: _pick_unique_place_pair мутирует set; не засоряем used до accept
            new_c, new_q = _re_pick_places(
                i,
                card,
                creepy,
                "1" if creepy == "2" else "2",
                set(used_place_tokens) | set(used_world_kinds),
            )
            spec["creepy_place"] = new_c
            spec["quiet_place"] = new_q
            seed_places = [new_c, new_q]
            seed_motifs = set()
            seed_wk = set()
            for sp in seed_places:
                seed_motifs |= _nii67_extract_place_motifs(sp)
                seed_wk |= {k for k in _pk_kinds(sp) if k.startswith("g:")}
            # creature hint тоже, если мотив существа уже занят штампом
            ch = str(spec.get("creature_hint") or "")
            ch_motifs = _nii67_extract_creature_hints(ch)
            if ch_motifs & used_creatures:
                spec["creature_hint"] = _re_pick_creature(i, used_creatures)
            # перезаписать карточку с новыми местами
            filled = format_nii67_card(apply_variant(card, spec))
            assignment = (
                "\n\n## Назначение варианта\n"
                f"Номер эксперимента словами: {spec.get('experiment_words') or ''}\n"
                f"Жуткая дверь: {creepy}\n"
                f"Смертельная дверь: {fatal}\n"
                f"Жуткое место: {spec.get('creepy_place') or ''}\n"
                f"Тихое место: {spec.get('quiet_place') or ''}\n"
                f"Класс механики смерти/угрозы: {spec.get('mechanic_key') or ''}\n"
                f"Механика (обязательно соблюсти, НЕ пол/плитка): "
                f"{spec.get('mechanic_hint') or ''}\n"
                f"Материалы мира (не плитка как крючок): {spec.get('material_hint') or ''}\n"
                f"Форма существа/угрозы (НЕ гуманоид с руками+лицом): {spec.get('creature_hint') or ''}\n"
                "РАЗНООБРАЗИЕ: штамп мест и существа UNIQUE в партии; "
                "не копируй мотивы (лес живой плоти / астероид / рой глаз / стальные кольца) "
                "из других вариантов. Чередуй оси миров.\n"
                "HARD: чёрная дыра/аккреционный диск ≠ лава/магма/шлак/мост из раскалённой стали. "
                "HARD: микс существ — нельзя все двери партии «существ нет»; "
                "≥3 из 10 миров-дверей с негуманоидным существом.\n"
                "НЕ делай смерть/угрозу через пол/плитку/кокон из пола. "
                "Пол — только материал мира, не ловушка. "
                "Запрет: колонны/столбы из пара; 4/6 рук + лицо/грудь.\n"
            )
            filled = filled.rstrip() + assignment
            card_path.write_text(filled, encoding="utf-8")
            gp.write_text(filled, encoding="utf-8")
        last_reply = ""
        vo = ""
        scenario = ""
        reject_reason: str | None = None
        max_attempts = 3  # hard cap for nii67 variant regeneration retries
        for attempt in range(1, max_attempts + 1):
            retry_extra = ""
            if attempt > 1 and reject_reason:
                low_reason = (reject_reason or "").casefold()
                if any(
                    k in low_reason
                    for k in ("места", "мотив", "world_kind", "оси мира", "существ", "механик")
                ):
                    # свежие места/существо под занятые мотивы партии
                    new_c, new_q = _re_pick_places(
                        i,
                        card,
                        creepy,
                        "1" if creepy == "2" else "2",
                        set(used_place_tokens) | set(used_world_kinds),
                    )
                    spec["creepy_place"] = new_c
                    spec["quiet_place"] = new_q
                    seed_places = [new_c, new_q]
                    seed_motifs = set()
                    seed_wk = set()
                    for sp in seed_places:
                        seed_motifs |= _nii67_extract_place_motifs(sp)
                        seed_wk |= {k for k in _pk_kinds(sp) if k.startswith("g:")}
                    ch = str(spec.get("creature_hint") or "")
                    if _nii67_extract_creature_hints(ch) & used_creatures:
                        spec["creature_hint"] = _re_pick_creature(i, set(used_creatures))
                    filled_retry = format_nii67_card(apply_variant(card, spec))
                    assignment_retry = (
                        "\n\n## Назначение варианта\n"
                        f"Номер эксперимента словами: {spec.get('experiment_words') or ''}\n"
                        f"Жуткая дверь: {creepy}\n"
                        f"Смертельная дверь: {fatal}\n"
                        f"Жуткое место: {spec.get('creepy_place') or ''}\n"
                        f"Тихое место: {spec.get('quiet_place') or ''}\n"
                        f"Класс механики смерти/угрозы: {spec.get('mechanic_key') or ''}\n"
                        f"Механика (обязательно соблюсти, НЕ пол/плитка): "
                        f"{spec.get('mechanic_hint') or ''}\n"
                        f"Материалы мира (не плитка как крючок): {spec.get('material_hint') or ''}\n"
                        f"Форма существа/угрозы (НЕ гуманоид с руками+лицом): "
                        f"{spec.get('creature_hint') or ''}\n"
                        "РАЗНООБРАЗИЕ: новые UNIQUE места после коллизии мотивов.\n"
                    )
                    filled_retry = filled_retry.rstrip() + assignment_retry
                    card_path.write_text(filled_retry, encoding="utf-8")
                    gp.write_text(filled_retry, encoding="utf-8")
                retry_extra = (
                    f" ПОВТОРНАЯ ГЕНЕРАЦИЯ ({attempt}/{max_attempts}): "
                    f"прошлый ответ отклонён ({reject_reason}). "
                    "Запрещено повторять номер эксперимента, ВИД/МОТИВ места "
                    "(лес живой плоти, астероидное поле, бетонное плато, ртутный океан, "
                    "шахта≈туннель; оранжерея≈теплица), существ (рой глаз, нити-агент) "
                    "и механику колец-тисков других вариантов; нельзя архив/прачечную/шахту/туннель; нельзя троп маятник-тени; "
                    "нельзя ручку двери вместо лица; нельзя шаг-как-механику; "
                    "НЕЛЬЗЯ пол/плитку как ловушку (плитки разъезжаются/смыкаются, пол складывается, "
                    "керамический кокон из пола) — убей/спаси через стены, потолок, существо, предмет, "
                    "жидкость, ткань, металл, зеркало/раму, мебель. "
                    f"Класс механики этого варианта: {spec.get('mechanic_key') or ''} — "
                    f"{spec.get('mechanic_hint') or ''}. "
                    "нельзя исчезает/стирается/сливается как главный бит. "
                    "Пиши только конкретные видимые действия. "
                    "Обязателен блок «Кадры.» с тайм-кодовыми строками "
                    "закадра. Запрет: чёрная дыра+лава/магма/шлак/раскалённый мост (прививка ада к дыре). Микс существ обязателен (не все «существ нет»). Сделай другой КАТЕГОРИИ ЭКСТРЕМАЛЬНЫЙ мир (ад/космос/солнце/чёрная дыра/зазеркалье/рай/демоны — НЕ обычная комната) и другое существо."
                )
            hint = base_hint + retry_extra
            logger.info(
                "[#{}] nii67 script variant {}/{} {} attempt={}",
                project.id,
                i,
                n,
                spec["id"],
                attempt,
            )
            last_reply = await _ask_script_files(
                project, chat_base + hint, attach, project_id=project_id
            )
            vo = strip_nii67_report_boilerplate(
                _voiceover_from_script_reply(last_reply)
            )
            scenario = strip_nii67_report_boilerplate(
                extract_scenario_or_preamble(last_reply or "")
            )
            if len(vo) < 200:
                reject_reason = f"слишком короткий закадр (len={len(vo)})"
                logger.warning(
                    "[#{}] nii67 variant {} rejected ({}): {}",
                    project.id,
                    spec["id"],
                    attempt,
                    reject_reason,
                )
                continue
            if len(scenario) < 40:
                # Не подменяем закадром: без сценария/Кадров — брак и retry.
                scenario = scenario or ""
                reject_reason = "нет сценария / слишком короткий SCENARIO"
                logger.warning(
                    "[#{}] nii67 variant {} rejected ({}): {}",
                    project.id,
                    spec["id"],
                    attempt,
                    reject_reason,
                )
                continue
            reject_reason = _nii67_variant_reject_reason(
                scenario,
                vo,
                used_experiments=used_experiments,
                used_places=used_place_tokens,
                used_creatures=used_creatures,
                seed_places=seed_places,
                floor_trap_hits=floor_trap_hits,
                mundane_batch_hits=mundane_batch_hits,
                used_place_motifs=used_place_motifs,
                used_mechanic_motifs=used_mechanic_motifs,
                used_world_kinds=used_world_kinds,
                seed_world_kinds=seed_wk,
            )
            if reject_reason is None:
                doors = _nii67_door_blocks(scenario)
                this_creature = sum(1 for _, b in doors if _nii67_door_has_creature(b))
                this_none = sum(
                    1 for _, b in doors if _NII67_CREATURE_NONE_RE.search(b or "")
                )
                both_none = len(doors) >= 2 and this_creature == 0 and this_none >= 2
                remaining_after = n - i
                ch = str(spec.get("creature_hint") or "")
                min_creature_doors = max(3, (n * 2) // 3)  # count=5 → ≥3 of 10 door-worlds (~half soft floor)
                if ch and not _creature_hint_is_env_only_safe(ch) and this_creature == 0:
                    reject_reason = (
                        "штамп требовал существо, а в сценарии нет существа на дверях "
                        "— добавь негуманоидное существо на ≥1 дверь"
                    )
                elif both_none and (variants_all_creatureless + 1) >= n:
                    reject_reason = (
                        "монокультура «существ нет» на всех вариантах партии — "
                        "нужен микс: часть дверей С существом"
                    )
                elif remaining_after == 0 and (
                    creature_doors_ok + this_creature
                ) < min_creature_doors:
                    reject_reason = (
                        f"в партии мало дверей с существом "
                        f"({creature_doors_ok + this_creature}<{min_creature_doors}) "
                        "— добавь негуманоида"
                    )
            if reject_reason is None:
                break
            logger.warning(
                "[#{}] nii67 variant {} rejected ({}): {}",
                project.id,
                spec["id"],
                attempt,
                reject_reason,
            )
        else:
            # Структура (Кадры) и коллизии вида места — не принимаем «как есть».
            hard = reject_reason or "unknown"
            raise RuntimeError(
                f"nii67 variant {spec['id']}: после {max_attempts} попыток "
                f"ответ всё ещё брак ({hard})"
            )

        exp = _nii67_extract_experiment(f"{scenario}\n{vo}") or str(
            spec.get("experiment_words") or ""
        ).casefold()
        if exp:
            used_experiments.add(exp)
        from app.services.nii67_card import normalize_place_head, place_kinds as _pk_all
        for sp in seed_places + _nii67_extract_door_places(scenario):
            tok = _nii67_place_token(sp)
            if tok:
                used_place_tokens.add(tok)
            used_place_tokens.update(k for k in _pk_all(sp) if k.startswith("g:"))
            head = normalize_place_head(sp)
            # полная голова (не одно короткое «зал») — иначе ложные коллизии
            if head and len(head) >= 6:
                used_place_tokens.add(head)
        used_creatures.update(_nii67_extract_creature_hints(f"{scenario}\n{vo}"))
        used_place_motifs.update(_nii67_extract_place_motifs(f"{scenario}\n{vo}"))
        for sp in seed_places:
            used_place_motifs.update(_nii67_extract_place_motifs(sp))
        used_mechanic_motifs.update(_nii67_extract_mechanic_motifs(f"{scenario}\n{vo}"))
        for sp in seed_places:
            used_world_kinds.update(k for k in _pk_kinds(sp) if k.startswith("g:"))
        for dp in _nii67_extract_door_places(scenario):
            used_world_kinds.update(k for k in _pk_kinds(dp) if k.startswith("g:"))
        if _NII67_FLOOR_TILE_CENTER_RE.search(f"{scenario}\n{vo}"):
            floor_trap_hits += 1
        _dp = _nii67_extract_door_places(scenario)
        if sum(1 for p in _dp if _NII67_BORING_INTERIOR_RE.search((p or '').split(',')[0])) >= 1:
            mundane_batch_hits += 1
        _door_blocks_acc = _nii67_door_blocks(scenario)
        _c_here = sum(1 for _, b in _door_blocks_acc if _nii67_door_has_creature(b))
        _n_here = sum(
            1 for _, b in _door_blocks_acc if _NII67_CREATURE_NONE_RE.search(b or "")
        )
        creature_doors_ok += _c_here
        creatureless_doors += _n_here
        if _c_here == 0 and _n_here >= 2:
            variants_all_creatureless += 1
        used_places_hint.append(
            f"{spec.get('experiment_words') or ''} · "
            f"{spec.get('creepy_place') or ''} / {spec.get('quiet_place') or ''} · "
            f"мех:{spec.get('mechanic_key') or ''}"
        )
        slot = out_root / str(spec["id"])
        slot.mkdir(parents=True, exist_ok=True)
        (slot / "voiceover.txt").write_text(vo.rstrip() + "\n", encoding="utf-8")
        (slot / "scenario.txt").write_text(scenario.rstrip() + "\n", encoding="utf-8")
        outputs.append(
            {
                "id": str(spec["id"]),
                "label": str(spec["label"]),
                "voiceover": vo,
                "scenario": scenario,
            }
        )
        voiceover_text = vo

    active = outputs[0]
    voiceover_text = cx.save_voiceover_text(
        project, proj_xlsx.parent / "voiceover.txt", active["voiceover"]
    )
    if active.get("scenario"):
        (proj_xlsx.parent / "scenario.txt").write_text(
            active["scenario"].rstrip() + "\n", encoding="utf-8"
        )
    meta = dict(project.meta or {}) if isinstance(project.meta, dict) else {}
    meta["nii67_outputs"] = outputs
    meta["nii67_active_id"] = active["id"]
    project.meta = meta
    flag_modified(project, "meta")
    return (
        XlsxRoundtripResult(
            reply_text=last_reply,
            downloaded_path=proj_xlsx.parent / "voiceover.txt",
            project_xlsx=proj_xlsx,
            apply_ops=[
                {
                    "target": "project",
                    "fields": {"закадровый_текст": voiceover_text},
                }
            ],
        ),
        voiceover_text,
    )


async def run_script_xlsx(
    project: Project,
    *,
    project_id: int | None = None,
) -> tuple[XlsxRoundtripResult, str]:
    """Шаг «Закадровый текст»: GPT → текст/apply-ops → DB+voiceover.txt (без download)."""
    from app.services.voiceover_sanitize import (
        ensure_voiceover_format_instruction,
        extract_scenario_block,
    )

    proj_xlsx = _ensure_project_xlsx(project)
    source_voiceover = cx.ensure_current_voiceover(project)

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_script_prompt_file(project, tmp_dir, ts=ts)
    from app.services.prompt_library import (
        PROMPTS_ROOT,
        resolve_project_prompt_with_source,
    )

    script_variant, _script_src = resolve_project_prompt_with_source(
        getattr(project, "prompt_overrides", None) or {},
        "script",
        meta=getattr(project, "meta", None)
        if isinstance(getattr(project, "meta", None), dict)
        else None,
    )
    chat_raw = cx.chat_message(project, "script", prompt_file_name=prompt_file.name)
    if script_variant == "nii67":
        chat_msg = f"{chat_raw}{_SCRIPT_NII67_HINT}"
    else:
        chat_msg = ensure_voiceover_format_instruction(chat_raw)
        chat_msg = f"{chat_msg}{_SCRIPT_DB_HINT}"
    attach_files: list[Path] = [prompt_file]
    # Контекст: общий план / прошлый закадр — без ожидания скачивания xlsx.
    if (project.general_plan or "").strip():
        gp = tmp_dir / f"general_plan_{ts}.txt"
        gp.write_text(project.general_plan or "", encoding="utf-8")
        attach_files.append(gp)

    if script_variant == "nii67":
        project_card = proj_xlsx.parent / "nii67-card.md"
        template_card = PROMPTS_ROOT / "nii67" / "anketa.md"
        card = project_card if project_card.is_file() else template_card
        if card.is_file():
            attach_files.append(card)
        from app.services.nii67_card import nii67_count_from_meta, nii67_run_specs

        meta = project.meta if isinstance(project.meta, dict) else {}
        raw_picked = meta.get("nii67_picked_ids")
        picked_ids = (
            [str(x) for x in raw_picked] if isinstance(raw_picked, list) else None
        )
        want_n = nii67_count_from_meta(meta)
        specs = nii67_run_specs(
            project.general_plan or "",
            count=want_n,
            picked_ids=picked_ids,
        )
        return await _run_nii67_script_variants(
            project,
            project_id=project_id,
            specs=specs,
            prompt_file=prompt_file,
            chat_base=chat_msg,
            source_voiceover=None,
            proj_xlsx=proj_xlsx,
            tmp_dir=tmp_dir,
            ts=ts,
        )
    if source_voiceover is not None:
        attach_files.append(source_voiceover)

    logger.info(
        "script_db: prompt_file={} chat_len={} (без xlsx-download)",
        prompt_file.name,
        len(chat_msg),
    )

    reply = await _ask_script_files(
        project, chat_msg, attach_files, project_id=project_id
    )

    voiceover_text = _voiceover_from_script_reply(reply)
    if len(voiceover_text) < 200:
        raise RuntimeError(
            "GPT не вернул закадр (apply-ops закадровый_текст или "
            f"<<<VOICEOVER>>>, len={len(voiceover_text)})"
        )

    voiceover_text = cx.save_voiceover_text(
        project, proj_xlsx.parent / "voiceover.txt", voiceover_text
    )
    scenario_text = extract_scenario_block(reply or "")
    if scenario_text:
        scenario_path = proj_xlsx.parent / "scenario.txt"
        scenario_path.write_text(scenario_text.rstrip() + "\n", encoding="utf-8")
        logger.info("script_db: scenario saved {}", scenario_path)

    return (
        XlsxRoundtripResult(
            reply_text=reply,
            downloaded_path=proj_xlsx.parent / "voiceover.txt",
            project_xlsx=proj_xlsx,
            apply_ops=[
                {
                    "target": "project",
                    "fields": {"закадровый_текст": voiceover_text},
                }
            ],
        ),
        voiceover_text,
    )


_SPLIT_DB_HINT = (
    "\n\n# РАЗБИВКА — ИСТОЧНИК ПРАВДЫ БАЗА (НЕ Excel)\n"
    "Верни ТОЛЬКО JSON apply-ops. Без TSV, без `# Лист:`, без `@row=`, "
    "без скачивания .xlsx:\n"
    '{"ops":[{"target":"replace_frames","frames":['
    '{"закадр":"текст кадра 1","длительность":3},'
    '{"закадр":"текст кадра 2"}'
    "]}]}\n"
    "Нужно ≥2 кадра. Каждый кадр — отдельный объект с полем закадр.\n"
)


def extract_frames_spec_from_gpt_reply(reply: str, *, voiceover_path: Path | None) -> list[dict]:
    """Достать кадры из replace_frames JSON или --- блоков / локальной разбивки."""
    from app.services import db_apply

    data = db_apply.extract_apply_ops_json(reply or "")
    if isinstance(data, dict):
        for op in data.get("ops") or []:
            if not isinstance(op, dict):
                continue
            if str(op.get("target") or "") != "replace_frames":
                continue
            raw = op.get("frames") or op.get("кадры") or []
            if isinstance(raw, list) and len(raw) >= 2:
                out: list[dict] = []
                for item in raw:
                    if isinstance(item, str) and item.strip():
                        out.append({"закадр": item.strip()})
                    elif isinstance(item, dict):
                        out.append(item)
                if len(out) >= 2:
                    return out
    blocks = parse_dash_separated_blocks(reply or "")
    if len(blocks) < 2 and voiceover_path is not None and voiceover_path.exists():
        blocks = split_voiceover_locally(
            voiceover_path.read_text(encoding="utf-8", errors="replace")
        )
    if len(blocks) >= 2:
        return [{"закадр": b.strip()} for b in blocks if b.strip()]
    return []


async def run_split_xlsx(
    project: Project,
    *,
    project_id: int | None = None,
) -> XlsxRoundtripResult:
    """Шаг «Разбивка»: GPT → replace_frames (DB); Excel только через export."""
    proj_xlsx = _ensure_project_xlsx(project)
    voiceover = cx.ensure_current_voiceover(project)
    if voiceover is None:
        raise FileNotFoundError(
            "voiceover.txt не найден — сначала пройди шаг «Закадровый текст»"
        )

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_split_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = (
        cx.chat_message(project, "split", prompt_file_name=prompt_file.name)
        + _SPLIT_DB_HINT
    )

    logger.info(
        "split_db: prompt={}, voiceover={}, chat_len={} (без xlsx-download)",
        prompt_file.name,
        voiceover.name,
        len(chat_msg),
    )

    async def _gpt() -> str:
        return await xgf.telegram_style_ask_with_files(
            chat_msg,
            [prompt_file, voiceover],
            project_id=project_id or project.id,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "split", _gpt)
    frames_spec = extract_frames_spec_from_gpt_reply(reply, voiceover_path=voiceover)
    if len(frames_spec) < 2:
        raise RuntimeError(
            "GPT не вернул разбивку в apply-ops replace_frames "
            f"(кадров={len(frames_spec)}). Нужен JSON "
            '{"ops":[{"target":"replace_frames","frames":[...]}]}'
        )

    # GPT as-is → DB (≥2 кадров). Лимиты символов — только в prompt settings.
    logger.info("split_db: кадров из GPT/fallback={}", len(frames_spec))
    return XlsxRoundtripResult(
        reply_text=reply,
        downloaded_path=proj_xlsx,
        project_xlsx=proj_xlsx,
        backup_path=None,
        frames_spec=frames_spec,
        apply_ops=[{"target": "replace_frames", "frames": frames_spec}],
    )


_IMG_PR_DB_HINT = (
    "\n\n# ПРОМТЫ КАРТИНОК — ИСТОЧНИК ПРАВДЫ БАЗА (НЕ Excel)\n"
    "Верни ТОЛЬКО JSON apply-ops. Без TSV, без `# Лист:`, без `@row=`, "
    "без скачивания .xlsx:\n"
    '{"ops":[{"frame_uuid":"<uuid>","fields":{"промт_картинки":"…"}}]}\n'
    "Адрес кадра — ТОЛЬКО frame_uuid из КОРНЯ frames[].uuid ЭТОГО батча "
    "(не coverage_parent.parent_uuid).\n"
    "Не пиши системную инструкцию и не собирай агента: ты уже img_pr. "
    "Ответ — только JSON apply-ops.\n"
    "Одна операция = один кадр. Пиши только полные ops; если не все влезли — "
    "верни сколько полных влезло, остальное не трогай. "
    'Пустой {"ops":[]} запрещён.\n'
    "Сборка кадра (порядок): ref(cXX?) → shot01_bg → shot01_action → "
    "lighting/scene_lighting → accent → scene_sense → scene_feature → "
    "shot01_description/props → place/время → STYLE. "
    "accent/scene_sense/scene_feature — отдельные строки. "
    "Если у кадра есть coverage_parent — это K2/K3 ТОЙ ЖЕ сцены: первый абзац "
    "Preserve/Change (Image 1 = still родителя). Не выдумывай новую локацию, "
    "не добавляй людей сверх состава родителя, не подменяй картину/предмет. "
    "В промт_картинки пиши ПОЛНЫЙ промт: сцена + STYLE LOCK / Final style "
    "lock / Negative из мастера. Оркестратор НИЧЕГО не дописывает. "
    "characters[] Entity. Не копируй voiceover_text.\n"
)

_PLASTILIN_IMG_PR_HINT = (
    "\n\n# ПЛАСТИЛИН — ЗАПИСЬ В БАЗУ (НЕ Excel)\n"
    "Верни ТОЛЬКО JSON apply-ops. Без TSV, без `# Лист:`, без `@row=`, "
    "без скачивания .xlsx:\n"
    '{"ops":[{"frame_uuid":"<uuid>","fields":{"промт_картинки":"…","персонажи":"c01"}}]}\n'
    "Адрес кадра — ТОЛЬКО frame_uuid из КОРНЯ frames[].uuid ЭТОГО батча "
    "(не coverage_parent.parent_uuid).\n"
    "Не пиши системную инструкцию и не собирай агента: ты уже img_pr. "
    "Ответ — только JSON apply-ops.\n"
    "Одна операция = один кадр. Пиши только полные ops; если не все влезли — "
    "верни сколько полных влезло, остальное не трогай. "
    'Пустой {"ops":[]} запрещён.\n'
    "В `промт_картинки` пиши ПОЛНЫЙ промт: стиль пластилина ТРИ раза + сцена "
    "+ Negative. Пайплайн НЕ допишет watercolor/noir — не копируй Archival Noir. "
    "Не копируй voiceover_text. Без ASCII двойных кавычек в тексте промта.\n"
)


@asynccontextmanager
async def _project_runtime_session(project: Project) -> AsyncIterator[AsyncSession]:
    """Сессия SoT кадров img_pr: isolated project.db, не master state.db.

    Wipe и валидатор шага уже ходят в project.db. Loader/apply на SessionLocal
    видели чужие старые промты и пропускали GPT.
    """
    data_dir = getattr(project, "data_dir", None)
    if data_dir:
        from app.project_db import get_project_sessionmaker

        sm = await get_project_sessionmaker(data_dir)
        async with sm() as session:
            yield session
        return
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session


async def _load_img_pr_context(
    project: Project,
    *,
    only_uuids: set[str] | None = None,
    skip_uuids: set[str] | None = None,
) -> tuple[list[Frame], list[dict], str, list[Frame]]:
    """Кадры + Entity cards + general_plan для img_pr."""
    from app.models import Entity
    from app.services import db_v2
    from app.services.excel_characters import entity_cards_for_gpt
    from app.services.vo_shot_expand import is_shot_child

    async with _project_runtime_session(project) as session:
        proj = await session.get(Project, project.id)
        if proj is None:
            raise RuntimeError(f"project #{project.id} not found for img_pr db_frames")
        await db_v2.backfill_project_v2(session, proj)
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == proj.id)
                    .order_by(Frame.number)
                )
            ).scalars().all()
        )
        ents = list(
            (
                await session.execute(
                    select(Entity).where(Entity.project_id == proj.id)
                )
            ).scalars().all()
        )
        general_plan = proj.general_plan or ""
        cards = entity_cards_for_gpt(ents)

    selected: list[Frame] = []
    n_parent = 0
    n_child = 0
    for fr in frames:
        uuid = (fr.uuid or "").strip()
        if not uuid:
            continue
        if only_uuids is not None and uuid not in only_uuids:
            continue
        if skip_uuids and uuid in skip_uuids:
            continue
        if not (fr.voiceover_text or "").strip():
            continue
        if is_shot_child(fr):
            n_child += 1
            continue
        # Уже заполненные пропускаем (resume / soft retry).
        if (fr.image_prompt or "").strip():
            continue
        n_parent += 1
        selected.append(fr)
    if selected or n_child:
        logger.info(
            "img_pr_db: need prompts parent={} shot_child_skipped={} skip_ckpt={}",
            n_parent,
            n_child,
            len(skip_uuids or ()),
        )
    return selected, cards, general_plan, frames


def _write_img_pr_db_frames_for(
    project: Project,
    tmp_dir: Path,
    frames: list[Frame],
    characters: list[dict],
    general_plan: str,
    *,
    batch_tag: str = "",
    include_characters: bool = True,
    all_frames: list[Frame] | None = None,
) -> Path:
    import json

    from app.services.db_frames_context import build_img_pr_db_context

    ctx = build_img_pr_db_context(
        project_id=project.id,
        slug=project.slug or "",
        frames=frames,
        characters=characters,
        general_plan=general_plan if include_characters else "",
        include_characters=include_characters,
        include_field_map=include_characters,
        all_frames=all_frames,
    )
    name = f"db_frames{('_' + batch_tag) if batch_tag else ''}.json"
    out = tmp_dir / name
    # Компактно: меньше шанс упереться в trim 60k в gpt_api.file_to_context.
    out.write_text(
        json.dumps(ctx, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    logger.info(
        "img_pr_db: {} frames={} chars={} bytes={}",
        out.name,
        len(ctx.get("frames") or []),
        len(ctx.get("characters") or []),
        out.stat().st_size,
    )
    return out


async def _write_img_pr_db_frames(project: Project, tmp_dir: Path) -> Path:
    """Пишет полный db_frames.json (smoke / legacy)."""
    frames, cards, general_plan, all_frames = await _load_img_pr_context(project)
    return _write_img_pr_db_frames_for(
        project, tmp_dir, frames, cards, general_plan, all_frames=all_frames
    )


async def _apply_img_pr_ops_now(
    project: Project,
    ops: list[dict],
    *,
    export_xlsx: bool = False,
    label: str = "",
) -> list[dict]:
    """Сразу записать apply-ops батча в DB (Excel — только явный Export)."""
    from app.services import img_pr_batches as ipb
    from app.services.vo_shot_expand import is_img_pr_vo_parent

    ops = ipb.writeable_img_pr_ops(ops)
    if not ops:
        return []
    import asyncio

    from app.services import db_apply

    last_err: Exception | None = None
    applied: list[dict] = []
    for apply_try in range(1, 6):
        try:
            async with _project_runtime_session(project) as session:
                proj = await session.get(Project, project.id)
                if proj is None:
                    raise RuntimeError(f"project #{project.id} gone during img_pr apply")
                frames = list(
                    (
                        await session.execute(
                            select(Frame).where(Frame.project_id == proj.id)
                        )
                    ).scalars().all()
                )
                by_uuid = {str(fr.uuid or ""): fr for fr in frames if fr.uuid}
                kept: list[dict] = []
                unknown: list[str] = []
                skipped_child: list[str] = []
                for op in ops:
                    uid = ipb.uuid_of_op(op)
                    fr = by_uuid.get(uid)
                    if fr is None:
                        unknown.append(uid)
                        continue
                    if not is_img_pr_vo_parent(fr):
                        skipped_child.append(uid)
                        continue
                    kept.append(op)
                if unknown:
                    logger.warning(
                        "img_pr_db: skip unknown frame_uuid n={} sample={} {}",
                        len(unknown),
                        unknown[:8],
                        label,
                    )
                if skipped_child:
                    logger.warning(
                        "img_pr_db: skip shot-child uuid n={} sample={} {}",
                        len(skipped_child),
                        skipped_child[:8],
                        label,
                    )
                if not kept:
                    return []
                await db_apply.apply_ops(
                    session, proj, kept, export_xlsx=export_xlsx, node_kind="img_pr"
                )
                frames = list(
                    (
                        await session.execute(
                            select(Frame).where(Frame.project_id == proj.id)
                        )
                    ).scalars().all()
                )
                from app.services.vo_shot_expand import write_coverage_child_prompts

                n_kids = write_coverage_child_prompts(frames)
                if n_kids:
                    logger.info(
                        "img_pr_db: coverage child prompts written n={} {}",
                        n_kids,
                        label,
                    )
                await session.commit()
                applied = kept
            last_err = None
            break
        except Exception as apply_err:  # noqa: BLE001
            last_err = apply_err
            msg = str(apply_err).lower()
            locked = "database is locked" in msg or "database locked" in msg
            if not locked or apply_try >= 5:
                raise
            wait_s = min(2 * apply_try, 10)
            logger.warning(
                "img_pr_db: apply locked try {}/5 — sleep {}s {}",
                apply_try,
                wait_s,
                label,
            )
            await asyncio.sleep(wait_s)
    if last_err is not None:
        raise last_err
    logger.info(
        "img_pr_db: applied to DB ops={} export_xlsx={} {}",
        len(applied),
        export_xlsx,
        label,
    )
    return applied


async def run_img_pr_xlsx(
    project: Project,
    *,
    n_frames: int | None = None,
    project_id: int | None = None,
    uuid_map_text: str = "",
    n_batches: int | None = None,
) -> XlsxRoundtripResult:
    """Шаг «Промты картинок»: GPT батчами → apply-ops (DB)."""
    from app.services import img_pr_batches as ipb

    proj_xlsx = _ensure_project_xlsx(project)
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_img_pr_prompt_file(project, tmp_dir, ts=_ts())
    from app.services.img_pr_style import is_plastilin_master, resolve_project_img_style

    master_head = ""
    try:
        master_head = prompt_file.read_text(encoding="utf-8")[:2000]
    except OSError:
        master_head = ""
    plastilin = is_plastilin_master(prompt_file.name, master_head)
    style_id = resolve_project_img_style(
        project, variant=prompt_file.name, master=master_head
    )
    logger.info("img_pr_db: style_id={!r} plastilin={}", style_id, plastilin)
    img_pr_hint = _PLASTILIN_IMG_PR_HINT if plastilin else _IMG_PR_DB_HINT
    if plastilin:
        logger.info("img_pr_db: plastilin master — keep clay style in prompt, no watercolor wrap")

    ckpt = ipb.load_checkpoint(project.data_dir)
    done_uuids = list(ckpt.get("done_uuids") or [])
    all_ops: list[dict] = list(ckpt.get("ops") or [])
    done_set = set(done_uuids)

    writeable = ipb.writeable_img_pr_ops(all_ops)
    dropped_short = len(all_ops) - len(writeable)
    if dropped_short:
        logger.warning(
            "img_pr_db: drop {} short/placeholder ops from checkpoint",
            dropped_short,
        )
    all_ops = writeable
    if all_ops:
        all_ops = await _apply_img_pr_ops_now(
            project, all_ops, export_xlsx=False, label="checkpoint"
        )
        done_uuids = [ipb.uuid_of_op(op) for op in all_ops if ipb.uuid_of_op(op)]
        done_set = set(done_uuids)
        ipb.save_checkpoint(project.data_dir, done_uuids=done_uuids, ops=all_ops)

    # После flush чекпоинта пустые кадры смотрим по DB, не по skip_uuids:
    # иначе промты живут только в JSON до конца всего прогона.
    frames, cards, general_plan, all_frames = await _load_img_pr_context(project)
    if not frames:
        if all_ops:
            logger.info(
                "img_pr_db: checkpoint complete ops={} — apply once at end",
                len(all_ops),
            )
            return XlsxRoundtripResult(
                reply_text="(from checkpoint)",
                downloaded_path=proj_xlsx,
                project_xlsx=proj_xlsx,
                backup_path=None,
                apply_ops=all_ops,
                ops_applied_inline=True,
            )
        # Уже всё в DB с прошлого успешного прогона.
        frames_db, _, _, _ = await _load_img_pr_context(project)
        if not frames_db:
            if n_frames:
                raise RuntimeError(
                    "img_pr: loader не видит пустые промты, хотя шаг ждёт "
                    f"{n_frames} кадров (state.db vs project.db?)"
                )
            logger.info("img_pr_db: nothing to do — all frames already have prompts")
            return XlsxRoundtripResult(
                reply_text="(already in DB)",
                downloaded_path=proj_xlsx,
                project_xlsx=proj_xlsx,
                backup_path=None,
                apply_ops=[],
                ops_applied_inline=True,
            )
        raise RuntimeError(
            "нет кадров без image_prompt для img_pr "
            f"(done_checkpoint={len(done_set)})"
        )

    from collections import deque
    import asyncio

    from app.services.adaptive_llm_batches import next_split_level, split_in_half
    from app.services.gpt_client import ApiGptClient
    from app.services.output_batch_plan import pack_frames_img_pr
    from app.services.step_cancel import raise_if_cancelled, sleep_cancellable

    # Старт: N батчей в волне; живых стримов = check_streams справа.
    live_n = img_pr_live_streams(project)
    parts = pack_frames_img_pr(frames, n_batches=n_batches)
    work: deque[tuple[list, int]] = deque((part, 1) for part in parts)
    logger.info(
        "img_pr_db: frames={} start_batches={} sizes={} parallel={} checkpoint_done={}",
        len(frames),
        len(parts),
        [len(p) for p in parts],
        live_n,
        len(done_set),
    )

    vo = cx.ensure_current_voiceover(project)
    replies: list[str] = []
    api_batches = 0

    async def _ask_batch_ops(
        *,
        bi: int,
        batch_n: int,
        batch: list,
        level: int,
    ) -> tuple[list[dict], str]:
        gpt_local = ApiGptClient()
        await gpt_local.new_conversation()
        batch_tag = f"b{bi:02d}"
        db_path = _write_img_pr_db_frames_for(
            project,
            tmp_dir,
            batch,
            cards,
            general_plan,
            batch_tag=batch_tag,
            include_characters=True,
            all_frames=all_frames,
        )
        uuid_lines = "\n".join(
            f"кадр {fr.number} = {fr.uuid}" for fr in batch if fr.uuid
        )
        footer = ipb.batch_footer(
            batch_i=bi, batch_n=batch_n, n=len(batch), plastilin=plastilin
        )
        chat_msg = cx.chat_message(
            project,
            "img_pr",
            prompt_file_name=prompt_file.name,
            n_frames=len(batch),
        )
        chat_msg = (
            f"{chat_msg}{img_pr_hint}\n{footer}\n"
            f"Адресация:\n{uuid_lines}\n"
        )
        if uuid_map_text.strip():
            chat_msg = (
                f"{chat_msg}\n# uuid map (справочно)\n"
                f"{uuid_map_text.strip()[:2000]}\n"
            )
        attach = ipb.batch_attach_files(
            batch_i=bi,
            prompt_file=prompt_file,
            db_path=db_path,
            voiceover=vo if bi == 1 else None,
        )
        batch_ops: list[dict] = []
        last_reply = ""
        for attempt in range(1, ipb._GPT_ATTEMPTS + 1):
            if attempt > 1:
                await gpt_local.new_conversation()
                attach = ipb.batch_attach_files(
                    batch_i=bi,
                    prompt_file=prompt_file,
                    db_path=db_path,
                    voiceover=None,
                )
                chat_msg = (
                    f"{img_pr_hint}\n{footer}\n"
                    f"Адресация:\n{uuid_lines}\n"
                    "Только JSON apply-ops. "
                    "Пустой {\"ops\":[]} запрещён — верни сколько полных ops влезло. "
                    + (
                        "Стиль пластилина оставь в промт_картинки.\n"
                        if plastilin
                        else "Полный промт: сцена + STYLE LOCK / Negative.\n"
                    )
                )
                logger.info(
                    "img_pr_db: batch {}/{} fresh session retry", bi, batch_n
                )
            logger.info(
                "img_pr_db: batch {}/{} attempt {} frames={} attach={} "
                "parallel={} bytes={}",
                bi,
                batch_n,
                attempt,
                [fr.number for fr in batch],
                [p.name for p in attach],
                live_n,
                db_path.stat().st_size,
            )
            last_reply = await gpt_local.ask_with_files(
                chat_msg,
                attach,
                project_id=project_id or project.id,
                expect_file_download=False,
                history=None,
                treat_txt_as_prompt=True,
                auto_pack=False,
            )
            batch_ops = ipb.parse_img_pr_ops(
                last_reply or "",
                wrap_style=not plastilin,
                style_id=style_id,
            )
            if batch_ops:
                break
            empty_stub = ipb.is_empty_ops_reply(last_reply or "")
            reason = "empty_ops_stub" if empty_stub else "no_prompt_ops"
            rej = ipb.write_rejected_reply(
                tmp_dir,
                batch_i=bi,
                attempt=attempt,
                reply=last_reply or "",
                reason=reason,
            )
            delay = _EMPTY_OPS_BACKOFF_S[
                min(attempt - 1, len(_EMPTY_OPS_BACKOFF_S) - 1)
            ]
            logger.warning(
                "img_pr_db: batch {}/{} attempt {} failed reply_len={} "
                "reason={} backoff={:.0f}s {}",
                bi,
                batch_n,
                attempt,
                len(last_reply or ""),
                reason,
                delay,
                rej.name,
            )
            if attempt < ipb._GPT_ATTEMPTS:
                await sleep_cancellable(
                    delay, project_id or project.id
                )
        return batch_ops, last_reply or ""

    async def _run_batches() -> None:
        nonlocal done_uuids, all_ops, done_set, api_batches
        bi_seq = 0
        in_flight: dict[asyncio.Task, tuple[list, int, int]] = {}
        any_ok = False

        def _enqueue_split(batch: list, level: int, *, why: str) -> bool:
            nxt = next_split_level(level)
            if nxt is not None and len(batch) >= 2:
                for half in split_in_half(batch):
                    work.append((half, nxt))
                logger.warning(
                    "img_pr_db: {} L{} frames={} → split {} (queue={})",
                    why,
                    level,
                    len(batch),
                    nxt,
                    len(work),
                )
                return True
            logger.warning(
                "img_pr_db: {} L{} frames={} — stop split",
                why,
                level,
                len(batch),
            )
            return False

        def _ingest(
            bi: int,
            batch: list,
            level: int,
            batch_ops: list[dict],
            last_reply: str,
        ) -> None:
            nonlocal api_batches, any_ok
            replies.append(last_reply)
            batch_ops = ipb.writeable_img_pr_ops(batch_ops)
            if not batch_ops:
                if _enqueue_split(batch, level, why="no ops"):
                    return
                if all_ops:
                    logger.error(
                        "img_pr_db: batch {} L{} failed — partial ops={}",
                        bi,
                        level,
                        len(all_ops),
                    )
                    return
                raise RuntimeError(
                    f"img_pr batch {bi} L{level}: нет apply-ops "
                    f"(reply_len={len(last_reply)}). "
                    f"Смотри tmp_gpt/img_pr_rejected_b{bi}_*.txt"
                )
            api_batches += 1
            any_ok = True
            expected = {
                (fr.uuid or "").strip()
                for fr in batch
                if (fr.uuid or "").strip()
            }
            got_uuids = {
                ipb.uuid_of_op(op) for op in batch_ops if ipb.uuid_of_op(op)
            } & expected
            for u in sorted(got_uuids):
                if u and u not in done_set:
                    done_uuids.append(u)
                    done_set.add(u)
            kept_ops = [
                op for op in batch_ops if ipb.uuid_of_op(op) in got_uuids
            ]
            all_ops.extend(kept_ops)
            missing_uuids = expected - got_uuids
            logger.info(
                "img_pr_db: batch {} L{} ops=+{} total={} missing={} "
                "(checkpoint, live={})",
                bi,
                level,
                len(kept_ops),
                len(all_ops),
                len(missing_uuids),
                live_n,
            )
            if missing_uuids:
                missing_frames = [
                    fr
                    for fr in batch
                    if (fr.uuid or "").strip() in missing_uuids
                ]
                _enqueue_split(
                    missing_frames,
                    level,
                    why=f"incomplete {len(got_uuids)}/{len(expected)}",
                )
            ipb.save_checkpoint(
                project.data_dir, done_uuids=done_uuids, ops=all_ops
            )

        def _launch() -> None:
            nonlocal bi_seq
            while work and len(in_flight) < live_n:
                batch, level = work.popleft()
                bi_seq += 1
                bi = bi_seq
                batch_n = bi_seq + len(work) + len(in_flight)
                task = asyncio.create_task(
                    _ask_batch_ops(
                        bi=bi, batch_n=max(batch_n, bi), batch=batch, level=level
                    )
                )
                in_flight[task] = (batch, level, bi)

        logger.info(
            "img_pr_db: pool start queued={} live={}",
            len(work),
            live_n,
        )
        while work or in_flight:
            raise_if_cancelled(project.id)
            _launch()
            if not in_flight:
                break
            done, _pending = await asyncio.wait(
                set(in_flight), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                batch, level, bi = in_flight.pop(task)
                exc = task.exception()
                if exc is not None:
                    logger.error(
                        "img_pr_db: parallel batch L{} frames={} raised: {}",
                        level,
                        len(batch),
                        exc,
                    )
                    nxt = next_split_level(level)
                    if nxt is not None and len(batch) >= 2:
                        for half in split_in_half(batch):
                            work.append((half, nxt))
                        continue
                    if all_ops:
                        continue
                    raise exc
                batch_ops, last_reply = task.result()
                before = len(all_ops)
                _ingest(bi, batch, level, batch_ops, last_reply or "")
                added = all_ops[before:]
                if added:
                    try:
                        await _apply_img_pr_ops_now(
                            project,
                            added,
                            export_xlsx=False,
                            label=f"batch-{bi}",
                        )
                    except Exception as apply_err:  # noqa: BLE001
                        logger.warning(
                            "img_pr_db: batch {} apply failed (ckpt keeps ops): {}",
                            bi,
                            apply_err,
                        )

        if not any_ok and not all_ops:
            raise RuntimeError(
                "img_pr: все параллельные батчи провалились без ops"
            )

    await xgf.run_under_xlsx_lock(project.id, "img_pr", _run_batches)

    if not all_ops:
        raise RuntimeError(
            "GPT не вернул apply-ops с промт_картинки по frame_uuid. "
            'Нужен {"ops":[{"frame_uuid":"…","fields":{"промт_картинки":"…"}}]}'
        )

    logger.info(
        "img_pr_db: complete ops={} api_batches={} — already in DB",
        len(all_ops),
        api_batches,
    )
    return XlsxRoundtripResult(
        reply_text="\n\n---\n\n".join(replies) if replies else "",
        downloaded_path=proj_xlsx,
        project_xlsx=proj_xlsx,
        backup_path=None,
        apply_ops=all_ops,
        ops_applied_inline=True,
    )


async def sync_after_plan(
    session: AsyncSession, project: Project, xlsx_path: Path
) -> None:
    await cx.sync_project_xlsx(session, project, xlsx_path, keep_fields=False)
    from app.services.plan_validation import is_meaningful_general_plan

    plan_text = (project.general_plan or "").strip()
    if not is_meaningful_general_plan(plan_text):
        logger.warning(
            "[#{}] sync_after_plan: general_plan len={} path={}",
            project.id,
            len(plan_text),
            xlsx_path,
        )
        raise _plan_empty_error(xlsx_path, plan_len=len(plan_text))


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
