"""ElevenLabs lab: connect via proxy IP, clone voice, redub word patch."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from app.services.elevenlabs_api import (
    ElevenLabsApiError,
    api_key_configured,
    apply_redub_splice,
    clone_and_redub_word,
    clone_voice_from_sample,
    connect_by_ip,
    fetch_account_diag,
    prepare_clone_sample,
    key_hint,
    lab_dir,
    list_remote_voices as api_list_remote_voices,
    preview_redub_word,
    proxy_profiles,
    resolve_api_key,
    slim_remote_voice,
    text_to_speech_file,
)
from app.services.elevenlabs_lab_store import add_voice, delete_voice, load_voices
from app.services.frame_audio import _run_ffmpeg
from app.services.media_probe import probe_duration
from app.settings import settings

router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs"])


def _lab_http_error(exc: Exception, *, default_kind: str = "api") -> HTTPException:
    return HTTPException(status_code=502, detail=_lab_error_payload(exc, default_kind=default_kind))


def _lab_error_payload(exc: Exception, *, default_kind: str = "api") -> dict:
    msg = str(exc).lower()
    if isinstance(exc, ElevenLabsApiError):
        kind = default_kind
        status = exc.status
        if exc.status in (401, 403):
            kind = "auth"
        elif "не задан" in msg:
            kind = "missing_key"
        elif (
            "instant voice cloning" in msg
            or "instant voice clone" in msg
            or "paid_plan" in msg
            or "can_not_use_instant" in msg
            or "тариф «" in msg
            or "api key не на creator" in msg
        ):
            kind = "auth"
        elif "образец" in msg or "ffmpeg" in msg or "ffprobe" in msg:
            kind = "sample"
        elif "сеть/proxy" in msg or "winerror 121" in msg or "таймаут" in msg:
            kind = "network"
        elif "proxy" in msg and "paid_plan" not in msg and "instant voice" not in msg:
            kind = "network"
        return {"message": str(exc), "error_kind": kind, "status": status}
    kind = "server"
    if "winerror 121" in msg or "proxy" in msg or "curl" in msg or "requests" in msg:
        kind = "network"
    return {"message": str(exc), "error_kind": kind, "status": None}


class ElevenLabsConnectBody(BaseModel):
    api_key: str | None = None
    proxy_url: str | None = None
    proxy_ip: str | None = None
    proxy_port: int | None = None
    proxy_user: str | None = None
    proxy_password: str | None = None
    proxy_scheme: str | None = "http"

    @field_validator("api_key", "proxy_url", "proxy_ip", "proxy_user", "proxy_password", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class SaveVoiceBody(BaseModel):
    name: str
    voice_id: str
    sample_path: str | None = None
    meta: dict | None = None


class TtsBody(BaseModel):
    text: str
    voice_id: str
    api_key: str | None = None
    proxy_url: str | None = None
    model_id: str | None = None

    @field_validator("api_key", "proxy_url", "model_id", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v


def _sample_preview_url(sample_path: str | None) -> str | None:
    if not sample_path:
        return None
    p = Path(sample_path)
    if not p.is_file():
        lab = settings.data_dir / "elevenlabs_lab"
        alt = lab / p.name
        if alt.is_file():
            p = alt
        else:
            return None
    return f"/api/elevenlabs/files/{p.name}"


def _enrich_saved_voice(row: dict) -> dict:
    out = dict(row)
    out["sample_preview_url"] = _sample_preview_url(row.get("sample_path"))
    return out


@router.get("/status")
async def elevenlabs_status() -> dict:
    env_key = (settings.elevenlabs_api_key or "").strip()
    env_proxy = (settings.elevenlabs_proxy_url or "").strip() or None
    env_alt = (settings.elevenlabs_proxy_alt_url or "").strip() or None
    upload_proxy = (settings.elevenlabs_upload_proxy_url or "").strip() or None
    tg_proxy = (settings.telegram_proxy_url or "").strip() or None
    profiles = proxy_profiles()
    return {
        "api_key_configured": api_key_configured(),
        "api_key_hint": key_hint(env_key) if env_key else None,
        "env_key_configured": api_key_configured(),
        "proxy_url": env_proxy or tg_proxy,
        "proxy_alt_url": env_alt,
        "proxy_profiles": profiles,
        "upload_proxy_url": upload_proxy,
        "proxy_configured": bool(env_proxy or env_alt or tg_proxy),
        "upload_proxy_configured": bool(upload_proxy),
        "proxy_scheme_hint": "http",
        "model": settings.elevenlabs_api_model,
        "lab_dir": str(lab_dir().resolve()),
    }


@router.post("/connect")
async def elevenlabs_connect(body: ElevenLabsConnectBody) -> dict:
    try:
        return await connect_by_ip(
            api_key=body.api_key,
            proxy_url=body.proxy_url,
            proxy_ip=body.proxy_ip,
            proxy_port=body.proxy_port,
            proxy_user=body.proxy_user,
            proxy_password=body.proxy_password,
            proxy_scheme=body.proxy_scheme,
        )
    except ElevenLabsApiError as exc:
        kind = "auth" if exc.status in (401, 403) else "api"
        if "не задан" in str(exc).lower():
            kind = "missing_key"
        elif "сеть/proxy" in str(exc).lower():
            kind = "network"
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "error_kind": kind, "status": exc.status},
        ) from exc
    except Exception as exc:
        logger.exception("elevenlabs_connect failed")
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "error_kind": "server", "status": None},
        ) from exc


@router.get("/check-env")
async def elevenlabs_check_env() -> dict:
    """Быстрая проверка только ключа/proxy из .env (без полей UI)."""
    try:
        _, key_source = resolve_api_key(None)
        result = await connect_by_ip()
        result["check"] = "env"
        result["key_source"] = key_source
        return result
    except ElevenLabsApiError as exc:
        kind = "auth" if exc.status in (401, 403) else "api"
        if "не задан" in str(exc).lower():
            kind = "missing_key"
        elif "сеть/proxy" in str(exc).lower() or "connect failed" in str(exc).lower():
            kind = "network"
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "error_kind": kind, "status": exc.status},
        ) from exc
    except Exception as exc:
        logger.exception("elevenlabs_check_env failed")
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "error_kind": "server", "status": None},
        ) from exc


@router.get("/account-diag")
async def elevenlabs_account_diag(
    api_key: str | None = None,
    proxy_url: str | None = None,
) -> dict:
    """Что ElevenLabs видит по API key: тариф, IVC, ссылки для проверки на сайте."""
    try:
        return await fetch_account_diag(api_key=api_key, proxy_url=proxy_url)
    except ElevenLabsApiError as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "error_kind": "api", "status": exc.status},
        ) from exc


@router.get("/voices")
async def elevenlabs_saved_voices() -> dict:
    return {"voices": [_enrich_saved_voice(v) for v in load_voices()]}


@router.post("/voices")
async def elevenlabs_save_voice(body: SaveVoiceBody) -> dict:
    if not body.name.strip() or not body.voice_id.strip():
        raise HTTPException(status_code=400, detail="name и voice_id обязательны")
    row = add_voice(
        name=body.name,
        voice_id=body.voice_id,
        sample_path=body.sample_path,
        meta=body.meta,
    )
    return _enrich_saved_voice(row)


@router.delete("/voices/{voice_row_id}")
async def elevenlabs_delete_voice(voice_row_id: str) -> dict:
    if not delete_voice(voice_row_id):
        raise HTTPException(status_code=404, detail="voice not found")
    return {"ok": True}


@router.get("/remote-voices")
async def elevenlabs_remote_voices(
    api_key: str | None = None,
    proxy_url: str | None = None,
    scope: str = "all",
    search: str | None = None,
    max_pages: int = 5,
    gender: str | None = None,
    age: str | None = None,
    accent: str | None = None,
    language: str | None = None,
    locale: str | None = None,
    sort: str | None = None,
    category: str | None = None,
) -> dict:
    try:
        voices, meta = await api_list_remote_voices(
            api_key=api_key,
            proxy_url=proxy_url,
            scope=scope,
            search=search,
            max_pages=max(1, min(max_pages, 20)),
            gender=gender,
            age=age,
            accent=accent,
            language=language,
            locale=locale,
            sort=sort,
            category=category,
        )
        slim = [row for v in voices if (row := slim_remote_voice(v))]
        return {"voices": slim, **meta}
    except ElevenLabsApiError as exc:
        raise HTTPException(status_code=502, detail=_lab_error_payload(exc)) from exc


@router.post("/tts")
async def elevenlabs_tts(body: TtsBody):
    text = (body.text or "").strip()
    voice_id = (body.voice_id or "").strip()
    if len(text) < 1:
        raise HTTPException(status_code=400, detail="text обязателен")
    if len(voice_id) < 5:
        raise HTTPException(status_code=400, detail="voice_id обязателен")
    lab = lab_dir()
    out = lab / f"tts_{uuid.uuid4().hex[:8]}.mp3"
    try:
        await text_to_speech_file(
            text=text,
            out_path=out,
            voice_id=voice_id,
            model_id=body.model_id,
            api_key=body.api_key,
            proxy_url=body.proxy_url,
        )
        duration_s = await probe_duration(out)
    except ElevenLabsApiError as exc:
        kind = "auth" if exc.status in (401, 403) else "api"
        if "сеть/proxy" in str(exc).lower() or "proxy" in str(exc).lower():
            kind = "network"
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "error_kind": kind, "status": exc.status},
        ) from exc
    except Exception as exc:
        logger.exception("elevenlabs_tts failed")
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "error_kind": "server", "status": None},
        ) from exc
    return {
        "ok": True,
        "preview_url": f"/api/elevenlabs/files/{out.name}",
        "filename": out.name,
        "duration_s": duration_s,
        "voice_id": voice_id,
    }


@router.post("/extract-clip")
async def elevenlabs_extract_clip(
    source: UploadFile = File(...),
    start_s: float = Form(...),
    end_s: float = Form(...),
) -> dict:
    if end_s <= start_s:
        raise HTTPException(status_code=400, detail="end_s must be > start_s")
    raw = await source.read()
    if len(raw) < 500:
        raise HTTPException(status_code=400, detail="source слишком короткий")

    lab = settings.data_dir / "elevenlabs_lab"
    lab.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]
    src_path = lab / f"upload_{run_id}_{source.filename or 'source.mp3'}"
    clip_path = lab / f"clip_{run_id}.mp3"
    src_path.write_bytes(raw)

    dur = end_s - start_s
    await _run_ffmpeg([
        "ffmpeg", "-y",
        "-ss", f"{start_s:.3f}",
        "-i", str(src_path),
        "-t", f"{dur:.3f}",
        "-c:a", "libmp3lame", "-q:a", "2",
        str(clip_path),
    ])
    clip_dur = await probe_duration(clip_path)
    return {
        "ok": True,
        "clip_path": str(clip_path.resolve()),
        "preview_url": f"/api/elevenlabs/files/{clip_path.name}",
        "start_s": start_s,
        "end_s": end_s,
        "duration_s": clip_dur,
    }


@router.post("/clone")
async def elevenlabs_clone(
    voice_name: str = Form(...),
    sample: UploadFile = File(...),
    api_key: str | None = Form(None),
    proxy_url: str | None = Form(None),
    save: bool = Form(default=True),
):
    try:
        sample_bytes = await sample.read()
        if len(sample_bytes) < 100:
            return JSONResponse(
                status_code=400,
                content={"message": "sample пустой или слишком короткий", "error_kind": "sample"},
            )

        sample_bytes, sample_filename, sample_dur = await prepare_clone_sample(
            sample_bytes,
            sample.filename or "sample.wav",
        )
        cloned = await clone_voice_from_sample(
            name=voice_name,
            sample_bytes=sample_bytes,
            sample_filename=sample_filename,
            api_key=api_key,
            proxy_url=proxy_url,
        )

        lab = settings.data_dir / "elevenlabs_lab"
        lab.mkdir(parents=True, exist_ok=True)
        sample_path = lab / f"sample_{uuid.uuid4().hex[:8]}_{sample_filename}"
        sample_path.write_bytes(sample_bytes)

        saved = None
        if save:
            saved = _enrich_saved_voice(
                add_voice(
                    name=voice_name,
                    voice_id=str(cloned["voice_id"]),
                    sample_path=str(sample_path.resolve()),
                    meta={"source": "clone", "duration_s": round(sample_dur, 3)},
                )
            )
        return {
            "clone": cloned,
            "saved": saved,
            "sample_path": str(sample_path.resolve()),
            "sample_preview_url": f"/api/elevenlabs/files/{sample_path.name}",
            "sample_duration_s": sample_dur,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("elevenlabs_clone failed")
        return JSONResponse(status_code=502, content=_lab_error_payload(exc))


@router.post("/clone-redub")
async def elevenlabs_clone_redub(
    voice_name: str = Form(...),
    sample: UploadFile = File(...),
    source_audio: UploadFile = File(...),
    start_s: float = Form(...),
    end_s: float = Form(...),
    fragment_text: str = Form(...),
    old_word: str = Form(...),
    new_word: str = Form(...),
    api_key: str | None = Form(None),
    proxy_url: str | None = Form(None),
    voice_id: str | None = Form(None),
    save_voice: bool = Form(True),
) -> dict:
    sample_bytes = await sample.read()
    if len(sample_bytes) < 1000:
        raise HTTPException(status_code=400, detail="sample слишком короткий")

    src_bytes = await source_audio.read()
    if len(src_bytes) < 1000:
        raise HTTPException(status_code=400, detail="source_audio слишком короткий")

    lab = settings.data_dir / "elevenlabs_lab"
    lab.mkdir(parents=True, exist_ok=True)
    src_path = lab / f"upload_src_{uuid.uuid4().hex[:8]}_{source_audio.filename or 'source.mp3'}"
    src_path.write_bytes(src_bytes)

    try:
        result = await clone_and_redub_word(
            sample_bytes=sample_bytes,
            sample_filename=sample.filename or "sample.mp3",
            voice_name=voice_name,
            source_path=src_path,
            start_s=start_s,
            end_s=end_s,
            fragment_text=fragment_text,
            old_word=old_word,
            new_word=new_word,
            api_key=api_key,
            proxy_url=proxy_url,
            voice_id=(voice_id or "").strip() or None,
        )
    except (ElevenLabsApiError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    out_name = Path(result["output_mp3"]).name
    result["preview_url"] = f"/api/elevenlabs/files/{out_name}"
    if save_voice and result.get("voice_id"):
        result["saved_voice"] = add_voice(
            name=voice_name,
            voice_id=result["voice_id"],
            meta={"source": "clone-redub", "run_id": result.get("run_id")},
        )
    return result


@router.post("/redub-preview")
async def elevenlabs_redub_preview(
    voice_name: str = Form(...),
    sample: UploadFile = File(...),
    fragment_text: str = Form(...),
    old_word: str = Form(...),
    new_word: str = Form(...),
    api_key: str | None = Form(None),
    proxy_url: str | None = Form(None),
    voice_id: str | None = Form(None),
    save_voice: bool = Form(True),
) -> dict:
    sample_bytes = await sample.read()
    if len(sample_bytes) < 1000:
        raise HTTPException(status_code=400, detail="sample слишком короткий")
    try:
        result = await preview_redub_word(
            sample_bytes=sample_bytes,
            sample_filename=sample.filename or "sample.mp3",
            voice_name=voice_name,
            fragment_text=fragment_text,
            old_word=old_word,
            new_word=new_word,
            api_key=api_key,
            proxy_url=proxy_url,
            voice_id=(voice_id or "").strip() or None,
        )
    except (ElevenLabsApiError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    patch_name = Path(result["patch_mp3"]).name
    result["patch_preview_url"] = f"/api/elevenlabs/files/{patch_name}"
    if save_voice and result.get("voice_id"):
        result["saved_voice"] = add_voice(
            name=voice_name,
            voice_id=result["voice_id"],
            meta={"source": "redub-preview", "run_id": result.get("run_id")},
        )
    return result


@router.post("/redub-apply")
async def elevenlabs_redub_apply(
    source_audio: UploadFile = File(...),
    patch_filename: str = Form(...),
    start_s: float = Form(...),
    end_s: float = Form(...),
    run_id: str | None = Form(None),
) -> dict:
    if ".." in patch_filename or "/" in patch_filename or "\\" in patch_filename:
        raise HTTPException(status_code=400, detail="invalid patch_filename")
    patch_path = lab_dir() / patch_filename
    if not patch_path.is_file():
        raise HTTPException(status_code=404, detail="patch не найден — сначала сгенерируйте превью")

    src_bytes = await source_audio.read()
    if len(src_bytes) < 1000:
        raise HTTPException(status_code=400, detail="source_audio слишком короткий")

    lab = settings.data_dir / "elevenlabs_lab"
    lab.mkdir(parents=True, exist_ok=True)
    src_path = lab / f"upload_src_{uuid.uuid4().hex[:8]}_{source_audio.filename or 'source.mp3'}"
    src_path.write_bytes(src_bytes)

    try:
        result = await apply_redub_splice(
            source_path=src_path,
            patch_path=patch_path,
            start_s=start_s,
            end_s=end_s,
            run_id=(run_id or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    out_name = Path(result["output_mp3"]).name
    result["preview_url"] = f"/api/elevenlabs/files/{out_name}"
    return result


@router.get("/files/{filename}")
async def elevenlabs_file(filename: str) -> FileResponse:
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    lab = settings.data_dir / "elevenlabs_lab"
    path = lab / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)
