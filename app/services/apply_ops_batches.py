"""Apply-ops батчи: длинный db_frames.json режется, чтобы SSE не обрывался.

Эмпирика #6 n_excel_gpt_2: 116 плотных shot_01 в одном ответе →
stream_partial, salvage ops=65, нода done. Аналитика (короткие поля)
на тех же 116 кадрах проходит целиком.
"""

from __future__ import annotations

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

_COMPLETE_ATTRS_DENSE = ("main_action", "shot01_description")
_IMG_SKIP_KEYS = ("image_prompt", "промт_картинки")

ApplyFn = Callable[[dict[str, Any]], Awaitable[None]]


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
        if not _has_voiceover(fr) and not skip_if_field:
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


def _batch_footer(batch_i: int, batch_n: int, n: int) -> str:
    return (
        f"\n# BATCH {batch_i}/{batch_n}\n"
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
) -> OperatorApiResult:
    """Несколько GPT-вызовов по кускам db_frames.json → один merge ops."""
    all_frames = list(db_ctx.get("frames") or [])
    pending = select_frames_for_batches(
        all_frames,
        dense=dense,
        skip_if_field=skip_if_field,
        target_batches=target_batches
        if target_batches is not None
        else (_DENSE_TARGET_BATCHES if dense else None),
    )
    json_bytes = len(
        json.dumps({"frames": pending}, ensure_ascii=False).encode("utf-8")
    )
    if target_batches is None and dense:
        target_batches = _DENSE_TARGET_BATCHES
    size = frames_per_batch(
        n_frames=len(pending),
        json_bytes=json_bytes,
        dense=dense,
        skip_if_field=skip_if_field,
        target_batches=target_batches,
    )
    chunks = split_frames(pending, size)
    if not chunks:
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

    logger.info(
        "[#{}] apply_ops batched node={!r}: pending={} batch_size={} "
        "batches={} dense={} json_bytes={}",
        project_id,
        node_key,
        len(pending),
        size,
        len(chunks),
        dense,
        json_bytes,
    )

    merged_ops: list[dict[str, Any]] = []
    replies: list[str] = []
    last_paths: list[Path] = [ctx_path]
    batch_n = len(chunks)

    for i, chunk in enumerate(chunks, start=1):
        batch_ctx = {
            **db_ctx,
            "frames": chunk,
            "batch": {"index": i, "total": batch_n, "frames": len(chunk)},
        }
        batch_path = ctx_path.with_name(f"db_frames_batch_{i:02d}.json")
        batch_path.write_text(
            json.dumps(batch_ctx, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ctx_path.write_text(
            json.dumps(batch_ctx, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        foot = _batch_footer(i, batch_n, len(chunk))
        res = await run_operator_api(
            project_dir=project_dir,
            node_key=node_key,
            role=role,
            output_mode=output_mode,
            prompt=prompt,
            accompanying=f"{accompanying}{foot}",
            input_paths=[batch_path],
        )
        last_paths = list(res.output_paths or []) or [batch_path]
        replies.append(res.reply_text or "")
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
                "[#{}] apply_ops batched node={!r}: batch {}/{} "
                "dropped {} unknown frame_uuid",
                project_id,
                node_key,
                i,
                batch_n,
                dropped,
            )
        ops = kept
        logger.info(
            "[#{}] apply_ops batched node={!r}: batch {}/{} "
            "sent={} got_ops={}",
            project_id,
            node_key,
            i,
            batch_n,
            len(chunk),
            len(ops),
        )
        if not ops:
            raise RuntimeError(
                f"enrich_xlsx node={node_key}: batch {i}/{batch_n} "
                f"без ops (ждали {len(chunk)} кадров). "
                "Стрим оборвался или модель не вернула JSON."
            )
        if apply_fn is not None:
            payload = dict(res.apply_ops or {})
            payload["ops"] = ops
            payload["export_xlsx"] = False
            await apply_fn(payload)
        merged_ops.extend(ops)
        got_uuids = {
            str(op.get("frame_uuid") or "").strip()
            for op in ops
            if isinstance(op, dict)
        }
        missing = [
            str(fr.get("uuid") or "")
            for fr in chunk
            if str(fr.get("uuid") or "") not in got_uuids
        ]
        if missing:
            raise RuntimeError(
                f"enrich_xlsx node={node_key}: batch {i}/{batch_n} "
                f"неполный apply-ops ({len(ops)}/{len(chunk)}). "
                f"Не записаны uuid: {', '.join(missing[:8])}"
                f"{'…' if len(missing) > 8 else ''}. "
                "Повтори ноду — уже записанные кадры пропустятся."
            )

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
