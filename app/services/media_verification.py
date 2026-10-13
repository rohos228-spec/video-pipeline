"""Independent result verification for paid media nodes (observation level, read-only).

After a native step returns (or fails) the supervisor re-reads what the step left behind with a
separate session and checks it structurally: the file exists, is not empty, decodes (image) or
probes (video/audio: streams, duration), lies inside the project folder, and has a provider trail.
``images`` is checked per frame (one image per frame with an image prompt, bound to the frame by an
Artifact row, decodes, size and dimensions at the native scene threshold); the voice file is
additionally checked by app/services/audio_verification.py (silence, format, zero duration).
It never changes a row or a file, never starts a provider request and never holds the run: it only
journals ``node_output`` and ``node_result_verification`` so diagnosis can show a typed incident.

What it does NOT prove: that the picture, clip or voice is any good (semantic_quality_verified is
always False), nor that a provider charged exactly once. The provider trail is the append-only
``provider_task_ledger``; a Kie task has a durable id there, an Outsee call has none.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from app.services import spend_guard

MEDIA_NODE_TYPES = ("images", "videos", "hero", "items", "music", "audio", "assemble")
MIN_IMAGE_SIDE = 256  # shorter side in pixels below which a "scene image" cannot be a real generation result
VERIFY_BUDGET_S = 90.0
PER_FILE_TIMEOUT_S = 25.0
MAX_ITEMS = 200
FACT_ITEMS = 40

_HEX_TAIL = re.compile(r"_[0-9a-f]{8}$")
_FRAME_FILE = re.compile(r"^frame_(\d+)_")

NODE_LABEL = {"images": "картинки кадров", "videos": "клипы", "hero": "герои", "items": "предметы", "music": "музыка",
              "audio": "озвучка", "assemble": "финальное видео"}


def _key_of(path: str | Path) -> str:
    return _HEX_TAIL.sub("", Path(str(path)).stem)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return Path(str(path)).name


async def _ledger(project_id: int) -> dict[str, Any]:
    """Tasks per key and unresolved submit intents. Never raises: 'unavailable' is a fact, not a pass."""
    try:
        from app.db import SessionLocal
        async with SessionLocal() as session:
            rows = await spend_guard.list_provider_tasks(session, project_id, limit=2000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("media_verification: ledger unavailable: {}", type(exc).__name__)
        return {"state": "unavailable", "tasks": {}, "unresolved": []}
    tasks: dict[str, list[dict]] = {}
    unresolved: list[dict] = []
    for row in rows:
        if str(row["task_id"]).startswith(spend_guard.INTENT_PREFIX):
            if row["status"] == "submit_unknown":
                unresolved.append({"key": row["key"], "provider": row["provider"] or "kie",
                                   "intent_id": row["task_id"][len(spend_guard.INTENT_PREFIX):]})
            continue
        tasks.setdefault(row["key"], []).append(row)
    return {"state": "ok", "tasks": tasks, "unresolved": unresolved}


def provenance(ledger: dict[str, Any], key: str) -> dict[str, Any]:
    """Who made this file, as far as the ledger can tell."""
    if ledger["state"] != "ok":
        return {"provider": "unknown", "durable_task_id": False, "reason": "ledger_unavailable"}
    rows = ledger["tasks"].get(key) or []
    if rows:
        done = [r for r in rows if r["status"] == "downloaded"]
        row = (done or rows)[-1]
        out = {"provider": row["provider"] or "kie", "durable_task_id": True, "task_id": row["task_id"],
               "task_status": row["status"]}
        if not done:
            out["note"] = "file is present but the task is not marked downloaded"
        return out
    if any(u["key"] == key and u["provider"] == "outsee" for u in ledger["unresolved"]) or key.startswith("clip_"):
        # an Outsee call writes an intent row only: no task id exists to resume or reconcile
        return {"provider": "outsee_or_untracked", "durable_task_id": False, "reason": "no_task_id_in_ledger"}
    return {"provider": "unknown", "durable_task_id": False, "reason": "no_ledger_row"}


async def _probe(path: Path, *, root: Path, kind: str, expected: dict | None = None) -> dict[str, Any]:
    from app.services.artifact_verifier import verify_artifact
    try:
        res = await asyncio.wait_for(verify_artifact(path, root=root, kind=kind, expected=expected),
                                     PER_FILE_TIMEOUT_S)
    except ValueError:
        return {"status": "failed", "reason": "path_outside_project"}
    except asyncio.TimeoutError:
        return {"status": "unknown", "reason": "probe_timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "reason": type(exc).__name__}
    keep = ("status", "reason", "bytes", "sha256", "duration", "width", "height", "streams", "format")
    return {k: res[k] for k in keep if k in res}


def _item(key: str, path: Path | None, root: Path, probe: dict | None, prov: dict | None, **extra) -> dict:
    row = {"key": key, "path": _rel(path, root) if path else None, **(probe or {}), **extra}
    if "status" not in row:
        row["status"] = "failed"
    if prov is not None:
        row["provenance"] = prov
    return row


def _existing(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    try:
        return path if path.is_file() else None
    except OSError:
        return None


async def _artifacts(reader, project_id: int, kind):
    from sqlalchemy import select
    from app.models import Artifact
    return list((await reader.scalars(select(Artifact).where(
        Artifact.project_id == project_id, Artifact.kind == kind).order_by(Artifact.id.desc()))).all())


async def _images(reader, project, ledger, budget) -> tuple[list[dict], dict]:
    """Scene images (shot 1) of every frame that has an image prompt: one item per frame.

    Per frame: an Artifact row of kind scene_image bound to THAT frame whose file exists (the business
    record), the file is not empty, decodes as PNG/JPEG/WEBP/GIF, is not smaller than the native scene
    threshold (scan_frames.is_valid_scene_image) nor than MIN_IMAGE_SIDE pixels, and the ledger trail
    is reported (a Kie task id, or none for Outsee). A file on disk without an Artifact row is
    ``unknown`` (the business record is missing), a frame without any file is ``failed``.
    """
    from pathlib import Path as _P
    from sqlalchemy import select
    from app.generation_options import is_skippable_empty_prompt
    from app.models import ArtifactKind, Frame
    from app.services.plan_shot2 import effective_shot_from_artifact
    from app.services.scan_frames import frame_image_prompt_text, is_valid_scene_image, newest_frame_image_path
    from app.services.vo_shot_expand import is_coverage_leftover
    frames = list((await reader.scalars(select(Frame).where(Frame.project_id == project.id)
                                        .order_by(Frame.number))).all())
    targets = [f for f in frames if not is_coverage_leftover(f)
               and not is_skippable_empty_prompt(frame_image_prompt_text(f, frames))]
    arts = await _artifacts(reader, project.id, ArtifactKind.scene_image)
    by_frame: dict[int, list] = {}
    for art in arts:
        by_frame.setdefault(art.frame_id, []).append(art)
    root = project.data_dir
    items = []
    for frame in targets[:MAX_ITEMS]:
        key = f"frame_{frame.number:03d}"
        path = next((p for a in by_frame.get(frame.id, []) if (p := _existing(a.path))
                     and effective_shot_from_artifact(a.meta, p) == 1), None)
        bound = path is not None
        if path is None:
            path = newest_frame_image_path(_P(root) / "scenes", frame.number)
        if path is None:
            items.append(_item(key, None, root, {"status": "failed", "reason": "missing_image"},
                               provenance(ledger, key), frame_number=frame.number, artifact_bound=False))
            continue
        probe = await _probe(path, root=root, kind="image") if budget() else {"status": "unknown",
                                                                             "reason": "verify_budget"}
        named = _FRAME_FILE.match(path.name)
        if probe.get("status") == "passed" and named and int(named.group(1)) != frame.number:
            probe = {**probe, "status": "failed", "reason": "file_belongs_to_another_frame"}
        if probe.get("status") == "passed":
            small = min(int(probe.get("width") or 0), int(probe.get("height") or 0)) < MIN_IMAGE_SIDE
            if small:
                probe = {**probe, "status": "failed", "reason": "image_dimensions_too_small"}
            elif not is_valid_scene_image(path):
                probe = {**probe, "status": "failed", "reason": "image_too_small_for_scene"}
            elif not bound:
                probe = {**probe, "status": "unknown", "reason": "no_artifact_row_for_frame"}
        items.append(_item(key, path, root, probe, provenance(ledger, key), frame_number=frame.number,
                           artifact_bound=bound))
    passed = sum(1 for i in items if i["status"] == "passed")
    return items, {"targets": len(targets), "frames": len(frames), "images_total": len(arts),
                   "images_ok": passed, "images_equal_targets": passed == len(targets)}


async def _videos(reader, project, ledger, budget) -> tuple[list[dict], dict]:
    from sqlalchemy import select
    from app.models import ArtifactKind, Frame
    from app.services.plan_shot2 import effective_shot_from_artifact
    frames = list((await reader.scalars(select(Frame).where(Frame.project_id == project.id)
                                        .order_by(Frame.number))).all())
    targets = [f for f in frames if str(f.animation_prompt or "").strip()]
    arts = await _artifacts(reader, project.id, ArtifactKind.scene_video)
    by_frame: dict[int, list] = {}
    for art in arts:
        by_frame.setdefault(art.frame_id, []).append(art)
    root = project.data_dir
    items = []
    for frame in targets[:MAX_ITEMS]:
        key = f"clip_{frame.number:03d}"
        path = next((p for a in by_frame.get(frame.id, []) if (p := _existing(a.path))
                     and effective_shot_from_artifact(a.meta, p) == 1), None)
        if path is None:
            items.append(_item(key, None, root, {"status": "failed", "reason": "missing_clip"},
                               provenance(ledger, key), frame_number=frame.number))
            continue
        probe = await _probe(path, root=root, kind="video") if budget() else {"status": "unknown",
                                                                               "reason": "verify_budget"}
        items.append(_item(key, path, root, probe, provenance(ledger, key), frame_number=frame.number))
    return items, {"targets": len(targets), "frames": len(frames), "clips_total": len(arts)}


async def _heroes(reader, project, ledger, budget) -> tuple[list[dict], dict]:
    from app.models import ArtifactKind
    arts = await _artifacts(reader, project.id, ArtifactKind.hero_reference)
    root = project.data_dir
    items, seen = [], set()
    for art in arts:
        if art.path in seen:
            continue
        seen.add(art.path)
        path = _existing(art.path)
        key = _key_of(art.path) if art.path else f"hero_artifact_{art.id}"
        if path is None:
            items.append(_item(key, None, root, {"status": "failed", "reason": "missing_file"},
                               provenance(ledger, key), artifact_id=art.id))
        else:
            probe = await _probe(path, root=root, kind="image") if budget() else {"status": "unknown",
                                                                                   "reason": "verify_budget"}
            items.append(_item(key, path, root, probe, provenance(ledger, key), artifact_id=art.id))
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
        # Шаг героя законно пропущен: флаг шага, режим «без героя» или нулевое число героев - ровно те
        # условия, по которым сам шаг generate_hero ничего не создаёт. Нет файла - не сбой (F10-D3, N-13).
        if (meta.get("hero_skipped_empty") or getattr(project, "hero_mode", None) == "no_hero"
                or getattr(project, "hero_count", None) == 0):
            return items, {"artifacts": len(arts), "skipped": "hero_skipped_empty"}
        items.append({"key": "hero", "status": "failed", "reason": "no_hero_artifact", "path": None})
    return items, {"artifacts": len(arts)}


async def _items(reader, project, ledger, budget) -> tuple[list[dict], dict]:
    from app.models import ArtifactKind
    wanted = [d for d in (getattr(project, "item_descriptions", None) or []) if isinstance(d, str) and d.strip()]
    arts = await _artifacts(reader, project.id, ArtifactKind.item_reference)
    by_index: dict[int, Any] = {}
    for art in arts:
        idx = (art.meta or {}).get("item_index")
        if isinstance(idx, int) and idx not in by_index and _existing(art.path):
            by_index[idx] = art
    root = project.data_dir
    items = []
    for idx in range(1, len(wanted) + 1):
        key = f"predmet{idx}"
        art = by_index.get(idx)
        if art is None:
            items.append(_item(key, None, root, {"status": "failed", "reason": "missing_item"},
                               provenance(ledger, key), item_index=idx))
            continue
        path = _existing(art.path)
        probe = await _probe(path, root=root, kind="image") if budget() else {"status": "unknown",
                                                                               "reason": "verify_budget"}
        items.append(_item(key, path, root, probe, provenance(ledger, key), item_index=idx))
    return items, {"descriptions": len(wanted), "artifacts": len(arts)}


async def _music(reader, project, ledger, budget) -> tuple[list[dict], dict]:
    from app.models import ArtifactKind
    arts = await _artifacts(reader, project.id, ArtifactKind.music)
    root = project.data_dir
    path = next((p for a in arts if (p := _existing(a.path))), None)
    if path is None:
        return [{"key": "music", "status": "failed", "reason": "missing_music", "path": None,
                 "provenance": provenance(ledger, "music")}], {"artifacts": len(arts)}
    probe = await _probe(path, root=root, kind="audio")
    return [_item("music", path, root, probe, provenance(ledger, "music"))], {"artifacts": len(arts)}


async def _audio_content_check(path: Path, probe: dict) -> tuple[dict, dict | None]:
    """Content check of the voice file (owned by app/services/audio_verification.py, step C04): silence,
    format, zero duration. Only runs on a file that already probed; a checker failure is never a pass."""
    if probe.get("status") != "passed":
        return probe, None
    try:
        from app.services.audio_verification import verify_audio
        verdict = await asyncio.wait_for(asyncio.to_thread(verify_audio, path), PER_FILE_TIMEOUT_S * 6)
    except Exception as exc:  # noqa: BLE001
        return {**probe, "status": "unknown", "reason": f"audio_check_{type(exc).__name__}"}, None
    data = {"ok": verdict.ok, "codes": list(verdict.codes), "silent": verdict.silent,
            "peak_db": verdict.peak_db, "rms_db": verdict.rms_db}
    if verdict.ok:
        return probe, data
    hard = [c for c in verdict.codes if c != "level_unknown"]
    if hard:
        return {**probe, "status": "failed", "reason": hard[0]}, data
    return {**probe, "status": "unknown", "reason": "level_unknown"}, data


async def _audio(reader, project, ledger, budget) -> tuple[list[dict], dict]:
    from sqlalchemy import select
    from app.models import ArtifactKind, Frame
    arts = await _artifacts(reader, project.id, ArtifactKind.audio)
    root = project.data_dir
    path = next((p for a in arts if (p := _existing(a.path))), None)
    if path is None:
        return [{"key": "voice", "status": "failed", "reason": "missing_voice_file", "path": None}], {
            "artifacts": len(arts)}
    probe = await _probe(path, root=root, kind="audio")
    probe, audio_check = await _audio_content_check(path, probe)
    art = next(a for a in arts if _existing(a.path) == path)
    frames = list((await reader.scalars(select(Frame).where(Frame.project_id == project.id))).all())
    spoken = sum(1 for f in frames if str(f.voiceover_text or "").strip())
    clips = (art.meta or {}).get("clip_count")
    extra: dict[str, Any] = {"source": (art.meta or {}).get("source"), "clip_count": clips, "spoken_frames": spoken}
    if audio_check is not None:
        extra["audio_check"] = audio_check
    if probe.get("status") == "passed" and isinstance(clips, int) and clips != spoken:
        probe = {**probe, "status": "unknown", "reason": "clip_count_differs_from_spoken_frames"}
    return [_item("voice", path, root, probe, None, **extra)], {"artifacts": len(arts), "spoken_frames": spoken}


def _tts_markers(project) -> list[dict]:
    try:
        from app.services.elevenlabs_api import _UNCERTAIN_PREFIX
        directory = Path(project.data_dir) / "audio"
        return [{"key": p.name, "provider": "elevenlabs", "intent_id": p.name[len(_UNCERTAIN_PREFIX):]}
                for p in sorted(directory.glob(_UNCERTAIN_PREFIX + "*"))][:20]
    except Exception:  # noqa: BLE001
        return []


async def _assemble(reader, project, ledger, budget) -> tuple[list[dict], dict]:
    from sqlalchemy import select
    from app.models import ArtifactKind, HITLKind, HITLRequest
    arts = await _artifacts(reader, project.id, ArtifactKind.final_video)
    root = project.data_dir
    if not arts:
        return [{"key": "final", "status": "failed", "reason": "no_final_artifact", "path": None}], {"artifacts": 0}
    art = arts[0]
    path = _existing(art.path)
    if path is None:
        return [{"key": "final", "status": "failed", "reason": "missing_final_file", "path": None}], {
            "artifacts": len(arts)}
    probe = await _probe(path, root=root, kind="assembly")
    recorded = (art.meta or {}).get("sha256")
    if probe.get("status") == "passed" and recorded and recorded != probe.get("sha256"):
        probe = {**probe, "status": "failed", "reason": "digest_differs_from_recorded"}
    requests = list((await reader.scalars(select(HITLRequest).where(
        HITLRequest.project_id == project.id, HITLRequest.kind == HITLKind.approve_final))).all())
    sha = probe.get("sha256")
    approval = {"requests_for_this_file": sum(1 for r in requests if (r.payload or {}).get("sha256") == sha),
                "pending": sum(1 for r in requests if getattr(r.decision, "value", r.decision) == "pending"),
                "human_barrier": "approve_final"}
    return [_item("final", path, root, probe, None, approval=approval)], {"artifacts": len(arts)}


_COLLECTORS = {"images": _images, "videos": _videos, "hero": _heroes, "items": _items, "music": _music, "audio": _audio,
               "assemble": _assemble}


def _summary(node_type: str, status: str, items: list[dict], unknown: list[dict]) -> str:
    bad = [i for i in items if i["status"] == "failed"]
    soft = [i for i in items if i["status"] == "unknown"]
    label = NODE_LABEL.get(node_type, node_type)
    parts = []
    if status == "passed":
        parts.append(f"{label}: проверено файлов {len(items)}, структура в порядке (качество не оценивалось)")
    elif bad:
        names = ", ".join(f"{i['key']} ({i.get('reason') or 'не прошёл'})" for i in bad[:8])
        parts.append(f"{label}: не прошли проверку {len(bad)} из {len(items)}: {names}")
    else:
        names = ", ".join(f"{i['key']} ({i.get('reason') or 'не проверен'})" for i in soft[:8])
        parts.append(f"{label}: результат не подтверждён у {len(soft)} из {len(items)}: {names}")
    if unknown:
        keys = ", ".join(sorted({str(u['key']) for u in unknown})[:8])
        parts.append(f"исход прошлой платной отправки неизвестен ({keys}): повторная отправка запрещена, решает человек")
    return "; ".join(parts) + "."


async def build_proof(reader, project, node_type: str) -> dict[str, Any]:
    """The verification proof of one media node. ``reader`` is an independent read-only session."""
    started = time.monotonic()
    budget = lambda: time.monotonic() - started < VERIFY_BUDGET_S  # noqa: E731
    ledger = await _ledger(project.id)
    items, counts = await _COLLECTORS[node_type](reader, project, ledger, budget)
    keys = {i["key"] for i in items}
    unknown = [u for u in ledger["unresolved"] if _family_match(node_type, u["key"])]
    if node_type == "audio":
        unknown = _tts_markers(project)
    statuses = {i["status"] for i in items}
    status = "failed" if "failed" in statuses else ("unknown" if (not items or "unknown" in statuses) else "passed")
    reason = next((i.get("reason") for i in items if i["status"] != "passed"), None)
    if not items:
        if node_type == "items" and not counts.get("descriptions"):
            status, reason = "passed", "no_item_descriptions"  # the step had nothing to generate
        elif node_type == "hero" and counts.get("skipped") == "hero_skipped_empty":
            status, reason = "passed", "hero_skipped_empty"  # законно пропущенный шаг: нет описания героя
        else:
            reason = "no_targets"
    return {"contract": "native_media", "node_type": node_type, "status": status,
            "reason": reason,
            "summary": _summary(node_type, status, items, unknown), "counts": counts,
            "items_total": len(items), "items_passed": sum(1 for i in items if i["status"] == "passed"),
            "ledger": ledger["state"], "outcome_unknown": unknown[:20],
            "keys": sorted(keys)[:FACT_ITEMS], "items": items[:FACT_ITEMS],
            "business_commit_claim": False, "semantic_quality_verified": False,
            "source": "independent read session; files re-read from disk"}


def _family_match(node_type: str, key: str) -> bool:
    from app.services.media_recovery import KEY_FAMILY
    family = KEY_FAMILY.get(node_type)
    return bool(family and family.match(key or ""))


# Local nodes (no provider job) whose failure is typed by the attempt's OWN outcome, not by the files on disk.
LOCAL_ATTEMPT_NODES = ("assemble",)


def _retryable_by_node(node_type: str, exc: BaseException) -> bool:
    """Does the node itself treat this failure as repeatable (the same rule as its in-node retry)? Fail-closed."""
    try:
        if node_type == "assemble":
            from app.orchestrator.steps.assemble import _assemble_retryable
            return bool(_assemble_retryable(exc))
    except Exception:  # noqa: BLE001
        return False
    return False


def typed_attempt_failure(proof: dict[str, Any], exc: BaseException, node_type: str) -> dict[str, Any]:
    """The failed attempt produced NO new verified artifact. A file an earlier run left behind (a final.mp4 that
    passes verify_artifact) is not its result: the proof says ``failed`` with the attempt's own error type, and
    the files read back are marked as stale. Without this the proof would pass on the old file and the failure
    would reach diagnosis as a plain contract_rejected instead of a typed media failure."""
    label = NODE_LABEL.get(node_type, node_type)
    error_type = type(exc).__name__
    items = [{**i, "stale_earlier_result": True} for i in proof.get("items", [])]
    return {**proof, "status": "failed", "reason": f"attempt_failed:{error_type}", "items": items,
            "items_passed": 0,
            "attempt_failure": {"error_type": error_type, "retryable_by_node": _retryable_by_node(node_type, exc),
                                "new_verified_artifact": False},
            "summary": (f"{label}: попытка завершилась ошибкой ({error_type}); нового проверенного результата нет, "
                        "файл на диске относится к прежней сборке.")}


async def observe_media_result(session, project, node_type):
    """Verifier hook (named registry): journals the proof; never raises, never holds the run."""
    from app.services.observation import _attempt, record_fact
    if node_type not in _COLLECTORS:
        return None
    ctx = _attempt.get()
    if ctx is None or session is None:
        return None
    if getattr(ctx, "media_result_observed", False):
        return None
    ctx.media_result_observed = True
    proof = {"contract": "native_media", "node_type": node_type, "status": "unknown",
             "reason": "committed native media result unavailable", "summary": "Результат не удалось перечитать.",
             "items": [], "outcome_unknown": [], "business_commit_claim": False,
             "semantic_quality_verified": False}
    full: dict[str, Any] = {"project_id": ctx.scope.project_id, "node_type": node_type}
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.models import Project as ProjectModel
        failure = getattr(ctx, "attempt_exception", None)
        from sqlalchemy.ext.asyncio import AsyncSession
        if failure is None and isinstance(session, AsyncSession) and project is not None:
            # A successful step writes its result rows (final artifact, clip/item artifacts recovered from disk,
            # approve requests) in the worker's own transaction, which commits only after this hook returns (the
            # step only flushes). An independent connection would still see the EARLIER rows and call a successful
            # step failed (missing_clip / missing_item). Read the flushed rows of this attempt through its own
            # session, without flushing anything (the files are probed from disk either way). A failed attempt keeps
            # the independent read: its uncommitted rows may be rolled back and must not count as proof.
            with session.no_autoflush:
                proof = await build_proof(session, project, node_type)
        else:
            async with async_sessionmaker(session.bind, expire_on_commit=False, autoflush=False)() as reader:
                native = await reader.get(ProjectModel, ctx.scope.project_id)
                if native is None:
                    raise LookupError("native media project unavailable")
                proof = await build_proof(reader, native, node_type)
            if failure is not None and node_type in LOCAL_ATTEMPT_NODES:
                proof = typed_attempt_failure(proof, failure, node_type)
        full["items"] = proof["items"]
    except Exception as exc:  # noqa: BLE001
        proof.update(reason=f"{type(exc).__name__}", error_type=type(exc).__name__)
    await record_fact("node_output", {"contract": "native_media", "node_type": node_type,
                                      "items_total": proof.get("items_total"),
                                      "source": "committed original executor rows and files"}, body=full)
    await record_fact("node_result_verification", proof, body=proof)
    return proof
