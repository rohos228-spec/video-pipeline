"""img_pr: крупные батчи + одна GPT-сессия (промт один раз).

STYLE LOCK вшивает пайплайн (`img_pr_style`) — GPT пишет только сцену.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.db_apply import extract_apply_ops_json
from app.services.img_pr_style import wrap_ops_styles

# Сцена без STYLE ~0.5–1.5k → 40 кадров нормально влезают в ответ.
_FRAMES_PER_BATCH = 40
_CHECKPOINT_NAME = "img_pr_checkpoint.json"
_GPT_ATTEMPTS = 2

_BATCH_FOOTER = """
# BATCH {batch_i}/{batch_n} — только эти {n} кадров из db_frames.json
Верни ТОЛЬКО JSON:
{{"ops":[{{"frame_uuid":"<uuid>","fields":{{"промт_картинки":"…","промт_картинки_2":"…","персонажи":"c01"}}}}]}}

В `промт_картинки` пиши ТОЛЬКО сцену (ref?/фон/действие/свет/accent/
scene_sense/scene_feature/детали/место-время). НЕ копируй STYLE LOCK —
пайплайн допишет стиль сам. Один op на каждый uuid батча. Без markdown.
""".strip()

_FOLLOWUP_MSG = """
Следующий батч. Те же правила. STYLE LOCK не пиши — только сцена.
db_frames.json во вложении — только кадры этого батча.
{footer}
""".strip()


def _checkpoint_path(project_dir: Path) -> Path:
    return project_dir / "tmp_gpt" / _CHECKPOINT_NAME


def load_checkpoint(project_dir: Path) -> dict[str, Any]:
    path = _checkpoint_path(project_dir)
    if not path.is_file():
        return {"done_uuids": [], "ops": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"done_uuids": [], "ops": []}
    if not isinstance(data, dict):
        return {"done_uuids": [], "ops": []}
    done = data.get("done_uuids") or []
    ops = data.get("ops") or []
    return {
        "done_uuids": [str(u) for u in done if str(u).strip()],
        "ops": [o for o in ops if isinstance(o, dict)],
    }


def save_checkpoint(project_dir: Path, *, done_uuids: list[str], ops: list[dict]) -> None:
    path = _checkpoint_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"done_uuids": done_uuids, "ops": ops},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def clear_checkpoint(project_dir: Path) -> None:
    path = _checkpoint_path(project_dir)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def chunk_frames(frames: list[Any], *, size: int = _FRAMES_PER_BATCH) -> list[list[Any]]:
    if size < 1:
        size = _FRAMES_PER_BATCH
    return [frames[i : i + size] for i in range(0, len(frames), size)]


def filter_prompt_ops(ops: list[Any]) -> list[dict]:
    clean: list[dict] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        if str(op.get("target") or "frame") not in {"frame", ""}:
            continue
        fields = op.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        if not any(
            k in fields
            for k in (
                "промт_картинки",
                "image_prompt",
                "промпт_картинки",
                "промт_картинки_2",
                "image_prompt_shot2",
            )
        ):
            continue
        clean.append(op)
    return clean


def parse_img_pr_ops(reply: str, *, wrap_style: bool = True) -> list[dict]:
    data = extract_apply_ops_json(reply or "")
    ops = list((data or {}).get("ops") or []) if isinstance(data, dict) else []
    clean = filter_prompt_ops(ops)
    if wrap_style:
        return wrap_ops_styles(clean)
    return clean


def batch_footer(*, batch_i: int, batch_n: int, n: int) -> str:
    return _BATCH_FOOTER.format(batch_i=batch_i, batch_n=batch_n, n=n)


def followup_message(*, batch_i: int, batch_n: int, n: int) -> str:
    return _FOLLOWUP_MSG.format(
        footer=batch_footer(batch_i=batch_i, batch_n=batch_n, n=n)
    )


def write_rejected_reply(
    tmp_dir: Path, *, batch_i: int, attempt: int, reply: str, reason: str
) -> Path:
    path = tmp_dir / f"img_pr_rejected_b{batch_i}_a{attempt}.txt"
    path.write_text(
        f"# reason: {reason}\n# reply_len: {len(reply or '')}\n\n{reply or ''}",
        encoding="utf-8",
    )
    return path


def uuid_of_op(op: dict) -> str:
    return str(op.get("frame_uuid") or op.get("uuid") or "").strip()
