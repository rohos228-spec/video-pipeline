"""WaveSpeed Eleven V4 — синтез речи (submit + poll + download).

POST https://api.wavespeed.ai/api/v3/elevenlabs/eleven-v4
GET  https://api.wavespeed.ai/api/v3/predictions/{id}/result

Ключ только на сервере: WAVESPEED_API_KEY (Authorization: Bearer).
Цена в документации WaveSpeed: $0.08 / 1000 символов входного text
(пробелы, пунктуация и теги вроде [warmly] тоже считаются).
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from app.services.elevenlabs_api import normalize_text_for_tts
from app.settings import settings

WAVESPEED_API_BASE = "https://api.wavespeed.ai/api/v3"
SUBMIT_URL = f"{WAVESPEED_API_BASE}/elevenlabs/eleven-v4"
DEFAULT_WAVESPEED_VOICE_ID = "Alicia"
MAX_TEXT_CHARS = 10_000
_TERMINAL_FAIL = frozenset({"failed", "cancelled", "canceled", "timeout", "deleted"})
_TRANSIENT_HTTP = frozenset({429, 502, 503, 504})
_PREDICTION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


class WaveSpeedElevenV4Error(RuntimeError):
    """Ошибка синтеза через WaveSpeed Eleven V4."""


def wavespeed_api_configured() -> bool:
    """Есть непустой WAVESPEED_API_KEY в настройках или окружении."""
    return bool(_api_key_or_empty())


def _api_key_or_empty() -> str:
    key = getattr(settings, "wavespeed_api_key", None) or os.environ.get("WAVESPEED_API_KEY", "")
    return str(key or "").strip()


def _resolve_api_key() -> str:
    key = _api_key_or_empty()
    if not key:
        raise WaveSpeedElevenV4Error(
            "Не задан WAVESPEED_API_KEY в .env — укажите ключ WaveSpeed на сервере, "
            "чтобы озвучивать через Eleven V4."
        )
    return key


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def resolve_wavespeed_voice_settings(project: Any | None = None) -> tuple[str, float, float]:
    """voice_id, stability, similarity: meta проекта, иначе .env, иначе Alicia / 0.5 / 0.75."""
    default_voice = str(
        getattr(settings, "wavespeed_eleven_v4_voice_id", "") or DEFAULT_WAVESPEED_VOICE_ID
    ).strip() or DEFAULT_WAVESPEED_VOICE_ID
    voice = default_voice
    meta = getattr(project, "meta", None) or {}
    raw = meta.get("node_step_params") if isinstance(meta, dict) else None
    audio = raw.get("audio") if isinstance(raw, dict) else None
    if isinstance(audio, dict):
        custom = audio.get("wavespeed_voice_id")
        if isinstance(custom, str) and custom.strip():
            voice = custom.strip()
    voice = _clean_voice_id(voice)
    stability = clamp_unit(float(getattr(settings, "wavespeed_eleven_v4_stability", 0.5) or 0.5))
    similarity = clamp_unit(float(getattr(settings, "wavespeed_eleven_v4_similarity", 0.75) or 0.75))
    return voice, stability, similarity


def _clean_voice_id(value: str) -> str:
    voice = " ".join(str(value or "").split())
    if not voice:
        raise WaveSpeedElevenV4Error(
            "WaveSpeed Eleven V4: не задан voice_id (пресет вроде Alicia или ID голоса ElevenLabs)."
        )
    if len(voice) > 128:
        raise WaveSpeedElevenV4Error("WaveSpeed Eleven V4: voice_id длиннее 128 символов.")
    return voice


def split_text_for_eleven_v4(text: str, limit: int = MAX_TEXT_CHARS) -> list[str]:
    """Режет текст на части не длиннее limit, по абзацу или концу предложения."""
    rest = (text or "").strip()
    if not rest:
        return []
    if limit < 1:
        raise WaveSpeedElevenV4Error("WaveSpeed Eleven V4: некорректный лимит длины текста.")
    parts: list[str] = []
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        window = rest[:limit]
        end = _split_end(window, limit)
        if end <= 0:
            end = limit
        chunk = rest[:end].strip()
        if not chunk:
            chunk = rest[:limit]
            end = limit
        parts.append(chunk)
        rest = rest[end:].strip()
        if len(parts) > 40:
            raise WaveSpeedElevenV4Error(
                "WaveSpeed Eleven V4: текст слишком длинный даже после нарезки по 10000 символов."
            )
    return parts


def _split_end(window: str, limit: int) -> int:
    best = -1
    for sep in ("\n\n", "\n", ". ", "! ", "? "):
        idx = window.rfind(sep)
        if idx < limit // 5:
            continue
        end = idx + 1 if sep in (". ", "! ", "? ") else idx
        if end > best:
            best = end
    return best if best > 0 else limit


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _unwrap_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WaveSpeedElevenV4Error("WaveSpeed Eleven V4: ответ сервера не JSON-объект.")
    code = payload.get("code")
    if code is not None:
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = -1
        if code_int != 200:
            message = payload.get("message") or payload
            raise WaveSpeedElevenV4Error(f"WaveSpeed Eleven V4: {message}")
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if "id" in payload or "status" in payload:
        return payload
    raise WaveSpeedElevenV4Error("WaveSpeed Eleven V4: в ответе нет объекта data.")


def _http_error(resp: httpx.Response, what: str) -> WaveSpeedElevenV4Error:
    snippet = (resp.text or "")[:300]
    if resp.status_code == 401:
        return WaveSpeedElevenV4Error(
            f"WaveSpeed Eleven V4: неверный WAVESPEED_API_KEY (401) при {what}."
        )
    return WaveSpeedElevenV4Error(
        f"WaveSpeed Eleven V4: HTTP {resp.status_code} при {what}. {snippet}"
    )


def _prediction_id(data: dict[str, Any]) -> str:
    raw = str(data.get("id") or "").strip()
    if not _PREDICTION_ID_RE.fullmatch(raw):
        raise WaveSpeedElevenV4Error(
            "WaveSpeed Eleven V4: в ответе submit нет корректного data.id."
        )
    return raw


def _output_url(data: dict[str, Any]) -> str:
    outputs = data.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise WaveSpeedElevenV4Error(
            "WaveSpeed Eleven V4: задача completed, но data.outputs пуст — MP3 не получен."
        )
    for item in outputs:
        if isinstance(item, str) and item.startswith(("http://", "https://")):
            return item
        if isinstance(item, dict):
            for key in ("url", "audio_url", "audio", "output"):
                value = item.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value
    raise WaveSpeedElevenV4Error(
        "WaveSpeed Eleven V4: data.outputs не содержит URL MP3."
    )


async def _read_json(resp: httpx.Response, what: str) -> dict[str, Any]:
    if resp.status_code in _TRANSIENT_HTTP:
        raise _TransientHttp(resp.status_code)
    if resp.status_code >= 400:
        raise _http_error(resp, what)
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise WaveSpeedElevenV4Error(
            f"WaveSpeed Eleven V4: ответ {what} не JSON (HTTP {resp.status_code})."
        ) from exc
    return _unwrap_data(payload)


class _TransientHttp(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


async def synthesize_speech(
    text: str,
    out_path: Path,
    *,
    voice_id: str | None = None,
    stability: float | None = None,
    similarity: float | None = None,
    timeout: float = 240.0,
    poll_interval: float = 2.0,
    client: httpx.AsyncClient | None = None,
) -> Path:
    """Синтезирует MP3 через WaveSpeed Eleven V4 и сохраняет его в out_path.

    Текст длиннее 10000 символов режется на части и склеивается ffmpeg.
    """
    clean = normalize_text_for_tts(text)
    if not clean:
        raise WaveSpeedElevenV4Error("Текст для озвучки пуст.")

    api_key = _resolve_api_key()
    default_voice, default_stability, default_similarity = resolve_wavespeed_voice_settings(None)
    effective_voice = _clean_voice_id(voice_id or default_voice)
    effective_stability = clamp_unit(
        default_stability if stability is None else stability
    )
    effective_similarity = clamp_unit(
        default_similarity if similarity is None else similarity
    )
    parts = split_text_for_eleven_v4(clean)
    if not parts:
        raise WaveSpeedElevenV4Error("Текст для озвучки пуст.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=True,
        )
    part_paths: list[Path] = []
    try:
        if len(parts) > 1:
            logger.info(
                "WaveSpeed Eleven V4: текст {} симв. нарезан на {} запросов (лимит {})",
                len(clean),
                len(parts),
                MAX_TEXT_CHARS,
            )
        for index, chunk in enumerate(parts):
            part_path = (
                out_path
                if len(parts) == 1
                else out_path.with_name(f"{out_path.stem}.part{index:02d}.mp3")
            )
            await _synthesize_chunk(
                client,
                api_key,
                chunk,
                part_path,
                voice_id=effective_voice,
                stability=effective_stability,
                similarity=effective_similarity,
                timeout=timeout,
                poll_interval=poll_interval,
            )
            part_paths.append(part_path)
        if len(part_paths) == 1:
            return out_path
        await _concat_mp3(part_paths, out_path)
        return out_path
    finally:
        for path in part_paths:
            if path != out_path and path.is_file():
                path.unlink(missing_ok=True)
        if own_client:
            await client.aclose()


async def _synthesize_chunk(
    client: httpx.AsyncClient,
    api_key: str,
    text: str,
    out_path: Path,
    *,
    voice_id: str,
    stability: float,
    similarity: float,
    timeout: float,
    poll_interval: float,
) -> None:
    logger.info(
        "WaveSpeed Eleven V4: submit voice_id={} stability={} similarity={} text_len={}",
        voice_id,
        stability,
        similarity,
        len(text),
    )
    deadline = time.monotonic() + max(1.0, float(timeout))
    payload = {
        "text": text,
        "voice_id": voice_id,
        "stability": stability,
        "similarity": similarity,
    }
    data = await _request_json(
        client,
        "POST",
        SUBMIT_URL,
        api_key,
        what="отправке задачи",
        deadline=deadline,
        poll_interval=poll_interval,
        json_body=payload,
    )
    prediction_id = _prediction_id(data)
    result_url = f"{WAVESPEED_API_BASE}/predictions/{prediction_id}/result"
    audio_url = await _poll_output_url(
        client,
        api_key,
        result_url,
        deadline=deadline,
        poll_interval=poll_interval,
    )
    await _download_mp3(client, api_key, audio_url, out_path)
    logger.info(
        "WaveSpeed Eleven V4: MP3 сохранён → {} ({} байт)",
        out_path.name,
        out_path.stat().st_size,
    )


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    api_key: str,
    *,
    what: str,
    deadline: float,
    poll_interval: float,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = _auth_headers(api_key)
    while True:
        if time.monotonic() >= deadline:
            raise WaveSpeedElevenV4Error(
                f"WaveSpeed Eleven V4: истекло время ожидания при {what}."
            )
        try:
            resp = await client.request(method, url, headers=headers, json=json_body)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            if time.monotonic() >= deadline:
                raise WaveSpeedElevenV4Error(
                    f"WaveSpeed Eleven V4: ошибка сети при {what}: {exc}"
                ) from exc
            await asyncio.sleep(max(poll_interval, 0.0) or 2.0)
            continue
        try:
            return await _read_json(resp, what)
        except _TransientHttp as exc:
            logger.warning(
                "WaveSpeed Eleven V4: HTTP {} при {} — повтор",
                exc.status_code,
                what,
            )
            await asyncio.sleep(max(poll_interval, 0.0) or 2.0)


async def _poll_output_url(
    client: httpx.AsyncClient,
    api_key: str,
    result_url: str,
    *,
    deadline: float,
    poll_interval: float,
) -> str:
    while True:
        if time.monotonic() >= deadline:
            raise WaveSpeedElevenV4Error(
                "WaveSpeed Eleven V4: истекло время ожидания результата (poll)."
            )
        data = await _request_json(
            client,
            "GET",
            result_url,
            api_key,
            what="опросе результата",
            deadline=deadline,
            poll_interval=poll_interval,
        )
        status = str(data.get("status") or "").strip().lower()
        if status == "completed":
            return _output_url(data)
        if status in _TERMINAL_FAIL:
            err = data.get("error") or status
            raise WaveSpeedElevenV4Error(
                f"WaveSpeed Eleven V4: генерация завершилась со статусом {status}: {err}"
            )
        await asyncio.sleep(max(0.0, poll_interval))


async def _download_mp3(
    client: httpx.AsyncClient,
    api_key: str,
    url: str,
    out_path: Path,
) -> None:
    headers: dict[str, str] = {}
    host = urlparse(url).netloc.lower()
    if host.endswith("wavespeed.ai"):
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = await client.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise WaveSpeedElevenV4Error(
            f"WaveSpeed Eleven V4: не удалось скачать MP3: {exc}"
        ) from exc
    if resp.status_code != 200:
        raise _http_error(resp, "скачивании MP3")
    body = resp.content
    if len(body) < 32:
        raise WaveSpeedElevenV4Error(
            f"WaveSpeed Eleven V4: скачанный файл слишком короткий ({len(body)} байт)."
        )
    out_path.write_bytes(body)


async def _concat_mp3(parts: list[Path], out_path: Path) -> None:
    list_file = out_path.with_name(f"{out_path.stem}.concat.txt")
    lines = []
    for path in parts:
        escaped = str(path.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(out_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
    except FileNotFoundError as exc:
        raise WaveSpeedElevenV4Error(
            "WaveSpeed Eleven V4: ffmpeg не найден, нечем склеить части озвучки длиннее 10000 символов."
        ) from exc
    finally:
        list_file.unlink(missing_ok=True)
    if proc.returncode != 0 or not out_path.is_file():
        tail = (stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise WaveSpeedElevenV4Error(
            f"WaveSpeed Eleven V4: не удалось склеить части MP3. {tail}"
        )
