"""Генерация звуков сопровождения (SFX) по плану ``sfx_plan``.

Провайдеры (по порядку):
1. ElevenLabs SFX API (``ELEVENLABS_API_KEY``) — ИИ-генерация по EN-промту.
2. Локальный синтез (чистый Python, wave) — офлайн-фолбэк: whoosh/hit/
   riser/ambience/transition/stinger/foley. Это ПОДМЕНА, а не норма: без ключа каждое событие
   получает факт provider_fallback (reason=no_api_key, synthetic=true) и поле provider_fallback в записи.

Приём результата: ``verify_audio_file`` → ``audio_verification.verify_audio`` (ffprobe + амплитуда) — брак не уходит
в монтаж. Файлы: ``data/videos/<slug>/sfx/sfx_NN_<kind>.wav|mp3``.
Чекпоинт per-event: ``meta.ai_jobs["sfx_progress"]`` (готовые события + метки «запрос
отправлен, ответа нет») пишется и коммитится по мере работы; итог — ``ai_jobs["sfx_files"]``
после всего цикла. Повтор переиспользует готовые события; событие с меткой «отправлено, ответа
нет» (исход неизвестен) автоматически НЕ отправляется повторно.
"""

from __future__ import annotations

import copy
import math
import random
import shutil
import struct
import wave
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services import media_spend, spend_guard
from app.services.ai_result_io import (
    ai_job_checkpoint,
    drop_ai_job_checkpoint,
    save_ai_job_checkpoint,
)
from app.services.audio_verification import verify_audio
from app.services.elevenlabs_api import (
    SFX_MAX_PROMPT_CHARS,
    SFX_PROMPT_INFLUENCE,
    sfx_find_cached,
    sfx_request_key,
    sfx_write_sidecar,
)
from app.services.sfx_plan import SfxEvent

_SAMPLE_RATE = 44100


def verify_audio_file(path: Path, *, expect_duration: float | None = None, duration_tol: float = 0.6) -> list[str]:
    return verify_audio(path, expect_duration=expect_duration, duration_tol=duration_tol).problems




def _write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SAMPLE_RATE)
        frames = b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples
        )
        w.writeframes(frames)


def _one_pole_lowpass(samples: list[float], alpha: float) -> list[float]:
    out: list[float] = []
    prev = 0.0
    for x in samples:
        prev = prev + alpha * (x - prev)
        out.append(prev)
    return out


def _noise(n: int, rng: random.Random) -> list[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(n)]


def _synth(kind: str, duration: float, *, seed: int = 42) -> list[float]:
    """Моно-сэмплы [-1..1] для вида звука (44.1kHz)."""
    rng = random.Random(seed)
    n = max(int(_SAMPLE_RATE * duration), 100)
    t = [i / _SAMPLE_RATE for i in range(n)]

    if kind == "whoosh":
        # Шум с набегающей/спадающей амплитудой + подъём фильтра.
        env = [math.sin(math.pi * i / n) ** 1.5 for i in range(n)]
        raw = _noise(n, rng)
        swept: list[float] = []
        prev = 0.0
        for i, x in enumerate(raw):
            alpha = 0.02 + 0.25 * (i / n)  # фильтр открывается к середине
            prev = prev + alpha * (x - prev)
            swept.append(prev * env[i])
        return swept

    if kind == "hit":
        # Низкий удар 55 Гц с эксп. затуханием + шумовой щелчок (≤ 1.0).
        out: list[float] = []
        for ti in t:
            decay = math.exp(-6.0 * ti / max(duration, 0.1))
            thump = math.sin(2 * math.pi * 55 * ti) * decay
            click = rng.uniform(-1, 1) * math.exp(-40 * ti) * 0.5
            out.append(max(-1.0, min(1.0, (thump * 0.8 + click) * 0.8)))
        return out

    if kind == "riser":
        # Подъём 200→1200 Гц + шум, амплитуда растёт.
        out = []
        phase = 0.0
        for i in range(n):
            f = 200 + 1000 * (i / n)
            phase += 2 * math.pi * f / _SAMPLE_RATE
            tone = math.sin(phase)
            noise = rng.uniform(-0.3, 0.3)
            amp = (i / n) ** 1.5
            out.append((tone * 0.5 + noise) * amp * 0.8)
        return out

    if kind == "ambience":
        # Коричневый шум, мягкий, с медленными fade in/out.
        raw = _noise(n, rng)
        brown: list[float] = []
        acc = 0.0
        for x in raw:
            acc = (acc + 0.02 * x) / 1.02
            brown.append(acc * 3.5)
        brown = _one_pole_lowpass(brown, 0.08)
        fade = min(n // 8, _SAMPLE_RATE)
        for i in range(n):
            g = 1.0
            if i < fade:
                g = i / fade
            elif i > n - fade:
                g = (n - i) / fade
            brown[i] *= g * 0.5
        return brown

    if kind == "transition":
        # Спад 900→150 Гц, быстрый.
        out = []
        phase = 0.0
        for i in range(n):
            f = 900 - 750 * (i / n)
            phase += 2 * math.pi * f / _SAMPLE_RATE
            env = math.sin(math.pi * i / n) ** 1.2
            out.append(math.sin(phase) * env * 0.6)
        return out

    if kind == "stinger":
        # Аккорд A4+E5+немного шума, быстрое затухание.
        out = []
        for ti in t:
            decay = math.exp(-3.5 * ti / max(duration, 0.1))
            chord = (
                math.sin(2 * math.pi * 440 * ti)
                + math.sin(2 * math.pi * 659.25 * ti) * 0.7
                + math.sin(2 * math.pi * 220 * ti) * 0.5
            )
            out.append(chord / 2.2 * decay * 0.85)
        return out

    # foley — серия коротких шумовых постукиваний.
    out = [0.0] * n
    taps = max(2, int(duration * 3))
    for k in range(taps):
        center = int(n * (k + 0.5) / taps)
        length = min(int(_SAMPLE_RATE * 0.05), n - center)
        for j in range(length):
            out[center + j] += rng.uniform(-1, 1) * math.exp(-30 * j / _SAMPLE_RATE) * 0.7
    return _one_pole_lowpass(out, 0.3)


# ── ElevenLabs SFX API (опционально) ──────────────────────────────────────


def _sfx_estimate(args: tuple, kwargs: dict) -> float:
    duration = args[1] if len(args) > 1 else kwargs.get("duration", 1.0)
    try:
        return min(max(float(duration), 0.5), 22.0) * media_spend.ELEVENLABS_SFX_USD_PER_SEC
    except (TypeError, ValueError):
        return 0.5 * media_spend.ELEVENLABS_SFX_USD_PER_SEC


@spend_guard.paid_seam("elevenlabs", "sfx post", estimate=_sfx_estimate)
@media_spend.track_eleven_sfx
async def _elevenlabs_sfx(prompt: str, duration: float, out_path: Path) -> Path:
    import asyncio
    import time
    import httpx

    from app.services.api_tracker_hook import check_api_allowed, record_api_call
    from app.settings import settings

    key = settings.elevenlabs_api_key
    if not key:
        raise RuntimeError("no elevenlabs key")

    check_api_allowed(provider="ElevenLabs", model="sound-generation")

    proxy_url = getattr(settings, "elevenlabs_proxy_url", None)
    proxy = str(proxy_url or "").strip() or None
    client_kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(90.0, connect=10.0),
        "follow_redirects": True,
    }
    if proxy:
        client_kwargs["proxy"] = proxy

    t0 = time.time()
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.post(
                    "https://api.elevenlabs.io/v1/sound-generation",
                    headers={"xi-api-key": key},
                    json={
                        "text": prompt[:SFX_MAX_PROMPT_CHARS],
                        "duration_seconds": min(max(duration, 0.5), 22.0),
                        "prompt_influence": SFX_PROMPT_INFLUENCE,
                    },
                )
        except httpx.ConnectError as e:
            if attempt < max_retries:
                logger.warning("11labs sfx attempt {}/{} connect error: {}, retrying...", attempt, max_retries, e)
                await asyncio.sleep(2.0 * attempt)
                continue
            record_api_call(
                provider="ElevenLabs",
                model="eleven-sound-generation",
                call_type="audio",
                duration_sec=time.time() - t0,
                status_code=500,
                error_message=str(e)[:200],
                project_source="sfx_gen",
            )
            raise
        except Exception as e:
            record_api_call(
                provider="ElevenLabs",
                model="eleven-sound-generation",
                call_type="audio",
                duration_sec=time.time() - t0,
                status_code=500,
                error_message=str(e)[:200],
                project_source="sfx_gen",
            )
            raise
        dur = time.time() - t0
        if resp.status_code != 200:
            record_api_call(
                provider="ElevenLabs",
                model="eleven-sound-generation",
                call_type="audio",
                duration_sec=dur,
                status_code=resp.status_code,
                error_message=resp.text[:200],
                project_source="sfx_gen",
            )
            err = RuntimeError(f"11labs sfx {resp.status_code}: {resp.text[:200]}")
            err.status_code = resp.status_code
            raise err
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)
        record_api_call(
            provider="ElevenLabs",
            model="eleven-sound-generation",
            call_type="audio",
            duration_sec=dur,
            cost_usd=0.01,
            media_count=1,
            status_code=200,
            project_source="sfx_gen",
            metadata={"prompt": prompt[:100], "duration": duration},
        )
        return out_path

    return out_path


def _record_provider_fallback(project: Project, idx: int, kind: str, exc: BaseException) -> dict[str, Any]:
    import httpx

    answered = isinstance(exc, RuntimeError) or isinstance(exc, httpx.ConnectError)
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    info = {
        "idx": idx,
        "kind": kind,
        "provider": "elevenlabs",
        "error_class": type(exc).__name__,
        "http_status": int(status) if isinstance(status, int) else None,
        "may_have_charged": "no" if answered else "uncertain",
    }
    logger.warning("[#{}] sfx_gen provider_fallback: {}", project.id, info)
    try:
        from app.services.observation import emit

        emit("provider_fallback", info)
    except Exception:
        pass
    return info




_PROGRESS = "sfx_progress"


def _load_progress(project: Project) -> dict[str, dict[str, Any]]:
    cp = ai_job_checkpoint(project, _PROGRESS)
    events = cp.get("events") if isinstance(cp, dict) else None
    inflight = cp.get("inflight") if isinstance(cp, dict) else None
    return {"events": dict(events) if isinstance(events, dict) else {},
            "inflight": dict(inflight) if isinstance(inflight, dict) else {}}


async def _save_progress(session: Any, project: Project, prog: dict[str, dict[str, Any]]) -> None:
    save_ai_job_checkpoint(project, _PROGRESS, copy.deepcopy(prog))
    await session.flush()
    commit = getattr(session, "commit", None)
    if callable(commit):
        await commit()


def _record_synthetic_substitute(project: Project, idx: int, kind: str) -> dict[str, Any]:
    info = {"idx": idx, "kind": kind, "provider": "elevenlabs", "error_class": "NoApiKey",
            "http_status": None, "may_have_charged": "no", "reason": "no_api_key", "synthetic": True}
    logger.warning("[#{}] sfx_gen: нет ключа ElevenLabs, событие {} ({}) — процедурный синтез (подмена)",
                   project.id, idx, kind)
    try:
        from app.services.observation import emit

        emit("provider_fallback", info)
    except Exception:
        pass
    return info


def _record_verify_substitute(project: Project, idx: int, kind: str) -> dict[str, Any]:
    info = {"idx": idx, "kind": kind, "provider": "elevenlabs", "error_class": "VerifyFailed",
            "http_status": None, "may_have_charged": "yes", "reason": "verify_failed", "synthetic": True}
    logger.warning("[#{}] sfx_gen: файл ElevenLabs события {} ({}) не прошёл приёмку — процедурный синтез (подмена)",
                   project.id, idx, kind)
    try:
        from app.services.observation import emit

        emit("provider_fallback", info)
    except Exception:
        pass
    return info


def _uncertain_fallback(project: Project, idx: int, kind: str) -> dict[str, Any]:
    info = {"idx": idx, "kind": kind, "provider": "elevenlabs", "error_class": "InterruptedAfterSend",
            "http_status": None, "may_have_charged": "uncertain"}
    logger.warning("[#{}] sfx_gen provider_fallback (обрыв после отправки): {}", project.id, info)
    try:
        from app.services.observation import emit

        emit("provider_fallback", info)
    except Exception:
        pass
    return info


def _retry_eligible(rec: dict[str, Any]) -> bool:
    fb = rec.get("provider_fallback")
    return (rec.get("provider") == "local_synth" and isinstance(fb, dict)
            and fb.get("may_have_charged") == "no" and not rec.get("provider_retry"))


async def generate_sfx_files(
    session: AsyncSession,
    project: Project,
    events: list[SfxEvent],
    *,
    force: bool = False,
    retry_degraded: bool = False,
) -> list[dict[str, Any]]:
    from app.settings import settings

    if not events:
        raise RuntimeError("sfx_gen: пустой план — сначала планировщик (sfx_plan)")

    sfx_dir = project.data_dir / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)

    existing = ai_job_checkpoint(project, "sfx_files")
    done_by_idx: dict[int, dict[str, Any]] = {}
    if isinstance(existing, dict) and isinstance(existing.get("files"), list):
        for rec in existing["files"]:
            if isinstance(rec, dict) and isinstance(rec.get("idx"), int):
                done_by_idx[rec["idx"]] = rec

    prog = _load_progress(project)
    if force:
        prog = {"events": {}, "inflight": {}}
    else:
        for rec in prog["events"].values():
            if isinstance(rec, dict) and isinstance(rec.get("idx"), int):
                done_by_idx[rec["idx"]] = rec

    meta = project.meta or {}
    forced_provider = meta.get("sfx_provider")
    use_api = bool(settings.elevenlabs_api_key) and forced_provider != "local_synth"
    files: list[dict[str, Any]] = []
    problems: list[str] = []
    fallbacks: list[dict[str, Any]] = []

    for idx, ev in enumerate(events):
        retry_of: dict[str, Any] | None = None
        sent_unknown = (not force) and str(idx) in prog["inflight"]
        if not force and idx in done_by_idx:
            rec = done_by_idx[idx]
            if Path(str(rec.get("path") or "")).is_file():
                if sent_unknown:
                    rec = {**rec, "provider_retry": 1, "provider_retry_outcome": "uncertain"}
                    prog["events"][str(idx)] = rec
                    prog["inflight"].pop(str(idx), None)
                    await _save_progress(session, project, prog)
                    files.append(rec)
                    continue
                if not (retry_degraded and use_api and _retry_eligible(rec)):
                    files.append(rec)
                    continue
                retry_of = rec
        ext = "mp3" if use_api else "wav"
        out_path = sfx_dir / f"sfx_{idx:02d}_{ev.kind}.{ext}"
        actual_provider = "elevenlabs" if use_api else "local_synth"
        fb_this: dict[str, Any] | None = None
        if sent_unknown:
            fb_this = _uncertain_fallback(project, idx, ev.kind)
            fallbacks.append(fb_this)
            out_path = sfx_dir / f"sfx_{idx:02d}_{ev.kind}.wav"
            _write_wav(out_path, _synth(ev.kind, ev.duration, seed=1000 + idx))
            actual_provider = "local_synth"
        else:
            try:
                if use_api:
                    req_key = sfx_request_key(ev.prompt, ev.duration)
                    cached = None if (force or retry_of is not None) else sfx_find_cached(sfx_dir, req_key)
                    if cached is not None:
                        if cached != out_path:
                            shutil.copy2(cached, out_path)
                            sfx_write_sidecar(out_path, req_key)
                        logger.info("[#{}] sfx_gen: событие {} с тем же запросом уже оплачено ({}), POST не нужен",
                                    project.id, idx, cached.name)
                    else:
                        prog["inflight"][str(idx)] = {"kind": ev.kind}
                        await _save_progress(session, project, prog)
                        await _elevenlabs_sfx(ev.prompt, ev.duration, out_path)
                        sfx_write_sidecar(out_path, req_key)
                else:
                    samples = _synth(ev.kind, ev.duration, seed=1000 + idx)
                    _write_wav(out_path, samples)
            except media_spend.ResumeOnlyBlocked:
                if prog["inflight"].pop(str(idx), None) is not None:
                    await _save_progress(session, project, prog)
                raise
            except Exception as e:
                logger.warning(
                    "[#{}] sfx_gen: событие {} ({}) упало: {} — локальный синтез",
                    project.id,
                    idx,
                    ev.kind,
                    e,
                )
                if use_api:
                    fb_this = _record_provider_fallback(project, idx, ev.kind, e)
                    fallbacks.append(fb_this)
                out_path = sfx_dir / f"sfx_{idx:02d}_{ev.kind}.wav"
                samples = _synth(ev.kind, ev.duration, seed=1000 + idx)
                _write_wav(out_path, samples)
                actual_provider = "local_synth"

        if actual_provider == "local_synth" and fb_this is None:
            fb_this = _record_synthetic_substitute(project, idx, ev.kind)
            fallbacks.append(fb_this)

        sfx_dur_tol = max(4.0, ev.duration * 2.0)
        errs = verify_audio_file(out_path, expect_duration=ev.duration, duration_tol=sfx_dur_tol)
        if errs:
            logger.warning("[#{}] sfx_gen verify on {}: {} — fallback to local synth", project.id, actual_provider, errs)
            if actual_provider != "local_synth":
                out_path = sfx_dir / f"sfx_{idx:02d}_{ev.kind}.wav"
                samples = _synth(ev.kind, ev.duration, seed=1000 + idx)
                _write_wav(out_path, samples)
                actual_provider = "local_synth"
                if fb_this is None:
                    fb_this = _record_verify_substitute(project, idx, ev.kind)
                    fallbacks.append(fb_this)
                errs = verify_audio_file(out_path, expect_duration=ev.duration, duration_tol=0.6)

        if errs:
            problems.extend(errs)
            logger.warning("[#{}] sfx_gen verify still failed: {}", project.id, errs)
            if prog["inflight"].pop(str(idx), None) is not None:
                await _save_progress(session, project, prog)
            continue
        rec_new = {
            "idx": idx,
            "path": str(out_path.resolve()),
            "frame_number": ev.frame_number,
            "t_start": ev.t_start,
            "duration": ev.duration,
            "kind": ev.kind,
            "gain": ev.gain,
            "duck": ev.duck,
            "provider": actual_provider,
            "prompt": ev.prompt,
            **({"provider_fallback": fb_this, "synthetic": True} if actual_provider == "local_synth" else {}),
            **({"provider_retry": 1} if retry_of is not None else {}),
        }
        files.append(rec_new)
        prog["events"][str(idx)] = rec_new
        prog["inflight"].pop(str(idx), None)
        await _save_progress(session, project, prog)

    if not files:
        raise RuntimeError(f"sfx_gen: ни один файл не прошёл верификацию: {problems}")

    save_ai_job_checkpoint(
        project, "sfx_files", {
            "files": files, "provider_fallbacks": len(fallbacks),
            "synthetic": sum(1 for f in files if f.get("provider") == "local_synth"),
            "no_api_key": not use_api})
    if not prog["inflight"]:
        drop_ai_job_checkpoint(project, _PROGRESS)
    await session.flush()
    logger.info(
        "[#{}] sfx_gen: {} файлов ({}), проблем {}, откатов провайдера {}",
        project.id,
        len(files),
        "elevenlabs" if use_api else "local_synth",
        len(problems),
        len(fallbacks),
    )
    return files


def load_sfx_files(project: Project) -> list[dict[str, Any]]:
    cached = ai_job_checkpoint(project, "sfx_files")
    if not isinstance(cached, dict) or not isinstance(cached.get("files"), list):
        return []
    out: list[dict[str, Any]] = []
    for rec in cached["files"]:
        if isinstance(rec, dict) and Path(str(rec.get("path") or "")).is_file():
            out.append(rec)
    return out
