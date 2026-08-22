"""Apply-ops батчи: длинный db_frames.json режется, чтобы SSE не обрывался.

Эмпирика #6 n_excel_gpt_2: 116 плотных shot_01 в одном ответе →
stream_partial, salvage ops=65, нода done. Аналитика (короткие поля)
на тех же 116 кадрах проходит целиком.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.gpt_operator_client import OperatorApiResult, run_operator_api

# Плотный выход (shot_01 + главное_действие): 160 кадров одним ответом
# рвёт SSE и подмешивает фейковые uuid. Режем на 5 пачек (~32 кадра).
_DENSE_TARGET_BATCHES = 5
_DENSE_FRAMES_PER_BATCH = 32
_DENSE_JSON_BYTES_PER_BATCH = 40_000
# Короткий выход (аналитика 54–59): 116 кадров / ~44k символов — ок.
_LIGHT_FRAMES_PER_BATCH = 120
_LIGHT_JSON_BYTES_PER_BATCH = 180_000
# Image prompts (~5000 символов/кадр): меньше, чем shot_fill.
_IMG_FRAMES_PER_BATCH = 8
_IMG_JSON_BYTES_PER_BATCH = 24_000
PROMPT_UNITS_PER_BATCH = _IMG_FRAMES_PER_BATCH
# Сценарист / закадр: пачка = 9 ячеек VO, до 6 пачек параллельно
# со сдвигом старта 1 с.
VO_UNITS_PER_BATCH = 9
VO_PARALLEL_MAX = 6
VO_STAGGER_SEC = 1.0

_COMPLETE_ATTRS_DENSE = ("main_action", "shot01_description")
_IMG_SKIP_KEYS = ("image_prompt", "промт_картинки")

ApplyFn = Callable[[dict[str, Any]], Awaitable[None]]
ProgressFn = Callable[[str], Awaitable[None]]


def frames_per_batch(
    *,
    n_frames: int,
    json_bytes: int,
    dense: bool,
    skip_if_field: str | None = None,
    target_batches: int | None = None,
) -> int:
    """Сколько кадров в одном GPT-вызове. 0 кадров → 1 (не делить на ноль)."""
    n = max(int(n_frames), 0)
    if n <= 0:
        return 1
    if target_batches and int(target_batches) > 0:
        from math import ceil

        # Добор хвоста (≤32 кадра) — один вызов, не 5 крошечных пачек.
        if dense and n <= _DENSE_FRAMES_PER_BATCH:
            return n
        return max(1, int(ceil(n / int(target_batches))))
    avg = max(int(json_bytes), 0) / n if n else 0
    if skip_if_field:
        by_count = _IMG_FRAMES_PER_BATCH
        if avg > 0:
            by_bytes = max(4, int(_IMG_JSON_BYTES_PER_BATCH / avg))
            return max(4, min(by_count, by_bytes, n))
        return min(by_count, n)
    if dense:
        by_count = _DENSE_FRAMES_PER_BATCH
        if avg > 0:
            by_bytes = max(8, int(_DENSE_JSON_BYTES_PER_BATCH / avg))
            return max(8, min(by_count, by_bytes, n))
        return min(by_count, n)
    by_count = _LIGHT_FRAMES_PER_BATCH
    if avg > 0:
        by_bytes = max(20, int(_LIGHT_JSON_BYTES_PER_BATCH / avg))
        return max(20, min(by_count, by_bytes, n))
    return min(by_count, n)


def should_batch_apply_ops(
    *,
    n_frames: int,
    json_bytes: int,
    dense: bool,
    skip_if_field: str | None = None,
    target_batches: int | None = None,
) -> bool:
    return n_frames > frames_per_batch(
        n_frames=n_frames,
        json_bytes=json_bytes,
        dense=dense,
        skip_if_field=skip_if_field,
        target_batches=target_batches,
    )


def split_frames(frames: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        size = 1
    return [frames[i : i + size] for i in range(0, len(frames), size)]


def _frame_complete(
    frame: dict[str, Any],
    *,
    dense: bool,
    skip_if_field: str | None = None,
) -> bool:
    attrs = frame.get("attrs") if isinstance(frame.get("attrs"), dict) else {}
    if skip_if_field:
        keys = (skip_if_field, *_IMG_SKIP_KEYS)
        for k in keys:
            if str(frame.get(k) or "").strip():
                return True
            if str(attrs.get(k) or "").strip():
                return True
        return False
    if not dense:
        return False
    # db_frames.json кладёт whitelist на верхний уровень, не в attrs.
    return all(
        str(frame.get(k) or attrs.get(k) or "").strip()
        for k in _COMPLETE_ATTRS_DENSE
    )


def _has_uuid(frame: dict[str, Any]) -> bool:
    return bool(str(frame.get("uuid") or "").strip())


def _has_voiceover(frame: dict[str, Any]) -> bool:
    return bool(str(frame.get("voiceover_text") or "").strip())


def _pending_frames(
    frames: list[dict[str, Any]],
    *,
    dense: bool,
    skip_if_field: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fr in frames:
        if not _has_uuid(fr):
            continue
        if not _has_voiceover(fr):
            continue
        if _frame_complete(fr, dense=dense, skip_if_field=skip_if_field):
            continue
        out.append(fr)
    return out


def select_frames_for_batches(
    frames: list[dict[str, Any]],
    *,
    dense: bool,
    skip_if_field: str | None = None,
    target_batches: int | None = None,
) -> list[dict[str, Any]]:
    """Кадры в GPT-пачки: уже заполненные shot-поля пропускаем."""
    del target_batches
    return _pending_frames(
        frames, dense=dense, skip_if_field=skip_if_field
    )


def _batch_footer(
    batch_i: int,
    split_level: int,
    n: int,
    *,
    footer_kind: str | None = None,
) -> str:
    kind = (footer_kind or "").strip().lower()
    if kind in {"vo", "voiceover"}:
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(закадр по {VO_UNITS_PER_BATCH})\n"
            f"В db_frames.json только этот кусок: {n} ячеек закадра.\n"
            "Верни ops ровно по каждому uuid: fields.закадр / voiceover_text. "
            "Чужие кадры не пиши. JSON apply-ops, без прозы.\n"
        )
    if kind in {"prompts", "img"}:
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(промты по {PROMPT_UNITS_PER_BATCH})\n"
            f"В db_frames.json только этот кусок: {n} кадров.\n"
            "Верни ops ровно по каждому uuid: fields.промт_картинки и "
            "промт_видео. Чужие кадры не пиши. JSON apply-ops, без прозы.\n"
        )
    return (
        f"\n# BATCH call={batch_i} split={split_level} (схема 1→2→4)\n"
        f"В db_frames.json только этот кусок: {n} кадров.\n"
        "Верни ops ровно по каждому uuid из ЭТОГО файла. "
        "Чужие кадры не пиши. JSON apply-ops, без прозы.\n"
    )


async def run_apply_ops_batched(
    *,
    project_dir: Path,
    node_key: str,
    role: str,
    output_mode: str,
    prompt: str,
    accompanying: str,
    db_ctx: dict[str, Any],
    ctx_path: Path,
    project_id: int,
    dense: bool,
    apply_fn: ApplyFn | None = None,
    skip_if_field: str | None = None,
    target_batches: int | None = None,
    chunk_size: int | None = None,
    parallel_max: int | None = None,
    stagger_sec: float | None = None,
    footer_kind: str | None = None,
    on_progress: ProgressFn | None = None,
    allow_empty_ops: bool = False,
) -> OperatorApiResult:
    """Один GPT-вызов на пачку. Ошибка внутри пачки → 2, затем 4.

    ``chunk_size`` — заранее резать pending (сценарист: 9 ячеек закадра).
    ``parallel_max`` — сколько пачек стартовать сразу (сценарист: 6),
    со сдвигом ``stagger_sec`` (1 с). Без chunk_size — как раньше:
    сначала все pending, при ошибке 1→2→4.
    """
    from app.services.adaptive_llm_batches import next_split_level, split_in_half

    del target_batches
    all_frames = list(db_ctx.get("frames") or [])
    pending = select_frames_for_batches(
        all_frames,
        dense=dense,
        skip_if_field=skip_if_field,
    )
    if not pending:
        logger.info(
            "[#{}] apply_ops batched node={!r}: нечего писать "
            "(все кадры уже заполнены или без закадра)",
            project_id,
            node_key,
        )
        return OperatorApiResult(
            reply_text='{"ops":[]}',
            output_paths=[ctx_path],
            apply_ops={"ops": [], "report": "already_filled"},
            applied_in_runner=True,
        )

    size = int(chunk_size) if chunk_size and int(chunk_size) > 0 else 0
    packs = split_frames(pending, size) if size else [pending]
    wave_n = int(parallel_max) if parallel_max and int(parallel_max) > 1 else 1
    delay_s = (
        float(stagger_sec)
        if stagger_sec is not None
        else (VO_STAGGER_SEC if wave_n > 1 else 0.0)
    )
    logger.info(
        "[#{}] apply_ops batched node={!r}: pending={} packs={} "
        "pack_size={} parallel={} stagger={}s adaptive 1→2→4 dense={}",
        project_id,
        node_key,
        len(pending),
        len(packs),
        size or "all",
        wave_n,
        delay_s,
        dense,
    )

    merged_ops: list[dict[str, Any]] = []
    replies: list[str] = []
    last_paths: list[Path] = [ctx_path]
    call_i = 0
    meta_lock = asyncio.Lock()
    apply_lock = asyncio.Lock()

    async def _one_chunk(
        chunk: list[dict[str, Any]], level: int
    ) -> list[dict[str, Any]]:
        nonlocal call_i, last_paths
        async with meta_lock:
            call_i += 1
            my_i = call_i
        batch_ctx = {
            **db_ctx,
            "frames": chunk,
            "batch": {
                "index": my_i,
                "split_level": level,
                "frames": len(chunk),
            },
        }
        batch_path = ctx_path.with_name(
            f"db_frames_batch_{my_i:02d}_L{level}.json"
        )
        batch_path.write_text(
            json.dumps(batch_ctx, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        foot = _batch_footer(
            my_i, level, len(chunk), footer_kind=footer_kind
        )
        res = await run_operator_api(
            project_dir=project_dir,
            node_key=node_key,
            role=role,
            output_mode=output_mode,
            prompt=prompt,
            accompanying=f"{accompanying}{foot}",
            input_paths=[batch_path],
            auto_pack=False,
        )
        ops = []
        if isinstance(res.apply_ops, dict):
            ops = list(res.apply_ops.get("ops") or [])
        known = {
            str(fr.get("uuid") or "").strip()
            for fr in chunk
            if str(fr.get("uuid") or "").strip()
        }
        dropped = 0
        kept: list[dict[str, Any]] = []
        for op in ops:
            if not isinstance(op, dict):
                continue
            uid = str(op.get("frame_uuid") or "").strip()
            if uid in known:
                kept.append(op)
            else:
                dropped += 1
        if dropped:
            logger.warning(
                "[#{}] apply_ops batched node={!r}: call {} L{} "
                "dropped {} unknown frame_uuid",
                project_id,
                node_key,
                my_i,
                level,
                dropped,
            )
        ops = kept
        logger.info(
            "[#{}] apply_ops batched node={!r}: call {} L{} sent={} got_ops={}",
            project_id,
            node_key,
            my_i,
            level,
            len(chunk),
            len(ops),
        )
        if on_progress is not None:
            try:
                await on_progress(
                    f"пачка {my_i}/{len(packs)} · {len(ops)} ops"
                )
            except Exception:
                logger.debug("apply_ops progress callback failed", exc_info=True)
        if not ops:
            if allow_empty_ops:
                # QC: ops только по нарушителям; пустой пакет = ок.
                return []
            raise RuntimeError(
                f"enrich_xlsx node={node_key}: L{level} call {my_i} "
                f"без ops (ждали {len(chunk)} кадров)."
            )
        if apply_fn is not None:
            payload = dict(res.apply_ops or {})
            payload["ops"] = ops
            payload["export_xlsx"] = False
            async with apply_lock:
                await apply_fn(payload)
        async with meta_lock:
            last_paths = list(res.output_paths or []) or [batch_path]
            replies.append(res.reply_text or "")
            merged_ops.extend(ops)
        if allow_empty_ops:
            return []
        got_uuids = {
            str(op.get("frame_uuid") or "").strip()
            for op in ops
            if isinstance(op, dict)
        }
        missing = [
            fr
            for fr in chunk
            if str(fr.get("uuid") or "").strip() not in got_uuids
        ]
        return missing

    async def _run_adaptive(chunk: list[dict[str, Any]], level: int) -> None:
        try:
            missing = await _one_chunk(chunk, level)
        except Exception as exc:
            if allow_empty_ops:
                logger.warning(
                    "[#{}] apply_ops node={!r}: L{} fail ({}) — "
                    "QC не ретраим пачку (промты уже в БД)",
                    project_id,
                    node_key,
                    level,
                    str(exc)[:160],
                )
                return
            nxt = next_split_level(level)
            if nxt is None or len(chunk) <= 1:
                raise
            parts = split_in_half(chunk)
            logger.warning(
                "[#{}] apply_ops node={!r}: L{} fail ({}) → split {} "
                "frames into {} packs",
                project_id,
                node_key,
                level,
                str(exc)[:160],
                len(chunk),
                len(parts),
            )
            for part in parts:
                await _run_adaptive(part, nxt)
            return
        if not missing:
            return
        # 1 пропущенный uuid: ещё раз только его. Раньше len<=1 сразу
        # валил всю пачку (7/8 → RuntimeError → soft retry всех 188).
        if len(missing) == 1:
            uid = str(missing[0].get("uuid") or "")[:8]
            if len(chunk) <= 1:
                raise RuntimeError(
                    f"enrich_xlsx node={node_key}: L{level} неполный apply-ops "
                    f"(0/1). uuid: {uid}"
                )
            logger.warning(
                "[#{}] apply_ops node={!r}: L{} incomplete {}/{} → retry uuid {}",
                project_id,
                node_key,
                level,
                len(chunk) - 1,
                len(chunk),
                uid,
            )
            await _run_adaptive(missing, next_split_level(level) or level)
            return
        nxt = next_split_level(level)
        if nxt is None:
            raise RuntimeError(
                f"enrich_xlsx node={node_key}: L{level} неполный apply-ops "
                f"({len(chunk) - len(missing)}/{len(chunk)}). uuid: "
                f"{', '.join(str(fr.get('uuid') or '')[:8] for fr in missing[:8])}"
            )
        logger.warning(
            "[#{}] apply_ops node={!r}: L{} incomplete {}/{} → split L{}",
            project_id,
            node_key,
            level,
            len(chunk) - len(missing),
            len(chunk),
            nxt,
        )
        for part in split_in_half(missing):
            await _run_adaptive(part, nxt)

    async def _run_pack(delay: float, pack: list[dict[str, Any]]) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await _run_adaptive(pack, 1)

    if wave_n <= 1 or len(packs) <= 1:
        for pack in packs:
            await _run_adaptive(pack, 1)
    else:
        for wave_start in range(0, len(packs), wave_n):
            wave = packs[wave_start : wave_start + wave_n]
            logger.info(
                "[#{}] apply_ops node={!r}: wave {}–{} / {} "
                "(parallel={}, stagger={}s)",
                project_id,
                node_key,
                wave_start + 1,
                wave_start + len(wave),
                len(packs),
                wave_n,
                delay_s,
            )
            if on_progress is not None:
                try:
                    await on_progress(
                        f"волна {wave_start + 1}–{wave_start + len(wave)} / {len(packs)}"
                    )
                except Exception:
                    logger.debug("apply_ops progress callback failed", exc_info=True)
            results = await asyncio.gather(
                *[
                    _run_pack(i * delay_s, pack)
                    for i, pack in enumerate(wave)
                ],
                return_exceptions=True,
            )
            errs = [r for r in results if isinstance(r, BaseException)]
            if errs:
                raise errs[0]

    ctx_path.write_text(
        json.dumps(db_ctx, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return OperatorApiResult(
        reply_text="\n\n".join(replies),
        output_paths=last_paths,
        apply_ops={"ops": merged_ops},
        applied_in_runner=apply_fn is not None,
    )
