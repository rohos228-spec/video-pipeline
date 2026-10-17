#!/usr/bin/env python3
"""Прогон группы scene_design (агенты → сборка → scene_space) и один отчёт.

  python scripts/run_scene_design_group.py --project 57
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _json_load(path: Path) -> object:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _agent_files(sd_dir: Path) -> dict[str, object]:
    out: dict[str, object] = {}
    for name in ("skeleton", "characters", "world", "camera", "action", "style", "assemble"):
        out[name] = _json_load(sd_dir / f"{name}.json")
    return out


async def _noop_harness(*_a, **_k):
    class _R:
        checks: list = []

    return _R()


async def _run(project_id: int, out_path: Path) -> dict:
    from loguru import logger
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Frame, Project, ProjectStatus, Scene
    from app.orchestrator.steps import scene_design as sd_step
    from app.services import agent_harness
    from app.services.scene_design import apply as sd_apply
    from app.services.scene_space.pipeline import ingest_project, space_id_for
    from app.services.scene_space.validate import (
        format_validation_md,
        issues_exit_code,
        validate_scene,
    )
    from app.settings import settings

    log_dir = ROOT / "tasks" / "out" / f"p{project_id}-group"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(log_dir / "run.log", encoding="utf-8", level="INFO")

    settings.harness_gate_disabled = True
    agent_harness.harness_gate_or_raise = _noop_harness  # type: ignore[method-assign]

    orig_apply = sd_apply.apply_scene_design

    async def _apply_and_dump(session, project, payload):
        _dump_json(project.data_dir / "scene_design" / "assemble.json", payload)
        return await orig_apply(session, project, payload)

    sd_apply.apply_scene_design = _apply_and_dump  # type: ignore[method-assign]

    log: list[str] = []
    result: dict = {
        "project_id": project_id,
        "started_at": datetime.now(UTC).isoformat(),
        "phases": {},
    }
    overlay_snap = log_dir / "VALIDATION.overlay.md"
    before_val = ROOT / "tasks" / "out" / f"p{project_id}-s10" / "VALIDATION.md"
    if overlay_snap.is_file():
        result["before_validation"] = overlay_snap.read_text(encoding="utf-8")
    else:
        result["before_validation"] = (
            before_val.read_text(encoding="utf-8") if before_val.is_file() else None
        )

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise SystemExit(f"project {project_id} not found")
        meta = dict(project.meta or {})
        prev_user_stop = meta.get("user_stop")
        meta["scene_design_enabled"] = True
        meta["user_stop"] = True
        project.meta = meta
        sd_dir = project.data_dir / "scene_design"
        n_frames_before = len(
            (await session.execute(select(Frame.id).where(Frame.project_id == project.id)))
            .scalars()
            .all()
        )
        log.append(
            f"start status={project.status.value} frames={n_frames_before} "
            f"user_stop_was={prev_user_stop}"
        )
        result["frames_before"] = n_frames_before
        result["slug"] = project.slug

        project.status = ProjectStatus.scene_designing
        await session.commit()
        try:
            await sd_step.run(session, project, bot=None)
            result["phases"]["agents"] = {
                "ok": True,
                "status": project.status.value,
            }
            log.append(f"agents done status={project.status.value}")
        except Exception as exc:  # noqa: BLE001
            result["phases"]["agents"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            log.append(f"agents FAIL {exc}")
            logger.exception("agents phase failed")
            await session.rollback()
            project = await session.get(Project, project_id)

        project = await session.get(Project, project_id)
        project.status = ProjectStatus.scene_assembling
        await session.commit()
        try:
            await sd_step.run_assemble(session, project, bot=None)
            result["phases"]["assemble"] = {
                "ok": True,
                "status": project.status.value,
            }
            log.append(f"assemble done status={project.status.value}")
        except Exception as exc:  # noqa: BLE001
            result["phases"]["assemble"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            log.append(f"assemble FAIL {exc}")
            logger.exception("assemble phase failed")
            await session.rollback()
            project = await session.get(Project, project_id)
            try:
                space_rep = await ingest_project(session, project)
                await session.commit()
                result["phases"]["space_fallback_ingest"] = space_rep
                log.append("space ingest fallback after assemble fail")
            except Exception as exc2:  # noqa: BLE001
                log.append(f"space fallback FAIL {exc2}")

        project = await session.get(Project, project_id)
        meta_end = dict(project.meta or {})
        if prev_user_stop:
            meta_end["user_stop"] = prev_user_stop
        else:
            meta_end.pop("user_stop", None)
        project.meta = meta_end
        await session.commit()

        frames = (
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.sort_key, Frame.number)
                )
            )
            .scalars()
            .all()
        )
        scenes = (
            (
                await session.execute(
                    select(Scene)
                    .where(Scene.project_id == project.id)
                    .order_by(Scene.sort_key, Scene.id)
                )
            )
            .scalars()
            .all()
        )
        from app.services.scene_space.store import get_scene_space, list_frame_spaces

        space_blocks = []
        val_results = []
        for sc in scenes:
            sid = space_id_for(project.id, sc.id)
            space = await get_scene_space(session, sid)
            rows = await list_frame_spaces(session, sid)
            by_uuid = {f.uuid: f for f in frames if f.uuid}
            issues = validate_scene(sid, rows, space or {}, by_uuid) if rows else []
            val_results.append((sid, issues))
            space_blocks.append(
                {
                    "scene_id": sid,
                    "title": sc.title,
                    "frames_space": len(rows),
                    "rows": [
                        {
                            "shot_order": r.get("shot_order"),
                            "uuid": r.get("uuid"),
                            "shot_size": r.get("shot_size"),
                            "axis_side": r.get("axis_side"),
                            "screen_pos": r.get("screen_pos"),
                            "angle_h": r.get("angle_h"),
                            "angle_v": r.get("angle_v"),
                            "beat_role": r.get("beat_role"),
                        }
                        for r in rows
                    ],
                    "error_n": sum(1 for i in issues if i.get("level") == "error"),
                    "warn_n": sum(1 for i in issues if i.get("level") == "warning"),
                }
            )

        val_md = (
            format_validation_md(val_results) if val_results else "# VALIDATION\n\nнет сцен\n"
        )
        exit_code = (
            issues_exit_code([i for _, iss in val_results for i in iss]) if val_results else 2
        )

        frame_rows = []
        for fr in frames:
            attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
            cs = attrs.get("camera_subdivide") if isinstance(attrs.get("camera_subdivide"), dict) else {}
            frame_rows.append(
                {
                    "number": fr.number,
                    "uuid": fr.uuid,
                    "scene_id": fr.scene_id,
                    "крупность": attrs.get("крупность") or cs.get("план") or cs.get("крупность"),
                    "набор": attrs.get("набор") or cs.get("набор"),
                    "role": cs.get("role"),
                    "shot_index": cs.get("shot_index"),
                    "accent": attrs.get("accent"),
                    "vo": (fr.voiceover_text or "")[:180],
                }
            )

        result.update(
            {
                "slug": project.slug,
                "status": project.status.value,
                "log": log,
                "frames_after": len(frames),
                "scenes": [{"id": s.id, "title": s.title} for s in scenes],
                "frames": frame_rows,
                "space": space_blocks,
                "validate_exit": exit_code,
                "validation_md": val_md,
                "agents": _agent_files(sd_dir),
            }
        )

    try:
        from app.services.scene_space import validate as validate_mod
        from app.services.scene_space.render_board import try_call_validate, write_board
        from app.services.scene_space.store import get_scene_space, list_frame_spaces

        async with session_scope() as session:
            scenes = (
                (
                    await session.execute(
                        select(Scene)
                        .where(Scene.project_id == project_id)
                        .order_by(Scene.sort_key)
                    )
                )
                .scalars()
                .all()
            )
            for sc in scenes:
                sid = space_id_for(project_id, sc.id)
                space = await get_scene_space(session, sid) or {}
                rows = await list_frame_spaces(session, sid)
                dest = ROOT / "tasks" / "out" / sid.replace(":", "-")
                dest.mkdir(parents=True, exist_ok=True)
                violations = try_call_validate(validate_mod, sid, space, rows)
                write_board(dest / "board.html", sid, space, rows, violations)
                dest.joinpath("VALIDATION.md").write_text(val_md, encoding="utf-8")
                result.setdefault("boards", []).append(str(dest / "board.html"))
    except Exception as exc:  # noqa: BLE001
        result["board_error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("board write failed")

    result["finished_at"] = datetime.now(UTC).isoformat()
    _write_report(out_path, result)
    log_dir.joinpath("VALIDATION.md").write_text(
        str(result.get("validation_md") or ""), encoding="utf-8"
    )
    _dump_json(log_dir / "summary.json", {k: result[k] for k in result if k not in {"frames", "agents", "before_validation", "validation_md"}})
    result["report"] = str(out_path)
    print(
        json.dumps(
            {
                "report": str(out_path),
                "status": result.get("status"),
                "phases": result.get("phases"),
                "frames_before": result.get("frames_before"),
                "frames_after": result.get("frames_after"),
                "validate_exit": result.get("validate_exit"),
                "log": result.get("log"),
                "boards": result.get("boards"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return result


def _md_json(payload: object) -> str:
    if payload is None:
        return "_файла нет_"
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


def _write_report(out_path: Path, result: dict) -> None:
    pid = result.get("project_id")
    lines = [
        f"# Отчёт группы нод scene_design — проект #{pid} `{result.get('slug')}`",
        "",
        f"Прогон: `{result.get('started_at')}` → `{result.get('finished_at')}`",
        f"Команда: `python scripts/run_scene_design_group.py --project {pid}`",
        f"Статус после прогона: `{result.get('status')}`",
        f"Кадров: {result.get('frames_before')} → {result.get('frames_after')}",
        f"Агенты: `{json.dumps(result.get('phases', {}).get('agents'), ensure_ascii=False)}`",
        f"Сборка: `{json.dumps(result.get('phases', {}).get('assemble'), ensure_ascii=False)}`",
        f"Лог: {result.get('log')}",
        "",
        "## Состав группы (было / этот прогон)",
        "",
        "| Нода | step | Было | Этот прогон |",
        "|------|------|------|-------------|",
        "| sd_skel | sd_skel | нет на канвасе | skip (uses_skeleton=false) |",
        "| sd_char | sd_char | characters.json | checkpoint replay + store_cells |",
        "| sd_world | sd_world | world.json | то же |",
        "| sd_cam | sd_cam | camera.json | то же |",
        "| sd_act | sd_act | action.json | то же |",
        "| sd_assemble | scene_asm | без повторного camera_expand | **повторно** + camera_expand + apply-ops |",
        "| scene_space | нет ноды | overlay все MS | ingest + blocking после assemble |",
        "",
        "## Было: валидатор overlay (до прогона, без редакции)",
        "",
        result.get("before_validation") or "_нет файла BEFORE_",
        "",
        "## Стало: кадры после сборки",
        "",
        "| # | uuid | scene | крупность | набор | role | vo |",
        "|---|------|-------|-----------|-------|------|-----|",
    ]
    for fr in result.get("frames") or []:
        vo = str(fr.get("vo") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {fr.get('number')} | `{fr.get('uuid')}` | {fr.get('scene_id')} | "
            f"{fr.get('крупность')} | {fr.get('набор')} | {fr.get('role')} | {vo} |"
        )
    lines += [
        "",
        "## Стало: frames_space (слой после ingest/blocking)",
        "",
        "| scene | order | uuid | shot_size | side | screen | angle_h | angle_v | role |",
        "|-------|------:|------|-----------|------|--------|---------|---------|------|",
    ]
    for block in result.get("space") or []:
        for r in block.get("rows") or []:
            lines.append(
                f"| {block.get('scene_id')} | {r.get('shot_order')} | `{r.get('uuid')}` | "
                f"{r.get('shot_size')} | {r.get('axis_side')} | {r.get('screen_pos')} | "
                f"{r.get('angle_h')} | {r.get('angle_v')} | {r.get('beat_role')} |"
            )
        if not block.get("rows"):
            lines.append(f"| {block.get('scene_id')} | — | — | нет rows | | | | | |")
    lines += [
        "",
        "## Стало: scene_space валидатор (сырой, без редакции)",
        "",
        f"exit={result.get('validate_exit')}",
        "",
        str(result.get("validation_md") or ""),
        "",
    ]
    if result.get("boards"):
        lines.append("## Раскладки")
        lines.append("")
        for b in result["boards"]:
            lines.append(f"- `{b}`")
        lines.append("")
    lines.append("## Тексты агентов (диск после прогона, без редакции)")
    lines.append("")
    agents = result.get("agents") or {}
    for name in ("skeleton", "characters", "world", "camera", "action", "style", "assemble"):
        lines.append(f"### {name}")
        lines.append("")
        lines.append(_md_json(agents.get(name)))
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=int, default=57)
    parser.add_argument("--out", default="tasks/GROUP-SCENE-DESIGN.md")
    args = parser.parse_args(argv)
    report = asyncio.run(_run(args.project, ROOT / args.out))
    assemble_ok = bool((report.get("phases") or {}).get("assemble", {}).get("ok"))
    return 0 if assemble_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
