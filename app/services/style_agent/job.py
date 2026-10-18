"""Состояние ноды ``style_extract`` в ``project.meta["style_agent"]`` + фоновый job.

Поток запуска (``run_style_extract``): ролики → детерминированное
извлечение → специалисты (vision/text, под бюджетом) → агрегация →
synth (1 вызов) → ``style_passport.draft.json`` + ``variance_report.md``.
Утверждение (``approve``) — отдельно, в ``hitl_style``: паспорт →
``style_passport.json`` → ``apply.materialize``.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from app.services.style_agent import META_KEY, STYLE_DIR_NAME, STYLE_EXTRACT_NODE_TYPE
from app.services.style_agent import aggregate, agents, extract, schema
from app.services.style_agent import blueprint as blueprint_mod
from app.services.style_agent import report as report_mod
from app.services.style_agent.budget import (
    DEFAULT_MAX_LLM_CALLS,
    PER_VIDEO_AGENTS,
    BudgetExceeded,
    LlmBudget,
    clamp_max_calls,
    estimate_cost,
    plan_calls,
)

VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"})
MAX_VIDEOS = 12
_tasks: dict[int, asyncio.Task[None]] = {}

DEFAULT_SETTINGS: dict[str, Any] = {
    "max_llm_calls": DEFAULT_MAX_LLM_CALLS,
    "min_support": aggregate.DEFAULT_MIN_SUPPORT,
    "iqr_threshold": aggregate.DEFAULT_IQR_THRESHOLD,
    "max_sheets_per_video": extract.DEFAULT_MAX_SHEETS,
    "frames_per_min": extract.DEFAULT_FRAMES_PER_MIN,
    "language": "ru",
    "style_id": "",
    "label": "",
    "skip_llm": False,
    "skip_asr": False,
    "skip_ocr": False,
    "agents": list(PER_VIDEO_AGENTS),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── пути ──────────────────────────────────────────────────────────────────


def style_dir(project: Any) -> Path:
    return Path(project.data_dir) / STYLE_DIR_NAME


def refs_dir(project: Any) -> Path:
    return style_dir(project) / "refs"


def analysis_dir(project: Any) -> Path:
    return style_dir(project) / "analysis"


def draft_path(project: Any) -> Path:
    return style_dir(project) / "style_passport.draft.json"


def approved_path(project: Any) -> Path:
    return style_dir(project) / "style_passport.json"


def report_path(project: Any) -> Path:
    return style_dir(project) / "style_report.md"


def llm_log_path(project: Any) -> Path:
    return style_dir(project) / "llm_calls.jsonl"


def safe_video_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._\-]+", "_", Path(name).name).strip("._") or "video"
    return base[:96]


def save_ref_upload(project: Any, filename: str, data: bytes) -> Path:
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTS:
        raise ValueError(f"не видео: {filename} (нужно {', '.join(sorted(VIDEO_EXTS))})")
    d = refs_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / safe_video_name(filename)
    n = 1
    while dest.exists():
        dest = d / f"{dest.stem}_{n}{dest.suffix}"
        n += 1
    dest.write_bytes(data)
    return dest


def remove_ref(project: Any, name: str) -> bool:
    fp = refs_dir(project) / safe_video_name(name)
    if fp.is_file():
        fp.unlink()
        shutil.rmtree(analysis_dir(project) / fp.stem, ignore_errors=True)
        return True
    return False


def list_ref_videos(project: Any, node_key: str | None = None) -> list[Path]:
    """Загруженные в ноду ролики + видео из подключённых нод «Файлы»."""
    out: list[Path] = []
    seen: set[str] = set()
    d = refs_dir(project)
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                out.append(p)
                seen.add(p.name)
    if node_key:
        try:
            from app.services.storage_node import _incoming_sources, is_storage_node_type, stored_paths
            from app.services.vibecode_catalog import find_canvas_node

            for src in _incoming_sources(project, node_key):
                node = find_canvas_node(project.meta, node_key=src)
                if node is None or not is_storage_node_type(str(node.get("type") or "")):
                    continue
                for p in stored_paths(project, src):
                    if p.suffix.lower() in VIDEO_EXTS and p.name not in seen:
                        out.append(p)
                        seen.add(p.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("style_agent: storage-источники не прочитаны: {}", exc)
    return out[:MAX_VIDEOS]


# ── meta ──────────────────────────────────────────────────────────────────


def get_meta(project: Any) -> dict[str, Any]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get(META_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def get_settings(project: Any) -> dict[str, Any]:
    cur = get_meta(project).get("settings")
    merged = dict(DEFAULT_SETTINGS)
    if isinstance(cur, dict):
        merged.update({k: v for k, v in cur.items() if k in DEFAULT_SETTINGS})
    merged["max_llm_calls"] = clamp_max_calls(merged.get("max_llm_calls"))
    return merged


def normalize_settings(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in (patch or {}).items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key == "max_llm_calls":
            out[key] = clamp_max_calls(val)
        elif key in ("min_support", "iqr_threshold"):
            try:
                out[key] = max(0.0, min(1.0, float(val)))
            except (TypeError, ValueError):
                continue
        elif key == "max_sheets_per_video":
            try:
                out[key] = max(1, min(12, int(val)))
            except (TypeError, ValueError):
                continue
        elif key == "frames_per_min":
            try:
                out[key] = max(30, min(600, int(val)))
            except (TypeError, ValueError):
                continue
        elif key in ("skip_llm", "skip_asr", "skip_ocr"):
            out[key] = bool(val)
        elif key == "agents":
            if isinstance(val, list):
                out[key] = [a for a in PER_VIDEO_AGENTS if a in {str(x) for x in val}]
        elif key == "style_id":
            out[key] = schema.slugify_style_id(str(val or "")) if str(val or "").strip() else ""
        else:
            out[key] = str(val or "").strip()
    return out


def set_meta(project: Any, patch: dict[str, Any]) -> dict[str, Any]:
    from app.services.project_meta import apply_project_meta_patch

    cur = get_meta(project)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(cur.get(key), dict):
            merged = dict(cur[key])
            merged.update(val)
            cur[key] = merged
        else:
            cur[key] = val
    apply_project_meta_patch(project, {META_KEY: cur}, source="style_agent")
    return cur


def job_state(project: Any) -> dict[str, Any]:
    job = get_meta(project).get("job")
    out = dict(job) if isinstance(job, dict) else {"status": "idle"}
    task = _tasks.get(int(getattr(project, "id", 0) or 0))
    if task is not None and not task.done():
        out["status"] = "running"
    elif out.get("status") == "running":
        out["status"] = "failed"
        out["error"] = out.get("error") or "процесс прерван (рестарт бэкенда)"
    return out


def is_running(project_id: int) -> bool:
    t = _tasks.get(project_id)
    return t is not None and not t.done()


def resolve_model(project: Any, node_key: str | None) -> dict[str, Any] | None:
    from app.services.vibecode_catalog import resolve_node_choice

    try:
        return resolve_node_choice(project.meta, node_key=node_key, node_type=STYLE_EXTRACT_NODE_TYPE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("style_agent: модель не определена: {}", exc)
        return None


def estimate(project: Any, node_key: str | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """План вызовов + цена до запуска (без LLM)."""
    st = get_settings(project)
    st.update(normalize_settings(overrides or {}))
    videos = list_ref_videos(project, node_key)
    plan = plan_calls(
        n_videos=len(videos),
        sheets_per_video=int(st["max_sheets_per_video"]),
        max_calls=int(st["max_llm_calls"]),
        agents=tuple(st["agents"]),
        with_synth=True,
        with_blueprint=True,
    )
    if st.get("skip_llm"):
        plan = {
            **plan,
            "per_video": {k: 0 for k in (plan.get("per_video") or {})},
            "planned_total": 0,
            "with_synth": False,
            "with_blueprint": False,
            "global_agents": [],
            "hint": "LLM отключён — только метрики, детерминированный blueprint",
        }
    model = resolve_model(project, node_key)
    cost = estimate_cost(plan, model=model, model_id=(model or {}).get("id"))
    return {
        "settings": st,
        "videos": [p.name for p in videos],
        "plan": plan,
        "cost": cost,
        "model": {"id": (model or {}).get("id"), "label": (model or {}).get("label")},
    }


def read_passport(project: Any, *, approved: bool = False) -> dict[str, Any] | None:
    fp = approved_path(project) if approved else draft_path(project)
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_passport(project: Any, passport: dict[str, Any], *, approved: bool = False) -> Path:
    fp = approved_path(project) if approved else draft_path(project)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(passport, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def snapshot(project: Any, node_key: str | None = None) -> dict[str, Any]:
    """Полное состояние для UI обеих нод."""
    meta = get_meta(project)
    st = get_settings(project)
    videos = list_ref_videos(project, node_key)
    draft = read_passport(project)
    approved = read_passport(project, approved=True)
    report = report_path(project)
    vids = []
    for p in videos:
        sheets_dir = analysis_dir(project) / p.stem / "sheets"
        sheets = (
            sorted(q.name for q in sheets_dir.iterdir() if q.is_file() and q.suffix.lower() in (".png", ".jpg", ".jpeg"))
            if sheets_dir.is_dir()
            else []
        )
        metrics_fp = analysis_dir(project) / p.stem / "metrics.json"
        vids.append(
            {
                "name": p.name,
                "size": p.stat().st_size if p.is_file() else 0,
                "uploaded": p.parent == refs_dir(project),
                "analyzed": metrics_fp.is_file(),
                "sheets": sheets,
            }
        )
    return {
        "settings": st,
        "job": job_state(project),
        "videos": vids,
        "passport": {
            "hasDraft": draft is not None,
            "hasApproved": approved is not None,
            "draftAt": (draft or {}).get("created_at"),
            "approvedAt": (meta.get("passport") or {}).get("approvedAt"),
            "style_id": (approved or draft or {}).get("style_id") or st.get("style_id") or "",
            "label": (approved or draft or {}).get("label") or "",
            "problems": schema.validate_passport(draft) if draft else [],
        },
        "report": report.read_text(encoding="utf-8") if report.is_file() else "",
        "apply": meta.get("apply") or {},
    }


# ── основной прогон (чистая функция; тесты вызывают напрямую) ─────────────


async def run_style_extract(
    videos: list[Path],
    out_root: Path,
    *,
    settings: dict[str, Any],
    budget: LlmBudget,
    project_id: int | None = None,
    model_id: str = "",
    asr_fn: extract.AsrFn | None = None,
    ocr_fn: Any = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    prompts_dir: Path | None = None,
) -> dict[str, Any]:
    """Ролики → паспорт-черновик v2 + отчёт. Возвращает {passport, per_video, report, budget, plan}.

    Порядок по ролику: детерминированное извлечение → ``shots`` (vision:
    планы + подтверждение склеек → слияние ложных) → ``visual`` → ``voice`` →
    ``narrative`` → ``patterns`` (видит разбор shots). Затем агрегация →
    ``synth`` (блоки промптов) → ``blueprint`` (цепочка нод) → отчёт.
    """

    def _p(**kw: Any) -> None:
        if progress:
            progress(kw)

    def _stopped() -> bool:
        return bool(should_stop and should_stop())

    out_root.mkdir(parents=True, exist_ok=True)
    log_path = out_root / "llm_calls.jsonl"
    skip_llm = bool(settings.get("skip_llm"))
    enabled_agents = tuple(a for a in PER_VIDEO_AGENTS if a in set(settings.get("agents") or PER_VIDEO_AGENTS))
    plan = plan_calls(
        n_videos=len(videos),
        sheets_per_video=int(settings.get("max_sheets_per_video") or extract.DEFAULT_MAX_SHEETS),
        max_calls=budget.max_calls,
        agents=enabled_agents,
        with_synth=True,
        with_blueprint=True,
    )
    videos_covered = int(plan.get("videos_covered") or 0) if not skip_llm else 0
    per_video_alloc = plan.get("per_video") or {}

    per_video: list[dict[str, Any]] = []
    for idx, video in enumerate(videos):
        if _stopped():
            break
        _p(stage="extract", video=video.name, index=idx, total=len(videos))
        vdir = out_root / "analysis" / video.stem
        try:
            profile = await asyncio.to_thread(
                extract.analyze_video,
                video,
                vdir,
                max_sheets=int(settings.get("max_sheets_per_video") or extract.DEFAULT_MAX_SHEETS),
                language=str(settings.get("language") or "ru"),
                asr_fn=asr_fn,
                skip_asr=bool(settings.get("skip_asr")),
                ocr_fn=ocr_fn,
                skip_ocr=bool(settings.get("skip_ocr")),
                frames_per_min=int(settings.get("frames_per_min") or extract.DEFAULT_FRAMES_PER_MIN),
                progress=lambda st, _v=video: _p(stage=f"extract:{st}", video=_v.name, index=idx, total=len(videos)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("style_extract: ролик {} не разобран", video.name)
            per_video.append({"video": video.name, "error": str(exc)[:400], "metrics": {}, "agents": {}})
            continue
        entry: dict[str, Any] = {"video": video.name, "metrics": profile, "agents": {}, "problems": {}}
        shots_final = [dict(s) for s in profile.get("shots") or []]
        if not skip_llm and idx < videos_covered:
            shots_reply: dict[str, Any] | None = None
            for role in enabled_agents:
                if _stopped():
                    break
                files = agents.sheets_for_role(role, profile) or None
                if role in ("voice", "narrative") and not (profile.get("speech") or {}).get("words"):
                    entry["problems"][role] = ["нет транскрипта (ни субтитров, ни ASR) — специалист пропущен"]
                    continue
                if role in ("shots", "visual", "patterns") and not files:
                    entry["problems"][role] = ["нет листов кадров — специалист пропущен"]
                    continue
                _p(stage="agent", video=video.name, agent=role, llm=budget.snapshot())
                try:
                    res = await agents.run_specialist(
                        role,
                        profile,
                        sheets=files,
                        budget=budget,
                        video_label=video.name,
                        project_id=project_id,
                        log_path=log_path,
                        prompts_dir=prompts_dir,
                        max_batches=int(per_video_alloc.get(role) or 1),
                        shots_reply=shots_reply,
                    )
                except BudgetExceeded as exc:
                    entry["problems"][role] = [str(exc)]
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("style_extract: {}/{} упал: {}", role, video.name, exc)
                    entry["problems"][role] = [str(exc)[:300]]
                    continue
                if res.get("reply"):
                    entry["agents"][role] = res["reply"]
                    (vdir / f"q_{role}.json").write_text(json.dumps(res["reply"], ensure_ascii=False, indent=2), encoding="utf-8")
                    if role == "shots":
                        shots_reply = res["reply"]
                        shots_final = agents.apply_false_cuts(shots_final, shots_reply.get("false_cuts"))
                        shots_final = agents.merge_shot_descriptions(shots_final, shots_reply)
                if res.get("problems"):
                    entry["problems"][role] = res["problems"]
        # закадр по планам (после возможного слияния ложных склеек)
        from app.services.style_agent import linkage as linkage_mod

        link2 = linkage_mod.link_subtitles_to_shots(profile.get("subtitles") or [], shots_final)
        for s, ls in zip(shots_final, link2.get("shots") or []):
            s["vo_text"] = ls.get("text") or ""
            s["line_ids"] = ls.get("line_ids") or []
        if shots_final != profile.get("shots"):
            profile["linkage"] = link2
            profile["motion"] = {**(profile.get("motion") or {}), **extract.shot_metrics(shots_final, float((profile.get("format") or {}).get("duration_s") or 0))}
        entry["shots_final"] = shots_final
        (vdir / "shots_final.json").write_text(json.dumps(shots_final, ensure_ascii=False, indent=1), encoding="utf-8")
        (vdir / "video_profile.json").write_text(
            json.dumps({k: v for k, v in entry.items() if k != "metrics"}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        per_video.append(entry)

    _p(stage="aggregate", llm=budget.snapshot())
    good = [v for v in per_video if not v.get("error")]
    passport = aggregate.build_passport(
        good,
        style_id=str(settings.get("style_id") or ""),
        label=str(settings.get("label") or ""),
        min_support=float(settings.get("min_support") or aggregate.DEFAULT_MIN_SUPPORT),
        iqr_threshold=float(settings.get("iqr_threshold") or aggregate.DEFAULT_IQR_THRESHOLD),
    )
    synth_reply: dict[str, Any] | None = None
    synth_problems: list[str] = []
    if not skip_llm and good and budget.can_reserve() and not _stopped():
        _p(stage="synth", llm=budget.snapshot())
        try:
            res = await agents.run_synth(passport, good, budget=budget, project_id=project_id, log_path=log_path, prompts_dir=prompts_dir)
            synth_reply = res.get("reply")
            synth_problems = list(res.get("problems") or [])
        except BudgetExceeded as exc:
            synth_problems = [str(exc)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("style_extract: synth упал: {}", exc)
            synth_problems = [str(exc)[:300]]
    aggregate.apply_synth(passport, synth_reply)
    aggregate.fallback_mapping(passport)
    if not passport.get("style_id"):
        passport["style_id"] = schema.slugify_style_id(passport.get("label") or (videos[0].stem if videos else "style"))
    if not passport.get("label"):
        passport["label"] = passport["style_id"]

    bp_reply: dict[str, Any] | None = None
    bp_problems: list[str] = []
    if not skip_llm and good and budget.can_reserve() and not _stopped():
        _p(stage="blueprint", llm=budget.snapshot())
        try:
            res = await agents.run_blueprint(passport, good, budget=budget, project_id=project_id, log_path=log_path, prompts_dir=prompts_dir)
            bp_reply = res.get("reply")
            bp_problems = list(res.get("problems") or [])
        except BudgetExceeded as exc:
            bp_problems = [str(exc)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("style_extract: blueprint упал: {}", exc)
            bp_problems = [str(exc)[:300]]
    passport["blueprint"] = blueprint_mod.build_blueprint(passport, bp_reply)

    passport["llm"] = {"calls_used": budget.used, "calls_limit": budget.max_calls, "model": model_id}
    passport["errors"] = {
        "videos": [{"video": v["video"], "error": v["error"]} for v in per_video if v.get("error")],
        "synth": synth_problems,
        "blueprint": bp_problems,
        "stopped": _stopped(),
    }
    _p(stage="report", llm=budget.snapshot())
    report = report_mod.render_report(passport)
    (out_root / "style_passport.draft.json").write_text(json.dumps(passport, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_root / "style_report.md").write_text(report, encoding="utf-8")
    (out_root / "variance_report.md").write_text(aggregate.variance_report(passport), encoding="utf-8")
    return {"passport": passport, "per_video": per_video, "report": report, "budget": budget.snapshot(), "plan": plan}


# ── фоновый job поверх проекта ────────────────────────────────────────────


async def _set_meta_retry(project_id: int, patch: dict[str, Any]) -> None:
    from app.models import Project
    from app.project_db import project_db_session_scope

    last: BaseException | None = None
    for attempt in range(1, 8):
        try:
            async with project_db_session_scope(project_id) as session:
                project = await session.get(Project, project_id)
                if project is None:
                    return
                set_meta(project, patch)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            if "locked" in str(exc).lower() and attempt < 7:
                await asyncio.sleep(min(1.5 * attempt, 8.0))
                continue
            raise
    if last is not None:
        raise last


async def _publish(project_id: int, status: str, extra: dict[str, Any] | None = None) -> None:
    try:
        from app.services.event_bus import publish_project_event

        await publish_project_event(
            project_id, event_type="project_updated", payload={"style_agent": True, "status": status, **(extra or {})}
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("style_agent: publish failed: {}", exc)


_style_cancel: set[int] = set()


def style_extract_cancel_requested(project_id: int) -> bool:
    return project_id in _style_cancel


def spawn_style_extract_job(project_id: int, *, node_key: str | None, overrides: dict[str, Any] | None = None) -> asyncio.Task[None]:
    prev = _tasks.get(project_id)
    if prev is not None and not prev.done():
        return prev
    _style_cancel.discard(project_id)
    task = asyncio.create_task(_run_job(project_id, node_key=node_key, overrides=overrides or {}), name=f"style-extract-{project_id}")
    _tasks[project_id] = task

    def _done(t: asyncio.Task[None]) -> None:
        _tasks.pop(project_id, None)

    task.add_done_callback(_done)
    return task


def _pipeline_text_override(model: dict[str, Any] | None) -> Any:
    """Как во всём пайплайне: текстовые вызовы разбора идут через 52pay (gpt-5.6-sol и т.п.), а не через vibecode."""
    from app.services.llm_override import NodeLlmOverride
    from app.services.pay52_routes import resolve_text
    from app.settings import settings

    mid = str((model or {}).get("id") or settings.gpt_model_effective or "gpt-5.6-sol")
    for cand in (mid, mid.replace("-vibecode", ""), "gpt-5.6-sol"):
        route = resolve_text(cand)
        if route is not None:
            return NodeLlmOverride(model_id=cand, channel="stable", kind="text", provider="52pay", label=f"52pay {route.api_model}")
    return None


async def _run_job(project_id: int, *, node_key: str | None, overrides: dict[str, Any]) -> None:
    from app.models import Project
    from app.project_db import project_db_session_scope
    from app.services.llm_override import use_override
    from app.services.step_cancel import is_stop_requested

    async with project_db_session_scope(project_id) as session:
        project = await session.get(Project, project_id)
        if project is None:
            return
        st = get_settings(project)
        st.update(normalize_settings(overrides))
        videos = list_ref_videos(project, node_key)
        root = style_dir(project)
        model = resolve_model(project, node_key)
        set_meta(
            project,
            {
                "settings": st,
                "node_key": node_key,
                "job": {
                    "status": "running",
                    "startedAt": _utc_now(),
                    "finishedAt": None,
                    "stage": "start",
                    "progress": {},
                    "error": None,
                    "llm": {"used": 0, "limit": int(st["max_llm_calls"])},
                    "videos": [p.name for p in videos],
                },
            },
        )
    await _publish(project_id, "running")

    budget = LlmBudget(int(st["max_llm_calls"]))
    loop = asyncio.get_running_loop()
    pending: list[dict[str, Any]] = []

    def _progress(info: dict[str, Any]) -> None:
        pending.append(info)
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_flush_progress()))

    async def _flush_progress() -> None:
        if not pending:
            return
        info = pending.pop()
        pending.clear()
        try:
            await _set_meta_retry(
                project_id,
                {"job": {"stage": str(info.get("stage") or ""), "progress": info, "llm": budget.snapshot()}},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("style_agent: progress not saved: {}", exc)

    try:
        ov = _pipeline_text_override(model)
        if ov is None:
            raise RuntimeError("нет маршрута 52pay для разбора стиля")
        if not videos:
            raise RuntimeError("нет роликов: загрузите mp4 в ноду или подключите «Файлы»")
        with use_override(ov):
            result = await run_style_extract(
                videos,
                root,
                settings=st,
                budget=budget,
                project_id=project_id,
                model_id=str((model or {}).get("id") or ""),
                progress=_progress,
                should_stop=lambda: (
                    style_extract_cancel_requested(project_id)
                    or is_stop_requested(project_id)
                ),
            )
        stopped = bool(result["passport"].get("errors", {}).get("stopped"))
        done_job: dict[str, Any] = {
            "status": "stopped" if stopped else "done",
            "finishedAt": _utc_now(),
            "stage": "done",
            "llm": budget.snapshot(),
            "error": None,
        }
        await _set_meta_retry(
            project_id,
            {
                "job": done_job,
                "passport": {"draftAt": _utc_now(), "style_id": result["passport"].get("style_id"), "approvedAt": None},
            },
        )
        await _publish(project_id, "stopped" if stopped else "done")
    except asyncio.CancelledError:
        await _set_meta_retry(project_id, {"job": {"status": "stopped", "finishedAt": _utc_now(), "llm": budget.snapshot()}})
        await _publish(project_id, "stopped")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("style_extract #{} failed", project_id)
        await _set_meta_retry(
            project_id,
            {"job": {"status": "failed", "finishedAt": _utc_now(), "error": str(exc)[:500], "llm": budget.snapshot()}},
        )
        await _publish(project_id, "failed")


def stop_job(project_id: int) -> bool:
    task = _tasks.get(project_id)
    if task is None or task.done():
        return False
    _style_cancel.add(project_id)
    task.cancel()
    return True


# ── approve / materialize ─────────────────────────────────────────────────


def approve(project: Any, passport: dict[str, Any] | None = None, *, materialize: bool = True, roots: Any = None) -> dict[str, Any]:
    """Утвердить паспорт (из UI или черновик) → style_passport.json (+ файлы Studio)."""
    from app.services.style_agent import apply as apply_mod

    data = passport or read_passport(project)
    if not data:
        raise ValueError("нет черновика паспорта — сначала запустите разбор")
    problems = schema.validate_passport(data)
    if problems:
        raise ValueError("паспорт не валиден: " + "; ".join(problems[:5]))
    data = dict(data)
    data["approved_at"] = _utc_now()
    write_passport(project, data, approved=True)
    write_passport(project, data, approved=False)
    report: dict[str, Any] = {"style_id": data.get("style_id"), "materialized": False}
    if materialize:
        report = apply_mod.materialize(data, roots=roots)
        report["materialized"] = True
        apply_mod.apply_to_project(project, report)
        try:
            (style_dir(project) / "apply_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    set_meta(
        project,
        {
            "passport": {"approvedAt": data["approved_at"], "style_id": data.get("style_id"), "draftAt": data.get("created_at")},
            "apply": {k: report.get(k) for k in ("style_id", "blocks", "frame_style", "skipped", "next_steps", "materialized", "at")},
        },
    )
    return report
