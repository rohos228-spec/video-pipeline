"""Адаптивные батчи, когда нода/kie вернула только часть результата.

Считаем: объём ответа превышен. ``delivered`` = сколько единиц
успешно пришло в первом (частичном) ответе.

- остаток ≤ delivered → один доборный запрос на весь остаток;
- остаток > delivered → нарезать пачками ≈ ``ratio`` (по умолчанию 80%)
  от ``delivered``.

Общий добор apply-ops (все ноды через ``gpt_api.chat``):
``volume_complete_apply_ops_reply``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence, TypeVar

from loguru import logger

T = TypeVar("T")

DEFAULT_VOLUME_RATIO = 0.8
_DB_FRAMES_NAME_RE = re.compile(r"^db_frames", re.IGNORECASE)


def inferred_batch_size(
    delivered: int, *, ratio: float = DEFAULT_VOLUME_RATIO
) -> int:
    """Размер следующих пачек ≈ ratio от того, что реально доехало."""
    if delivered < 1:
        return 1
    r = float(ratio) if ratio and ratio > 0 else DEFAULT_VOLUME_RATIO
    return max(1, int(delivered * r))


def plan_remainder_batches(
    remaining: Sequence[T],
    *,
    delivered: int,
    ratio: float = DEFAULT_VOLUME_RATIO,
) -> list[list[T]]:
    """План добора после частичного ответа ноды.

    ``remaining`` — единицы, которые ещё не покрыты этим запросом.
    """
    items = list(remaining)
    if not items:
        return []
    if delivered < 1:
        # Нет сигнала по ёмкости — один повтор всего остатка (решает caller).
        return [items]
    if len(items) <= delivered:
        return [items]
    size = inferred_batch_size(delivered, ratio=ratio)
    return [items[i : i + size] for i in range(0, len(items), size)]


def rechunk_tail(
    tail: Sequence[T],
    *,
    size: int,
) -> list[list[T]]:
    """Перенарезать ещё не отправленный хвост под новый размер пачки."""
    items = list(tail)
    n = max(1, int(size))
    if not items:
        return []
    return [items[i : i + n] for i in range(0, len(items), n)]


def is_db_frames_path(path: Path | str) -> bool:
    name = Path(path).name
    return bool(_DB_FRAMES_NAME_RE.match(name)) and name.lower().endswith(
        (".json", ".jsonl")
    )


def load_expected_frame_uuids(paths: Sequence[Path | str] | None) -> list[str]:
    """Уникальные frame uuid из db_frames*.json во вложениях (порядок файла)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        path = Path(raw)
        if not is_db_frames_path(path) or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        frames = data.get("frames") if isinstance(data, dict) else None
        if not isinstance(frames, list):
            continue
        for fr in frames:
            if not isinstance(fr, dict):
                continue
            uid = str(fr.get("uuid") or fr.get("frame_uuid") or "").strip()
            if uid and uid not in seen:
                seen.add(uid)
                out.append(uid)
    return out


def _op_uuid(op: dict[str, Any]) -> str:
    return str(op.get("frame_uuid") or op.get("uuid") or "").strip()


def missing_frame_uuids_for_volume(
    reply_text: str,
    expected_uuids: Sequence[str],
) -> tuple[list[str], int, dict[str, Any] | None]:
    """Если ответ — частичный apply-ops по кадрам: (missing, delivered, data).

    None-data / пустой missing → добор не нужен.
    Сценарии characters/scenes без ops (scene_grammar outline) — пропускаем.
    """
    expected = [u for u in expected_uuids if str(u).strip()]
    if not expected:
        return [], 0, None
    try:
        from app.services.db_apply import extract_apply_ops_json
    except Exception:  # noqa: BLE001
        return [], 0, None
    data = extract_apply_ops_json(reply_text or "")
    if not isinstance(data, dict):
        return [], 0, None
    ops = [o for o in (data.get("ops") or []) if isinstance(o, dict)]
    chars = data.get("characters") or []
    scenes = data.get("scenes") or []
    if not ops and (chars or scenes):
        return [], 0, data
    if not ops:
        return [], 0, data
    exp_set = set(expected)
    got = {_op_uuid(o) for o in ops if _op_uuid(o)} & exp_set
    if not got:
        return [], 0, data
    missing = [u for u in expected if u not in got]
    if not missing:
        return [], len(got), data
    return missing, len(got), data


def merge_apply_ops_payloads(
    base: dict[str, Any] | None,
    *parts: dict[str, Any] | None,
) -> dict[str, Any]:
    """Склеить apply-ops: ops по uuid (последний побеждает), chars/scenes — append."""
    merged_ops: dict[str, dict[str, Any]] = {}
    characters: list[Any] = []
    scenes: list[Any] = []
    reports: list[str] = []

    def _ingest(payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return
        for op in payload.get("ops") or []:
            if not isinstance(op, dict):
                continue
            uid = _op_uuid(op)
            if uid:
                merged_ops[uid] = op
        chars = payload.get("characters")
        if isinstance(chars, list) and chars and not characters:
            characters.extend(chars)
        for sc in payload.get("scenes") or []:
            if isinstance(sc, dict):
                scenes.append(sc)
        rep = str(payload.get("report") or "").strip()
        if rep:
            reports.append(rep)

    _ingest(base)
    for p in parts:
        _ingest(p)

    out: dict[str, Any] = {"ops": list(merged_ops.values())}
    if characters:
        out["characters"] = characters
    if scenes:
        out["scenes"] = scenes
    if reports:
        out["report"] = " | ".join(reports)
    return out


def _volume_continue_prompt(
    *,
    missing_uuids: Sequence[str],
    batch_i: int,
    batch_n: int,
) -> str:
    lines = "\n".join(f"- {u}" for u in missing_uuids)
    return (
        "# VOLUME CONTINUE (ответ обрезан / объём превышен)\n"
        f"Батч добора {batch_i}/{batch_n}.\n"
        "Предыдущий ответ покрыл только часть кадров. "
        "Верни ТОЛЬКО JSON apply-ops на НЕДОСТАЮЩИЕ uuid ниже "
        "(не повторяй уже отданные).\n"
        'Формат: {"ops":[{"frame_uuid":"<uuid>","fields":{…}}]}\n'
        "Без markdown, без TSV, без просьб о файлах.\n\n"
        f"Недостающие frame_uuid ({len(missing_uuids)}):\n{lines}\n"
    )


async def volume_complete_apply_ops_reply(
    reply_text: str,
    *,
    input_paths: Sequence[Path] | None,
    prompt: str,
    accompanying: str = "",
    system: str | None = None,
    history: list[dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
) -> tuple[str, bool]:
    """Добрать недостающие frame ops после частичного ответа kie.

    Returns:
        (text, did_continue)
    """
    expected = load_expected_frame_uuids(input_paths)
    missing, delivered, base = missing_frame_uuids_for_volume(
        reply_text, expected
    )
    if not missing or delivered < 1 or base is None:
        return reply_text or "", False

    batches = plan_remainder_batches(missing, delivered=delivered)
    logger.warning(
        "volume_batches: partial apply-ops got={}/{} → {} continue batch(es) "
        "sizes={}",
        delivered,
        len(expected),
        len(batches),
        [len(b) for b in batches],
    )

    from app.services.db_apply import extract_apply_ops_json
    from app.services.gpt_api import chat

    merged = dict(base)
    # Лёгкие вложения: db_frames + без xlsx (как JSON-retry оператора).
    cont_paths = [
        Path(p)
        for p in (input_paths or [])
        if Path(p).suffix.lower() not in {".xlsx", ".xlsm", ".xls"}
    ] or [Path(p) for p in (input_paths or [])]

    for bi, batch_uuids in enumerate(batches, start=1):
        cont_prompt = _volume_continue_prompt(
            missing_uuids=batch_uuids,
            batch_i=bi,
            batch_n=len(batches),
        )
        # Не тащим весь исходный prompt повторно — только контракт добора.
        try:
            cont = await chat(
                prompt=cont_prompt,
                accompanying=(accompanying or "")[:4000],
                input_paths=cont_paths,
                system=system,
                history=None,
                model=model,
                temperature=0.0 if temperature is None else temperature,
                timeout=min(float(timeout or 300.0), 300.0),
                max_retries=0,
                xlsx_write_contract="apply_ops",
                volume_complete=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "volume_batches: continue batch {}/{} failed: {}",
                bi,
                len(batches),
                e,
            )
            break
        part = extract_apply_ops_json(cont.text or "")
        if not isinstance(part, dict) or not (part.get("ops") or []):
            logger.warning(
                "volume_batches: continue batch {}/{} — нет ops (chars={})",
                bi,
                len(batches),
                len(cont.text or ""),
            )
            break
        merged = merge_apply_ops_payloads(merged, part)
        # Обновим missing для лога; не перепланируем mid-flight.
        still, _, _ = missing_frame_uuids_for_volume(
            json.dumps(merged, ensure_ascii=False), expected
        )
        logger.info(
            "volume_batches: continue {}/{} ops_total={} still_missing={}",
            bi,
            len(batches),
            len(merged.get("ops") or []),
            len(still),
        )
        if not still:
            break

    final_text = json.dumps(merged, ensure_ascii=False, indent=2)
    return final_text, True
