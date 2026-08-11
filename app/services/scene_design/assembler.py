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


def _fold_ru(text: str) -> str:
    """casefold + снять combining accents (Алекса́ндр → александр)."""
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", text or "")
    bare = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return _norm_words(bare)


def _as_plain_text(value: Any) -> str:
    """Никогда не отдавать dict/list в Excel/UI как ``[object Object]``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_as_plain_text(x) for x in value]
        return " → ".join(p for p in parts if p)
    if isinstance(value, dict):
        # Фаза chrono_dyn / баг камеры: объект вместо строки.
        for key in (
            "action",
            "действие",
            "текст",
            "text",
            "описание",
            "value",
            "label",
        ):
            if key in value:
                return _as_plain_text(value.get(key))
        import json

        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _parse_action_chain(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [_as_plain_text(x) for x in raw if _as_plain_text(x)]
    text = _as_plain_text(raw)
    if not text:
        return []
    if text.startswith("["):
        import json

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [_as_plain_text(x) for x in data if _as_plain_text(x)]
        except Exception:  # noqa: BLE001
            pass
    for sep in ("→", "->", "⇒", ";"):
        if sep in text:
            return [p.strip() for p in text.split(sep) if p.strip()]
    return [text]


def attach_action_chains_to_scenes(
    scenes_chrono: list[dict[str, Any]],
    action_scenes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Перенести цепь_действия из action в сцены camera_expand по пересечению цитат."""
    if not scenes_chrono or not action_scenes:
        return scenes_chrono
    vo_norm = ""  # сравнение по нормализованным цитатам без полного VO
    action_rows: list[tuple[str, str, list[str], dict[str, Any]]] = []
    for ac in action_scenes:
        if not isinstance(ac, dict):
            continue
        sw = _norm_words(str(ac.get("start_words") or ""))
        ew = _norm_words(str(ac.get("end_words") or ""))
        chain = _parse_action_chain(ac.get("цепь_действия"))
        if sw or chain:
            action_rows.append((sw, ew, chain, ac))

    out: list[dict[str, Any]] = []
    for sc in scenes_chrono:
        row = dict(sc)
        sw = _norm_words(str(row.get("start_words") or ""))
        best: dict[str, Any] | None = None
        best_score = -1
        for a_sw, a_ew, chain, ac in action_rows:
            score = 0
            if a_sw and sw and (a_sw in sw or sw in a_sw):
                score += 2
            if a_ew and _norm_words(str(row.get("end_words") or "")):
                ew = _norm_words(str(row.get("end_words") or ""))
                if a_ew in ew or ew in a_ew:
                    score += 1
            if score > best_score:
                best_score = score
                best = ac
                row["цепь_действия"] = chain or _parse_action_chain(
                    ac.get("цепь_действия")
                )
        if best is not None and best_score >= 0:
            if not row.get("смысл_сцены"):
                row["смысл_сцены"] = best.get("смысл_сцены") or ""
            if not row.get("структура_сцены"):
                row["структура_сцены"] = best.get("структура_сцены") or "continuity"
            # V3 narrative glue from action — даже если camera_expand обнулил.
            for key in (
                "связь_с_прошлой",
                "крючок_в_следующую",
                "переход_в_сцену",
                "тип_стыка",
                "мотив",
            ):
                if best.get(key) and not row.get(key):
                    row[key] = best.get(key)
            if best_score >= 2 and best.get("id_scene"):
                row["id_scene"] = best.get("id_scene")
        out.append(row)
    _ = vo_norm
    return out


def stamp_characters_onto_ops(
    payload: dict[str, Any],
    assembly_input: dict[str, Any],
    frames: list[Frame] | None = None,
) -> dict[str, Any]:
    """Прописать коды персонажей (c01…) в ops, если GPT оставил пусто.

    Источники: ``персонажи``/``персонажи_сцены`` из ответа GPT; иначе — совпадение
    имён из реестра characters с закадром кадра.
    """
    char_rows = [
        c
        for c in (payload.get("characters") or assembly_input.get("characters") or [])
        if isinstance(c, dict)
    ]
    name_to_id: dict[str, str] = {}
    for c in char_rows:
        cid = str(c.get("id") or "").strip()
        if not re.fullmatch(r"c\d{2}", cid):
            continue
        name = str(c.get("имя") or c.get("name") or "").strip()
        if not name:
            continue
        name_to_id[_fold_ru(name)] = cid
        for part in re.split(r"[\s,]+", name):
            p = _fold_ru(part)
            if len(p) >= 4:
                name_to_id.setdefault(p, cid)

    scene_persons: dict[str, str] = {}
    for sc in payload.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id_scene") or "").strip()
        raw = sc.get("персонажи_сцены") or sc.get("персонажи") or sc.get("characters")
        if sid and raw is not None and str(raw).strip():
            scene_persons[sid] = _as_plain_text(raw)

    # Сохранить персонажи_сцены из сырого GPT до force_scenes (если ещё в payload).
    for sc in assembly_input.get("scenes_chrono") or []:
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id_scene") or "").strip()
        if sid and sid not in scene_persons:
            raw = sc.get("персонажи_сцены") or sc.get("персонажи")
            if raw is not None and str(raw).strip():
                scene_persons[sid] = _as_plain_text(raw)

    uuid_vo: dict[str, str] = {}
    for fr in frames or []:
        if getattr(fr, "uuid", None):
            uuid_vo[str(fr.uuid)] = _fold_ru(fr.voiceover_text or "")

    out = dict(payload)
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
        current = _as_plain_text(
            fields.get("персонажи")
            or fields.get("characters")
            or fields.get("persons")
            or ""
        )
        if current:
            fields["персонажи"] = current
            fields["characters"] = current
            op2["fields"] = fields
            ops_out.append(op2)
            continue

        sid = str(fields.get("id_scene") or "").strip()
        picked = scene_persons.get(sid, "").strip()
        if not picked:
            vo = uuid_vo.get(str(op2.get("frame_uuid") or "").strip(), "")
            ids: list[str] = []
            for name, cid in name_to_id.items():
                if name and name in vo and cid not in ids:
                    ids.append(cid)
            # стабильный порядок c01, c02…
            ids.sort()
            picked = ", ".join(ids)
        if picked:
            fields["персонажи"] = picked
            fields["characters"] = picked
        op2["fields"] = fields
        ops_out.append(op2)
    out["ops"] = ops_out
    return out


def stamp_actions_onto_ops(
    payload: dict[str, Any],
    assembly_input: dict[str, Any],
) -> dict[str, Any]:
    """Прописать уникальные фазы цепь_действия в ``действие`` каждого op.

    Запрет: GPT-«камера вводит…» и копипаст одного действия на все кадры сцены.
    """
    chrono = [
        sc
        for sc in (assembly_input.get("scenes_chrono") or [])
        if isinstance(sc, dict)
    ]
    by_scene: dict[str, dict[str, Any]] = {
        str(sc.get("id_scene") or "").strip(): sc for sc in chrono if sc.get("id_scene")
    }
    # uuid → (id_scene, index_in_scene)
    uuid_pos: dict[str, tuple[str, int]] = {}
    for sc in chrono:
        sid = str(sc.get("id_scene") or "").strip()
        kids = [k for k in (sc.get("кадры") or []) if isinstance(k, dict)]
        for i, kid in enumerate(kids):
            uid = str(kid.get("uuid") or "").strip()
            if uid:
                uuid_pos[uid] = (sid, i)

    out = dict(payload)
    ops_out: list[dict[str, Any]] = []
    seen_actions: set[str] = set()
    for op in out.get("ops") or []:
        if not isinstance(op, dict):
            continue
        op2 = dict(op)
        fields = (
            dict(op2.get("fields") or {})
            if isinstance(op2.get("fields"), dict)
            else {}
        )
        # Почистить [object Object] / вложенные объекты во всех строковых полях.
        for k, v in list(fields.items()):
            if isinstance(v, (dict, list)) or (
                isinstance(v, str) and "[object Object]" in v
            ):
                fields[k] = _as_plain_text(v) if "[object Object]" not in str(v) else ""

        uid = str(op2.get("frame_uuid") or "").strip()
        pos = uuid_pos.get(uid)
        if pos:
            sid, idx = pos
            sc = by_scene.get(sid) or {}
            chain = _parse_action_chain(sc.get("цепь_действия"))
            action = ""
            if chain:
                action = chain[idx] if idx < len(chain) else chain[-1]
            # Анти-повтор: если фаза уже была — дописать отличие по индексу шота.
            key = _norm_words(action)
            if action and key in seen_actions and len(chain) > 1:
                # взять следующую неиспользованную фазу
                for cand in chain:
                    ck = _norm_words(cand)
                    if ck not in seen_actions:
                        action = cand
                        key = ck
                        break
            if action:
                seen_actions.add(key)
                fields["действие"] = action
                fields["shot01_action"] = action
            sense = _as_plain_text(sc.get("смысл_сцены") or "")
            if sense:
                fields["смысл_сцены"] = sense
        op2["fields"] = fields
        ops_out.append(op2)
    out["ops"] = ops_out
    return out


def force_scenes_from_chrono(
    payload: dict[str, Any],
    assembly_input: dict[str, Any],
    frames: list[Frame] | None = None,
) -> dict[str, Any]:
    """Границы сцен — только из camera_expand (scenes_chrono), не из GPT.

    GPT часто ломает start_words/end_words при чанках. Машинная хронология
    уже склеена по лестнице/SET и цитаты проверены на уникальность.
    ops сохраняем; id_scene у каждого op переписываем по uuid кадра.
    """
    # Персонажи сцен из сырого GPT — force перезапишет scenes[].
    gpt_scene_persons: dict[str, str] = {}
    for sc in payload.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id_scene") or "").strip()
        raw = sc.get("персонажи_сцены") or sc.get("персонажи") or sc.get("characters")
        if sid and raw is not None and str(raw).strip():
            gpt_scene_persons[sid] = _as_plain_text(raw)

    chrono = [
        sc
        for sc in (assembly_input.get("scenes_chrono") or [])
        if isinstance(sc, dict) and str(sc.get("id_scene") or "").strip()
    ]
    if not chrono:
        return stamp_characters_onto_ops(payload, assembly_input, frames)

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
                "цепь_действия": _parse_action_chain(sc.get("цепь_действия")),
                "набор": sc.get("набор"),
                "camera_ladder": sc.get("camera_ladder") or [],
                "camera_shots_required": sc.get("camera_shots_required"),
                "персонажи_сцены": gpt_scene_persons.get(sid, ""),
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
        # Почистить object-поля до stamp_actions.
        for k, v in list(fields.items()):
            if isinstance(v, (dict, list)):
                fields[k] = _as_plain_text(v)
            elif isinstance(v, str) and "[object Object]" in v:
                fields[k] = ""
        op2["fields"] = fields
        ops_out.append(op2)
    out["ops"] = ops_out
    out = stamp_actions_onto_ops(out, {**assembly_input, "scenes_chrono": chrono})
    out = stamp_characters_onto_ops(out, assembly_input, frames)
    report = str(out.get("report") or "")
    stamp = (
        f"camera_scenes:{len(scenes_out)}; "
        f"multi_frame:{sum(1 for s in chrono if int(s.get('кадров') or 0) >= 2)}; "
        f"actions_stamped:1; chars_stamped:1"
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
    # fold: Алекса́ндр / О́льга vs без ударений в VO.
    vo_norm = _fold_ru(full_vo)
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
            raw_quote = str(sc.get(key) or "")
            quote = _fold_ru(raw_quote)
            if not quote:
                problems.append(f"{sid}: пустые {key}")
                continue
            if quote not in vo_norm:
                problems.append(
                    f"{sid}: {key} не найдены в закадре: {_norm_words(raw_quote)[:60]!r}"
                )
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
