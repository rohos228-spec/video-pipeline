# -*- coding: utf-8 -*-
"""Нарезка db_frames на батчи по символам (без пустых JSON-оценок).

img_pr:
  на каждый кадр планируем 4000 символов ответа;
  число батчей = ceil(n_frames * 4000 / 228000)
  (228000 = 57×4000 → на 171 кадр ровно 3 параллельных батча).

Прочие ноды с db_frames:
  число батчей = ceil(len(закадр) / 3500).

img_pr всегда важнее VO-формулы (даже если во вложениях есть voiceover.txt).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence, TypeVar

from loguru import logger

T = TypeVar("T")

# img_pr: ожидаемый объём ответа на кадр (символы) и бюджет одного ответа.
# 228_000 = 57 кадров × 4000 → на 171 кадр ровно 3 параллельных батча.
IMG_PR_CHARS_PER_FRAME = 4_000
IMG_PR_BATCH_CHAR_BUDGET = 228_000

# Прочие ноды: один батч на каждые 3500 символов закадра.
VO_CHARS_PER_BATCH = 3_500

# Старый имя — оставляем для тестов/импортов; теперь это символьный бюджет img_pr.
OUTPUT_TOKEN_BUDGET = IMG_PR_BATCH_CHAR_BUDGET


def batch_count_img_pr(n_frames: int) -> int:
    """ceil(n_frames * 4000 / 228000), минимум 1."""
    n = max(0, int(n_frames))
    if n <= 0:
        return 1
    return max(
        1,
        math.ceil(n * IMG_PR_CHARS_PER_FRAME / IMG_PR_BATCH_CHAR_BUDGET),
    )


def batch_count_by_voiceover(vo_chars: int) -> int:
    """ceil(vo_chars / 3500), минимум 1."""
    c = max(0, int(vo_chars))
    if c <= 0:
        return 1
    return max(1, math.ceil(c / VO_CHARS_PER_BATCH))


def split_into_n_batches(frames: Sequence[T], n_batches: int) -> list[list[T]]:
    """Делит список кадров на n почти равных батчей (порядок сохраняется)."""
    items = list(frames)
    if not items:
        return []
    n = max(1, min(int(n_batches), len(items)))
    if n == 1:
        return [items]
    size = len(items)
    out: list[list[T]] = []
    start = 0
    for i in range(n):
        # Равномерное деление: первые (size % n) батчей на 1 длиннее.
        chunk = size // n + (1 if i < (size % n) else 0)
        end = start + chunk
        out.append(items[start:end])
        start = end
    return [b for b in out if b]


def pack_frames_img_pr(frames: Sequence[T]) -> list[list[T]]:
    n = batch_count_img_pr(len(frames))
    batches = split_into_n_batches(frames, n)
    logger.info(
        "output_batch_plan img_pr: frames={} chars/frame={} budget={} "
        "batches={} sizes={}",
        len(frames),
        IMG_PR_CHARS_PER_FRAME,
        IMG_PR_BATCH_CHAR_BUDGET,
        len(batches),
        [len(b) for b in batches],
    )
    return batches


def pack_frames_by_voiceover(
    frames: Sequence[T], vo_text: str | None
) -> list[list[T]]:
    vo = vo_text or ""
    n = batch_count_by_voiceover(len(vo))
    batches = split_into_n_batches(frames, n)
    logger.info(
        "output_batch_plan vo: frames={} vo_chars={} per_batch={} "
        "batches={} sizes={}",
        len(frames),
        len(vo),
        VO_CHARS_PER_BATCH,
        len(batches),
        [len(b) for b in batches],
    )
    return batches


def detect_pack_kind(
    paths: Sequence[Path] | None,
    *,
    prompt: str | None = None,
    accompanying: str | None = None,
    pack_kind: str | None = None,
) -> str:
    """img_pr имеет приоритет над VO (voiceover.txt не перебивает)."""
    explicit = (pack_kind or "").strip().lower()
    if explicit in {"img_pr", "vo"}:
        return explicit
    for raw in paths or []:
        name = Path(raw).name.lower()
        if "img_pr" in name:
            return "img_pr"
    blob = f"{prompt or ''}\n{accompanying or ''}".casefold()
    if (
        "img_pr" in blob
        or "промт_картинки" in blob
        or "промты картинок" in blob
        or "image_prompt" in blob
    ):
        return "img_pr"
    return "vo"


def read_voiceover_chars(paths: Sequence[Path] | None) -> int:
    for raw in paths or []:
        p = Path(raw)
        if p.name.lower() in {"voiceover.txt", "закадр.txt"} and p.is_file():
            try:
                return len(p.read_text(encoding="utf-8"))
            except OSError:
                return 0
    return 0


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
    pack_kind: str | None = None,
    vo_text: str | None = None,
    prompt: str | None = None,
    accompanying: str | None = None,
    force_batches: int | None = None,
) -> list[Path] | None:
    """Нарезать db_frames на файлы-батчи. None = одна пачка, резать нечего.

    ``force_batches``: 2 или 4 — Adaptive 1→2→4 (игнор VO/img_pr формул).
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
    n_force = int(force_batches) if force_batches else 0
    if n_force >= 2:
        batches = split_into_n_batches(frames, n_force)
        if len(batches) <= 1:
            return None
        logger.info(
            "output_batch_plan force_batches={} frames={} sizes={}",
            n_force,
            len(frames),
            [len(b) for b in batches],
        )
        return [
            write_db_frames_slice(src, payload, list(batch), tag=f"p{i:02d}")
            for i, batch in enumerate(batches, start=1)
        ]
    kind = detect_pack_kind(
        paths,
        prompt=prompt,
        accompanying=accompanying,
        pack_kind=pack_kind,
    )
    if kind == "img_pr":
        batches = pack_frames_img_pr(frames)
    else:
        vo = vo_text
        if vo is None:
            n_chars = read_voiceover_chars(paths)
            if n_chars <= 0:
                for key in ("voiceover", "закадр", "vo_text", "full_vo"):
                    raw_vo = payload.get(key)
                    if isinstance(raw_vo, str) and raw_vo.strip():
                        vo = raw_vo
                        break
            else:
                vo = "x" * n_chars
        batches = pack_frames_by_voiceover(frames, vo)

    if len(batches) <= 1:
        return None
    return [
        write_db_frames_slice(src, payload, list(batch), tag=f"p{i:02d}")
        for i, batch in enumerate(batches, start=1)
    ]


# --- совместимость со старыми тестами/вызовами (deprecated path) -------------

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
    count_tokens: Any = None,
    body_cap: int = _BODY_CAP,
) -> int:
    """Deprecated: для img_pr больше не используется (см. pack_frames_img_pr)."""
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
    count_tokens: Any = None,
    pack_kind: str = "img_pr",
    vo_text: str | None = None,
) -> list[list[T]]:
    """Новый SoT: img_pr по 4000 симв/кадр; иначе по длине закадра/3500.

    ``budget`` / ``count_tokens`` игнорируются (совместимость сигнатуры).
    """
    del budget, count_tokens
    if (pack_kind or "img_pr").strip().lower() == "img_pr":
        return pack_frames_img_pr(frames)
    return pack_frames_by_voiceover(frames, vo_text)
