"""Прямой HTTP REST API клиент ElevenLabs для синтеза речи (TTS).

Заменяет устаревший Playwright/Chrome-кликер. Вызывает официальный
эндпоинт POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}
с авторизацией через заголовок xi-api-key и сохраняет MP3 напрямую.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.services import media_spend, spend_guard
from app.services.elevenlabs_voices import DEFAULT_ELEVENLABS_VOICE_ID
from app.settings import settings

ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsApiError(RuntimeError):
    """Ошибка вызова ElevenLabs API."""


class ElevenLabsUncertainError(ElevenLabsApiError):
    pass


def _blocked_as_eleven(blocked: spend_guard.PaidCallBlocked) -> "ElevenLabsApiError":
    err = ElevenLabsApiError(blocked.message_ru)
    err.context = dict(blocked.context)
    return err


_UNCERTAIN_CAUSES = (httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError)
_PRE_SEND_CAUSES = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError, httpx.PoolTimeout)

MAX_POSTS_PER_SEGMENT = 2
_UNCERTAIN_PREFIX = ".tts_uncertain_"
_SIDECAR_SUFFIX = ".tts.json"
_run_budget: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "elevenlabs_tts_run_budget", default=None)


@contextlib.contextmanager
def tts_run_budget(limit: int | None = None):
    if limit is None:
        limit = int(getattr(settings, "audio_max_tts_posts_per_run", 60) or 0)
    token = _run_budget.set([0, int(limit)])
    try:
        yield _run_budget.get()
    finally:
        _run_budget.reset(token)


def _reserve_post() -> None:
    budget = _run_budget.get()
    if budget is None:
        return
    if 0 < budget[1] <= budget[0]:
        raise ElevenLabsApiError(
            f"11Labs API: достигнут потолок TTS POST за запуск ({budget[1]}, AUDIO_MAX_TTS_POSTS_PER_RUN)"
        )
    budget[0] += 1


def _request_key(voice_id: str, model: str, payload: dict[str, Any], clean_text: str) -> str:
    raw = json.dumps([voice_id, model, payload.get("voice_settings"), clean_text], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_sidecar(mp3: Path, key: str) -> None:
    try:
        mp3.with_name(mp3.name + _SIDECAR_SUFFIX).write_text(json.dumps({"key": key}), encoding="utf-8")
    except OSError:
        pass


def _find_cached(directory: Path, key: str) -> Path | None:
    try:
        for side in directory.glob("*" + _SIDECAR_SUFFIX):
            try:
                if json.loads(side.read_text(encoding="utf-8")).get("key") != key:
                    continue
            except (OSError, ValueError, AttributeError):
                continue
            mp3 = side.with_name(side.name[: -len(_SIDECAR_SUFFIX)])
            if mp3.is_file() and mp3.stat().st_size >= 1000:
                return mp3
    except OSError:
        pass
    return None


_SFX_SIDECAR_SUFFIX = ".sfx.json"
SFX_PROMPT_INFLUENCE = 0.5
SFX_MAX_PROMPT_CHARS = 450


def sfx_request_key(prompt: str, duration: float) -> str:
    raw = json.dumps(["sfx", (prompt or "")[:SFX_MAX_PROMPT_CHARS],
                      round(min(max(float(duration), 0.5), 22.0), 3), SFX_PROMPT_INFLUENCE], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sfx_write_sidecar(sound: Path, key: str) -> None:
    try:
        sound.with_name(sound.name + _SFX_SIDECAR_SUFFIX).write_text(json.dumps({"key": key}), encoding="utf-8")
    except OSError:
        pass


def sfx_find_cached(directory: Path, key: str) -> Path | None:
    try:
        for side in directory.glob("*" + _SFX_SIDECAR_SUFFIX):
            try:
                if json.loads(side.read_text(encoding="utf-8")).get("key") != key:
                    continue
            except (OSError, ValueError, AttributeError):
                continue
            sound = side.with_name(side.name[: -len(_SFX_SIDECAR_SUFFIX)])
            if sound.is_file() and sound.stat().st_size > 0:
                return sound
    except OSError:
        pass
    return None


def elevenlabs_api_configured() -> bool:
    key = getattr(settings, "elevenlabs_api_key", None) or os.environ.get("ELEVENLABS_API_KEY", "")
    return bool(key and str(key).strip())


def _resolve_api_key() -> str:
    key = getattr(settings, "elevenlabs_api_key", None) or os.environ.get("ELEVENLABS_API_KEY", "")
    k = str(key or "").strip()
    if not k:
        raise ElevenLabsApiError(
            "Не задан ELEVENLABS_API_KEY в .env — укажите ключ для генерации озвучки."
        )
    return k


def _resolve_proxy_url() -> str | None:
    proxy = getattr(settings, "elevenlabs_proxy_url", None) or os.environ.get("ELEVENLABS_PROXY_URL", "")
    p = str(proxy or "").strip()
    return p or None


import re

_YEAR_CASES = {
    "2050": "две тысячи пятидесятый",
    "2030": "две тысячи тридцатый",
    "2025": "две тысячи двадцать пятый",
    "2024": "две тысячи двадцать четвёртый",
}


def normalize_text_for_tts(text: str) -> str:
    """Подготавливает текст для максимально естественной озвучки диктором TTS.

    Преобразует типичные цифровые конструкции, годы и проценты в слова.
    """
    if not text:
        return ""
    t = text
    # Обработка годов в падежах с сохранением регистра
    t = re.sub(
        r"\b([Вв])\s+2050(\s*году|\s*-?м\s*году|\s*-?м)\b",
        lambda m: f"{m.group(1)} две тысячи пятидесятом году",
        t,
    )
    t = re.sub(
        r"\b([Вв])\s+2050\b",
        lambda m: f"{m.group(1)} две тысячи пятидесятом",
        t,
    )
    t = re.sub(
        r"\b([Кк])\s+2050(\s*году|\s*-?му)?\b",
        lambda m: f"{m.group(1)} две тысячи пятидесятому году",
        t,
    )
    t = re.sub(
        r"\b([Дд])о\s+2050(\s*года)?\b",
        lambda m: f"{m.group(1)}о две тысячи пятидесятого года",
        t,
    )
    t = re.sub(
        r"\b2050(\s*год[а-я]*|\s*-?й)?\b",
        "две тысячи пятидесятый год",
        t,
        flags=re.IGNORECASE,
    )

    t = re.sub(
        r"\b([Вв])\s+2030(\s*году|\s*-?м\s*году|\s*-?м)\b",
        lambda m: f"{m.group(1)} две тысячи тридцатом году",
        t,
    )
    t = re.sub(
        r"\b2030(\s*год[а-я]*|\s*-?й)?\b",
        "две тысячи тридцатый год",
        t,
        flags=re.IGNORECASE,
    )

    # Проценты
    t = re.sub(r"\b100\s*%", "сто процентов", t)
    t = re.sub(r"\b50\s*%", "пятьдесят процентов", t)
    t = re.sub(r"(\d+)\s*%", r"\1 процентов", t)

    # Лишние пробелы
    t = re.sub(r"\s+", " ", t).strip()
    return t


async def synthesize_speech(
    text: str,
    out_path: Path,
    *,
    voice_id: str | None = None,
    model_id: str | None = None,
    stability: float = 0.5,
    similarity_boost: float = 0.75,
    timeout: float = 240.0,
) -> Path:
    """Синтезирует аудиофайл через официальный ElevenLabs HTTP REST API.

    Args:
        text: Закадровый текст для озвучки.
        out_path: Целевой путь для сохранения MP3-файла.
        voice_id: ID голоса (по умолчанию Адам / DEFAULT_ELEVENLABS_VOICE_ID).
        model_id: ID модели (по умолчанию eleven_multilingual_v2).
        stability: Стабильность тембра (0.0 .. 1.0).
        similarity_boost: Схожесть с оригинальным голосом (0.0 .. 1.0).
        timeout: Таймаут HTTP-запроса в секундах.

    Returns:
        Path к сохранённому файлу out_path.
    """
    clean_text = normalize_text_for_tts(text)
    if not clean_text:
        raise ElevenLabsApiError("Текст для озвучки пуст.")

    api_key = _resolve_api_key()
    proxy_url = _resolve_proxy_url()
    effective_voice_id = str(voice_id or DEFAULT_ELEVENLABS_VOICE_ID).strip()
    effective_model = str(
        model_id
        or getattr(settings, "elevenlabs_model_id", "eleven_multilingual_v2")
        or "eleven_multilingual_v2"
    ).strip()

    from app.services.api_tracker_hook import check_api_allowed
    check_api_allowed(provider="ElevenLabs", model=effective_model)

    url = f"{ELEVENLABS_API_BASE_URL}/text-to-speech/{effective_voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload: dict[str, Any] = {
        "text": clean_text,
        "model_id": effective_model,
        "voice_settings": {
            "stability": max(0.0, min(1.0, float(stability))),
            "similarity_boost": max(0.0, min(1.0, float(similarity_boost))),
        },
    }

    logger.info(
        "11Labs API: запрос TTS voice_id={} model={} text_len={} симв. (proxy={})",
        effective_voice_id,
        effective_model,
        len(clean_text),
        "on" if proxy_url else "off",
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    async def _do_post(active_proxy: str | None) -> httpx.Response:
        req_timeout = httpx.Timeout(10.0, connect=5.0) if active_proxy else httpx.Timeout(timeout)
        client_kwargs: dict[str, Any] = {
            "timeout": req_timeout,
            "follow_redirects": True,
        }
        if active_proxy:
            client_kwargs["proxy"] = active_proxy
        async with spend_guard.paid_call(
            "elevenlabs", "tts post",
            estimate_usd=len(clean_text) * media_spend.ELEVENLABS_USD_PER_CHAR,
            wrap_error=_blocked_as_eleven,
        ):
            async with httpx.AsyncClient(**client_kwargs) as client:
                return await media_spend.track_eleven_post(
                    client.post(url, headers=headers, json=payload),
                    chars=len(clean_text), model=effective_model)

    import asyncio

    key = _request_key(effective_voice_id, effective_model, payload, clean_text)
    cached = _find_cached(out_path.parent, key)
    if cached is not None:
        if cached != out_path:
            shutil.copy2(cached, out_path)
            _write_sidecar(out_path, key)
        logger.info("11Labs API: озвучка с тем же хэшем текста уже есть ({}), POST не нужен", cached.name)
        return out_path
    marker = out_path.parent / f"{_UNCERTAIN_PREFIX}{key[:16]}"
    if marker.exists():
        raise ElevenLabsUncertainError(
            "11Labs API: предыдущий запрос с этим текстом оборвался после отправки, "
            f"результат неизвестен; повторный POST запрещён (удалите {marker.name} или положите файл озвучки)"
        )

    resp: httpx.Response | None = None
    route_proxy = proxy_url
    posts = 0
    while True:
        _reserve_post()
        posts += 1
        try:
            resp = await _do_post(route_proxy)
        except _PRE_SEND_CAUSES as exc:
            if posts >= MAX_POSTS_PER_SEGMENT:
                raise ElevenLabsApiError(
                    f"11Labs API: ошибка сетевого соединения: {exc}"
                ) from exc
            if route_proxy:
                logger.warning("11Labs API: соединение через прокси не удалось ({}), прямой запрос", exc)
                route_proxy = None
            else:
                await asyncio.sleep(2.0 * posts)
            continue
        except httpx.RequestError as exc:
            try:
                marker.write_text(type(exc).__name__, encoding="utf-8")
            except OSError:
                pass
            raise ElevenLabsUncertainError(
                f"11Labs API: ошибка сетевого соединения после отправки, повтор не выполняется: {exc}"
            ) from exc

        if resp.status_code in {429, 502, 503, 504} and posts < MAX_POSTS_PER_SEGMENT:
            wait_sec = 2.5 * posts
            logger.warning(
                "11Labs API: HTTP {} (Rate limit / upstream) — повтор через {:.1f}с ({}/{})",
                resp.status_code, wait_sec, posts, MAX_POSTS_PER_SEGMENT,
            )
            await asyncio.sleep(wait_sec)
            continue
        break

    if resp.status_code != 200:
        err_msg = f"HTTP {resp.status_code}"
        try:
            err_json = resp.json()
            detail = err_json.get("detail")
            if isinstance(detail, dict):
                err_msg = f"HTTP {resp.status_code}: {detail.get('message') or detail.get('code') or err_json}"
            elif isinstance(detail, str):
                err_msg = f"HTTP {resp.status_code}: {detail}"
            else:
                err_msg = f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception:  # noqa: BLE001
            err_msg = f"HTTP {resp.status_code}: {resp.text[:300]}"

        try:
            from app.services.api_tracker_hook import record_api_call
            record_api_call(
                provider="ElevenLabs",
                model=effective_model,
                call_type="audio",
                prompt_tokens=len(clean_text),
                duration_sec=0.0,
                cost_usd=0.0,
                status_code=resp.status_code,
                error_message=err_msg[:300],
                project_source="elevenlabs_tts",
                metadata={"voice_id": effective_voice_id},
            )
        except Exception:
            pass

        if resp.status_code == 401:
            raise ElevenLabsApiError(
                f"11Labs API: неверный API-ключ (401 Unauthorized) — {err_msg}"
            )
        if resp.status_code == 402:
            raise ElevenLabsApiError(
                f"11Labs API: недостаточно кредитов или выбранный голос требует платный тариф (402 Payment Required) — {err_msg}"
            )
        if resp.status_code == 429:
            raise ElevenLabsApiError(
                f"11Labs API: превышен лимит запросов Rate Limit (429) после {posts} попыток — {err_msg}"
            )

        raise ElevenLabsApiError(f"11Labs API ошибка генерации: {err_msg}")

    audio_bytes = resp.content
    if len(audio_bytes) < 1000:
        raise ElevenLabsApiError(
            f"11Labs API вернул слишком короткий файл ({len(audio_bytes)} байт) — возможно повреждение данных."
        )

    tmp_path = out_path.with_name(out_path.name + ".part")
    tmp_path.write_bytes(audio_bytes)
    os.replace(tmp_path, out_path)
    _write_sidecar(out_path, key)
    try:
        marker.unlink()
    except OSError:
        pass
    logger.info(
        "11Labs API: озвучка успешно сгенерирована → {} ({} байт)",
        out_path.name,
        len(audio_bytes),
    )
    try:
        from app.services.api_tracker_hook import record_api_call
        char_count = len(clean_text)
        cost_est = round(char_count * 0.00015, 6)
        record_api_call(
            provider="ElevenLabs",
            model=effective_model,
            call_type="audio",
            prompt_tokens=char_count,
            duration_sec=0.0,
            cost_usd=cost_est,
            media_count=1,
            status_code=200,
            project_source="elevenlabs_tts",
            metadata={"voice_id": effective_voice_id, "chars": char_count},
        )
    except Exception:
        pass
    return out_path
