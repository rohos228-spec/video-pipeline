"""Outsee.io Developer API — Bearer OUTSEE_API_KEY (не Grsai, не cookies).

Документированные эндпоинты (проверено живым ключом):
  GET  /api/v1/models
  GET  /api/v1/balance | /api/v1/usage
  POST /api/v1/images/generate  → {id, status: queued|…}
  POST /api/v1/videos/generate
  GET  /api/v1/generations/{id} → {status, result_url, result_urls, error}

Auth: Authorization: Bearer <OUTSEE_API_KEY>
"""

from __future__ import annotations

import asyncio
import base64
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.bots.outsee import GenerationResult, OutseeImageError
from app.generation_options import prepend_gen_id
from app.settings import settings

_DATA_URL_RE = re.compile(
    r"^data:(image/(?:png|jpeg|jpg|webp));base64,([A-Za-z0-9+/=\s]+)$",
    re.IGNORECASE,
)

_DEFAULT_BASE = "https://outsee.io"
_POLL_INTERVAL_S = 2.5
_DEFAULT_TIMEOUT_S = 600.0

# Живой каталог /api/v1/models (image)
OUTSEE_WIRED_IMAGE_MODELS: tuple[str, ...] = (
    "nano-banana-2",
    "nano-banana-pro",
    "gpt-image-2",
)
# Живой каталог /api/v1/models (video)
OUTSEE_WIRED_VIDEO_MODELS: tuple[str, ...] = ("veo-3-1-lite",)


class OutseeApiError(OutseeImageError):
    """Ошибка Outsee Developer API (совместима с outsee_retry)."""


# Обратная совместимость с cookie-era hooks в OutseeBot
OutseeHttpError = OutseeApiError
OutseeHttpAuthError = OutseeApiError


def outsee_api_key() -> str:
    return (settings.outsee_api_key or "").strip()


def outsee_api_configured() -> bool:
    return bool(outsee_api_key())


def outsee_api_enabled_for_image() -> bool:
    """IMAGE_PROVIDER=outsee + ключ."""
    return outsee_api_configured() and (
        (settings.image_provider or "").lower() == "outsee"
    )


def outsee_api_enabled_for_video() -> bool:
    return outsee_api_configured() and (
        (getattr(settings, "video_provider", None) or "").lower() == "outsee"
    )


def outsee_http_enabled() -> bool:
    """Есть Developer API key — можно звать Bearer без Chrome."""
    return outsee_api_configured()


def _base_url() -> str:
    return (settings.outsee_api_base_url or _DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    key = outsee_api_key()
    if not key:
        raise OutseeHttpAuthError(
            "OUTSEE_API_KEY пуст — добавь ключ из https://outsee.io/profile",
            context={"hint": "Authorization: Bearer <ключ>"},
        )
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def studio_id_to_outsee_image_slug(studio_id: str | None) -> str:
    default = settings.outsee_default_image_model or "gpt-image-2"
    if not studio_id:
        return default
    s = studio_id.strip()
    if s in OUTSEE_WIRED_IMAGE_MODELS:
        return s
    mapping = {
        "gpt_image_2": "gpt-image-2",
        "gpt-image-2": "gpt-image-2",
        "nano_banana_2": "nano-banana-2",
        "nano-banana-2": "nano-banana-2",
        "nano_banana_pro": "nano-banana-pro",
        "nano-banana-pro": "nano-banana-pro",
        "gpt_image_1_5": "gpt-image-2",
        "nano_banana_2_lite": "nano-banana-2",
        "nano-banana-2-lite": "nano-banana-2",
        "nano_banana_fast": "nano-banana-2",
        "nano-banana-fast": "nano-banana-2",
        "nano_banana": "nano-banana-2",
        "nano-banana": "nano-banana-2",
    }
    mapped = mapping.get(s, s.replace("_", "-"))
    if mapped in OUTSEE_WIRED_IMAGE_MODELS:
        return mapped
    return default


def studio_id_to_outsee_video_slug(studio_id: str | None) -> str:
    default = settings.outsee_default_video_model or "veo-3-1-lite"
    if not studio_id:
        return default
    s = studio_id.strip()
    if s in OUTSEE_WIRED_VIDEO_MODELS:
        return s
    mapping = {
        "veo_3_1_lite": "veo-3-1-lite",
        "veo-3-1-lite": "veo-3-1-lite",
        "veo_3_1_fast": "veo-3-1-lite",
        "veo-3-1-fast": "veo-3-1-lite",
        "veo_3_fast": "veo-3-1-lite",
        "veo-3-fast": "veo-3-1-lite",
        "veo3.1-fast": "veo-3-1-lite",
        "veo3.1-lite": "veo-3-1-lite",
        "kling_2_5_turbo": "veo-3-1-lite",
        "kling-2-5-turbo": "veo-3-1-lite",
    }
    return mapping.get(s, default)


def _raise_api(resp: httpx.Response, *, where: str) -> None:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {"raw": resp.text[:400]}
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        code = err.get("code") or "error"
        msg = err.get("message") or resp.text[:300]
    else:
        code = f"http_{resp.status_code}"
        msg = str(err or data)[:300]
    if resp.status_code in (401, 403) or code in {"unauthorized", "forbidden"}:
        raise OutseeHttpAuthError(
            f"Outsee API auth ({where}): {msg}",
            context={"status": resp.status_code, "code": code},
        )
    raise OutseeApiError(
        f"Outsee API {where}: {msg}",
        context={"status": resp.status_code, "code": code, "body": data},
    )


async def fetch_models() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{_base_url()}/api/v1/models", headers=_headers())
        if r.status_code >= 400:
            _raise_api(r, where="models")
        data = r.json()
        return data if isinstance(data, dict) else {}


async def fetch_balance() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{_base_url()}/api/v1/balance", headers=_headers())
        if r.status_code >= 400:
            _raise_api(r, where="balance")
        data = r.json()
        return data if isinstance(data, dict) else {}


async def _post_generate(path: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{_base_url()}{path}", headers=_headers(), json=body
        )
        if r.status_code >= 400:
            _raise_api(r, where=path)
        data = r.json()
        if not isinstance(data, dict) or data.get("id") is None:
            raise OutseeApiError(
                "Outsee generate: нет id",
                context={"path": path, "body": data},
            )
        logger.info(
            "outsee_api.submitted path={} id={} status={}",
            path,
            data.get("id"),
            data.get("status"),
        )
        return data


async def _poll_generation(
    gen_id: int | str, *, timeout: float
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    last: dict[str, Any] = {}
    url = f"{_base_url()}/api/v1/generations/{gen_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            r = await client.get(url, headers=_headers())
            if r.status_code >= 400:
                _raise_api(r, where=f"generations/{gen_id}")
            data = r.json()
            if not isinstance(data, dict):
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            last = data
            status = (data.get("status") or "").lower()
            if status in {"queued", "processing", "pending", "running"}:
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            if status in {"failed", "error", "rejected", "cancelled"}:
                raise OutseeApiError(
                    f"Outsee generation failed: {data.get('error') or status}",
                    context={"id": gen_id, "status": data},
                )
            return data
    raise OutseeApiError(
        f"Outsee: таймаут ожидания id={gen_id} ({timeout:.0f}s)",
        context={"last": last},
    )


async def _download(url: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(
                timeout=180.0, follow_redirects=True
            ) as client:
                r = await client.get(url)
                if r.status_code >= 400 or len(r.content) < 64:
                    raise OutseeApiError(
                        f"Outsee download HTTP {r.status_code}",
                        context={"url": url[:160], "bytes": len(r.content)},
                    )
                out_path.write_bytes(r.content)
            return out_path
        except (httpx.HTTPError, OSError, OutseeApiError) as e:
            last_err = e
            logger.warning(
                "outsee_api.download retry {}/3 url={} err={}",
                attempt,
                url[:120],
                e,
            )
            await asyncio.sleep(1.5 * attempt)
    raise OutseeApiError(
        f"Outsee download failed after retries: {last_err}",
        context={"url": url[:160]},
    ) from last_err


def _pick_result_url(status: dict[str, Any]) -> str:
    urls = status.get("result_urls")
    if isinstance(urls, list):
        for u in urls:
            if isinstance(u, str) and u.startswith("http"):
                return u
    u = status.get("result_url")
    if isinstance(u, str) and u.startswith("http"):
        return u
    raise OutseeApiError(
        "Outsee: нет result_url",
        context={"keys": list(status.keys())[:20], "status": status.get("status")},
    )


def _path_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    mime = "image/png"
    suf = path.suffix.lower()
    if suf in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    elif suf == ".webp":
        mime = "image/webp"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _decode_data_url(url: str) -> tuple[bytes, str] | None:
    m = _DATA_URL_RE.match((url or "").strip())
    if not m:
        return None
    mime = m.group(1).lower().replace("image/jpg", "image/jpeg")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=False)
    except Exception:  # noqa: BLE001
        return None
    if len(raw) < 32:
        return None
    return raw, mime


async def ensure_public_image_url(url: str | None) -> str | None:
    """Outsee first_frame_url принимает только http(s); data: молча игнорит.

    data: → временный публичный URL (litterbox 1h). http → как есть.
    """
    if not url or not str(url).strip():
        return None
    u = str(url).strip()
    if u.startswith(("http://", "https://")):
        return u
    decoded = _decode_data_url(u)
    if not decoded:
        logger.warning("outsee_api.frame: не data/http URL, пропускаю")
        return None
    raw, mime = decoded
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    import os

    tmp = Path(tempfile.gettempdir()) / f"outsee_frame_{os.getpid()}_{abs(hash(raw)) & 0xFFFFFFFF}.{ext}"
    try:
        tmp.write_bytes(raw)
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            with tmp.open("rb") as fh:
                r = await client.post(
                    "https://litterbox.catbox.moe/resources/internals/api.php",
                    data={"reqtype": "fileupload", "time": "1h"},
                    files={"fileToUpload": (tmp.name, fh, mime)},
                )
            text = (r.text or "").strip()
            if r.status_code >= 400 or not text.startswith("http"):
                raise OutseeApiError(
                    f"frame upload failed HTTP {r.status_code}: {text[:200]}",
                    context={"mime": mime, "bytes": len(raw)},
                )
            logger.info("outsee_api.frame hosted {} bytes → {}", len(raw), text[:120])
            return text
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


async def probe_has_audio(path: Path) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return bool((stdout or b"").strip())


async def postprocess_veo_mp4(
    path: Path,
    *,
    duration: int | None,
    generate_audio: bool | None,
) -> Path:
    """Outsee Veo всегда отдаёт ~8с + AAC. Подрезаем/глушим локально под UI."""
    from app.services.media_probe import probe_duration

    want_dur = int(duration) if duration is not None else None
    want_mute = generate_audio is False
    if not want_mute and want_dur not in {4, 6}:
        return path

    actual = 0.0
    try:
        actual = await probe_duration(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("veo postprocess: probe failed {} — {}", path.name, e)

    trim_to: float | None = None
    if want_dur in {4, 6} and actual >= float(want_dur) + 0.35:
        trim_to = float(want_dur)

    has_a = False
    if want_mute:
        try:
            has_a = await probe_has_audio(path)
        except Exception:  # noqa: BLE001
            has_a = True

    if trim_to is None and not (want_mute and has_a):
        return path

    out = path.with_name(f"{path.stem}_pp{path.suffix}")
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(path)]
    if trim_to is not None:
        cmd.extend(["-t", f"{trim_to:.3f}"])
    if want_mute and has_a:
        cmd.append("-an")
    else:
        cmd.extend(["-c:a", "copy"])
    cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
    # If only mute (no trim), stream-copy video is faster:
    if trim_to is None and want_mute and has_a:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-c:v",
            "copy",
            "-an",
            str(out),
        ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 64:
        logger.warning(
            "veo postprocess ffmpeg failed rc={} err={}",
            proc.returncode,
            (stderr or b"")[:400].decode(errors="ignore"),
        )
        out.unlink(missing_ok=True)
        return path
    path.write_bytes(out.read_bytes())
    out.unlink(missing_ok=True)
    logger.info(
        "veo postprocess ok path={} trim={} mute={}",
        path.name,
        trim_to,
        want_mute and has_a,
    )
    return path


def _normalize_refs(refs: list[Any] | None) -> list[str] | None:
    if not refs:
        return None
    out: list[str] = []
    for item in refs:
        if isinstance(item, Path):
            if item.is_file():
                out.append(_path_to_data_url(item))
        elif isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out[:9] or None


async def generate_image(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str | None = None,
    aspect_ratio: str = "9:16",
    resolution: str | None = "2K",
    detail_level: str | None = None,
    reference_images: list[Any] | None = None,
    prompt_id_prefix: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
    gen_id: str | None = None,
    project_id: int | None = None,
    **_kwargs: Any,
) -> GenerationResult:
    """Картинка через POST /api/v1/images/generate."""
    if prompt_id_prefix:
        prompt = prepend_gen_id(prompt, prompt_id_prefix)
    model = studio_id_to_outsee_image_slug(model_slug)
    body: dict[str, Any] = {
        "prompt": prompt,
        "model": model,
        "aspect_ratio": (aspect_ratio or "9:16").replace("_", ":"),
    }
    if resolution:
        # Каталог /api/v1/models сейчас: все image-модели только "2K"
        body["resolution"] = "2K"
    if detail_level:
        dl = str(detail_level).strip().lower()
        mapping = {
            "low": "low",
            "medium": "medium",
            "high": "medium",
            "низкое": "low",
            "среднее": "medium",
            "высокое": "medium",
        }
        body["detail_level"] = mapping.get(dl, dl)
    refs = _normalize_refs(reference_images)
    if refs:
        body["reference_images"] = refs

    logger.info(
        "outsee_api.image model={} aspect={} res={} project={}",
        model,
        body.get("aspect_ratio"),
        body.get("resolution"),
        project_id,
    )
    submitted = await _post_generate("/api/v1/images/generate", body)
    task_id = submitted["id"]
    done = await _poll_generation(task_id, timeout=timeout)
    url = _pick_result_url(done)
    suf = Path(url.split("?")[0]).suffix.lower()
    if suf in {".png", ".jpg", ".jpeg", ".webp"} and out_path.suffix.lower() != suf:
        out_path = out_path.with_suffix(suf if suf != ".jpeg" else ".jpg")
    await _download(url, out_path)
    return GenerationResult(
        file_path=out_path,
        raw_url=url,
        gen_id=str(gen_id or task_id),
    )


async def generate_video(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str | None = None,
    aspect_ratio: str = "9:16",
    resolution: str | None = "720p",
    duration: int | float | None = None,
    generate_audio: bool | None = None,
    first_frame_url: str | None = None,
    last_frame_url: str | None = None,
    reference_image: Path | str | None = None,
    last_frame_image: Path | str | None = None,
    prompt_id_prefix: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
    gen_id: str | None = None,
    project_id: int | None = None,
    **_kwargs: Any,
) -> GenerationResult:
    """Видео через POST /api/v1/videos/generate.

    Veo 3.1 Lite (Outsee): aspect 16:9|9:16, resolution 720p,
    duration_sec 4|6|8, generate_audio, first_frame_url, last_frame_url.
    """
    if prompt_id_prefix:
        prompt = prepend_gen_id(prompt, prompt_id_prefix)
    model = studio_id_to_outsee_video_slug(model_slug)
    aspect = (aspect_ratio or "9:16").replace("_", ":")
    if aspect not in {"16:9", "9:16"}:
        aspect = "16:9"
    body: dict[str, Any] = {
        "prompt": prompt,
        "model": model,
        "aspect_ratio": aspect,
    }

    # Veo Outsee: только 720p (1080p → invalid_request)
    res = (resolution or "720p").strip().lower()
    if model == "veo-3-1-lite":
        body["resolution"] = "720p"
    else:
        body["resolution"] = res if res in {"720p", "1080p"} else "720p"

    # Veo: каталог Outsee фиксирует duration_sec=8; 4/6 шлём и потом режем ffmpeg.
    dur = int(duration or 8)
    if model == "veo-3-1-lite":
        if dur not in {4, 6, 8}:
            dur = 8
    body["duration_sec"] = dur

    if generate_audio is not None:
        body["generate_audio"] = bool(generate_audio)

    frame = first_frame_url
    if not frame and isinstance(reference_image, str) and reference_image.startswith(
        ("http", "data:")
    ):
        frame = reference_image
    elif not frame and isinstance(reference_image, Path) and reference_image.is_file():
        frame = _path_to_data_url(reference_image)
    frame = await ensure_public_image_url(frame)
    if frame:
        body["first_frame_url"] = frame

    last = last_frame_url
    if not last and isinstance(last_frame_image, str) and last_frame_image.startswith(
        ("http", "data:")
    ):
        last = last_frame_image
    elif not last and isinstance(last_frame_image, Path) and last_frame_image.is_file():
        last = _path_to_data_url(last_frame_image)
    last = await ensure_public_image_url(last)
    if last:
        body["last_frame_url"] = last

    logger.info(
        "outsee_api.video model={} aspect={} res={} dur={} audio={} first={} last={} project={}",
        model,
        body.get("aspect_ratio"),
        body.get("resolution"),
        body.get("duration_sec"),
        body.get("generate_audio"),
        bool(body.get("first_frame_url")),
        bool(body.get("last_frame_url")),
        project_id,
    )
    submitted = await _post_generate("/api/v1/videos/generate", body)
    task_id = submitted["id"]
    done = await _poll_generation(task_id, timeout=timeout)
    url = _pick_result_url(done)
    suf = Path(url.split("?")[0]).suffix.lower()
    if suf in {".mp4", ".webm"} and out_path.suffix.lower() != suf:
        out_path = out_path.with_suffix(suf)
    await _download(url, out_path)
    if model == "veo-3-1-lite":
        await postprocess_veo_mp4(
            out_path,
            duration=dur,
            generate_audio=generate_audio,
        )
    return GenerationResult(
        file_path=out_path,
        raw_url=url,
        gen_id=str(gen_id or task_id),
    )


async def generate_image_http(
    page: Any = None,
    **kwargs: Any,
) -> GenerationResult:
    """Совместимость с OutseeBot._try_http_image(page, …) — page игнорируется."""
    del page
    prompt = kwargs.pop("prompt")
    out_path = kwargs.pop("out_path")
    return await generate_image(prompt, out_path, **kwargs)


async def generate_video_http(
    page: Any = None,
    **kwargs: Any,
) -> GenerationResult:
    """Совместимость с OutseeBot._try_http_video(page, …) — page игнорируется."""
    del page
    prompt = kwargs.pop("prompt")
    out_path = kwargs.pop("out_path")
    return await generate_video(prompt, out_path, **kwargs)


async def generate_music_http(
    page: Any = None,
    *args: Any,
    **kwargs: Any,
) -> GenerationResult:
    del page, args, kwargs
    raise OutseeApiError(
        "Outsee Developer API не поддерживает audio — "
        "используй пайплайн ElevenLabs/Suno или CDP UI",
        context={"hint": "нет /api/v1/audio"},
    )
