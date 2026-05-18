"""Шаг 6: генерация картинок по уже готовым промтам (outsee nano-banana-2).

Промты должны быть подготовлены на шаге 5 (generate_image_prompts).
Этот шаг только генерит и валидирует картинки.

Входной статус: generating_images.
Выходной статус: images_ready.

Алгоритм (НЕ БЛОКИРУЕТСЯ на ожидании approve пользователя):
  1. Берёт следующий кадр в статусе image_prompt_ready.
  2. Генерит картинку в outsee, сохраняет файл, шлёт в TG карточку
     с кнопками ✅/🔁/❌/✏ — но НЕ ждёт решения, переходит дальше.
  3. После того как все кадры «выпущены» в TG, loop ждёт пока каждый
     из них станет либо approved, либо failed. Параллельно обрабатывает
     возникающие 🔁 / ✏️ решения — ставит соответствующий кадр на
     повторную генерацию и запускает новый outsee-проход.

Таким образом пока ты одобряешь кадр N, бот уже может генерить кадр N+1.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import OutseeBot, OutseeImageError
from app.generation_options import (
    ASPECT_RATIOS_BY_ID,
    DEFAULTS,
    IMAGE_GENERATORS_BY_ID,
    IMAGE_RESOLUTIONS_BY_ID,
    build_gen_id_prefix,
)
from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)
from app.services.hitl import send_hitl_photo
from app.services.outsee_retry import generate_image_with_retries
from app.services.step_cancel import StepCancelledError, raise_if_cancelled
from app.settings import settings
from app.storage import for_project as _sheet_for_project

# Лист «план» v8 — какие строки в столбце кадра используются для рефов.
_XLSX_SHEET_PLAN = "план"
# v8-шаблон «план» дублирует лейблы «персонажи» / «предметы» в нескольких
# блоках (под заголовками «кадр1», «кадр2», «кадр3»). Юзер может вписать
# id в ЛЮБОЙ из этих строк. Раньше код смотрел только row=38/39 (3-й
# блок), и если юзер вписал в row=8 — рефы не подгружались. Теперь
# читаем ВСЕ три строки и сливаем (с dedupe сохраняя порядок).
_XLSX_ROWS_PERSONS = (8, 23, 38)   # «персонажи» — id c01..c05
_XLSX_ROWS_ITEMS = (9, 24, 39)     # «предметы» — id predmet1+


def _parse_ref_ids(cell_value: object) -> list[str]:
    """Парсит строку из xlsx-ячейки в список ID. Поддерживает разделители:
    запятая, пробел, точка с запятой, знак «+». Пустые токены и whitespace
    игнорируются. Регистр приводим к lower-case (id хранится как c01/predmet1).
    """
    if cell_value is None:
        return []
    s = str(cell_value).strip()
    if not s:
        return []
    # Заменим разделители на запятые и split.
    for ch in (";", "+", "/", "|", " "):
        s = s.replace(ch, ",")
    out: list[str] = []
    for tok in s.split(","):
        t = tok.strip().lower()
        if t:
            out.append(t)
    return out


def _find_ref_file(base_dir: Path, ref_id: str) -> Path | None:
    """Ищет файл вида `<ref_id>_<anything>.png` в указанной папке.
    Возвращает САМЫЙ СВЕЖИЙ (по mtime) — если у юзера несколько
    регенераций одного персонажа/предмета. None если ничего нет.
    """
    if not base_dir.is_dir():
        return None
    candidates: list[Path] = []
    for ext in ("png", "jpg", "jpeg", "webp"):
        candidates.extend(base_dir.glob(f"{ref_id}_*.{ext}"))
        # Бывают legacy-имена для hero: `hero_<N>_v1_<uuid>.png`. Для
        # ref_id="c01" это не подойдёт — но если юзер положил «c01.png»
        # без суффикса, тоже подберём.
        for p in base_dir.glob(f"{ref_id}.{ext}"):
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _hero_legacy_ref(project_data_dir: Path, persons_id: str) -> Path | None:
    """Fallback для старых проектов: реф персонажа c0X лежит как
    `hero_X_v1_<uuid>.png` (нумерация по old hero_index, X = int(ID[1:])).
    Возвращает самый свежий v1 — если c0X парсится в число.
    """
    if not persons_id.startswith("c"):
        return None
    try:
        idx = int(persons_id[1:])
    except ValueError:
        return None
    chars_dir = project_data_dir / "characters"
    if not chars_dir.is_dir():
        return None
    candidates = sorted(
        chars_dir.glob(f"hero_{idx}_v1_*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _load_refs_for_frame(
    project: Project, frame_number: int
) -> list[Path]:
    """Читает xlsx-ячейки R38 (персонажи) и R39 (предметы) для столбца
    кадра frame_number (1-based, column 2+frame_number в openpyxl, т.к.
    колонки 1-2 — заголовки).

    Возвращает список Path рефов (до 2 шт — outsee ограничение):
    первый — персонаж (если найден), второй — предмет. Если ячейка пуста
    или файлы не найдены — соответствующий ref не добавляется.
    """
    refs: list[Path] = []
    xlsx_path = (
        project.data_dir / "project.xlsx"
    )
    persons_ids: list[str] = []
    items_ids: list[str] = []
    if xlsx_path.exists():
        try:
            from openpyxl import load_workbook  # ленивый импорт
            wb = load_workbook(xlsx_path, data_only=True, read_only=True)
            if _XLSX_SHEET_PLAN in wb.sheetnames:
                ws = wb[_XLSX_SHEET_PLAN]
                # В v8 столбцы кадров — с 3 (1=label, 2=зарезервировано).
                col = frame_number + 2

                # Читаем ВСЕ три «persons» строки и сливаем с dedupe,
                # сохраняя порядок: row=8 (под кадр1) первой имеет
                # приоритет, потом 23, потом 38. Так юзер может вписать
                # id в ЛЮБУЮ из них.
                def _merged(rows: tuple[int, ...]) -> list[str]:
                    merged: list[str] = []
                    seen: set[str] = set()
                    for r in rows:
                        for x in _parse_ref_ids(
                            ws.cell(row=r, column=col).value
                        ):
                            if x not in seen:
                                seen.add(x)
                                merged.append(x)
                    return merged

                persons_ids = _merged(_XLSX_ROWS_PERSONS)
                items_ids = _merged(_XLSX_ROWS_ITEMS)
            wb.close()
        except ImportError:
            logger.warning(
                "openpyxl не установлен — не могу прочитать xlsx-рефы"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] frame {}: ошибка чтения xlsx-рефов: {}",
                project.id, frame_number, e,
            )

    chars_dir = project.data_dir / "characters"
    items_dir = project.data_dir / "items"

    # Персонажи — берём первый успешно найденный.
    for pid in persons_ids:
        f = _find_ref_file(chars_dir, pid) or _hero_legacy_ref(project.data_dir, pid)
        if f is not None:
            refs.append(f)
            logger.info(
                "[#{}] frame {} ref персонаж '{}' → {}",
                project.id, frame_number, pid, f,
            )
            break
        else:
            logger.warning(
                "[#{}] frame {} ref персонаж '{}' не найден в {}",
                project.id, frame_number, pid, chars_dir,
            )

    # Предметы — берём первый успешно найденный.
    for iid in items_ids:
        f = _find_ref_file(items_dir, iid)
        if f is not None:
            refs.append(f)
            logger.info(
                "[#{}] frame {} ref предмет '{}' → {}",
                project.id, frame_number, iid, f,
            )
            break
        else:
            logger.warning(
                "[#{}] frame {} ref предмет '{}' не найден в {}",
                project.id, frame_number, iid, items_dir,
            )

    # Постоянный продукт массового (если есть). Подставляем как
    # дополнительный ref, если в кадре остался свободный слот (< 2 рефов).
    # Outsee лимит — 2 ref'а на генерацию, поэтому если уже занято обоими
    # (char + item) — продукт не помещается, оставляем кадр без него.
    meta = getattr(project, "meta", None) or {}
    prod = meta.get("permanent_product") or {}
    prod_ref_path = prod.get("reference_image_path")
    if prod_ref_path and len(refs) < 2:
        prod_path = Path(prod_ref_path)
        if prod_path.exists():
            refs.append(prod_path)
            logger.info(
                "[#{}] frame {} ref продукт '{}' → {} (slot {})",
                project.id, frame_number,
                prod.get("name") or "?", prod_path, len(refs),
            )
        else:
            logger.warning(
                "[#{}] frame {}: продукт-референс {} не найден на диске",
                project.id, frame_number, prod_ref_path,
            )
    elif prod_ref_path and len(refs) >= 2:
        logger.warning(
            "[#{}] frame {}: у кадра уже 2 ref'а (char+item), продукт-референс "
            "не помещается — Outsee лимит. Кадр уйдёт без продукта.",
            project.id, frame_number,
        )

    return refs[:2]  # outsee лимит — 2 рефа на генерацию


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_images:
        return
    logger.info("[#{}] generate_images starting", project.id)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров — нечего генерировать")

    # Кадры без image_prompt: пробуем АВТО-РЕКАВЕРИ из xlsx (вдруг
    # промты ЕСТЬ в xlsx, но не подтянулись в БД), затем — если всё
    # ещё пусто — НЕ ВАЛИМ ВЕСЬ ШАГ, а помечаем такие кадры как
    # failed и продолжаем работать с теми, у кого промт есть.
    missing_prompts = [fr.number for fr in frames if not fr.image_prompt]
    if missing_prompts:
        proj_xlsx = project.data_dir / "project.xlsx"
        if proj_xlsx.exists():
            try:
                from app.services.xlsx_sync import reload_from_xlsx
                from app.services.xlsx_v8_import import import_v8_xlsx

                logger.info(
                    "[#{}] generate_images: missing image_prompt у {} кадров — "
                    "пробую авто-импорт из {}",
                    project.id, len(missing_prompts), proj_xlsx.name,
                )
                try:
                    await import_v8_xlsx(
                        session, project, proj_xlsx,
                        keep_fields=False, update_frames_voiceover=False,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("[#{}] v8 reload failed: {}", project.id, e)
                try:
                    await reload_from_xlsx(session, project, proj_xlsx)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[#{}] v7 reload failed: {}", project.id, e)
                await session.flush()
                frames = (
                    await session.execute(
                        select(Frame)
                        .where(Frame.project_id == project.id)
                        .order_by(Frame.number)
                    )
                ).scalars().all()
                missing_prompts = [
                    fr.number for fr in frames if not fr.image_prompt
                ]
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "[#{}] generate_images авто-импорт упал: {}",
                    project.id, e,
                )

        if missing_prompts:
            # Кадры без image_prompt — помечаем failed, остальные
            # продолжают обрабатываться. Юзер увидит failed-кадры в
            # TG-меню и сможет их перегенерить вручную (шаг 5 точечно
            # / правка xlsx и reload).
            logger.warning(
                "[#{}] generate_images: у {} кадров нет image_prompt "
                "ни в БД, ни в xlsx — помечаю как failed, продолжаю с "
                "остальными {} кадрами. Failed-frames: {}",
                project.id, len(missing_prompts),
                len(frames) - len(missing_prompts), missing_prompts,
            )
            failed_count = 0
            for fr in frames:
                if not fr.image_prompt and fr.status not in (
                    FrameStatus.image_approved,
                    FrameStatus.image_generated,
                    FrameStatus.failed,
                ):
                    fr.status = FrameStatus.failed
                    attrs = dict(fr.attrs or {})
                    attrs["fail_reason"] = "no_image_prompt"
                    fr.attrs = attrs
                    failed_count += 1
            await session.flush()
            try:
                await bot.send_message(
                    settings.telegram_owner_chat_id,
                    (
                        f"⚠️ Проект #{project.id}: у {failed_count} кадров "
                        f"({missing_prompts[:5]}{'...' if len(missing_prompts) > 5 else ''}) "
                        f"нет image_prompt. Помечены failed, остальные "
                        f"{len(frames) - failed_count} кадров пойдут в генерацию. "
                        "Чтобы догнать — либо впиши промты в xlsx и нажми "
                        "«🔄 Перечитать xlsx», либо запусти шаг 5 заново "
                        "(он перегенерит для всех кадров)."
                    )[:3800],
                )
            except Exception:  # noqa: BLE001
                pass

    out_dir = project.data_dir / "scenes"

    sheet = _sheet_for_project(project)
    try:
        sheet.ensure_frame_columns(len(frames))
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet ensure_frame_columns failed: {}", project.id, e)

    # Кадры, у которых нет картинки (статус не image_generated/image_approved)
    # → ставим в image_prompt_ready, чтобы цикл их подхватил.
    for fr in frames:
        if fr.status in (
            FrameStatus.image_approved,
            FrameStatus.failed,
            FrameStatus.image_generated,
        ):
            continue
        fr.status = FrameStatus.image_prompt_ready
    await session.flush()

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        # `gpt` нужен для GPT-rewrite внутри generate_image_with_retries —
        # после 3 неудачных попыток в outsee он попросит ChatGPT переписать
        # промт без триггеров модерации, потом ещё 3 попытки.
        gpt = ChatGPTBot(bs)
        try:
            while True:
                # 0) юзер нажал «⏹ Остановить» — кооперативно выходим.
                # Проверка между кадрами: текущий кадр (если уже в генерации)
                # доработается, но новый цикл не начнётся. Браузер не трогаем.
                raise_if_cancelled(project.id)

                # 1) подхватить HITL-решения, требующие перегенерации
                await _apply_pending_regens(session, project.id)

                # 2) взять следующий кадр к обработке
                target = await _next_frame_to_process(session, project.id)
                if target is not None:
                    await _generate_and_send(
                        session, bot, outsee, gpt, project, target, out_dir
                    )
                    continue

                # 3) все кадры обработаны? (approved / failed / image_generated)
                if await _all_frames_have_image_or_failed(session, project.id):
                    break

                # 4) иначе ждём пока пользователь нажмёт кнопку в TG
                await asyncio.sleep(3)
        except StepCancelledError as e:
            # ⏹ Остановить — статус уже откачен обработчиком кнопки в
            # другой сессии. Обновляем наш ORM-объект, чтобы worker'овый
            # commit() не перезаписал откат старым running-статусом.
            # НЕ ставим images_ready.
            logger.info("[#{}] generate_images: {} — выхожу из цикла",
                        project.id, e)
            try:
                await session.refresh(project)
            except Exception:  # noqa: BLE001
                logger.warning("[#{}] не смог refresh project после ⏹", project.id)
            return

    project.status = ProjectStatus.images_ready
    await session.flush()
    logger.info("[#{}] generate_images complete", project.id)


# ---------------------------------------------------------------------------


async def _next_frame_to_process(
    session: AsyncSession, project_id: int
) -> Frame | None:
    """Ищет первый кадр в статусе image_prompt_ready — т.е. «готов к outsee»."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    for fr in frames:
        if fr.status == FrameStatus.image_prompt_ready:
            return fr
    return None


async def _all_frames_have_image_or_failed(
    session: AsyncSession, project_id: int
) -> bool:
    """True если у каждого кадра картинка сгенерирована/одобрена или статус
    failed. В ручном режиме мы не ждём явного approve, но если пользователь
    нажал ✅ — это тоже считается."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    for fr in frames:
        if fr.status not in (
            FrameStatus.image_approved,
            FrameStatus.image_generated,
            FrameStatus.failed,
        ):
            return False
    return True


async def _apply_pending_regens(session: AsyncSession, project_id: int) -> None:
    """Находит HITL-решения regenerate/edit_prompt, которые ещё не
    «потреблены», возвращает соответствующие кадры в image_prompt_ready
    и помечает HITL как consumed."""
    hitls = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.project_id == project_id)
            .where(HITLRequest.kind == HITLKind.approve_images)
            .where(
                HITLRequest.decision.in_(
                    [HITLDecision.regenerate, HITLDecision.edit_prompt]
                )
            )
            .order_by(HITLRequest.id.desc())
        )
    ).scalars().all()
    for h in hitls:
        payload = dict(h.payload or {})
        if payload.get("consumed"):
            continue
        if h.frame_id is None:
            payload["consumed"] = True
            h.payload = payload
            continue
        frame = (
            await session.execute(select(Frame).where(Frame.id == h.frame_id))
        ).scalar_one_or_none()
        if frame is None:
            payload["consumed"] = True
            h.payload = payload
            continue
        # Возвращаем кадр в очередь на outsee. Выбор «Повторить» vs
        # заполнение промта делается в _generate_and_send на основе
        # последнего решения пользователя.
        frame.status = FrameStatus.image_prompt_ready
        payload["consumed"] = True
        h.payload = payload
        logger.info(
            "[#{}] frame {}: повторная генерация по решению '{}' (HITL #{})",
            project_id,
            frame.number,
            h.decision.value,
            h.id,
        )
    await session.flush()


# Точная формулировка от пользователя для ✏️ Изменить промт-флоу.
# Шлём ChatGPT'у: <оригинальный промт> + эта подпись + <текст из TG>.
_GPT_EDIT_PROMPT_META = (
    "измени промт что бы он удовлетворял сообщение ниже"
)

# Минимальная длина «осмысленного» rewrite — отсекает «ок», «готово» и
# прочие пустышки. То же значение что в outsee_retry._ask_gpt_to_rewrite.
_MIN_GPT_EDIT_REWRITE_LEN = 30


async def _gpt_rewrite_edited_prompt(
    session: AsyncSession,
    gpt: ChatGPTBot,
    project: Project,
    frame: Frame,
    hitl: HITLRequest,
    payload: dict,
    bot: Bot,
) -> None:
    """Просит ChatGPT улучшить image_prompt по правке юзера из TG.

    Контекст: юзер нажал ✏️ Изменить промт в HITL-карточке и написал
    в TG свой текст (что подкрутить). В bot.py этот текст уже сохранён
    в `frame.image_prompt` (как fallback) и в `payload`. Тут мы
    переписываем `frame.image_prompt` через GPT, чтобы:
      1) сохранить структуру/детали старого image_prompt;
      2) учесть правки юзера.

    Если GPT упал или вернул пустоту — оставляем frame.image_prompt
    как есть (т.е. сырой текст юзера) и помечаем gpt_rewrite_done=True,
    чтобы не зацикливать.
    """
    original_prompt = (payload.get("original_image_prompt") or "").strip()
    user_edit = (payload.get("edited_prompt") or frame.image_prompt or "").strip()
    if not original_prompt or not user_edit:
        # Нечего комбинировать — отдадим сырой текст в outsee.
        payload["gpt_rewrite_done"] = True
        hitl.payload = payload
        await session.flush()
        logger.warning(
            "[#{}] frame {}: GPT-rewrite пропущен — нет original_prompt "
            "({} симв) или user_edit ({} симв)",
            project.id, frame.number,
            len(original_prompt), len(user_edit),
        )
        return

    full_request = (
        f"{original_prompt}\n\n{_GPT_EDIT_PROMPT_META}\n\n{user_edit}"
    )
    logger.info(
        "[#{}] frame {}: ✏️ GPT-rewrite старт ({} симв original + "
        "{} симв правка)",
        project.id, frame.number,
        len(original_prompt), len(user_edit),
    )
    try:
        reply = await gpt.ask_fresh(full_request, timeout=900)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] frame {}: ChatGPT-rewrite упал ({}: {}) — fallback "
            "на сырой текст юзера",
            project.id, frame.number, type(e).__name__, e,
        )
        payload["gpt_rewrite_done"] = True
        payload["gpt_rewrite_error"] = f"{type(e).__name__}: {e}"[:500]
        hitl.payload = payload
        await session.flush()
        return

    text = (reply or "").strip()
    if len(text) < _MIN_GPT_EDIT_REWRITE_LEN:
        logger.warning(
            "[#{}] frame {}: ChatGPT-rewrite вернул слишком короткий "
            "ответ ({} симв) — fallback на сырой текст юзера",
            project.id, frame.number, len(text),
        )
        payload["gpt_rewrite_done"] = True
        payload["gpt_rewrite_short_reply"] = text[:200]
        hitl.payload = payload
        await session.flush()
        return

    # Успех — пишем переписанный промт в БД и xlsx.
    frame.image_prompt = text
    payload["gpt_rewrite_done"] = True
    payload["gpt_rewrite_result_len"] = len(text)
    hitl.payload = payload
    try:
        _sheet_for_project(project).write_frame(
            frame.number, image_prompt=text
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] frame {}: xlsx write_frame(image_prompt) failed: {}",
            project.id, frame.number, e,
        )
    await session.flush()
    logger.info(
        "[#{}] frame {}: ✏️ GPT-rewrite OK ({} симв → {} симв)",
        project.id, frame.number, len(user_edit), len(text),
    )
    # Информируем юзера в TG, что промт улучшен и сейчас идёт outsee.
    try:
        await bot.send_message(
            settings.telegram_owner_chat_id,
            (
                f"✏️ Кадр #{frame.number}: ChatGPT улучшил промт "
                f"({len(text)} симв). Запускаю outsee."
            ),
        )
    except Exception:  # noqa: BLE001
        pass


async def _generate_and_send(
    session: AsyncSession,
    bot: Bot,
    outsee: OutseeBot,
    gpt: ChatGPTBot,
    project: Project,
    frame: Frame,
    out_dir: Path,
) -> None:
    """Один прогон outsee → сохранение артефакта → HITL-карточка."""
    # Проверяем последний HITL: если последнее решение было regenerate —
    # используем кнопку «Повторить» (без перезаполнения промта); иначе —
    # обычная генерация с текущим image_prompt.
    last_hitl = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.frame_id == frame.id)
            .where(HITLRequest.kind == HITLKind.approve_images)
            .order_by(HITLRequest.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    use_regen_button = (
        last_hitl is not None
        and last_hitl.decision is HITLDecision.regenerate
    )

    # ✏️ Edit prompt: если юзер только что прислал в TG свой текст для
    # правки промта (HITLDecision.edit_prompt + needs_gpt_rewrite=True
    # в payload) — ДО outsee гоняем ChatGPT с meta-промтом:
    #
    #   <оригинальный image_prompt>
    #
    #   измени промт чтобы он удовлетворял сообщение ниже
    #
    #   <текст из TG-сообщения юзера>
    #
    # Ответ ChatGPT становится новым frame.image_prompt. Если GPT упал
    # или вернул пустоту — fallback на сырой текст юзера (он уже
    # записан в frame.image_prompt обработчиком в bot.py).
    if (
        last_hitl is not None
        and last_hitl.decision is HITLDecision.edit_prompt
    ):
        payload = dict(last_hitl.payload or {})
        if (
            payload.get("needs_gpt_rewrite")
            and not payload.get("gpt_rewrite_done")
        ):
            await _gpt_rewrite_edited_prompt(
                session, gpt, project, frame, last_hitl, payload, bot,
            )

    attempt = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.frame_id == frame.id)
            .where(HITLRequest.kind == HITLKind.approve_images)
        )
    ).scalars().all()
    attempt_number = len(attempt) + 1

    gen_id = uuid.uuid4().hex
    short_uuid = gen_id[:8]
    file_path = out_dir / f"frame_{frame.number:03d}_{short_uuid}.png"
    prompt_id_prefix = build_gen_id_prefix(project.id, frame.number, short_uuid)

    # Политика «без накопления вариантов в папке»: orphan-cleanup делается
    # ТОЛЬКО ПОСЛЕ успешной новой генерации (см. ниже после save).
    #
    # Раньше cleanup делался ПЕРЕД генерацией — это приводило к тому, что
    # если новая генерация падала (network/proxy/whatever), старый рабочий
    # файл уже был удалён → шаг видео не мог найти исходник кадра и падал
    # с WinError 2. Теперь старые файлы доживают до момента когда новый
    # уже сохранён и валиден.

    # Настройки картинки из проекта (с дефолтами).
    img_gen = IMAGE_GENERATORS_BY_ID.get(
        project.image_generator or DEFAULTS["image_generator"]
    )
    ar = ASPECT_RATIOS_BY_ID.get(
        project.aspect_ratio or DEFAULTS["aspect_ratio"]
    )
    ir = IMAGE_RESOLUTIONS_BY_ID.get(
        project.image_resolution or DEFAULTS["image_resolution"]
    )
    aspect_slug = ar.outsee_slug if ar else "9:16"
    model_slug = img_gen.outsee_slug if img_gen else None
    res_slug = ir.outsee_slug if ir else None
    logger.info(
        "[#{}] frame {} attempt {} gen_id={}: outsee {}",
        project.id,
        frame.number,
        attempt_number,
        gen_id[:8],
        "regenerate" if use_regen_button else "generate",
    )
    sheet = _sheet_for_project(project)
    try:
        sheet.write_frame(
            frame.number,
            image_gen_id=gen_id,
            attempt=attempt_number,
            frame_status="image_generating",
            last_error="",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] xlsx write_frame(gen_id) failed: {}", project.id, e)

    # Загружаем рефы (персонаж + предмет) из xlsx R38/R39 для этого кадра.
    # Передаются в outsee как 1-2 reference_image. На regenerate (если
    # кнопка «Повторить» используется) рефы не пересабмитятся — outsee
    # внутри регенерации работает на основе предыдущего state.
    refs: list[Path] = _load_refs_for_frame(project, frame.number)
    if refs:
        logger.info(
            "[#{}] frame {}: {} ref(ов) подгружено: {}",
            project.id, frame.number, len(refs), [str(r) for r in refs],
        )

    try:
        if use_regen_button:
            try:
                result = await outsee.regenerate_image(
                    file_path,
                    gen_id=gen_id,
                    prompt_id_prefix=prompt_id_prefix,
                )
            except OutseeImageError:
                # Если на странице нет предыдущего результата (или другая
                # «структурная» ошибка regenerate) — падаем на полноценный
                # generate с тем же gen_id, чтобы не плодить ложных файлов.
                logger.warning(
                    "[#{}] frame {}: «Повторить» не сработала — падаю на generate",
                    project.id,
                    frame.number,
                )
                result = await generate_image_with_retries(
                    outsee, gpt,
                    prompt=frame.image_prompt,
                    out_path=file_path,
                    max_attempts_per_prompt=3,
                    gpt_rewrite=True,
                    aspect_ratio=aspect_slug,
                    gen_id=gen_id,
                    model_slug=model_slug,
                    resolution=res_slug,
                    relax=bool(project.image_relax),
                    prompt_id_prefix=prompt_id_prefix,
                    reference_image=refs if refs else None,
                )
        else:
            # До 3 попыток с исходным image_prompt; если все 3 провалились —
            # GPT-rewrite промта (убирает триггеры модерации) + ещё 3 попытки.
            result = await generate_image_with_retries(
                outsee, gpt,
                prompt=frame.image_prompt,
                out_path=file_path,
                max_attempts_per_prompt=3,
                gpt_rewrite=True,
                aspect_ratio=aspect_slug,
                gen_id=gen_id,
                model_slug=model_slug,
                resolution=res_slug,
                relax=bool(project.image_relax),
                prompt_id_prefix=prompt_id_prefix,
                reference_image=refs if refs else None,
            )
    except OutseeImageError as e:
        # Не «возьму последнюю картинку», не silent retry: помечаем кадр
        # failed и шлём в TG понятное описание ошибки (с gen_id, baseline-ом
        # и тем что нашли). Пайплайн пойдёт к следующему кадру; общая логика
        # анти-зацикливания (MAX_FAIL=3) защитит проект целиком.
        logger.exception(
            "[#{}] frame {}: outsee fail (gen_id={})",
            project.id,
            frame.number,
            gen_id[:8],
        )
        frame.status = FrameStatus.failed
        try:
            sheet.write_frame(
                frame.number,
                frame_status=frame.status.value,
                last_error=e.format_text()[:1500],
            )
        except Exception:  # noqa: BLE001
            pass
        await session.flush()
        try:
            await bot.send_message(
                settings.telegram_owner_chat_id,
                (
                    f"⚠️ Кадр #{frame.number} проекта #{project.id}: "
                    f"картинку поймать не удалось.\n\n"
                    f"<pre>{_html_escape(e.format_text())}</pre>"
                )[:3800],
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            pass
        await session.commit()
        return

    # Orphan-cleanup СЕЙЧАС (после успешной генерации): удаляем все
    # старые frame_NNN_*.png кроме того что только что сохранили. Так
    # сохраняется политика «без накопления вариантов в папке», но при
    # этом если новая генерация упала бы выше — старый файл бы дожил.
    try:
        new_name = Path(str(result.file_path)).name
        for stale in out_dir.glob(f"frame_{frame.number:03d}_*.png"):
            if stale.name == new_name:
                continue
            try:
                stale.unlink()
                logger.info(
                    "[#{}] frame {}: удалил старый вариант {}",
                    project.id, frame.number, stale.name,
                )
            except OSError as e:
                logger.warning(
                    "[#{}] frame {}: не удалось удалить {}: {}",
                    project.id, frame.number, stale, e,
                )
    except OSError as e:
        logger.warning(
            "[#{}] frame {}: post-save glob scenes/ failed: {}",
            project.id, frame.number, e,
        )

    art = Artifact(
        project_id=project.id,
        frame_id=frame.id,
        kind=ArtifactKind.scene_image,
        uuid=uuid.uuid4().hex,
        path=str(result.file_path),
        meta={"gen_id": gen_id, "raw_url": result.raw_url or ""},
    )
    session.add(art)
    frame.status = FrameStatus.image_generated
    await session.flush()

    try:
        sheet.write_frame(
            frame.number,
            image_path=str(result.file_path),
            image_url=result.raw_url,
            frame_status=frame.status.value,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] xlsx write_frame(image_path) failed: {}", project.id, e)

    caption = (
        f"{prompt_id_prefix}\n"
        f"Кадр #{frame.number} / P{project.id}. Попытка {attempt_number}.\n"
        f"{(frame.voiceover_text or '')[:600]}"
    )
    await send_hitl_photo(
        bot,
        session,
        project,
        kind=HITLKind.approve_images,
        photo_path=str(result.file_path),
        caption=caption,
        payload={
            "step": "image",
            "frame_id": frame.id,
            "attempt": attempt_number,
            "gen_id": gen_id,
            "prompt_id_prefix": prompt_id_prefix,
            "photo_path": str(result.file_path),
        },
        frame_id=frame.id,
        allow_edit=True,
    )
    # Коммитим сразу, чтобы callback-хендлер в другом таске видел HITL.
    await session.commit()


def _html_escape(s: str) -> str:
    import html as _h

    return _h.escape(s)
