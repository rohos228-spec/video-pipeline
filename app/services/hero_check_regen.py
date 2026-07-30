"""Цикл: excel_gpt checkMode (после hero) → fail → переген плохих PNG → снова check.

Meta keys:
  hero_check_regen_ids   — list[str] excel_id (c01…)
  hero_check_round       — int, сколько раз уже уходили в regen
  hero_check_return_node — node_key проверочной ноды
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Project, ProjectStatus

META_IDS = "hero_check_regen_ids"
META_ROUND = "hero_check_round"
META_RETURN = "hero_check_return_node"

# Сколько fail→regen раундов до сдачи (fail-ветка / без слепого полного regen).
MAX_HERO_CHECK_REGEN_ROUNDS = 3


def get_hero_check_regen_ids(project: Project) -> list[str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get(META_IDS) or []
    if not isinstance(raw, list):
        return []
    from app.services.check_analysis import normalize_hero_excel_id

    out: list[str] = []
    seen: set[str] = set()
    for x in raw:
        nid = normalize_hero_excel_id(str(x or ""))
        if nid and nid not in seen:
            seen.add(nid)
            out.append(nid)
    return out


def hero_check_loop_active(project: Project) -> bool:
    meta = project.meta if isinstance(project.meta, dict) else {}
    return bool(str(meta.get(META_RETURN) or "").strip())


def clear_hero_check_regen_meta(project: Project) -> None:
    meta = dict(project.meta or {})
    changed = False
    for key in (META_IDS, META_ROUND, META_RETURN):
        if key in meta:
            meta.pop(key, None)
            changed = True
    if changed:
        project.meta = meta
        flag_modified(project, "meta")


def _check_reply_text(project: Project, node_key: str) -> str:
    """Текст отчёта/ответа проверки для парсера regen ids."""
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
    """Снять done у конкретной check-ноды, чтобы её можно было перезапустить."""
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


async def maybe_start_hero_check_regen_after_check(
    session: AsyncSession,
    project: Project,
    node_key: str | None,
) -> bool:
    """После checkMode fail (upstream=hero): meta + generating_hero.

    Returns True если цикл стартовал (caller НЕ должен auto-chain дальше).
    """
    key = (node_key or "").strip()
    if not key:
        return False

    from app.services.gpt_operator import is_check_operator, operator_config, upstream_node_type_for_check

    cfg = operator_config(project, key)
    if not is_check_operator(cfg):
        return False
    if upstream_node_type_for_check(project, key) != "hero":
        return False

    gate = (_gate_status(project, key) or "").strip().lower()
    if gate == "pass":
        if hero_check_loop_active(project):
            clear_hero_check_regen_meta(project)
            logger.info(
                "[#{}] hero_check_regen: pass на {} — цикл сброшен",
                project.id,
                key,
            )
        return False
    if gate != "fail":
        return False

    from app.services.check_analysis import extract_hero_regen_ids

    ids = extract_hero_regen_ids(_check_reply_text(project, key))
    if not ids:
        logger.info(
            "[#{}] hero_check_regen: fail на {} без regen ids — без цикла",
            project.id,
            key,
        )
        return False

    meta = dict(project.meta or {})
    round_n = int(meta.get(META_ROUND) or 0) + 1
    if round_n > MAX_HERO_CHECK_REGEN_ROUNDS:
        logger.warning(
            "[#{}] hero_check_regen: лимит {} раундов на {} (ids={}) — сдаёмся",
            project.id,
            MAX_HERO_CHECK_REGEN_ROUNDS,
            key,
            ids,
        )
        clear_hero_check_regen_meta(project)
        return False

    meta[META_IDS] = ids
    meta[META_ROUND] = round_n
    meta[META_RETURN] = key
    project.meta = meta
    flag_modified(project, "meta")

    from app.services.run_sync import prepare_node_for_step_start

    try:
        await prepare_node_for_step_start(
            session,
            project,
            "hero",
            strict=False,
            explicit_ui_start=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "[#{}] hero_check_regen: prepare hero failed",
            project.id,
        )

    project.status = ProjectStatus.generating_hero
    await session.flush()
    logger.info(
        "[#{}] hero_check_regen: fail → round {} regen ids={} return={}",
        project.id,
        round_n,
        ids,
        key,
    )
    return True


async def maybe_return_to_check_after_hero(
    session: AsyncSession,
    project: Project,
) -> ProjectStatus | None:
    """После hero_ready: если цикл активен — снова enriching_N check-ноды.

    Returns running status или None (обычный auto_advance).
    """
    meta = project.meta if isinstance(project.meta, dict) else {}
    return_key = str(meta.get(META_RETURN) or "").strip()
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
            "[#{}] hero_check_regen: return node {} без slot — сброс цикла",
            project.id,
            return_key,
        )
        clear_hero_check_regen_meta(project)
        return None

    # Ids уже отработали в generate_hero — чистим список, return_node оставляем
    # до pass/лимита на check.
    ids_left = get_hero_check_regen_ids(project)
    meta2 = dict(project.meta or {})
    if ids_left:
        meta2[META_IDS] = []
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
            "[#{}] hero_check_regen: prepare check {} failed",
            project.id,
            return_key,
        )

    logger.info(
        "[#{}] hero_check_regen: hero_ready → снова check {} ({})",
        project.id,
        return_key,
        nxt.value,
    )
    return nxt


def filter_chars_for_hero_check_regen(
    chars: list[Any],
    project: Project,
) -> tuple[list[Any], set[str] | None]:
    """Если заданы regen ids — вернуть только их; иначе (chars, None)."""
    ids = get_hero_check_regen_ids(project)
    if not ids:
        return chars, None
    id_set = set(ids)
    filtered = [ch for ch in chars if getattr(ch, "id", None) in id_set]
    return filtered, id_set
