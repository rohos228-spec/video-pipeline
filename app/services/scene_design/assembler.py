"""Валидация финального payload сборщика против закадра и кадров."""

from __future__ import annotations

import re
from typing import Any

from app.models import Frame, Project
from app.services.scene_design.context_builder import frame_seconds


class SceneDesignValidationError(RuntimeError):
    """Собранный scene_registry не прошёл проверку покрытия/границ."""


def _norm_words(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def force_scenes_from_chrono(
    payload: dict[str, Any],
    assembly_input: dict[str, Any],
) -> dict[str, Any]:
    """Границы сцен — только из camera_expand (scenes_chrono), не из GPT.

    GPT часто ломает start_words/end_words при чанках. Машинная хронология
    уже склеена по лестнице/SET и цитаты проверены на уникальность.
    ops сохраняем; id_scene у каждого op переписываем по uuid кадра.
    """
    chrono = [
        sc
        for sc in (assembly_input.get("scenes_chrono") or [])
        if isinstance(sc, dict) and str(sc.get("id_scene") or "").strip()
    ]
    if not chrono:
        return payload

    uuid_to_scene: dict[str, str] = {}
    scenes_out: list[dict[str, Any]] = []
    for sc in chrono:
        sid = str(sc.get("id_scene") or "").strip()
        scenes_out.append(
            {
                "id_scene": sid,
                "start_words": sc.get("start_words") or "",
                "end_words": sc.get("end_words") or "",
                "время_сек": sc.get("время_сек") or 0,
                "структура_сцены": sc.get("структура_сцены") or "continuity",
                "тип_стыка": sc.get("тип_стыка") or "action",
                "переход_в_сцену": sc.get("переход_в_сцену") or "cut",
                "смысл_сцены": sc.get("смысл_сцены") or sc.get("мотив") or "",
                "набор": sc.get("набор"),
                "camera_ladder": sc.get("camera_ladder") or [],
                "camera_shots_required": sc.get("camera_shots_required"),
            }
        )
        for kid in sc.get("кадры") or []:
            if not isinstance(kid, dict):
                continue
            uid = str(kid.get("uuid") or "").strip()
            if uid:
                uuid_to_scene[uid] = sid

    out = dict(payload)
    out["scenes"] = scenes_out
    ops_out: list[dict[str, Any]] = []
    for op in out.get("ops") or []:
        if not isinstance(op, dict):
            continue
        op2 = dict(op)
        fields = (
            dict(op2.get("fields") or {})
            if isinstance(op2.get("fields"), dict)
            else {}
        )
        uid = str(op2.get("frame_uuid") or "").strip()
        if uid in uuid_to_scene:
            fields["id_scene"] = uuid_to_scene[uid]
        op2["fields"] = fields
        ops_out.append(op2)
    out["ops"] = ops_out
    report = str(out.get("report") or "")
    stamp = (
        f"camera_scenes:{len(scenes_out)}; "
        f"multi_frame:{sum(1 for s in chrono if int(s.get('кадров') or 0) >= 2)}"
    )
    out["report"] = f"{report}; {stamp}" if report else stamp
    return out


def validate_payload(
    project: Project,
    frames: list[Frame],
    payload: dict[str, Any],
    full_vo: str,
) -> list[str]:
    """Список проблем; пустой = сборка принята.

    Проверки: границы сцен — дословные цитаты из полного закадра; каждый
    uuid кадра покрыт ровно одним op; ops.id_scene существует в scenes[];
    у каждой сцены есть хронометраж ``время_сек``, согласованный с суммой
    времён её кадров.
    """
    _ = project
    problems: list[str] = []
    vo_norm = _norm_words(full_vo)
    scenes = payload.get("scenes") or []
    ops = payload.get("ops") or []

    scene_ids: set[str] = set()
    seen_quotes: dict[str, str] = {}
    declared_time: dict[str, float] = {}
    for sc in scenes:
        if not isinstance(sc, dict):
            problems.append("scenes: элемент — не объект")
            continue
        sid = str(sc.get("id_scene") or "").strip()
        if not sid:
            problems.append("scenes: сцена без id_scene")
            continue
        scene_ids.add(sid)
        time_raw = sc.get("время_сек")
        try:
            declared = float(time_raw)
        except (TypeError, ValueError):
            declared = 0.0
        if declared <= 0:
            problems.append(
                f"{sid}: нет время_сек — задай хронометраж сцены "
                "(сумма время_сек её кадров из входа)"
            )
        else:
            declared_time[sid] = declared
        for key in ("start_words", "end_words"):
            quote = _norm_words(str(sc.get(key) or ""))
            if not quote:
                problems.append(f"{sid}: пустые {key}")
                continue
            if quote not in vo_norm:
                problems.append(f"{sid}: {key} не найдены в закадре: {quote[:60]!r}")
            owner = seen_quotes.get(quote)
            if owner is not None and owner != sid:
                problems.append(f"{sid}: {key} дублируют цитату сцены {owner}")
            else:
                seen_quotes[quote] = sid

    frame_uuids = {fr.uuid for fr in frames if fr.uuid}
    seen_ops: set[str] = set()
    for op in ops:
        if not isinstance(op, dict):
            problems.append("ops: элемент — не объект")
            continue
        uuid = str(op.get("frame_uuid") or "").strip()
        if uuid not in frame_uuids:
            problems.append(f"ops: неизвестный frame_uuid {uuid!r}")
            continue
        if uuid in seen_ops:
            problems.append(f"ops: дубль frame_uuid {uuid!r}")
        seen_ops.add(uuid)
        fields = op.get("fields") or {}
        if not isinstance(fields, dict) or not fields:
            problems.append(f"ops {uuid}: пустые fields")
            continue
        sid = str(
            fields.get("id_scene") or fields.get("shot01_id_scene") or ""
        ).strip()
        if sid and sid not in scene_ids:
            problems.append(f"ops {uuid}: id_scene {sid!r} отсутствует в scenes[]")

    missing = frame_uuids - seen_ops
    if missing:
        problems.append(f"ops: кадры без привязки: {len(missing)} шт")

    # После camera_subdivide сцена с multi-shot SET не должна остаться с 1 кадром.
    frames_per_scene: dict[str, int] = {}
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        sid = str(
            fields.get("id_scene") or fields.get("shot01_id_scene") or ""
        ).strip()
        if not sid:
            continue
        frames_per_scene[sid] = frames_per_scene.get(sid, 0) + 1
    if len(scenes) > 1:
        # Сцен должно быть много (≈ VO-диапазонов), не 20 из склейки.
        if len(scenes) < max(3, int(0.5 * len(frame_uuids))):
            # После дроби frames >> scenes; сравниваем с числом сцен из payload.
            # Если сцен мало относительно ops — скорее всего опять склеили VO.
            if len(scenes) * 3 < len(ops):
                problems.append(
                    f"слишком мало сцен ({len(scenes)} на {len(ops)} кадров): "
                    f"один VO-диапазон = сцена (или две), кадры SET — внутри неё; "
                    f"не склеивай соседние VO в одну сцену"
                )
        singles = [sid for sid, n in frames_per_scene.items() if n < 2]
        if singles and len(singles) > max(1, int(0.35 * len(frames_per_scene))):
            problems.append(
                f"слишком много сцен с 1 кадром ({len(singles)}/{len(frames_per_scene)}): "
                f"camera SET/лестница требует дробить VO-диапазон на кадры "
                f"(примеры: {', '.join(singles[:8])})"
            )

    # Сверка хронометража: declared сцены vs сумма времён привязанных кадров.
    frame_by_uuid = {fr.uuid: fr for fr in frames if fr.uuid}
    expected_time: dict[str, float] = {}
    for op in ops:
        if not isinstance(op, dict):
            continue
        uuid = str(op.get("frame_uuid") or "").strip()
        fr = frame_by_uuid.get(uuid)
        if fr is None:
            continue
        fields = op.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        sid = str(
            fields.get("id_scene") or fields.get("shot01_id_scene") or ""
        ).strip()
        if not sid:
            continue
        sec, _source = frame_seconds(fr)
        expected_time[sid] = expected_time.get(sid, 0.0) + sec
    for sid, expected in expected_time.items():
        declared = declared_time.get(sid)
        if declared is None:
            continue  # про отсутствие время_сек уже reported выше
        if expected <= 0:
            continue  # у кадров нет хронометража — сверять не с чем
        tolerance = max(1.5, 0.35 * expected)
        if abs(declared - expected) > tolerance:
            problems.append(
                f"{sid}: время_сек={declared} не бьётся с суммой кадров "
                f"{round(expected, 1)} сек — возьми сумму время_сек кадров сцены"
            )
    return problems
