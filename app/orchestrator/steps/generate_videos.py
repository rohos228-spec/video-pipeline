"""Шаг 9: per-frame генерация 8-сек клипов в outsee + per-video HITL.

Алгоритм аналогичен `generate_images.py` — НЕ блокируется на ожидании
approve пользователя:

  1. Берёт следующий кадр в статусе `animation_prompt_ready` (готов к outsee).
  2. Генерит видео в outsee (использует scene_image как стартовый кадр),
     сохраняет mp4, шлёт в TG карточку с кнопками
     ✅ Одобрить / 🔁 Перегенерировать / ✏️ Изменить промт / ❌ Отклонить —
     но НЕ ждёт решения, идёт к следующему кадру.
  3. Когда все кадры «выпущены» в TG, loop ждёт пока каждый
     не станет либо `video_generated` / `video_approved` (есть mp4),
     либо `failed` (юзер отклонил или генерация упала окончательно).
     Параллельно обрабатывает 🔁 / ✏️ — возвращает кадр обратно в
     `animation_prompt_ready` и запускает новый outsee-проход.

Пока юзер думает над кадром N, бот уже может рендерить кадр N+1 — outsee
физически рендерит по одному, но «карточку с кнопками» бот не ждёт.

Вход:  ProjectStatus.generating_videos
Выход: ProjectStatus.videos_ready
"""

from __future__ import annotations

import asyncio
import contextlib
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
    VIDEO_GENERATORS_BY_ID,
    VIDEO_RESOLUTIONS_BY_ID,
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
from app.services.hitl import send_hitl_video
from app.services.outsee_retry import generate_video_with_retries
from app.services.step_cancel import (
    StepCancelledError,
    is_stop_requested,
    raise_if_cancelled,
)
from app.settings import settings
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_videos:
        return
    logger.info("[#{}] generate_videos starting", project.id)

    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        project.status = ProjectStatus.videos_ready
        await session.flush()
        logger.info("[#{}] generate_videos: нет кадров", project.id)
        return

    out_dir = project.data_dir / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Настройки видео из проекта (с дефолтами).
    vg = VIDEO_GENERATORS_BY_ID.get(
        project.video_generator or DEFAULTS["video_generator"]
    )
    vr_o = VIDEO_RESOLUTIONS_BY_ID.get(
        project.video_resolution or DEFAULTS["video_resolution"]
    )
    ar = ASPECT_RATIOS_BY_ID.get(
        project.aspect_ratio or DEFAULTS["aspect_ratio"]
    )
    video_model_slug = vg.outsee_slug if vg else None
    video_res_slug = vr_o.outsee_slug if vr_o else None
    aspect_slug = ar.outsee_slug if ar else "9:16"

    # Подтягиваем «висящие» кадры в очередь:
    # - те что НЕ в терминальном (video_generated/video_approved/failed/done)
    # - имеют animation_prompt
    # → ставим в animation_prompt_ready
    # У кого animation_prompt пустой — failed, пусть юзер запускает шаг 8.
    for fr in frames:
        if fr.status in (
            FrameStatus.video_generated,
            FrameStatus.video_approved,
            FrameStatus.failed,
            FrameStatus.done,
        ):
            continue
        if not fr.animation_prompt:
            attrs = dict(fr.attrs or {})
            attrs["fail_reason"] = "no_animation_prompt"
            fr.attrs = attrs
            fr.status = FrameStatus.failed
            logger.warning(
                "[#{}] frame {}: animation_prompt пуст — помечаю failed",
                project.id, fr.number,
            )
            continue
        fr.status = FrameStatus.animation_prompt_ready
    await session.flush()

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        # `gpt` — для GPT-rewrite внутри generate_video_with_retries и
        # для ✏️ Изменить промт.
        gpt = ChatGPTBot(bs)
        try:
            while True:
                # 0) ⏹ Остановить — кооперативно выходим.
                raise_if_cancelled(project.id)

                # 1) Подхватить HITL-решения 🔁 / ✏️ — вернуть кадр в очередь.
                await _apply_pending_regens(session, project.id)

                # 2) Взять следующий кадр к обработке.
                target = await _next_frame_to_process(session, project.id)
                if target is not None:
                    await _generate_and_send(
                        session, bot, outsee, gpt, project, target, out_dir,
                        video_model_slug=video_model_slug,
                        video_res_slug=video_res_slug,
                        aspect_slug=aspect_slug,
                    )
                    continue

                # 3) Все кадры обработаны? (video_generated/approved/failed)
                if await _all_frames_have_video_or_failed(session, project.id):
                    break

                # 4) Иначе ждём пока пользователь нажмёт кнопку в TG.
                await asyncio.sleep(3)
        except StepCancelledError as e:
            logger.info(
                "[#{}] generate_videos: {} — выхожу из цикла",
                project.id, e,
            )
            try:
                await session.refresh(project)
            except Exception:  # noqa: BLE001
                logger.warning("[#{}] не смог refresh project после ⏹", project.id)
            return

    project.status = ProjectStatus.videos_ready
    await session.flush()
    logger.info("[#{}] generate_videos complete", project.id)


# ---------------------------------------------------------------------------


async def _next_frame_to_process(
    session: AsyncSession, project_id: int
) -> Frame | None:
    """Ищет первый кадр в статусе animation_prompt_ready (готов к outsee video)."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    for fr in frames:
        if fr.status == FrameStatus.animation_prompt_ready:
            return fr
    return None


async def _all_frames_have_video_or_failed(
    session: AsyncSession, project_id: int
) -> bool:
    """True если у каждого кадра либо есть клип, либо frame.status=failed.

    Approve явно не требуется — наличие mp4 (статус video_generated) уже
    означает, что кадр годен для следующего шага (юзер может ещё нажать
    🔁 / ✏️ — это вернёт кадр в очередь и цикл пойдёт ещё раз). Если
    юзер нажал ❌ — frame.status = failed, composition его пропустит.
    """
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    for fr in frames:
        if fr.status not in (
            FrameStatus.video_approved,
            FrameStatus.video_generated,
            FrameStatus.failed,
        ):
            return False
    return True


async def _apply_pending_regens(session: AsyncSession, project_id: int) -> None:
    """HITL-решения 🔁 / ✏️ / ❌ по видео, которые ещё не «потреблены».

    - 🔁 regenerate / ✏️ edit_prompt → кадр возвращается в
      `animation_prompt_ready` и попадёт в следующую итерацию outsee.
    - ❌ rejected → кадр становится `failed`. Composition его пропустит
      (политика «отказался — не вставляем в финальную сборку»).
    Файл клипа уже удалён callback-хендлером (`delete_hitl_artifact_file`).
    """
    hitls = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.project_id == project_id)
            .where(HITLRequest.kind == HITLKind.approve_videos)
            .where(
                HITLRequest.decision.in_(
                    [
                        HITLDecision.regenerate,
                        HITLDecision.edit_prompt,
                        HITLDecision.rejected,
                    ]
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
        if h.decision is HITLDecision.rejected:
            # ❌ — кадр выкидываем из финальной сборки.
            frame.status = FrameStatus.failed
        else:
            # 🔁 / ✏️ — кадр возвращается в очередь на outsee.
            frame.status = FrameStatus.animation_prompt_ready
        payload["consumed"] = True
        h.payload = payload
        logger.info(
            "[#{}] frame {}: HITL '{}' (#{}) → status={}",
            project_id, frame.number, h.decision.value, h.id,
            frame.status.value,
        )
    await session.flush()


# Точная формулировка для ✏️ Изменить промт-флоу (видео).
# Шлём ChatGPT'у: <оригинальный animation_prompt> + эта подпись + <текст из TG>.
_GPT_EDIT_PROMPT_META = (
    "измени промт что бы он удовлетворял сообщение ниже"
)

# Минимальная длина «осмысленного» rewrite — отсекает «ок», «готово» и
# прочие пустышки. То же значение что в outsee_retry / generate_images.
_MIN_GPT_EDIT_REWRITE_LEN = 30


async def _gpt_rewrite_edited_animation_prompt(
    session: AsyncSession,
    gpt: ChatGPTBot,
    project: Project,
    frame: Frame,
    hitl: HITLRequest,
    payload: dict,
    bot: Bot,
) -> None:
    """Просит ChatGPT улучшить animation_prompt по правке юзера из TG.

    Контекст: юзер нажал ✏️ Изменить промт в видео-HITL и написал
    в TG свой текст (что подкрутить). В bot.py этот текст уже сохранён
    в `frame.animation_prompt` (как fallback) и в `payload`. Тут мы
    переписываем `frame.animation_prompt` через GPT, чтобы:
      1) сохранить структуру/детали старого animation_prompt;
      2) учесть правки юзера.

    Если GPT упал или вернул пустоту — оставляем frame.animation_prompt
    как есть (т.е. сырой текст юзера) и помечаем gpt_rewrite_done=True,
    чтобы не зацикливать.
    """
    original_prompt = (payload.get("original_animation_prompt") or "").strip()
    user_edit = (
        payload.get("edited_prompt") or frame.animation_prompt or ""
    ).strip()
    if not original_prompt or not user_edit:
        payload["gpt_rewrite_done"] = True
        hitl.payload = payload
        await session.flush()
        logger.warning(
            "[#{}] frame {}: GPT-rewrite (video) пропущен — нет original "
            "({} симв) или user_edit ({} симв)",
            project.id, frame.number,
            len(original_prompt), len(user_edit),
        )
        return

    full_request = (
        f"{original_prompt}\n\n{_GPT_EDIT_PROMPT_META}\n\n{user_edit}"
    )
    logger.info(
        "[#{}] frame {}: ✏️ GPT-rewrite animation_prompt старт "
        "({} симв original + {} симв правка)",
        project.id, frame.number,
        len(original_prompt), len(user_edit),
    )
    try:
        reply = await gpt.ask_fresh(full_request, timeout=900)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] frame {}: ChatGPT-rewrite (video) упал ({}: {}) — "
            "fallback на сырой текст юзера",
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
            "[#{}] frame {}: ChatGPT-rewrite (video) вернул слишком короткий "
            "ответ ({} симв) — fallback на сырой текст юзера",
            project.id, frame.number, len(text),
        )
        payload["gpt_rewrite_done"] = True
        payload["gpt_rewrite_short_reply"] = text[:200]
        hitl.payload = payload
        await session.flush()
        return

    # Успех — пишем переписанный промт в БД и xlsx.
    frame.animation_prompt = text
    payload["gpt_rewrite_done"] = True
    payload["gpt_rewrite_result_len"] = len(text)
    hitl.payload = payload
    try:
        _sheet_for_project(project).write_frame(
            frame.number, animation_prompt=text,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] frame {}: xlsx write_frame(animation_prompt) failed: {}",
            project.id, frame.number, e,
        )
    await session.flush()
    logger.info(
        "[#{}] frame {}: ✏️ GPT-rewrite animation_prompt OK ({} → {} симв)",
        project.id, frame.number, len(user_edit), len(text),
    )
    with contextlib.suppress(Exception):
        await bot.send_message(
            settings.telegram_owner_chat_id,
            (
                f"✏️ Кадр #{frame.number}: ChatGPT улучшил animation_prompt "
                f"({len(text)} симв). Запускаю outsee video."
            ),
        )


async def _generate_and_send(
    session: AsyncSession,
    bot: Bot,
    outsee: OutseeBot,
    gpt: ChatGPTBot,
    project: Project,
    frame: Frame,
    out_dir: Path,
    *,
    video_model_slug: str | None,
    video_res_slug: str | None,
    aspect_slug: str,
) -> None:
    """Один прогон outsee video → сохранение артефакта → HITL-карточка."""
    # 1) Найдём картинку этого кадра (scene_image) на диске.
    # Берём самый свежий Artifact, который ЕЩЁ ЖИВ на диске — старые
    # могут быть удалены orphan-cleanup'ом из шага картинок (regen с тем
    # же frame.number ⇒ старый файл удалён).
    imgs = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.frame_id == frame.id,
                Artifact.kind == ArtifactKind.scene_image,
            )
            .order_by(Artifact.id.desc())
        )
    ).scalars().all()
    start_frame_path: Path | None = None
    for cand in imgs:
        cand_path = Path(cand.path)
        if cand_path.is_file():
            start_frame_path = cand_path
            break

    if start_frame_path is None:
        msg_txt = (
            f"⚠️ Кадр #{frame.number} проекта #{project.id}: "
            f"картинка-источник для видео не найдена на диске "
            f"(scene_image artifacts: {len(imgs)}). "
            f"Перегенерируй картинку этого кадра, потом перезапусти шаг видео."
        )
        logger.warning(
            "[#{}] frame {}: scene_image file missing on disk "
            "(artifacts={}), помечаю failed",
            project.id, frame.number,
            [str(a.path) for a in imgs],
        )
        frame.status = FrameStatus.failed
        await session.flush()
        try:
            await bot.send_message(
                settings.telegram_owner_chat_id, msg_txt[:3800],
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "[#{}] frame {}: не смог отправить TG-уведомление",
                project.id, frame.number,
            )
        await session.commit()
        return

    # 2) ✏️ Edit prompt: если последний HITL по этому кадру был edit_prompt
    # с `needs_gpt_rewrite=True` — ДО outsee гоняем ChatGPT по meta-промту.
    last_hitl = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.frame_id == frame.id)
            .where(HITLRequest.kind == HITLKind.approve_videos)
            .order_by(HITLRequest.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        last_hitl is not None
        and last_hitl.decision is HITLDecision.edit_prompt
    ):
        payload = dict(last_hitl.payload or {})
        if (
            payload.get("needs_gpt_rewrite")
            and not payload.get("gpt_rewrite_done")
        ):
            await _gpt_rewrite_edited_animation_prompt(
                session, gpt, project, frame, last_hitl, payload, bot,
            )

    # 3) Готовим имя файла и параметры.
    attempt = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.frame_id == frame.id)
            .where(HITLRequest.kind == HITLKind.approve_videos)
        )
    ).scalars().all()
    attempt_number = len(attempt) + 1

    # Relax по словам пользователя поддерживает только veo-3-1-fast.
    # Для остальных моделей даже если флаг True — _toggle_relax тихо
    # ничего не сделает (кнопки нет).
    video_relax = bool(project.video_relax) and (
        project.video_generator == "veo_3_1_fast"
    )

    # КЛЮЧЕВОЕ: на КАЖДОЙ попытке (всего до 6: 3 original + 3 после
    # GPT-rewrite) генерим НОВЫЙ short_uuid → НОВЫЙ prompt_id_prefix
    # и НОВЫЙ путь файла. Иначе после первой провальной попытки в
    # outsee остаётся карточка-«Ошибка» с тем же `[ID:]`, и
    # `_video_error_tile_for_prompt_id` ловит ЕЁ ЖЕ на следующей
    # попытке — все ретраи становятся no-op'ом.
    _attempt_state: dict[int, tuple[str, Path]] = {}

    def _ensure_state(attempt_no: int) -> tuple[str, Path]:
        if attempt_no not in _attempt_state:
            new_uuid = uuid.uuid4().hex[:8]
            new_prefix = build_gen_id_prefix(
                project.id, frame.number, new_uuid,
            )
            new_path = out_dir / f"clip_{frame.number:03d}_{new_uuid}.mp4"
            _attempt_state[attempt_no] = (new_prefix, new_path)
            logger.info(
                "[#{}] frame {} video попытка {} gen_id={}: "
                "новый prompt_id={}",
                project.id, frame.number, attempt_no, new_uuid, new_prefix,
            )
        return _attempt_state[attempt_no]

    def _make_prefix(attempt_no: int) -> str:
        return _ensure_state(attempt_no)[0]

    def _make_out_path(attempt_no: int) -> Path:
        return _ensure_state(attempt_no)[1]

    # Префикс для caption/payload — берём первый attempt (он же
    # used-prefix самого первого «оригинального» прогона). После
    # успеха обновим на тот, что реально дал результат.
    prompt_id_prefix = build_gen_id_prefix(
        project.id, frame.number, uuid.uuid4().hex[:8],
    )  # fallback, перекроется при первом factory call

    logger.info(
        "[#{}] frame {} video attempt {}: запускаю outsee generate_video "
        "(до 6 попыток: 3 original + GPT-rewrite + 3 rewritten)",
        project.id, frame.number, attempt_number,
    )

    # 4) Сам outsee-прогон с retry/GPT-rewrite.
    # cancel_check — даёт outsee реагировать на ⏹ Стоп шаг ВНУТРИ
    # длинного ожидания результата (75-120 сек/ролик). Без этого юзер
    # ждёт окончания текущего кадра до того, как цикл сверху увидит
    # `raise_if_cancelled`.
    _pid_for_cancel = project.id

    def _cancel_check() -> bool:
        return is_stop_requested(_pid_for_cancel)

    try:
        result = await generate_video_with_retries(
            outsee, gpt,
            prompt=frame.animation_prompt,
            out_path_factory=_make_out_path,
            prompt_id_prefix_factory=_make_prefix,
            max_attempts_per_prompt=3,
            gpt_rewrite=True,
            start_frame=start_frame_path,
            aspect_ratio=aspect_slug,
            # 600 сек — даём outsee 10 минут на ролик. Если за это
            # время не пришёл результат или плашка «Ошибка» в карточке
            # с нашим [ID:] — _wait_video_url raise OutseeImageError,
            # кадр помечается failed и юзер увидит TG-уведомление
            # (та же логика, что у картинок).
            timeout=600,
            model_slug=video_model_slug,
            resolution=video_res_slug,
            relax=video_relax,
            cancel_check=_cancel_check,
        )
    except OutseeImageError as e:
        # «cancelled by user via ⏹» из _wait_video_url пробрасываем
        # как StepCancelledError, чтобы шаг закрылся как «остановлен
        # пользователем», а не как «упал».
        if "cancelled by user" in (e.reason or "").lower():
            raise StepCancelledError(
                f"проект #{project.id}: остановка по запросу пользователя "
                "(cancel внутри outsee wait)"
            ) from e
        # Не silent retry: помечаем кадр failed и шлём в TG понятное
        # описание ошибки. Пайплайн пойдёт к следующему кадру.
        # Используем prompt_id_prefix последней попытки (если уже было
        # хоть одно обращение к factory).
        last_prefix = (
            _attempt_state[max(_attempt_state)][0]
            if _attempt_state else prompt_id_prefix
        )
        logger.exception(
            "[#{}] frame {} video: все попытки провалились "
            "(last gen_id={})",
            project.id, frame.number, last_prefix,
        )
        frame.status = FrameStatus.failed
        await session.flush()
        with contextlib.suppress(Exception):
            await bot.send_message(
                settings.telegram_owner_chat_id,
                (
                    f"⚠️ Кадр #{frame.number} проекта #{project.id}: "
                    f"видео поймать не удалось за 6 попыток "
                    f"(3 original + GPT-rewrite + 3 rewritten).\n\n"
                    f"<pre>{_html_escape(e.format_text())}</pre>"
                )[:3800],
                parse_mode="HTML",
            )
        await session.commit()
        return

    # Найдём prompt_id_prefix, который привёл к успешному результату
    # (по совпадению file_path в state). Иначе оставим fallback.
    result_path_str = str(result.file_path)
    for pref, path in _attempt_state.values():
        if str(path) == result_path_str:
            prompt_id_prefix = pref
            break

    # 5) Orphan-cleanup: удалить старые clip_NNN_*.mp4 кроме только что
    # сохранённого. Политика «без накопления вариантов в папке».
    try:
        new_name = Path(str(result.file_path)).name
        for stale in out_dir.glob(f"clip_{frame.number:03d}_*.mp4"):
            if stale.name == new_name:
                continue
            try:
                stale.unlink()
                logger.info(
                    "[#{}] frame {}: удалил старый клип {}",
                    project.id, frame.number, stale.name,
                )
            except OSError as e:
                logger.warning(
                    "[#{}] frame {}: не удалось удалить {}: {}",
                    project.id, frame.number, stale, e,
                )
    except OSError as e:
        logger.warning(
            "[#{}] frame {}: post-save glob videos/ failed: {}",
            project.id, frame.number, e,
        )

    # 6) Сохраняем Artifact + статус кадра.
    art = Artifact(
        project_id=project.id,
        frame_id=frame.id,
        kind=ArtifactKind.scene_video,
        uuid=uuid.uuid4().hex,
        path=str(result.file_path),
    )
    session.add(art)
    frame.status = FrameStatus.video_generated
    await session.flush()
    logger.info(
        "[#{}] frame {} video saved: {}",
        project.id, frame.number, result.file_path,
    )

    # 7) HITL-карточка на это видео — ✅ / 🔁 / ✏️ / ❌, не блокируем
    # цикл генерации других кадров.
    caption = (
        f"{prompt_id_prefix}\n"
        f"Кадр #{frame.number} / P{project.id}. Попытка {attempt_number}.\n"
        f"{(frame.voiceover_text or '')[:600]}"
    )
    await send_hitl_video(
        bot,
        session,
        project,
        kind=HITLKind.approve_videos,
        video_path=str(result.file_path),
        caption=caption,
        payload={
            "step": "video",
            "frame_id": frame.id,
            "attempt": attempt_number,
            "prompt_id_prefix": prompt_id_prefix,
            "video_path": str(result.file_path),
        },
        frame_id=frame.id,
        allow_edit=True,
    )
    # Коммитим сразу, чтобы callback-хендлер в другом таске видел HITL.
    await session.commit()


def _html_escape(s: str) -> str:
    import html as _h

    return _h.escape(s)
