"""Цикл checkMode vision: FAIL → db_patch → точечный regen (hero|scenes) → снова check.

Meta keys:
  vision_check_return_node — node_key проверочной ноды
  vision_check_kind        — hero | scenes
  vision_check_round       — int
  hero_check_regen_ids     — list[str] (совместимость с generate_hero)
  hero_check_return_node   — зеркало return для старого hero_check_regen
  hero_check_round         — зеркало round
  scene_check_regen        — list[{number, shot}]
  vision_check_passed      — list[str] уже pass (c01 / f3 / f7s2)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Artifact, ArtifactKind, Frame, Project, ProjectStatus

META_RETURN = "vision_check_return_node"
META_KIND = "vision_check_kind"
META_ROUND = "vision_check_round"
META_SCENE = "scene_check_regen"
META_PASSED = "vision_check_passed"

# Совместимость с hero_check_regen / generate_hero
META_HERO_IDS = "hero_check_regen_ids"
META_HERO_ROUND = "hero_check_round"
META_HERO_RETURN = "hero_check_return_node"

MAX_VISION_CHECK_ROUNDS = 3

VisionKind = Literal["hero", "scenes"]


def vision_check_loop_active(project: Project) -> bool:
    meta = project.meta if isinstance(project.meta, dict) else {}
    return bool(
        str(meta.get(META_RETURN) or meta.get(META_HERO_RETURN) or "").strip()
    )


def get_vision_return_node(project: Project) -> str:
    meta = project.meta if isinstance(project.meta, dict) else {}
    return str(meta.get(META_RETURN) or meta.get(META_HERO_RETURN) or "").strip()


def get_vision_kind(project: Project) -> VisionKind | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    k = str(meta.get(META_KIND) or "").strip().lower()
    if k in ("hero", "scenes"):
        return k  # type: ignore[return-value]
    if meta.get(META_HERO_RETURN) or meta.get(META_HERO_IDS):
        return "hero"
    if meta.get(META_SCENE):
        return "scenes"
    return None


def get_scene_check_regen(project: Project) -> list[dict[str, Any]]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get(META_SCENE) or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for item in raw:
        if isinstance(item, dict):
            try:
                num = int(item.get("number"))
                shot = int(item.get("shot") or 1)
            except (TypeError, ValueError):
                continue
        elif isinstance(item, (int, float)):
            num, shot = int(item), 1
        else:
            continue
        if shot not in (1, 2):
            shot = 1
        key = (num, shot)
        if key in seen:
            continue
        seen.add(key)
        out.append({"number": num, "shot": shot})
    return out


def get_vision_passed(project: Project) -> set[str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get(META_PASSED) or []
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if str(x).strip()}


def clear_vision_check_meta(project: Project) -> None:
    meta = dict(project.meta or {})
    changed = False
    for key in (
        META_RETURN,
        META_KIND,
        META_ROUND,
        META_SCENE,
        META_PASSED,
        META_HERO_IDS,
        META_HERO_ROUND,
        META_HERO_RETURN,
    ):
        if key in meta:
            meta.pop(key, None)
            changed = True
    if changed:
        project.meta = meta
        flag_modified(project, "meta")


def _check_input_image_paths(project: Project, node_key: str) -> list[Path]:
    """PNG, которые ушли на последнюю проверку (для mark passed)."""
    from app.services.gpt_api import is_image_path

    meta = project.meta if isinstance(project.meta, dict) else {}
    results = meta.get("gpt_operator_results")
    paths: list[Path] = []
    if isinstance(results, dict):
        entry = results.get(node_key)
        if isinstance(entry, dict):
            for raw in entry.get("inputPaths") or []:
                p = Path(str(raw))
                if not p.is_file():
                    p = project.data_dir / str(raw)
                if p.is_file() and is_image_path(p):
                    paths.append(p)
    if paths:
        return paths
    # Fallback: scenes / characters на диске
    kind = _infer_kind(project, node_key)
    if kind == "hero":
        root = project.data_dir / "characters"
    else:
        root = project.data_dir / "scenes"
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if p.is_file() and is_image_path(p):
                paths.append(p)
    return paths


def _check_reply_text(project: Project, node_key: str) -> str:
    from app.services.excel_gpt_node import upload_dir

    out_dir = upload_dir(project, node_key)
    for name in ("check_report.txt", "gpt_reply.txt"):
        path = out_dir / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if (text or "").strip():
                return text
    meta = project.meta if isinstance(project.meta, dict) else {}
    results = meta.get("gpt_operator_results")
    if isinstance(results, dict):
        entry = results.get(node_key)
        if isinstance(entry, dict):
            preview = str(entry.get("replyPreview") or "")
            if preview.strip():
                return preview
    return ""


def _gate_status(project: Project, node_key: str) -> str | None:
    from app.services.gpt_operator import gate_allows_successors

    allowed = gate_allows_successors(project, node_key)
    if allowed is True:
        return "pass"
    if allowed is False:
        return "fail"
    return None


def _clear_check_node_completion(project: Project, node_key: str) -> None:
    from app.services.excel_gpt_node import slot_for_excel_gpt_node_key

    key = (node_key or "").strip()
    if not key:
        return
    meta = dict(project.meta or {})
    keys = [str(k) for k in (meta.get("excel_gpt_completed_keys") or [])]
    if key in keys:
        meta["excel_gpt_completed_keys"] = [k for k in keys if k != key]
    slot = slot_for_excel_gpt_node_key(project, key)
    if slot is not None:
        completed = [
            int(x) for x in (meta.get("enrich_completed_slots") or []) if str(x).isdigit()
        ]
        if slot in completed:
            meta["enrich_completed_slots"] = [s for s in completed if s != slot]
    meta["active_excel_gpt_node_key"] = key
    project.meta = meta
    flag_modified(project, "meta")


def _infer_kind(project: Project, node_key: str) -> VisionKind | None:
    from app.services.gpt_operator import upstream_node_type_for_check

    typ = upstream_node_type_for_check(project, node_key)
    if typ == "hero":
        return "hero"
    if typ in ("images", "hitl_images", "image_prompts"):
        return "scenes"
    return None


def _token_hero(cid: str) -> str:
    return cid.strip().lower()


def _token_frame(number: int, shot: int) -> str:
    return f"f{int(number)}" + ("s2" if int(shot) == 2 else "")


async def _apply_db_patch(
    session: AsyncSession,
    project: Project,
    patch: dict[str, Any] | None,
) -> bool:
    if not patch:
        return False
    chars = patch.get("characters") if isinstance(patch.get("characters"), list) else None
    ops = patch.get("ops") if isinstance(patch.get("ops"), list) else None
    if not chars and not ops:
        return False
    from app.services.db_apply import ApplyOpsError, apply_ops

    try:
        await apply_ops(
            session,
            project,
            list(ops or []),
            characters=list(chars) if chars else None,
            export_xlsx=True,
        )
        await session.flush()
        logger.info(
            "[#{}] vision_check_loop: db_patch applied chars={} ops={}",
            project.id,
            len(chars or []),
            len(ops or []),
        )
        return True
    except ApplyOpsError as e:
        logger.warning(
            "[#{}] vision_check_loop: db_patch apply failed: {}",
            project.id,
            e,
        )
        return False
    except Exception:  # noqa: BLE001
        logger.exception("[#{}] vision_check_loop: db_patch unexpected error", project.id)
        return False


async def _delete_scene_pngs(
    session: AsyncSession,
    project: Project,
    targets: list[dict[str, Any]],
) -> int:
    """Удалить PNG плохих кадров (+ artifact), чтобы generate_images перегенерил."""
    from app.models import FrameStatus
    from app.services.plan_shot2 import (
        SHOT2_STATUS_ATTR,
        find_shot1_image,
        find_shot2_image,
    )

    out_dir = Path(project.data_dir) / "scenes"
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id)
        )
    ).scalars().all()
    by_num = {fr.number: fr for fr in frames}
    removed = 0
    for t in targets:
        num = int(t["number"])
        shot = int(t.get("shot") or 1)
        path = (
            find_shot2_image(out_dir, num)
            if shot == 2
            else find_shot1_image(out_dir, num)
        )
        fr = by_num.get(num)
        if fr is not None:
            if shot == 2:
                attrs = dict(fr.attrs or {})
                attrs[SHOT2_STATUS_ATTR] = "image_prompt_ready"
                fr.attrs = attrs
            else:
                fr.status = FrameStatus.image_prompt_ready
        if path is None or not path.is_file():
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            logger.warning(
                "[#{}] vision_check_loop: cannot delete {}",
                project.id,
                path,
            )
            continue
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.kind == ArtifactKind.scene_image,
                )
            )
        ).scalars().all()
        for art in arts:
            ap = Path(str(art.path or ""))
            if ap.name == path.name or str(ap) == str(path):
                await session.delete(art)
    await session.flush()
    return removed


async def maybe_start_vision_check_loop_after_check(
    session: AsyncSession,
    project: Project,
    node_key: str | None,
) -> bool:
    """После checkMode: pass→clear; fail→patch+regen loop.

    Returns True если цикл стартовал (caller не auto-chain дальше).
    """
    key = (node_key or "").strip()
    if not key:
        return False

    from app.services.gpt_operator import is_check_operator, operator_config

    cfg = operator_config(project, key)
    if not is_check_operator(cfg):
        return False

    kind = _infer_kind(project, key)
    if kind is None:
        return False

    from app.services.check_analysis import (
        extract_critical_frame_regen_targets,
        extract_critical_hero_regen_ids,
        extract_db_patch,
        has_critical_vision_issues,
        looks_like_scored_vision_report,
        resolve_vision_check_gate,
    )

    reply = _check_reply_text(project, key)
    resolved = resolve_vision_check_gate(reply)
    gate = (_gate_status(project, key) or "").strip().lower()
    if resolved in ("pass", "fail"):
        gate = resolved
    if gate == "pass":
        if vision_check_loop_active(project):
            clear_vision_check_meta(project)
            logger.info(
                "[#{}] vision_check_loop: pass на {} — цикл сброшен",
                project.id,
                key,
            )
        return False
    if gate != "fail":
        return False

    # Scores/severity: auto-regen только при critical. Warning/minor или
    # overall < threshold без critical → fail-edge, но без wipe/regen.
    if looks_like_scored_vision_report(reply) and not has_critical_vision_issues(
        reply
    ):
        logger.info(
            "[#{}] vision_check_loop: fail на {} без critical "
            "(scores/threshold) — без auto-regen",
            project.id,
            key,
        )
        return False

    hero_ids = extract_critical_hero_regen_ids(reply) if kind == "hero" else []
    frame_tgts = (
        extract_critical_frame_regen_targets(reply) if kind == "scenes" else []
    )
    patch = extract_db_patch(reply)

    if kind == "hero" and not hero_ids and not patch:
        logger.info(
            "[#{}] vision_check_loop: hero fail на {} без critical regen/patch — без цикла",
            project.id,
            key,
        )
        return False
    if kind == "scenes" and not frame_tgts and not patch:
        logger.info(
            "[#{}] vision_check_loop: scenes fail на {} без critical frames/patch — без цикла",
            project.id,
            key,
        )
        return False

    meta = dict(project.meta or {})
    round_n = int(meta.get(META_ROUND) or meta.get(META_HERO_ROUND) or 0) + 1
    if round_n > MAX_VISION_CHECK_ROUNDS:
        logger.warning(
            "[#{}] vision_check_loop: лимит {} раундов на {} — сдаёмся",
            project.id,
            MAX_VISION_CHECK_ROUNDS,
            key,
        )
        clear_vision_check_meta(project)
        return False

    await _apply_db_patch(session, project, patch)

    # Сначала проверили ВСЕ; на recheck уйдут только critical.
    # Остальные PNG помечаем passed (не слать в GPT снова).
    all_paths = _check_input_image_paths(project, key)
    mark_passed_except_regen(
        project,
        kind=kind,
        all_image_paths=all_paths,
        regen_hero_ids=hero_ids if kind == "hero" else None,
        regen_frames=frame_tgts if kind == "scenes" else None,
    )

    meta = dict(project.meta or {})
    meta[META_RETURN] = key
    meta[META_KIND] = kind
    meta[META_ROUND] = round_n
    if kind == "hero":
        meta[META_HERO_IDS] = hero_ids
        meta[META_HERO_ROUND] = round_n
        meta[META_HERO_RETURN] = key
        meta[META_SCENE] = []
    else:
        meta[META_SCENE] = frame_tgts
        meta[META_HERO_IDS] = []
        meta[META_HERO_RETURN] = key  # return node also used by maybe_return
        meta[META_HERO_ROUND] = round_n
    project.meta = meta
    flag_modified(project, "meta")

    from app.services.run_sync import prepare_node_for_step_start

    if kind == "hero":
        try:
            await prepare_node_for_step_start(
                session,
                project,
                "hero",
                strict=False,
                explicit_ui_start=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("[#{}] vision_check_loop: prepare hero failed", project.id)
        project.status = ProjectStatus.generating_hero
        await session.flush()
        logger.info(
            "[#{}] vision_check_loop: fail → round {} hero regen ids={} return={}",
            project.id,
            round_n,
            hero_ids,
            key,
        )
        return True

    # scenes
    deleted = await _delete_scene_pngs(session, project, frame_tgts)
    try:
        await prepare_node_for_step_start(
            session,
            project,
            "img",
            strict=False,
            explicit_ui_start=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("[#{}] vision_check_loop: prepare img failed", project.id)
    project.status = ProjectStatus.generating_images
    await session.flush()
    logger.info(
        "[#{}] vision_check_loop: fail → round {} scenes regen={} deleted={} return={}",
        project.id,
        round_n,
        frame_tgts,
        deleted,
        key,
    )
    return True


async def maybe_return_to_check_after_vision_ready(
    session: AsyncSession,
    project: Project,
) -> ProjectStatus | None:
    """После hero_ready / images_ready: вернуться на check-ноду."""
    return_key = get_vision_return_node(project)
    if not return_key:
        return None

    from app.services.excel_gpt_node import (
        running_status_for_slot,
        slot_for_excel_gpt_node_key,
    )
    from app.services.run_sync import prepare_node_for_step_start

    slot = slot_for_excel_gpt_node_key(project, return_key)
    if slot is None or slot < 1:
        logger.warning(
            "[#{}] vision_check_loop: return node {} без slot — сброс",
            project.id,
            return_key,
        )
        clear_vision_check_meta(project)
        return None

    # Ids/frames уже отработали — чистим очереди, return оставляем до pass.
    meta2 = dict(project.meta or {})
    if meta2.get(META_HERO_IDS):
        meta2[META_HERO_IDS] = []
    if meta2.get(META_SCENE):
        meta2[META_SCENE] = []
    project.meta = meta2
    flag_modified(project, "meta")

    _clear_check_node_completion(project, return_key)
    nxt = running_status_for_slot(slot)
    try:
        await prepare_node_for_step_start(
            session,
            project,
            "excel_gpt",
            node_key=return_key,
            enrich_slot=slot,
            strict=False,
            explicit_ui_start=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "[#{}] vision_check_loop: prepare check {} failed",
            project.id,
            return_key,
        )

    logger.info(
        "[#{}] vision_check_loop: ready → снова check {} ({})",
        project.id,
        return_key,
        nxt.value,
    )
    return nxt


def _parse_image_token(path: Path) -> str | None:
    """c01.png → c01; frame_003_….png → f3; frame_003_s2_….png → f3s2."""
    name = path.name
    m = re.match(r"^(c\d{1,3})\.", name, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    fm = re.search(
        r"frame[_-]?(\d{1,4})(?:[_-]s2[_-]|[_-]?(?:s2|shot2|02))?",
        name,
        re.IGNORECASE,
    )
    if not fm:
        return None
    num = int(fm.group(1))
    shot = 2 if re.search(r"(?:_s2_|s2|shot2|_02)", name, re.IGNORECASE) else 1
    return _token_frame(num, shot)


def filter_image_paths_for_recheck(
    project: Project,
    paths: list[Path],
) -> list[Path]:
    """Первый проход — все PNG. Recheck — только переделанные (regen targets)."""
    if not vision_check_loop_active(project):
        return paths

    kind = get_vision_kind(project)
    # Явный список на переген: шлём ТОЛЬКО их (остальные уже приняты).
    if kind == "scenes":
        targets = get_scene_check_regen(project)
        if targets:
            want = {
                _token_frame(int(t["number"]), int(t.get("shot") or 1))
                for t in targets
            }
            out = [p for p in paths if _parse_image_token(p) in want]
            if out:
                logger.info(
                    "[#{}] vision_check_loop: recheck только {} PNG (из {})",
                    project.id,
                    len(out),
                    len(paths),
                )
                return out
    if kind == "hero":
        meta = project.meta if isinstance(project.meta, dict) else {}
        raw_ids = meta.get(META_HERO_IDS) or []
        want = {
            str(x).strip().lower()
            for x in raw_ids
            if str(x).strip()
        }
        if want:
            out = [p for p in paths if (_parse_image_token(p) or "") in want]
            if out:
                logger.info(
                    "[#{}] vision_check_loop: recheck только {} hero PNG",
                    project.id,
                    len(out),
                )
                return out

    passed = get_vision_passed(project)
    if not passed:
        return paths
    out = []
    for p in paths:
        tok = _parse_image_token(p)
        if tok and tok in passed:
            continue
        out.append(p)
    return out or paths


def mark_passed_except_regen(
    project: Project,
    *,
    kind: VisionKind,
    all_image_paths: list[Path] | None,
    regen_hero_ids: list[str] | None = None,
    regen_frames: list[dict[str, Any]] | None = None,
) -> None:
    """После полного check: всё кроме critical-regen помечаем passed."""
    passed = get_vision_passed(project)
    regen_tokens: set[str] = set()
    if kind == "hero":
        for cid in regen_hero_ids or []:
            regen_tokens.add(_token_hero(cid))
    else:
        for t in regen_frames or []:
            regen_tokens.add(
                _token_frame(int(t["number"]), int(t.get("shot") or 1))
            )
    for p in all_image_paths or []:
        tok = _parse_image_token(p)
        if not tok:
            continue
        if tok in regen_tokens:
            passed.discard(tok)
        else:
            passed.add(tok)
    meta = dict(project.meta or {})
    meta[META_PASSED] = sorted(passed)
    project.meta = meta
    flag_modified(project, "meta")


def scene_regen_allows(project: Project, frame_number: int, shot: int = 1) -> bool | None:
    """None = фильтра нет (обычный режим); True/False = точечный regen."""
    targets = get_scene_check_regen(project)
    if not targets:
        return None
    want = {(int(t["number"]), int(t.get("shot") or 1)) for t in targets}
    return (int(frame_number), int(shot)) in want
