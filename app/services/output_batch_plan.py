"""Оценка выхода GPT до запроса и нарезка независимых пачек.

Бюджет — полезный JSON в одном ответе (ниже 128k output tokens).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

from loguru import logger

T = TypeVar("T")

# 128k потолок Sol × 0.65 reasoning × 0.85 запас JSON/CF.
OUTPUT_TOKEN_BUDGET = 70_000
_ATTR_KEYS = (
    "shot01_bg",
    "shot01_action",
    "shot01_description",
    "shot01_props",
    "place",
    "lighting",
    "scene_lighting",
    "accent",
    "scene_sense",
    "scene_feature",
    "main_action",
)
_BODY_CAP = 4_000
_WRAP_OVERHEAD = 24


def _default_count_tokens(text: str) -> int:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(text or ""))
    except Exception:  # noqa: BLE001
        return max(1, len(text or "") // 3)


def _frame_uuid(frame: Any) -> str:
    if isinstance(frame, dict):
        return str(frame.get("uuid") or frame.get("frame_uuid") or "").strip()
    return str(getattr(frame, "uuid", None) or "").strip()


def _attrs_map(frame: Any) -> dict[str, str]:
    if isinstance(frame, dict):
        return frame
    raw = getattr(frame, "attrs", None)
    return raw if isinstance(raw, dict) else {}


def proxy_prompt_body(frame: Any, *, body_cap: int = _BODY_CAP) -> str:
    """Текст, который кладём в оценку, пока реального ответа нет."""
    if isinstance(frame, dict):
        existing = str(
            frame.get("промт_картинки")
            or frame.get("image_prompt")
            or frame.get("промт_видео")
            or frame.get("animation_prompt")
            or ""
        ).strip()
    else:
        existing = (
            getattr(frame, "image_prompt", None)
            or getattr(frame, "animation_prompt", None)
            or ""
        ).strip()
    if existing:
        return existing[:body_cap]
    attrs = _attrs_map(frame)
    parts: list[str] = []
    for key in _ATTR_KEYS:
        val = str(attrs.get(key) or "").strip()
        if val:
            parts.append(val)
    return "\n".join(parts).strip()[:body_cap]


def estimate_frame_output_tokens(
    frame: Any,
    *,
    count_tokens: Callable[[str], int] | None = None,
    body_cap: int = _BODY_CAP,
) -> int:
    """Токены одного apply-op (прокси тела + JSON-обёртка)."""
    count = count_tokens or _default_count_tokens
    uuid = _frame_uuid(frame) or "x"
    body = proxy_prompt_body(frame, body_cap=body_cap)
    op = {"frame_uuid": uuid, "fields": {"промт_картинки": body}}
    blob = json.dumps(op, ensure_ascii=False, separators=(",", ":"))
    return max(1, count(blob))


def pack_frames_for_output(
    frames: Sequence[T],
    *,
    budget: int = OUTPUT_TOKEN_BUDGET,
    count_tokens: Callable[[str], int] | None = None,
) -> list[list[T]]:
    """Жадная нарезка по сумме оценок. Кадр больше бюджета — один в пачке."""
    cap = max(1, int(budget))
    items = list(frames)
    if not items:
        return []
    batches: list[list[T]] = []
    cur: list[T] = []
    acc = _WRAP_OVERHEAD
    for fr in items:
        est = estimate_frame_output_tokens(fr, count_tokens=count_tokens)
        if cur and acc + est > cap:
            batches.append(cur)
            cur = []
            acc = _WRAP_OVERHEAD
        cur.append(fr)
        acc += est
    if cur:
        batches.append(cur)
    logger.info(
        "output_batch_plan: frames={} batches={} sizes={} budget={}",
        len(items),
        len(batches),
        [len(b) for b in batches],
        cap,
    )
    return batches


def load_db_frames_payload(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    frames = data.get("frames")
    if not isinstance(frames, list) or not frames:
        return None
    return data


def write_db_frames_slice(
    src: Path,
    payload: dict[str, Any],
    frames: list[dict[str, Any]],
    *,
    tag: str,
) -> Path:
    out = src.with_name(f"{src.stem}_{tag}{src.suffix}")
    blob = dict(payload)
    blob["frames"] = frames
    out.write_text(
        json.dumps(blob, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return out


def plan_db_frames_slices(
    paths: Sequence[Path] | None,
    *,
    budget: int = OUTPUT_TOKEN_BUDGET,
    count_tokens: Callable[[str], int] | None = None,
) -> list[Path] | None:
    """Если во вложениях db_frames слишком жирный для одного ответа — нарезать файлы.

    None = нечего резать (нет файла / одна пачка).
    """
    from app.services.volume_batches import is_db_frames_path

    src: Path | None = None
    for raw in paths or []:
        p = Path(raw)
        if is_db_frames_path(p) and p.is_file():
            src = p
            break
    if src is None:
        return None
    payload = load_db_frames_payload(src)
    if payload is None:
        return None
    frames = [fr for fr in (payload.get("frames") or []) if isinstance(fr, dict)]
    batches = pack_frames_for_output(
        frames, budget=budget, count_tokens=count_tokens
    )
    if len(batches) <= 1:
        return None
    return [
        write_db_frames_slice(src, payload, list(batch), tag=f"p{i:02d}")
        for i, batch in enumerate(batches, start=1)
    ]
