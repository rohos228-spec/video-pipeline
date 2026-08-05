"""Scene grammar: один ▶ → несколько GPT-вызовов → один merge в DB.

Модель может наплодить сколько угодно shots — пайплайн дробит приём
по батчам сцен, пользователь не перезапускает ноду.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.db_apply import extract_apply_ops_json
from app.services.gpt_operator_client import OperatorApiResult, _apply_ops_has_payload

_SCENES_PER_SHOT_BATCH = 3

_OUTLINE_FOOTER = """
# BATCH MODE — phase=outline (1/{total_hint})
Верни JSON на ВЕСЬ закадр:
{{"characters":[...],"scenes":[...],"ops":[],"report":"..."}}

У КАЖДОЙ сцены — полный паспорт (переход_в_сцену, структура_сцены, тип_стыка,
место, персонажи_сцены, особенность_доминанта, тип_сцены, смысл_сцены, акцент,
номер_кластера, start_words, end_words).

shots[] в ЭТОМ батче оставь ПУСТЫМ [] — детали шотов допишут следующие вызовы.
ops=[] обязательно.
Сколько сцен нужно по смыслу — столько и пиши. Не отказывайся из‑за объёма.
Без markdown.
""".strip()

_SHOTS_FOOTER = """
# BATCH MODE — phase=shots {batch_i}/{batch_n}
Уже зафиксированы сцены (ниже). Заполни ПОЛНЫЙ shots[] ТОЛЬКО для:
{scene_ids}

Сколько шотов требует монтаж — столько и пиши (хоть 15 на сцену). Это нормально.
Верни ТОЛЬКО:
{{"characters":[],"scenes":[...только эти id_scene, полный паспорт + shots...],"ops":[],"report":"..."}}

Не трогай другие сцены. Не пиши error про объём — дроби смысл на shots.
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


async def _gpt_apply_json(
    *,
    prompt: str,
    accompanying: str,
    input_paths: list[Path],
) -> dict[str, Any]:
    from app.services.gpt_api import chat

    result = await chat(
        prompt=prompt,
        accompanying=accompanying,
        input_paths=list(input_paths),
        temperature=0.0,
        xlsx_write_contract="apply_ops",
    )
    data = extract_apply_ops_json(result.text or "")
    if data is not None and _apply_ops_has_payload(data):
        return data
    err = ""
    if isinstance(data, dict):
        err = str(data.get("error") or data.get("report") or "")[:240]
    # один retry без «отказов»
    retry_acc = (
        f"{accompanying}\n\n"
        "# ПОВТОР\n"
        "Предыдущий ответ отклонён. Верни непустой JSON "
        "{characters, scenes, ops, report}. "
        f"Детали прошлого отказа (игнор если про объём): {err or 'пусто'}"
    )
    result2 = await chat(
        prompt=prompt,
        accompanying=retry_acc,
        input_paths=list(input_paths),
        temperature=0.0,
        xlsx_write_contract="apply_ops",
    )
    data2 = extract_apply_ops_json(result2.text or "")
    if data2 is not None and _apply_ops_has_payload(data2):
        return data2
    raise RuntimeError(
        "scene_grammar batch: GPT не вернул scenes/characters "
        f"({err or 'пустой ответ'})"
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
    """Outline всех сцен → батчи shots → один merge."""
    from app.services.step_cancel import raise_if_cancelled

    out_dir = project_dir / "excel_gpt_uploads" / str(node_key)
    out_dir.mkdir(parents=True, exist_ok=True)

    if project_id is not None:
        raise_if_cancelled(project_id)

    logger.info(
        "scene_grammar batch: node={} phase=outline start",
        node_key,
    )
    outline_acc = f"{accompanying}\n\n{_OUTLINE_FOOTER.format(total_hint='N')}"
    outline = await _gpt_apply_json(
        prompt=master_prompt,
        accompanying=outline_acc,
        input_paths=input_paths,
    )
    parts: list[dict[str, Any]] = [outline]
    scenes = list(outline.get("scenes") or [])
    logger.info(
        "scene_grammar batch: node={} outline scenes={} characters={}",
        node_key,
        len(scenes),
        len(outline.get("characters") or []),
    )

    need = _scenes_need_shots(scenes)
    # Если модель уже залила shots в outline — второй фазы нет.
    if not need and scenes:
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
                "characters": outline.get("characters") or [],
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
            part = await _gpt_apply_json(
                prompt=master_prompt,
                accompanying=acc,
                input_paths=input_paths,
            )
            parts.append(part)

    merged = merge_scene_grammar_payloads(parts)
    reply_text = json.dumps(merged, ensure_ascii=False, indent=2)
    reply_path = out_dir / "gpt_reply.txt"
    reply_path.write_text(reply_text + "\n", encoding="utf-8")
    meta_path = out_dir / "scene_grammar_batches.json"
    meta_path.write_text(
        json.dumps(
            {
                "batches": len(parts),
                "scenes": len(merged.get("scenes") or []),
                "shots": sum(
                    len(s.get("shots") or [])
                    for s in (merged.get("scenes") or [])
                    if isinstance(s, dict)
                ),
                "characters": len(merged.get("characters") or []),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(
        "scene_grammar batch: node={} done scenes={} shots={} chars={}",
        node_key,
        len(merged.get("scenes") or []),
        sum(
            len(s.get("shots") or [])
            for s in (merged.get("scenes") or [])
            if isinstance(s, dict)
        ),
        len(merged.get("characters") or []),
    )
    return OperatorApiResult(
        reply_text=reply_text,
        output_paths=[reply_path, meta_path],
        apply_ops=merged,
    )
