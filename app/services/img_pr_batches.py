"""img_pr: крупные батчи + одна GPT-сессия (промт один раз).

STYLE LOCK вшивает пайплайн (`img_pr_style`) — GPT пишет только сцену.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.db_apply import extract_apply_ops_json
from app.services.img_pr_style import wrap_ops_styles

# Меньше кадров → меньше битого JSON (лишние } / персонажи вне fields).
_FRAMES_PER_BATCH = 25
_CHECKPOINT_NAME = "img_pr_checkpoint.json"
_GPT_ATTEMPTS = 3

_BATCH_FOOTER = """
# BATCH {batch_i}/{batch_n} — только эти {n} кадров из db_frames.json
Верни ТОЛЬКО валидный JSON (без markdown, без прозы):
{{"ops":[{{"frame_uuid":"<uuid>","fields":{{"промт_картинки":"…","персонажи":"c01"}}}}]}}

ЖЁСТКО:
- `персонажи` ТОЛЬКО внутри `fields`, не рядом с frame_uuid;
- в тексте промта НЕ используй символ ASCII двойной кавычки \"; пиши «ёлочки»;
- ровно одна закрывающая скобка на конец каждого op; не закрывай массив ops раньше времени;
- один op на каждый uuid батча.

В `промт_картинки` пиши ТОЛЬКО сцену на русском (Референс?/Фон/Действие/Свет/
Акцент/Смысл/План-ракурс/Эмоция/Детали/Место). НЕ копируй STYLE/Negative —
пайплайн допишет сам. Тело сцены ≤ 4000 символов.

=== ФОН / ПЛАН / РЕФ / ТЕКСТ (КРИТИЧНО) ===
- Фон = подробно из shot01_bg (священно); план/ракурс = scene_feature + shot01_description.
- При укорачивании до 4000 НЕ выкидывай фон и план/ракурс.
- Один cXX = один референс = одно тело; запрет клонов/идентичных лиц.
- Без читаемого текста, если надпись не задана во входе (никакой каши/латиницы).

=== ДЕЙСТВИЕ / АНТИ-TWIN (КРИТИЧНО) ===
- Action = mid-motion freeze (тело/рука/предмет В ДВИЖЕНИИ), не поза.
- БРАК: стоит/сидит/смотрит/замер/posed/standing looking без глагола движения.
- Один place на соседних кадрах = ЦЕПЬ фаз (не 4 одинаковых постера).
- Соседи отличаются ≥2 из: фаза действия, поза тела, состояние пропа, камера/accent.
- Визуальный twin (тот же силуэт+prop+дистанция) = брак — перепиши Action/камеру.
""".strip()

_FOLLOWUP_MSG = """
Следующий батч. Те же правила. STYLE/Negative не пиши — только сцена на русском.
Фон и план священны; ≤4000; 1 cXX = 1 тело; mid-motion; анти-twin.
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


_PROMPT_FIELD_KEYS = (
    "промт_картинки",
    "image_prompt",
    "промпт_картинки",
    "промт_картинки_2",
    "image_prompt_shot2",
)


def filter_prompt_ops(ops: list[Any]) -> list[dict]:
    clean: list[dict] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        if str(op.get("target") or "frame") not in {"frame", ""}:
            continue
        raw_fields = op.get("fields")
        if raw_fields is None:
            fields = {}
        elif isinstance(raw_fields, dict):
            fields = dict(raw_fields)
        else:
            continue
        # Модель часто пишет персонажи рядом с fields — заберём внутрь.
        for k in ("персонажи", "characters"):
            if k in op and k not in fields and op.get(k) is not None:
                fields[k] = op.get(k)
        if not any(k in fields for k in _PROMPT_FIELD_KEYS):
            # Иногда промт лежит на верхнем уровне op.
            for k in _PROMPT_FIELD_KEYS:
                if k in op and str(op.get(k) or "").strip():
                    fields[k] = op.get(k)
        if not any(k in fields for k in _PROMPT_FIELD_KEYS):
            continue
        out = {**op, "fields": fields}
        clean.append(out)
    return clean


def _read_json_string(text: str, start: int) -> tuple[str, int] | None:
    """Прочитать JSON-строку начиная с start (символ сразу после открывающей \")."""
    if start < 0 or start > len(text):
        return None
    i = start
    chars: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            if i + 1 >= len(text):
                return None
            chars.append(text[i : i + 2])
            i += 2
            continue
        if ch == '"':
            # decode escapes via json
            raw = '"' + "".join(chars) + '"'
            try:
                return json.loads(raw), i + 1
            except Exception:  # noqa: BLE001
                return "".join(chars), i + 1
        chars.append(ch)
        i += 1
    return None


def salvage_img_pr_ops(reply: str) -> list[dict]:
    """Достать ops даже из битого JSON (лишние }, персонажи вне fields)."""
    if not reply:
        return []
    ops: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'"frame_uuid"\s*:\s*"([a-fA-F0-9]+)"',
        reply,
    ):
        uuid = m.group(1).strip()
        if not uuid or uuid in seen:
            continue
        window = reply[m.end() : m.end() + 12000]
        pm = re.search(
            r'"промт_картинки"\s*:\s*"',
            window,
        ) or re.search(
            r'"image_prompt"\s*:\s*"',
            window,
        )
        if not pm:
            continue
        read = _read_json_string(window, pm.end())
        if not read:
            continue
        prompt, after = read
        if not str(prompt).strip():
            continue
        fields: dict[str, Any] = {"промт_картинки": prompt}
        rest = window[after : after + 200]
        ch = re.search(r'"персонажи"\s*:\s*"([^"]*)"', rest)
        if ch:
            fields["персонажи"] = ch.group(1)
        ops.append({"frame_uuid": uuid, "fields": fields})
        seen.add(uuid)
    return ops


def parse_img_pr_ops(reply: str, *, wrap_style: bool = True) -> list[dict]:
    data = extract_apply_ops_json(reply or "")
    ops = list((data or {}).get("ops") or []) if isinstance(data, dict) else []
    clean = filter_prompt_ops(ops)
    if not clean:
        clean = filter_prompt_ops(salvage_img_pr_ops(reply or ""))
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
