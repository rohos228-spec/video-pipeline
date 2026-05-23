"""Action Tracker — monkey-patch обёртки ключевых методов для записи:
  - время начала/конца каждого действия
  - параметры (промт, путь, генератор)
  - результат (успех/ошибка)
  - скриншот до и после (если browser_watcher активен)

Вызывается один раз: `patch_all(watcher)` — после этого все вызовы
ChatGPT.ask_fresh, OutseeBot.generate_image, advance_project и т.д.
автоматически логируются в events.jsonl.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable

from loguru import logger

from app.monitor.log_sink import emit_event

_watcher = None
_patched = False


def _truncate(s: str | None, max_len: int = 200) -> str:
    if s is None:
        return ""
    return s[:max_len] + ("…" if len(s) > max_len else "")


async def _screenshot_if_available(label: str) -> list[str]:
    if _watcher is not None:
        try:
            return await _watcher.take_screenshot_now(label=label)
        except BaseException:
            # Catch CancelledError (BaseException, not Exception, in Python 3.8+)
            # in addition to regular exceptions.  Screenshots are monitoring
            # artefacts and must never affect the pipeline: a CancelledError
            # raised here would otherwise escape into the finally block of
            # _wrap_async / _tracked_hero_run, replacing a successful return
            # value with an exception and causing the completed pipeline work
            # (e.g. an outsee image that was already saved to disk) to be lost.
            pass
    return []


def _wrap_async(
    cls: type,
    method_name: str,
    event_name: str,
    *,
    extract_params: Callable[..., dict] | None = None,
    screenshot_before: bool = False,
    screenshot_after: bool = False,
) -> None:
    """Оборачивает async-метод класса: добавляет emit_event до/после."""
    original = getattr(cls, method_name, None)
    if original is None:
        logger.debug("action_tracker: {}.{} не найден", cls.__name__, method_name)
        return

    @functools.wraps(original)
    async def wrapper(self, *args, **kwargs):
        params = {}
        if extract_params is not None:
            try:
                params = extract_params(self, *args, **kwargs)
            except Exception:
                pass

        emit_event(
            f"{event_name}_start",
            project_id=params.get("project_id"),
            step=params.get("step"),
            detail=params,
        )

        if screenshot_before:
            await _screenshot_if_available(f"{event_name}_before")

        t0 = time.monotonic()
        error_info = None
        result_info = {}
        try:
            result = await original(self, *args, **kwargs)
            try:
                if hasattr(result, "file_path"):
                    result_info["file_path"] = str(result.file_path)
                if hasattr(result, "raw_url"):
                    result_info["raw_url"] = _truncate(
                        getattr(result, "raw_url", None), 300
                    )
                if isinstance(result, str):
                    result_info["reply_len"] = len(result)
            except Exception:
                pass
            return result
        except Exception as e:
            error_info = {
                "error_type": type(e).__name__,
                "error_msg": _truncate(str(e), 500),
            }
            raise
        finally:
            duration = time.monotonic() - t0
            detail = {
                **params,
                "duration_s": round(duration, 2),
                **result_info,
            }
            if error_info:
                detail.update(error_info)

            emit_event(
                f"{event_name}_end",
                project_id=params.get("project_id"),
                step=params.get("step"),
                detail=detail,
            )

            if screenshot_after:
                await _screenshot_if_available(f"{event_name}_after")

    setattr(cls, method_name, wrapper)


def _extract_chatgpt_params(self, prompt: str, *a, **kw) -> dict:
    return {
        "prompt_len": len(prompt),
        "prompt_preview": _truncate(prompt, 150),
        "timeout": kw.get("timeout", 300),
    }


def _extract_outsee_generate_params(
    self, prompt, out_path, *a, **kw
) -> dict:
    return {
        "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        "prompt_preview": _truncate(prompt if isinstance(prompt, str) else "", 150),
        "out_path": str(out_path),
        "aspect_ratio": kw.get("aspect_ratio", "9:16"),
        "model_slug": kw.get("model_slug"),
        "resolution": kw.get("resolution"),
        "relax": kw.get("relax", False),
        "gen_id": kw.get("gen_id"),
        "prompt_id_prefix": kw.get("prompt_id_prefix"),
        "has_reference": kw.get("reference_image") is not None,
    }


def _extract_outsee_regen_params(self, out_path, *a, **kw) -> dict:
    return {
        "out_path": str(out_path),
        "gen_id": kw.get("gen_id"),
    }


def _extract_advance_params(session, project, bot, *a, **kw) -> dict:
    return {
        "project_id": getattr(project, "id", None),
        "step": getattr(project, "status", None)
        and project.status.value
        or "",
        "topic": _truncate(getattr(project, "topic", ""), 80),
    }


def _extract_hero_params(session, project, bot, *a, **kw) -> dict:
    return {
        "project_id": getattr(project, "id", None),
        "step": "generate_hero",
        "hero_mode": getattr(project, "hero_mode", ""),
        "hero_count": getattr(project, "hero_count", None),
    }


def _extract_images_params(session, project, bot, *a, **kw) -> dict:
    return {
        "project_id": getattr(project, "id", None),
        "step": "generate_images",
    }


def patch_all(watcher=None) -> None:
    """Навешивает трекинг на все ключевые методы.

    Вызывать ОДИН раз при старте монитора. Безопасно при повторном вызове.
    """
    global _watcher, _patched
    if _patched:
        return
    _watcher = watcher

    try:
        from app.bots.chatgpt import ChatGPTBot

        _wrap_async(
            ChatGPTBot,
            "ask_fresh",
            "chatgpt_ask_fresh",
            extract_params=_extract_chatgpt_params,
            screenshot_before=True,
            screenshot_after=True,
        )
        _wrap_async(
            ChatGPTBot,
            "ask",
            "chatgpt_ask",
            extract_params=_extract_chatgpt_params,
        )
        _wrap_async(
            ChatGPTBot,
            "ask_with_file",
            "chatgpt_ask_with_file",
            screenshot_before=True,
            screenshot_after=True,
        )
        _wrap_async(
            ChatGPTBot,
            "ask_with_files",
            "chatgpt_ask_with_files",
            screenshot_before=True,
            screenshot_after=True,
        )
        logger.info("action_tracker: ChatGPTBot patched")
    except Exception as e:
        logger.warning("action_tracker: ChatGPTBot patch failed: {}", e)

    try:
        from app.bots.outsee import OutseeBot

        _wrap_async(
            OutseeBot,
            "generate_image",
            "outsee_generate_image",
            extract_params=_extract_outsee_generate_params,
            screenshot_before=True,
            screenshot_after=True,
        )
        _wrap_async(
            OutseeBot,
            "regenerate_image",
            "outsee_regenerate_image",
            extract_params=_extract_outsee_regen_params,
            screenshot_before=True,
            screenshot_after=True,
        )
        _wrap_async(
            OutseeBot,
            "generate_video",
            "outsee_generate_video",
            screenshot_before=True,
            screenshot_after=True,
        )
        logger.info("action_tracker: OutseeBot patched")
    except Exception as e:
        logger.warning("action_tracker: OutseeBot patch failed: {}", e)

    try:
        import app.orchestrator.pipeline as pipeline_mod
        from app.orchestrator.pipeline import advance_project

        @functools.wraps(advance_project)
        async def _tracked_advance(session, project, bot, *a, **kw):
            params = _extract_advance_params(session, project, bot)
            emit_event(
                "advance_project_start",
                project_id=params.get("project_id"),
                step=params.get("step"),
                detail=params,
            )
            t0 = time.monotonic()
            error_info = None
            try:
                result = await advance_project(session, project, bot, *a, **kw)
                return result
            except Exception as e:
                error_info = {
                    "error_type": type(e).__name__,
                    "error_msg": _truncate(str(e), 500),
                }
                raise
            finally:
                duration = time.monotonic() - t0
                new_status = (
                    getattr(project, "status", None)
                    and project.status.value
                    or ""
                )
                detail = {
                    **params,
                    "duration_s": round(duration, 2),
                    "new_status": new_status,
                }
                if error_info:
                    detail.update(error_info)
                emit_event(
                    "advance_project_end",
                    project_id=params.get("project_id"),
                    step=params.get("step"),
                    detail=detail,
                )

        pipeline_mod.advance_project = _tracked_advance
        logger.info("action_tracker: advance_project patched")
    except Exception as e:
        logger.warning("action_tracker: advance_project patch failed: {}", e)

    try:
        from app.orchestrator.steps import generate_hero

        _wrap_async(
            type("_module", (), {"run": staticmethod(generate_hero.run)}),
            "run",
            "generate_hero",
            extract_params=_extract_hero_params,
            screenshot_before=True,
            screenshot_after=True,
        )
        original_hero_run = generate_hero.run

        @functools.wraps(original_hero_run)
        async def _tracked_hero_run(session, project, bot, *a, **kw):
            params = _extract_hero_params(session, project, bot)
            emit_event("generate_hero_start", project_id=params.get("project_id"), step="generate_hero", detail=params)
            if _watcher:
                await _screenshot_if_available("hero_before")
            t0 = time.monotonic()
            error_info = None
            try:
                return await original_hero_run(session, project, bot, *a, **kw)
            except Exception as e:
                error_info = {"error_type": type(e).__name__, "error_msg": _truncate(str(e), 500)}
                raise
            finally:
                duration = time.monotonic() - t0
                detail = {**params, "duration_s": round(duration, 2)}
                if error_info:
                    detail.update(error_info)
                emit_event("generate_hero_end", project_id=params.get("project_id"), step="generate_hero", detail=detail)
                if _watcher:
                    await _screenshot_if_available("hero_after")

        generate_hero.run = _tracked_hero_run
        logger.info("action_tracker: generate_hero.run patched")
    except Exception as e:
        logger.warning("action_tracker: generate_hero patch failed: {}", e)

    _patched = True
    logger.info("action_tracker: все обёртки установлены")
