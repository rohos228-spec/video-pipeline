"""img_pr: крупные батчи + одна GPT-сессия (мастер во вложении на каждый батч).

Промт картинки — как вернул GPT (без пайплайн-обёртки STYLE_HEAD/TAIL).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Sequence, TypeVar

from app.services.db_apply import extract_apply_ops_json
from app.services.volume_batches import (
    MIN_CONTINUE_SIZE,
    plan_remainder_batches,
)

# Fallback only when caller omits size (xlsx uses plan_batch_size).
_FRAMES_PER_BATCH = 30
_T = TypeVar("_T")
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
- пиши только полные ops (сцена + STYLE + Negative); если не все uuid влезли —
  верни сколько полных влезло, остальные uuid не включай;
- пустой {{"ops":[]}} запрещён.

В `промт_картинки` пиши ПОЛНЫЙ промт: сцена на русском (Референс?/Фон/Действие/
Свет/Акцент/Смысл/План-ракурс/Эмоция/Детали/Место) + STYLE LOCK / Final style
lock / Negative из мастера. Оркестратор НИЧЕГО не дописывает.
Тело ≤ 4877 символов (режь сюжет, не STYLE/Negative).

=== ФОН / ПЛАН / РЕФ / ТЕКСТ (КРИТИЧНО) ===
- Фон = подробно из shot01_bg (священно); план/ракурс = scene_feature + shot01_description.
- При укорачивании до 4877 НЕ выкидывай фон, план/ракурс и STYLE LOCK.
- Один cXX = один референс = одно тело; запрет клонов/идентичных лиц.
- Без читаемого текста, если надпись не задана во входе (никакой каши/латиницы).

=== ДЕЙСТВИЕ / АНТИ-TWIN (КРИТИЧНО) ===
- Action = mid-motion freeze (тело/рука/предмет В ДВИЖЕНИИ), не поза.
- БРАК: стоит/сидит/смотрит/замер/posed/standing looking без глагола движения.
- Один place на соседних кадрах = ЦЕПЬ фаз (не 4 одинаковых постера).
- Соседи отличаются ≥2 из: фаза действия, поза тела, состояние пропа, камера/accent.
- Визуальный twin (тот же силуэт+prop+дистанция) = брак — перепиши Action/камеру.

=== COVERAGE K2/K3 (КРИТИЧНО) ===
Если у кадра есть coverage_parent — это НЕ новая сцена, а следующий ракурс той же.
Первый абзац промта:
Image 1 is the previous coverage still of the SAME scene (layout / cast-count / prop-identity lock).
Preserve: [place, shot01_bg, lighting, персонажи — то же число тел, key props = тот же экземпляр].
Change: camera + действие ЭТОГО шота (shot01_description / shot01_action).
Запрещено: новая локация; второй человек, которого не было в master;
крупный план другой картины/документа (нужен punch-in той же).
Фон/мебель/стены/люди/предметы бери из coverage_parent.
""".strip()

_FOLLOWUP_MSG = """
Следующий батч. Те же правила. Полный промт: сцена + STYLE LOCK / Negative.
Фон и план священны; ≤4877; 1 cXX = 1 тело; mid-motion; анти-twin.
K2/K3 с coverage_parent: Preserve/Change, тот же сет/состав/предметы что у родителя, только камера.
Мастер-промт и db_frames.json во вложении — только кадры этого батча.
{footer}
""".strip()

_PLASTILIN_BATCH_FOOTER = """
# BATCH {batch_i}/{batch_n} — только эти {n} кадров из db_frames.json
Верни ТОЛЬКО валидный JSON (без markdown, без прозы):
{{"ops":[{{"frame_uuid":"<uuid>","fields":{{"промт_картинки":"…","персонажи":"c01"}}}}]}}

ЖЁСТКО:
- `персонажи` ТОЛЬКО внутри `fields`, не рядом с frame_uuid;
- в тексте промта НЕ используй символ ASCII двойной кавычки \"; пиши «ёлочки»;
- ровно одна закрывающая скобка на конец каждого op;
- пиши только полные ops; если не все uuid влезли — верни сколько полных
  влезло, остальные uuid не включай;
- пустой {{"ops":[]}} запрещён.

В `промт_картинки` пиши ПОЛНЫЙ промт: стиль пластилина ТРИ раза + сцена + Negative.
Пайплайн НЕ допишет watercolor/noir. Не копируй Archival Noir.
Тело ≤ 5000 символов. Без текста на картинке. Либо люди, либо крупный план предмета без рук.
""".strip()

_PLASTILIN_FOLLOWUP_MSG = """
Следующий батч. Те же правила. Стиль пластилина оставь в промт_картинки (три раза).
Watercolor/noir не пиши. ≤5000; 1 cXX = 1 тело; без текста на картинке.
Мастер-промт и db_frames.json во вложении — только кадры этого батча.
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


def batch_attach_files(
    *,
    batch_i: int,
    prompt_file: Path,
    db_path: Path,
    voiceover: Path | None = None,
) -> list[Path]:
    """Мастер-промт на каждый батч; voiceover — только на первом."""
    files = [prompt_file, db_path]
    if batch_i == 1 and voiceover is not None:
        files.append(voiceover)
    return files


def plan_batch_size(
    n_frames: int,
    *,
    target_batches: int = 3,
    min_size: int = MIN_CONTINUE_SIZE,
    max_size: int = 40,
) -> int:
    """Initial img_pr chunk size: aim for ``target_batches`` (110 → 37 → 3).

    ``n_frames <= min_size`` → one batch (return n, not a split below floor).
    ``max_size`` is a soft cap: exceeded when needed to keep ~target batches
    (199 → ~67 × 3, not 40 × 5).
    """
    n = int(n_frames)
    tb = max(1, int(target_batches))
    mn = max(1, int(min_size))
    mx = max(mn, int(max_size))
    if n <= 0:
        return mn
    if n <= mn:
        return n
    size = max(mn, math.ceil(n / tb))
    capped = min(size, mx)
    # Clamp to max_size only when that still yields <= target_batches.
    if math.ceil(n / capped) <= tb:
        return max(mn, capped)
    return size


def chunk_frames(frames: list[Any], *, size: int = _FRAMES_PER_BATCH) -> list[list[Any]]:
    if size < 1:
        size = _FRAMES_PER_BATCH
    return [frames[i : i + size] for i in range(0, len(frames), size)]


def repartition_remaining(
    remaining: Sequence[_T],
    delivered: int,
    *,
    min_size: int = MIN_CONTINUE_SIZE,
) -> list[list[_T]]:
    """Rechunk leftover frames after a partial batch; never continue by onesie."""
    return plan_remainder_batches(
        remaining, delivered=delivered, min_size=min_size
    )


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


def is_empty_ops_reply(reply: str) -> bool:
    """True если ответ — валидный JSON с пустым ops (заглушка/отказ)."""
    text = (reply or "").strip()
    if not text:
        return False
    data = extract_apply_ops_json(text)
    if isinstance(data, dict) and isinstance(data.get("ops"), list):
        if data["ops"]:
            return False
        if filter_prompt_ops(salvage_img_pr_ops(text)):
            return False
        return True
    compact = re.sub(r"\s+", "", text)
    return compact == '{"ops":[]}'


def parse_img_pr_ops(
    reply: str,
    *,
    wrap_style: bool = False,
    style_id: str | None = None,
    style_block: str | None = None,
) -> list[dict]:
    _ = wrap_style, style_id, style_block  # legacy kwargs, wrap отключён
    data = extract_apply_ops_json(reply or "")
    ops = list((data or {}).get("ops") or []) if isinstance(data, dict) else []
    clean = filter_prompt_ops(ops)
    salvaged = filter_prompt_ops(salvage_img_pr_ops(reply or ""))
    partial = bool(isinstance(data, dict) and data.get("_salvaged_partial"))
    # Битый/обрезанный JSON: extract взял мало ops — regex достаёт все uuid.
    if not clean:
        clean = salvaged
    elif (partial or len(clean) <= 1) and len(salvaged) > len(clean):
        clean = salvaged
    return clean


def batch_footer(*, batch_i: int, batch_n: int, n: int, plastilin: bool = False) -> str:
    tmpl = _PLASTILIN_BATCH_FOOTER if plastilin else _BATCH_FOOTER
    return tmpl.format(batch_i=batch_i, batch_n=batch_n, n=n)


def followup_message(*, batch_i: int, batch_n: int, n: int, plastilin: bool = False) -> str:
    tmpl = _PLASTILIN_FOLLOWUP_MSG if plastilin else _FOLLOWUP_MSG
    return tmpl.format(
        footer=batch_footer(
            batch_i=batch_i, batch_n=batch_n, n=n, plastilin=plastilin
        )
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
