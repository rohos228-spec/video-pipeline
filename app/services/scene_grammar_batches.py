"""Scene grammar: один ▶ → несколько GPT-вызовов → один merge в DB.

Модель может наплодить сколько угодно shots — пайплайн дробит приём
по батчам сцен, пользователь не перезапускает ноду.

Чекпоинт: после outline и каждого shot-батча пишем progress на диск.
Soft retry / пустой ответ GPT → продолжаем с незакрытых сцен, не с нуля.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.db_apply import extract_apply_ops_json
from app.services.gpt_operator_client import OperatorApiResult, _apply_ops_has_payload

# Меньше сцен на вызов — меньше пустых обрывов GPT на длинных паспортах.
_SCENES_PER_SHOT_BATCH = 2
_CHECKPOINT_NAME = "scene_grammar_checkpoint.json"
_GPT_JSON_ATTEMPTS = 3

_OUTLINE_FOOTER = """
# BATCH MODE — phase=outline (1/{total_hint})
Верни JSON на ВЕСЬ закадр:
{{"characters":[...],"scenes":[...],"ops":[],"report":"..."}}

У КАЖДОЙ сцены — полный паспорт (переход_в_сцену, структура_сцены, тип_стыка,
место, освещение, персонажи_сцены, особенность_доминанта, тип_сцены,
смысл_сцены=видимый факт, акцент, номер_кластера, start_words, end_words).

смысл_сцены: только то, что видно. Запрет: «зритель не знает», «напряжение», «намёк».
освещение сцены: источник+направление+контраст+тон (не «кинематографично»).

shots[] в ЭТОМ батче оставь ПУСТЫМ [] — детали шотов допишут следующие вызовы.
ops=[] обязательно.
Сколько сцен нужно по смыслу — столько и пиши. Не отказывайся из‑за объёма.
Без markdown.
""".strip()

_SHOTS_FOOTER = """
# BATCH MODE — phase=shots {batch_i}/{batch_n}
Уже зафиксированы сцены (ниже). Заполни ПОЛНЫЙ shots[] ТОЛЬКО для:
{scene_ids}

Сколько шотов требует монтаж — столько и пиши. Это нормально.
У КАЖДОГО shot обязательны: роль, особенность_сцены (словарь крупностей),
акцент (уникальный объект/жест), действие, описание_кадра (камера+композиция),
логика_перехода, фон (подробно: поверхности/глубина/цвет), освещение, предметы.

Непрерывность: соседние shots в том же месте → тот же базовый фон и то же
освещение; в описание_кадра пиши только дельту камеры (высота/ось/крупность).
Запрещено: одинаковый акцент; абстрактный смысл; «интерьер» в одно слово;
разный свет в одном помещении без смены времени/места.
Верни ТОЛЬКО:
{{"characters":[],"scenes":[...только эти id_scene, полный паспорт + shots...],"ops":[],"report":"..."}}

Не трогай другие сцены. Не пиши error про объём.
Без markdown.
""".strip()


def _merge_scene(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if k == "shots":
            if isinstance(v, list) and v:
                out["shots"] = v
            continue
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        out[k] = v
    return out


def merge_scene_grammar_payloads(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Склеить outline + shot-батчи в один apply-ops JSON."""
    characters: list[Any] = []
    scenes_by_id: dict[str, dict[str, Any]] = {}
    ops: list[Any] = []
    reports: list[str] = []

    for part in parts:
        if not isinstance(part, dict):
            continue
        chars = part.get("characters")
        if isinstance(chars, list) and chars and not characters:
            characters = list(chars)
        for sc in part.get("scenes") or []:
            if not isinstance(sc, dict):
                continue
            sid = str(sc.get("id_scene") or "").strip()
            if not sid:
                continue
            if sid in scenes_by_id:
                scenes_by_id[sid] = _merge_scene(scenes_by_id[sid], sc)
            else:
                scenes_by_id[sid] = dict(sc)
        for op in part.get("ops") or []:
            if isinstance(op, dict):
                ops.append(op)
        rep = str(part.get("report") or "").strip()
        if rep:
            reports.append(rep)

    scenes = list(scenes_by_id.values())
    # стабильный порядок scene_01, scene_02…
    scenes.sort(key=lambda s: str(s.get("id_scene") or ""))
    return {
        "characters": characters,
        "scenes": scenes,
        "ops": ops,
        "report": " | ".join(reports) if reports else f"scenes:{len(scenes)}",
    }


def _scenes_need_shots(scenes: list[Any]) -> list[dict[str, Any]]:
    need: list[dict[str, Any]] = []
    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        shots = sc.get("shots")
        if not isinstance(shots, list) or len(shots) == 0:
            need.append(sc)
    return need


def _checkpoint_path(out_dir: Path) -> Path:
    return out_dir / _CHECKPOINT_NAME


def load_scene_grammar_checkpoint(out_dir: Path) -> list[dict[str, Any]] | None:
    path = _checkpoint_path(out_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    parts = raw.get("parts") if isinstance(raw, dict) else None
    if not isinstance(parts, list) or not parts:
        return None
    out: list[dict[str, Any]] = []
    for p in parts:
        if isinstance(p, dict) and (
            isinstance(p.get("scenes"), list) or isinstance(p.get("characters"), list)
        ):
            out.append(p)
    return out or None


def save_scene_grammar_checkpoint(out_dir: Path, parts: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(out_dir)
    path.write_text(
        json.dumps({"parts": parts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_scene_grammar_checkpoint(out_dir: Path) -> None:
    path = _checkpoint_path(out_dir)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def diagnose_apply_ops_text(text: str) -> tuple[str, dict[str, Any] | None]:
    """Почему ответ не годится: reason + распарсенный dict если есть."""
    raw = text or ""
    if not raw.strip():
        return "empty_body", None
    data = extract_apply_ops_json(raw)
    if data is None:
        has_scenes_key = '"scenes"' in raw or '"characters"' in raw or '"ops"' in raw
        if has_scenes_key and raw.count("{") > raw.count("}"):
            return "json_truncated", None
        if has_scenes_key:
            return "json_unparseable", None
        if len(raw) < 1200:
            return "short_non_json", None
        return "no_apply_ops_json", None
    if not _apply_ops_has_payload(data):
        err = str(data.get("error") or "").strip()
        if err:
            return f"model_error:{err[:180]}", data
        return "empty_arrays", data
    return "ok", data


def _save_rejected_reply(
    out_dir: Path | None,
    *,
    attempt: int,
    reason: str,
    text: str,
) -> None:
    if out_dir is None:
        return
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"scene_grammar_rejected_a{attempt}_{reason.split(':')[0]}.txt"
        path.write_text(text or "", encoding="utf-8")
        logger.warning(
            "scene_grammar batch: saved rejected reply → {} ({} chars, reason={})",
            path.name,
            len(text or ""),
            reason[:120],
        )
    except OSError as exc:
        logger.warning("scene_grammar batch: cannot save rejected reply: {}", exc)


async def _gpt_apply_json(
    *,
    prompt: str,
    accompanying: str,
    input_paths: list[Path],
    out_dir: Path | None = None,
) -> dict[str, Any]:
    from app.services.gpt_api import chat

    last_err = "пустой ответ"
    acc = accompanying
    for attempt in range(1, _GPT_JSON_ATTEMPTS + 1):
        result = await chat(
            prompt=prompt,
            accompanying=acc,
            input_paths=list(input_paths),
            temperature=0.0,
            xlsx_write_contract="apply_ops",
        )
        text = result.text or ""
        reason, data = diagnose_apply_ops_text(text)
        if reason == "ok" and data is not None:
            return data
        err = ""
        if isinstance(data, dict):
            err = str(data.get("error") or data.get("report") or "")[:240]
        last_err = f"{reason}" + (f" | {err}" if err else "")
        _save_rejected_reply(out_dir, attempt=attempt, reason=reason, text=text)
        logger.warning(
            "scene_grammar batch: GPT JSON attempt {}/{} failed: {} (chars={})",
            attempt,
            _GPT_JSON_ATTEMPTS,
            last_err[:160],
            len(text),
        )
        if attempt >= _GPT_JSON_ATTEMPTS:
            break
        hint = {
            "json_truncated": (
                "Предыдущий JSON обрезан (не закрыты скобки). "
                "Верни ПОЛНЫЙ короткий JSON только на запрошенные сцены."
            ),
            "json_unparseable": (
                "Предыдущий JSON битый. Верни один валидный JSON-объект без markdown."
            ),
            "short_non_json": (
                "Предыдущий ответ слишком короткий / без scenes. Не пиши отказ — верни JSON."
            ),
            "empty_arrays": (
                "Предыдущий JSON с пустыми scenes/characters. Заполни scenes[]."
            ),
        }.get(reason.split(":")[0], "Верни непустой JSON {characters, scenes, ops, report}.")
        acc = (
            f"{accompanying}\n\n"
            f"# ПОВТОР (причина отказа: {reason})\n"
            f"{hint}\n"
            f"Детали: {err or reason}"
        )
    raise RuntimeError(
        "scene_grammar batch: GPT не вернул scenes/characters "
        f"({last_err})"
    )


async def run_scene_grammar_batched(
    *,
    project_dir: Path,
    node_key: str,
    master_prompt: str,
    accompanying: str,
    input_paths: list[Path],
    project_id: int | None = None,
    scenes_per_batch: int = _SCENES_PER_SHOT_BATCH,
) -> OperatorApiResult:
    """Outline всех сцен → батчи shots → один merge (с resume с чекпоинта)."""
    from app.services.step_cancel import raise_if_cancelled

    out_dir = project_dir / "excel_gpt_uploads" / str(node_key)
    out_dir.mkdir(parents=True, exist_ok=True)

    if project_id is not None:
        raise_if_cancelled(project_id)

    parts = load_scene_grammar_checkpoint(out_dir)
    if parts:
        merged_so_far = merge_scene_grammar_payloads(parts)
        scenes_so_far = list(merged_so_far.get("scenes") or [])
        still = _scenes_need_shots(scenes_so_far)
        logger.info(
            "scene_grammar batch: node={} resume checkpoint parts={} "
            "scenes={} still_need_shots={}",
            node_key,
            len(parts),
            len(scenes_so_far),
            len(still),
        )
        outline = parts[0]
        if not still and scenes_so_far:
            merged = merged_so_far
            # уже всё есть — сразу финал
            return _finalize_batched(out_dir, node_key, parts, merged)
        # база для батчей — актуальный merge (outline + готовые shots)
        base_scenes = scenes_so_far
        characters = list(merged_so_far.get("characters") or [])
    else:
        logger.info(
            "scene_grammar batch: node={} phase=outline start",
            node_key,
        )
        outline_acc = f"{accompanying}\n\n{_OUTLINE_FOOTER.format(total_hint='N')}"
        try:
            outline = await _gpt_apply_json(
                prompt=master_prompt,
                accompanying=outline_acc,
                input_paths=input_paths,
                out_dir=out_dir,
            )
        except Exception:
            raise
        parts = [outline]
        save_scene_grammar_checkpoint(out_dir, parts)
        base_scenes = list(outline.get("scenes") or [])
        characters = list(outline.get("characters") or [])
        logger.info(
            "scene_grammar batch: node={} outline scenes={} characters={}",
            node_key,
            len(base_scenes),
            len(characters),
        )

    need = _scenes_need_shots(base_scenes)
    if not need and base_scenes:
        logger.info(
            "scene_grammar batch: node={} shots уже в outline — skip shot-batches",
            node_key,
        )
    else:
        batches: list[list[dict[str, Any]]] = []
        for i in range(0, len(need), max(1, scenes_per_batch)):
            batches.append(need[i : i + max(1, scenes_per_batch)])
        batch_n = max(1, len(batches))
        for batch_i, batch in enumerate(batches, start=1):
            if project_id is not None:
                raise_if_cancelled(project_id)
            ids = [str(s.get("id_scene") or "") for s in batch]
            ids = [x for x in ids if x]
            stub = {
                "characters": characters,
                "scenes": batch,
            }
            footer = _SHOTS_FOOTER.format(
                batch_i=batch_i,
                batch_n=batch_n,
                scene_ids=", ".join(ids),
            )
            acc = (
                f"{accompanying}\n\n{footer}\n\n"
                f"# СЦЕНЫ ДЛЯ ЭТОГО БАТЧА\n"
                f"{json.dumps(stub, ensure_ascii=False)}"
            )
            logger.info(
                "scene_grammar batch: node={} phase=shots {}/{} ids={}",
                node_key,
                batch_i,
                batch_n,
                ids,
            )
            try:
                part = await _gpt_apply_json(
                    prompt=master_prompt,
                    accompanying=acc,
                    input_paths=input_paths,
                    out_dir=out_dir,
                )
            except Exception:
                # частичный прогресс уже на диске — soft retry подхватит
                save_scene_grammar_checkpoint(out_dir, parts)
                raise
            parts.append(part)
            save_scene_grammar_checkpoint(out_dir, parts)

    merged = merge_scene_grammar_payloads(parts)
    return _finalize_batched(out_dir, node_key, parts, merged)


def _finalize_batched(
    out_dir: Path,
    node_key: str,
    parts: list[dict[str, Any]],
    merged: dict[str, Any],
) -> OperatorApiResult:
    reply_text = json.dumps(merged, ensure_ascii=False, indent=2)
    reply_path = out_dir / "gpt_reply.txt"
    reply_path.write_text(reply_text + "\n", encoding="utf-8")
    meta_path = out_dir / "scene_grammar_batches.json"
    n_shots = sum(
        len(s.get("shots") or [])
        for s in (merged.get("scenes") or [])
        if isinstance(s, dict)
    )
    meta_path.write_text(
        json.dumps(
            {
                "batches": len(parts),
                "scenes": len(merged.get("scenes") or []),
                "shots": n_shots,
                "characters": len(merged.get("characters") or []),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    clear_scene_grammar_checkpoint(out_dir)
    logger.info(
        "scene_grammar batch: node={} done scenes={} shots={} chars={}",
        node_key,
        len(merged.get("scenes") or []),
        n_shots,
        len(merged.get("characters") or []),
    )
    return OperatorApiResult(
        reply_text=reply_text,
        output_paths=[reply_path, meta_path],
        apply_ops=merged,
    )
