"""Волна 0 scene_design: разметка VO-ячеек → проверки кода → редактор → staging.

Промпты: ``sd_skeleton.md`` (черновик), ``sd_skeleton_editor.md`` (редактор).
Контракт V3: ``cells`` (биты + предметы на ячейку), не нарезка сцен.
Внутри пайплайна ``normalize_skeleton_draft`` держит мост ``cells`` → ``scenes``
(id_scene/биты) для action/camera/ячеек staging.
Чекпоинт: ``scene_design/skeleton.json`` — soft retry не жжёт GPT повторно.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.scene_design import agents as ag
from app.services.scene_design import cells as sd_cells
from app.services.scene_design import context_builder, runner
from app.settings import settings

EDITOR_MAX_ROUNDS = 2
_STOP_TOKENS = frozenset(
    {
        "и",
        "в",
        "во",
        "на",
        "с",
        "со",
        "к",
        "ко",
        "у",
        "о",
        "об",
        "от",
        "по",
        "из",
        "за",
        "для",
        "это",
        "тот",
        "та",
        "те",
        "a",
        "the",
        "of",
        "to",
        "in",
        "on",
        "at",
        "an",
    }
)
_TIME_MARKERS = (
    "спустя",
    "через",
    "на рассвете",
    "на закате",
    "утром",
    "вечером",
    "ночью",
    "днём",
    "днем",
    "три ночи",
    "две ночи",
    "на следующий",
    "на следующую",
    "на следующий день",
    "на следующее утро",
    "годом позже",
    "годами позже",
    "месяц спустя",
    "неделю спустя",
    "час спустя",
    "минуту спустя",
    "позже",
    "рано утром",
    "поздно вечером",
    "назавтра",
    "назавтра",
    "наутро",
    "через день",
    "через год",
    "через час",
    "через неделю",
    "через месяц",
    "тогда",
    "вспомнил",
    "вспомнила",
    "флешбек",
)


def _cell_frame_number(cell: dict[str, Any]) -> int | None:
    raw = cell.get("кадр")
    if raw is None:
        raw = cell.get("number")
    if raw is None:
        frames = cell.get("кадры")
        if isinstance(frames, list) and frames:
            raw = frames[0]
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def normalize_skeleton_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """V3 ``cells`` → внутренний мост ``scenes`` (1 VO = 1 карточка).

    Legacy-ответы с ``scenes`` оставляем; при наличии ``cells`` пересобираем
    ``scenes`` / ``биты`` / ``суть`` для action и staging.
    """
    if not isinstance(draft, dict):
        return draft
    cells = [c for c in (draft.get("cells") or []) if isinstance(c, dict)]
    if cells:
        prev_scenes = {
            _cell_frame_number(s): s
            for s in (draft.get("scenes") or [])
            if isinstance(s, dict) and _cell_frame_number(s) is not None
        }
        scenes: list[dict[str, Any]] = []
        for i, cell in enumerate(cells):
            num = _cell_frame_number(cell)
            if num is None:
                num = i + 1
            sid = f"scene_{num:02d}"
            prev = prev_scenes.get(num) or {}
            parts = [
                p
                for p in (
                    cell.get("смысловые_части")
                    or cell.get("биты")
                    or []
                )
                if isinstance(p, dict)
            ]
            bits: list[dict[str, Any]] = []
            main_part: dict[str, Any] | None = None
            for bi, part in enumerate(parts):
                try:
                    order = int(part.get("порядок") or bi + 1)
                except (TypeError, ValueError):
                    order = bi + 1
                bit = {
                    "порядок": order,
                    "глагол": str(
                        part.get("глагол_или_суть")
                        or part.get("глагол")
                        or part.get("суть")
                        or ""
                    ).strip(),
                    "изменение": str(part.get("изменение") or "").strip(),
                    "якорь": str(part.get("якорь") or "").strip(),
                    "тип": str(part.get("тип") or "").strip(),
                }
                if part.get("нить") not in (None, ""):
                    bit["нить"] = str(part.get("нить")).strip()
                bits.append(bit)
                if part.get("главный") is True or part.get("главный") == "true":
                    main_part = part
            if main_part is None and parts:
                main_part = parts[-1]
            glav = cell.get("главное") if isinstance(cell.get("главное"), dict) else {}
            glav = dict(glav)
            if main_part and not str(glav.get("якорь_в_кадрах") or "").strip():
                glav = {
                    **glav,
                    "тип": str(
                        glav.get("тип") or main_part.get("тип") or "действие"
                    ).strip()
                    or "действие",
                    "глагол": str(
                        glav.get("глагол")
                        or main_part.get("глагол_или_суть")
                        or main_part.get("глагол")
                        or ""
                    ).strip(),
                    "изменение": str(
                        glav.get("изменение") or main_part.get("изменение") or ""
                    ).strip(),
                    "якорь_в_кадрах": str(main_part.get("якорь") or "").strip(),
                    "нить": str(
                        glav.get("нить") or main_part.get("нить") or ""
                    ).strip(),
                }
            link = cell.get("связь_с_прошлой")
            if not isinstance(link, dict):
                link = {
                    "тип": "начало" if i == 0 else "продолжение",
                    "что_связывает": "",
                }
            scene: dict[str, Any] = {
                "id_scene": sid,
                "id_cell": str(cell.get("id_cell") or f"cell_{i+1:02d}").strip(),
                "кадры": [num],
                "связь_с_прошлой": link,
                "суть": str(cell.get("фокус") or cell.get("суть") or "").strip(),
                "фокус": str(cell.get("фокус") or cell.get("суть") or "").strip(),
                "главное": glav,
                "биты": bits,
                "смысловые_части": parts,
                "предметы": [
                    p for p in (cell.get("предметы") or []) if isinstance(p, dict)
                ],
                "персонажи": [
                    p for p in (cell.get("персонажи") or []) if isinstance(p, dict)
                ],
                "время": str(cell.get("время") or "").strip(),
                "длительность_сек": cell.get("длительность_сек"),
            }
            mid = str(cell.get("место_id") or "").strip()
            if mid:
                scene["место_id"] = mid
            bg = str(cell.get("фон_разовый") or "").strip()
            if bg:
                scene["фон_разовый"] = bg
            niti = cell.get("нити")
            if niti is None:
                niti = prev.get("нити")
            if niti:
                scene["нити"] = niti
            scenes.append(scene)
        draft["scenes"] = scenes
    elif isinstance(draft.get("scenes"), list):
        # Legacy → синтетические cells для редактора V3 / отчётов.
        synth: list[dict[str, Any]] = []
        for i, sc in enumerate(draft.get("scenes") or []):
            if not isinstance(sc, dict):
                continue
            num = _cell_frame_number(sc)
            if num is None:
                continue
            parts = sc.get("смысловые_части")
            if not isinstance(parts, list) or not parts:
                parts = []
                for bit in sc.get("биты") or []:
                    if not isinstance(bit, dict):
                        continue
                    parts.append(
                        {
                            "порядок": bit.get("порядок"),
                            "тип": bit.get("тип") or "действие",
                            "глагол_или_суть": bit.get("глагол") or "",
                            "изменение": bit.get("изменение") or "",
                            "якорь": bit.get("якорь") or "",
                            "нить": bit.get("нить") or "",
                            "главный": False,
                        }
                    )
                if parts:
                    parts[-1]["главный"] = True
            glav = sc.get("главное") if isinstance(sc.get("главное"), dict) else {}
            if not parts and glav:
                ank = str(glav.get("якорь_в_кадрах") or "").strip()
                if ank or glav.get("глагол") or glav.get("что"):
                    parts = [
                        {
                            "порядок": 1,
                            "тип": glav.get("тип") or "действие",
                            "глагол_или_суть": str(
                                glav.get("глагол") or glav.get("что") or ""
                            ).strip(),
                            "изменение": str(glav.get("изменение") or "").strip(),
                            "якорь": ank,
                            "нить": str(glav.get("нить") or "").strip(),
                            "главный": True,
                        }
                    ]
            cell_out: dict[str, Any] = {
                "id_cell": str(sc.get("id_cell") or f"cell_{i+1:02d}"),
                "кадр": num,
                "связь_с_прошлой": sc.get("связь_с_прошлой")
                or {"тип": "начало" if i == 0 else "продолжение", "что_связывает": ""},
                "фокус": str(sc.get("фокус") or sc.get("суть") or "").strip(),
                "смысловые_части": parts,
                "предметы": sc.get("предметы") or [],
                "персонажи": sc.get("персонажи") or [],
                "время": sc.get("время") or "",
                "длительность_сек": sc.get("длительность_сек"),
            }
            if glav:
                cell_out["главное"] = glav
            if sc.get("место_id"):
                cell_out["место_id"] = sc.get("место_id")
            if sc.get("фон_разовый"):
                cell_out["фон_разовый"] = sc.get("фон_разовый")
            synth.append(cell_out)
        if synth and not draft.get("cells"):
            draft["cells"] = synth
    if not isinstance(draft.get("items_seed"), list):
        draft["items_seed"] = []
    if not isinstance(draft.get("characters_seed"), list):
        draft["characters_seed"] = []
    if not isinstance(draft.get("locations_seed"), list):
        draft["locations_seed"] = []
    return draft


def skeleton_pipeline_enabled(project: Project | None) -> bool:
    """Новый поток draft→editor: settings + meta/canvas (uses_skeleton)."""
    if project is None or not ag.uses_skeleton(project):
        return False
    meta = getattr(project, "meta", None)
    if isinstance(meta, dict):
        raw = meta.get("scene_design_skeleton")
        if raw is False:
            return False
        if isinstance(raw, str) and raw.strip().lower() in ("0", "false", "off", "no"):
            return False
    return bool(getattr(settings, "scene_design_skeleton_enabled", True))


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


def _norm_cf(text: str) -> str:
    return _norm_ws(text).casefold()


def _significant_tokens(text: str) -> set[str]:
    toks = re.findall(r"[a-zа-яё0-9]{3,}", _norm_cf(text), flags=re.IGNORECASE)
    return {t for t in toks if t not in _STOP_TOKENS}


def _vo_by_frame(frames: list[Frame]) -> dict[int, str]:
    out: dict[int, str] = {}
    for fr in frames:
        try:
            n = int(fr.number)
        except (TypeError, ValueError):
            continue
        out[n] = (fr.voiceover_text or "").strip()
    return out


def _sec_by_frame(frames: list[Frame]) -> dict[int, float]:
    out: dict[int, float] = {}
    for fr in frames:
        try:
            n = int(fr.number)
        except (TypeError, ValueError):
            continue
        sec, _ = context_builder.frame_seconds(fr)
        out[n] = float(sec)
    return out


def _scene_vo(scene: dict[str, Any], vo_map: dict[int, str]) -> str:
    parts: list[str] = []
    for raw in scene.get("кадры") or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        t = vo_map.get(n) or ""
        if t:
            parts.append(t)
    return "\n".join(parts)


def _anchor_in_text(anchor: str, haystack: str) -> bool:
    a = _norm_cf(anchor)
    if not a or len(a) < 2:
        return False
    return a in _norm_cf(haystack)


def _gap(
    address: str, problem: str, how: str
) -> dict[str, str]:
    return {"адрес": address, "проблема": problem, "как_исправить": how}


def _scene_frames(scene: dict[str, Any]) -> list[int]:
    raw = scene.get("кадры")
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _link_type(scene: dict[str, Any]) -> str:
    link = scene.get("связь_с_прошлой")
    if isinstance(link, dict):
        return str(link.get("тип") or "").strip().casefold()
    return str(link or "").strip().casefold()


def _has_time_marker(text: str) -> bool:
    low = _norm_cf(text)
    return any(m in low for m in _TIME_MARKERS)


def validate_skeleton(
    draft: dict[str, Any],
    frames: list[Frame],
    full_vo: str,
) -> list[dict[str, str]]:
    """Проверки покрытия/якорей/реестров → разрывы {адрес, проблема, как_исправить}."""
    normalize_skeleton_draft(draft)
    gaps: list[dict[str, str]] = []
    scenes = [s for s in (draft.get("scenes") or []) if isinstance(s, dict)]
    chars = [c for c in (draft.get("characters_seed") or []) if isinstance(c, dict)]
    locs = [loc for loc in (draft.get("locations_seed") or []) if isinstance(loc, dict)]
    items = [it for it in (draft.get("items_seed") or []) if isinstance(it, dict)]
    expect = sorted({int(fr.number) for fr in frames if fr.number is not None})
    vo_map = _vo_by_frame(frames)
    sec_map = _sec_by_frame(frames)
    rate = max(float(getattr(settings, "scene_design_vo_chars_per_sec", 14.0) or 14.0), 1.0)
    item_ids = {
        str(it.get("id") or "").strip()
        for it in items
        if str(it.get("id") or "").strip()
    }

    # 1. Покрытие
    covered: dict[int, str] = {}
    prev_max = 0
    for i, sc in enumerate(scenes):
        sid = str(sc.get("id_scene") or f"scenes[{i}]").strip() or f"scenes[{i}]"
        nums = _scene_frames(sc)
        if not nums:
            gaps.append(_gap(sid, "пустые кадры", "укажи соседние номера кадров сцены"))
            continue
        if len(nums) != 1:
            gaps.append(
                _gap(
                    sid,
                    f"кадры {nums}: склейка VO-ячеек запрещена",
                    "1 ячейка закадра = 1 сцена, кадры:[N] ровно один номер; "
                    "разбей на отдельные scene_XX",
                )
            )
        if nums != sorted(nums):
            gaps.append(_gap(sid, f"кадры не по порядку: {nums}", "отсортируй кадры по возрастанию"))
        for a, b in zip(nums, nums[1:], strict=False):
            if b != a + 1:
                gaps.append(
                    _gap(
                        sid,
                        f"кадры не соседние: {nums}",
                        "оставь только непрерывный диапазон соседних номеров",
                    )
                )
                break
        if prev_max and nums and nums[0] != prev_max + 1:
            gaps.append(
                _gap(
                    sid,
                    f"сцена начинается с {nums[0]}, ожидался {prev_max + 1}",
                    "сцены должны идти подряд по кадрам без дыр и перестановок",
                )
            )
        if nums:
            prev_max = nums[-1]
        for n in nums:
            if n in covered:
                gaps.append(
                    _gap(
                        sid,
                        f"кадр {n} уже в {covered[n]}",
                        f"убери кадр {n} из одной из сцен — каждый кадр ровно в одной",
                    )
                )
            else:
                covered[n] = sid
    missing = [n for n in expect if n not in covered]
    if missing:
        gaps.append(
            _gap(
                "coverage",
                f"не покрыты кадры: {missing}",
                "добавь пропущенные кадры в соседние сцены, сохранив порядок",
            )
        )
    extra = sorted(n for n in covered if n not in expect)
    if extra:
        gaps.append(
            _gap(
                "coverage",
                f"лишние кадры вне входа: {extra}",
                "убери номера, которых нет во входных КАДРАХ",
            )
        )

    char_ids = {str(c.get("id") or "").strip() for c in chars if c.get("id")}
    loc_ids = {str(loc.get("id") or "").strip() for loc in locs if loc.get("id")}

    # 10. Реестр-ссылки (+ собираем для 5)
    for i, sc in enumerate(scenes):
        sid = str(sc.get("id_scene") or f"scenes[{i}]").strip()
        mid = str(sc.get("место_id") or "").strip()
        if mid and mid not in loc_ids:
            gaps.append(
                _gap(sid, f"место_id {mid!r} нет в locations_seed", f"заведи {mid} в locations_seed или смени id")
            )
        for p in sc.get("персонажи") or []:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or "").strip()
            if pid and pid not in char_ids:
                gaps.append(
                    _gap(
                        sid,
                        f"персонаж {pid!r} нет в characters_seed",
                        f"добавь {pid} в characters_seed или убери ссылку",
                    )
                )

    # 2. Якоря дословно
    for i, sc in enumerate(scenes):
        sid = str(sc.get("id_scene") or f"scenes[{i}]").strip()
        svo = _scene_vo(sc, vo_map)
        glav = sc.get("главное")
        if isinstance(glav, dict):
            ank = str(glav.get("якорь_в_кадрах") or "").strip()
            if ank and not _anchor_in_text(ank, svo):
                gaps.append(
                    _gap(
                        f"{sid}.главное",
                        f"якорь не из закадра сцены: {ank[:80]!r}",
                        "замени якорь_в_кадрах на дословную фразу из закадра кадров сцены",
                    )
                )
        for bi, bit in enumerate(sc.get("биты") or []):
            if not isinstance(bit, dict):
                continue
            ank = str(bit.get("якорь") or "").strip()
            if ank and not _anchor_in_text(ank, svo):
                gaps.append(
                    _gap(
                        f"{sid}.биты[{bi}]",
                        f"якорь бита не из закадра: {ank[:80]!r}",
                        "якорь бита — дословный кусок закадра этой сцены",
                    )
                )
    for c in chars:
        cid = str(c.get("id") or "?").strip()
        ank = str(c.get("якорь") or "").strip()
        if ank and not _anchor_in_text(ank, full_vo):
            gaps.append(
                _gap(
                    f"characters_seed.{cid}",
                    f"якорь не найден в полном закадре: {ank[:80]!r}",
                    "укажи дословную фразу из ПОЛНОГО ЗАКАДРА",
                )
            )
    for loc in locs:
        lid = str(loc.get("id") or "?").strip()
        ank = str(loc.get("якорь") or "").strip()
        if ank and not _anchor_in_text(ank, full_vo):
            gaps.append(
                _gap(
                    f"locations_seed.{lid}",
                    f"якорь не найден в полном закадре: {ank[:80]!r}",
                    "укажи дословную фразу из ПОЛНОГО ЗАКАДРА",
                )
            )

    # 3–4. Двойники локаций / персонажей
    def _dup_gaps(
        items: list[dict[str, Any]], *, kind: str, name_keys: tuple[str, ...]
    ) -> None:
        for i, a in enumerate(items):
            aid = str(a.get("id") or f"{kind}[{i}]").strip()
            a_tok = _significant_tokens(
                " ".join(str(a.get(k) or "") for k in ("якорь",) + name_keys)
            )
            if not a_tok:
                continue
            for b in items[i + 1 :]:
                bid = str(b.get("id") or "").strip()
                if not bid or bid == aid:
                    continue
                b_tok = _significant_tokens(
                    " ".join(str(b.get(k) or "") for k in ("якорь",) + name_keys)
                )
                inter = a_tok & b_tok
                if len(inter) >= 1 and (
                    len(inter) >= 2 or (a_tok <= b_tok or b_tok <= a_tok)
                ):
                    gaps.append(
                        _gap(
                            f"{kind}.{bid}",
                            f"похоже на {aid} (токены: {sorted(inter)[:6]})",
                            f"слить {bid} в {aid}: удали двойника и перенаправь ссылки",
                        )
                    )

    _dup_gaps(locs, kind="locations_seed", name_keys=("name", "имя", "название"))
    _dup_gaps(chars, kind="characters_seed", name_keys=("имя", "name", "роль"))
    _dup_gaps(items, kind="items_seed", name_keys=("имя", "name", "название"))

    # Предметы карточки → items_seed; якоря items_seed
    for i, sc in enumerate(scenes):
        sid = str(sc.get("id_scene") or f"scenes[{i}]").strip()
        for pi, prop in enumerate(sc.get("предметы") or []):
            if not isinstance(prop, dict):
                continue
            pid = str(prop.get("id") or "").strip()
            if pid and pid not in item_ids:
                gaps.append(
                    _gap(
                        f"{sid}.предметы[{pi}]",
                        f"предмет {pid!r} нет в items_seed",
                        f"добавь {pid} в items_seed или убери ссылку",
                    )
                )
    for it in items:
        iid = str(it.get("id") or "").strip() or "items_seed"
        anchor = str(it.get("якорь") or "").strip()
        if anchor and not _anchor_in_text(anchor, full_vo):
            gaps.append(
                _gap(
                    f"items_seed.{iid}",
                    f"якорь не найден в полном закадре: {anchor[:80]!r}",
                    "укажи дословную фразу из ПОЛНОГО ЗАКАДРА",
                )
            )

    # 5. Тип связи
    seen_locs: list[str] = []
    for i, sc in enumerate(scenes):
        sid = str(sc.get("id_scene") or f"scenes[{i}]").strip()
        claimed = _link_type(sc)
        mid = str(sc.get("место_id") or "").strip()
        svo = _scene_vo(sc, vo_map)
        if i == 0:
            expected = "начало"
        else:
            prev = scenes[i - 1]
            prev_mid = str(prev.get("место_id") or "").strip()
            if mid and prev_mid and mid == prev_mid:
                expected = "сдвиг_времени" if _has_time_marker(svo) else "продолжение"
            elif mid and mid in seen_locs:
                expected = "возврат"
            elif mid and mid != prev_mid:
                expected = "новое_место"
            else:
                expected = claimed or "продолжение"
            # флешбек — только если заявлен и есть маркер
            if claimed == "флешбек" and _has_time_marker(svo):
                expected = "флешбек"
        if claimed != expected:
            # допускаем синонимы новое_место при пустом mid + фон_разовый
            if claimed == "новое_место" and not mid and sc.get("фон_разовый"):
                pass
            else:
                gaps.append(
                    _gap(
                        f"{sid}.связь_с_прошлой",
                        f"тип {claimed!r}, по сигналам ожидается {expected!r}",
                        f"поставь тип «{expected}» и согласуй что_связывает с местом/временем",
                    )
                )
        if mid:
            seen_locs.append(mid)

    # 6. Тайминг
    for i, sc in enumerate(scenes):
        sid = str(sc.get("id_scene") or f"scenes[{i}]").strip()
        nums = _scene_frames(sc)
        if not nums:
            continue
        vo_len = sum(len(vo_map.get(n) or "") for n in nums)
        vo_sec = vo_len / rate
        claimed_sec = 0.0
        try:
            claimed_sec = float(sc.get("длительность_сек") or 0)
        except (TypeError, ValueError):
            claimed_sec = 0.0
        frame_sec = sum(sec_map.get(n, 0.0) for n in nums)
        # Сравниваем сумму длительностей кадров (SoT) с vo/rate; заявленное поле — справка
        base = frame_sec if frame_sec > 0 else vo_sec
        if base <= 0:
            continue
        # |vo_sec - frame_sec| / frame_sec
        rel = abs(vo_sec - frame_sec) / base
        if rel > 0.15:
            gaps.append(
                _gap(
                    f"{sid}.длительность",
                    f"тайминг: vo≈{vo_sec:.1f}с vs кадры≈{frame_sec:.1f}с (δ={rel:.0%})",
                    "перегруппируй кадры с соседней сценой или поправь суть под реальный объём; "
                    f"длительность_сек должна быть ≈{frame_sec:.1f}",
                )
            )
        if claimed_sec > 0 and frame_sec > 0:
            rel2 = abs(claimed_sec - frame_sec) / frame_sec
            if rel2 > 0.15:
                gaps.append(
                    _gap(
                        f"{sid}.длительность_сек",
                        f"заявлено {claimed_sec:.1f}с, сумма кадров {frame_sec:.1f}с",
                        f"поставь длительность_сек={frame_sec:.1f} (сумма время_сек кадров)",
                    )
                )

    # 7. Биты — порядок по смещению якоря
    for i, sc in enumerate(scenes):
        sid = str(sc.get("id_scene") or f"scenes[{i}]").strip()
        svo_cf = _norm_cf(_scene_vo(sc, vo_map))
        bits = [b for b in (sc.get("биты") or []) if isinstance(b, dict)]
        offsets: list[tuple[int, int, str]] = []
        for bi, bit in enumerate(bits):
            ank = str(bit.get("якорь") or "").strip()
            try:
                order = int(bit.get("порядок") or bi + 1)
            except (TypeError, ValueError):
                order = bi + 1
            off = _norm_cf(ank) and svo_cf.find(_norm_cf(ank))
            if isinstance(off, int) and off < 0:
                off = 10**9
            offsets.append((order, int(off) if isinstance(off, int) else 10**9, f"{sid}.биты[{bi}]"))
        by_order = sorted(offsets, key=lambda t: t[0])
        by_off = sorted(offsets, key=lambda t: t[1])
        if by_order and [t[2] for t in by_order] != [t[2] for t in by_off]:
            gaps.append(
                _gap(
                    f"{sid}.биты",
                    "порядок битов не совпадает с порядком якорей в тексте",
                    "перенумеруй порядок битов по появлению якорей в закадре сцены",
                )
            )

    # 8. Нити
    open_threads: dict[str, str] = {}
    for i, sc in enumerate(scenes):
        sid = str(sc.get("id_scene") or f"scenes[{i}]").strip()
        # явное поле нити
        for nt in sc.get("нити") or []:
            if isinstance(nt, str) and nt.strip():
                open_threads[nt.strip().casefold()] = sid
            elif isinstance(nt, dict):
                name = str(nt.get("имя") or nt.get("нить") or nt.get("name") or "").strip()
                state = str(nt.get("статус") or nt.get("state") or "").strip().casefold()
                if not name:
                    continue
                key = name.casefold()
                if state in ("закрыта", "closed", "исполнена", "done"):
                    open_threads.pop(key, None)
                else:
                    open_threads[key] = sid
        glav = sc.get("главное")
        if isinstance(glav, dict):
            th = str(glav.get("нить") or "").strip()
            if th:
                # кульминация часто исполняет нить
                change = _norm_cf(str(glav.get("изменение") or ""))
                if any(x in change for x in ("исполн", "закры", "решил", "сделал", "→ есть")):
                    open_threads.pop(th.casefold(), None)
                else:
                    open_threads[th.casefold()] = sid
    if open_threads and scenes:
        last_sid = str(scenes[-1].get("id_scene") or "scene_last").strip()
        for th, where in open_threads.items():
            if where == last_sid:
                continue  # явно продолжена в последней сцене — ок
            gaps.append(
                _gap(
                    where,
                    f"нить {th!r} открыта и не закрыта к концу ролика",
                    f"закрой/исполни нить в {last_sid} (главное.нить или нити[].статус=закрыта) "
                    f"либо явно продолжи её в последней сцене",
                )
            )

    # 9. Разовая локация: якорь в одном кадре, сцена на 1 кадр, нет возврата
    loc_scene_hits: dict[str, list[str]] = {lid: [] for lid in loc_ids}
    loc_span_frames: dict[str, int] = {lid: 0 for lid in loc_ids}
    for i, sc in enumerate(scenes):
        sid = str(sc.get("id_scene") or f"scenes[{i}]").strip()
        mid = str(sc.get("место_id") or "").strip()
        if mid in loc_scene_hits:
            loc_scene_hits[mid].append(sid)
            loc_span_frames[mid] = loc_span_frames.get(mid, 0) + len(_scene_frames(sc))
    for loc in locs:
        lid = str(loc.get("id") or "").strip()
        if not lid:
            continue
        ank = str(loc.get("якорь") or "").strip()
        frames_hit: set[int] = set()
        if ank:
            for n, text in vo_map.items():
                if _anchor_in_text(ank, text):
                    frames_hit.add(n)
        # Реестр — только для повторяемых мест. Однокадровый фон → фон_разовый.
        if (
            len(frames_hit) <= 1
            and len(loc_scene_hits.get(lid) or []) == 1
            and loc_span_frames.get(lid, 0) <= 1
        ):
            gaps.append(
                _gap(
                    f"locations_seed.{lid}",
                    "разовая локация с id — пиши фон_разовый",
                    f"удали {lid} из locations_seed; в сцене поставь фон_разовый и очисти место_id",
                )
            )

    return gaps


def heal_open_threads(draft: dict[str, Any]) -> int:
    """Нить, открытая в середине, автоматически продолжается в последней сцене.

    Документалка часто держит семейную/следственную нить до титров — это не
    разрыв. GPT-редактор два круга не закрывает такие нити и валит скелет.
    """
    scenes = [s for s in (draft.get("scenes") or []) if isinstance(s, dict)]
    if len(scenes) < 2:
        return 0
    open_names: dict[str, str] = {}
    display: dict[str, str] = {}
    for sc in scenes:
        sid = str(sc.get("id_scene") or "").strip()
        for nt in sc.get("нити") or []:
            if isinstance(nt, str) and nt.strip():
                key = nt.strip().casefold()
                open_names[key] = sid
                display[key] = nt.strip()
            elif isinstance(nt, dict):
                name = str(nt.get("имя") or nt.get("нить") or nt.get("name") or "").strip()
                if not name:
                    continue
                key = name.casefold()
                state = str(nt.get("статус") or nt.get("state") or "").strip().casefold()
                if state in ("закрыта", "closed", "исполнена", "done"):
                    open_names.pop(key, None)
                else:
                    open_names[key] = sid
                    display[key] = name
        glav = sc.get("главное")
        if isinstance(glav, dict):
            th = str(glav.get("нить") or "").strip()
            if th:
                key = th.casefold()
                change = _norm_cf(str(glav.get("изменение") or ""))
                if any(x in change for x in ("исполн", "закры", "решил", "сделал", "→ есть")):
                    open_names.pop(key, None)
                else:
                    open_names[key] = sid
                    display.setdefault(key, th)
    last = scenes[-1]
    last_sid = str(last.get("id_scene") or "").strip()
    niti = list(last.get("нити") or [])
    have: set[str] = set()
    for nt in niti:
        if isinstance(nt, str) and nt.strip():
            have.add(nt.strip().casefold())
        elif isinstance(nt, dict):
            have.add(str(nt.get("имя") or nt.get("нить") or "").strip().casefold())
    added = 0
    for key, where in open_names.items():
        if where == last_sid or key in have:
            continue
        niti.append({"имя": display.get(key, key), "статус": "продолжена"})
        have.add(key)
        added += 1
    if added:
        last["нити"] = niti
        # Держим cells в синхроне — иначе normalize сотрёт heal.
        last_num = _cell_frame_number(last)
        for cell in draft.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            if _cell_frame_number(cell) == last_num or str(cell.get("id_cell") or "") == str(
                last.get("id_cell") or ""
            ):
                cell["нити"] = niti
                break
        logger.info("skeleton: heal_open_threads +{} → {}", added, last_sid)
    return added


def explode_glued_vo_scenes(
    draft: dict[str, Any],
    frames: list[Frame] | None = None,
) -> int:
    """1 ячейка закадра = 1 сцена: разрезать ``кадры:[N,N+1,…]`` на карточки.

    GPT-черновик часто склеивает соседние VO. Камера потом добавит SET-шоты;
    на скелете склейка запрещена. Код режет сам — редактор это не закрывает.
    """
    scenes = [s for s in (draft.get("scenes") or []) if isinstance(s, dict)]
    if not scenes:
        return 0
    vo_map = _vo_by_frame(frames) if frames else {}
    sec_map = _sec_by_frame(frames) if frames else {}
    exploded: list[dict[str, Any]] = []
    glued = 0
    for sc in scenes:
        nums = _scene_frames(sc)
        if len(nums) <= 1:
            exploded.append(sc)
            continue
        glued += 1
        for n in nums:
            child = json.loads(json.dumps(sc, ensure_ascii=False))
            child["кадры"] = [n]
            vo = (vo_map.get(n) or "").strip()
            if vo:
                child["суть"] = vo[:280]
                glav = child.get("главное") if isinstance(child.get("главное"), dict) else {}
                glav = dict(glav)
                glav["якорь_в_кадрах"] = vo[:80]
                child["главное"] = glav
            if n in sec_map:
                child["длительность_сек"] = round(float(sec_map[n]), 1)
            exploded.append(child)
    exploded.sort(key=lambda s: (_scene_frames(s) or [10**9])[0])
    for i, sc in enumerate(exploded):
        nums = _scene_frames(sc)
        n0 = nums[0] if nums else i + 1
        sc["id_scene"] = f"scene_{n0:02d}"
        link = sc.get("связь_с_прошлой") if isinstance(sc.get("связь_с_прошлой"), dict) else {}
        bind = str(link.get("что_связывает") or sc.get("место_id") or "").strip()
        if i == 0:
            sc["связь_с_прошлой"] = {"тип": "начало", "что_связывает": bind}
        elif str(link.get("тип") or "") == "начало":
            prev_nums = _scene_frames(exploded[i - 1])
            prev_mid = str(exploded[i - 1].get("место_id") or "").strip()
            sc["связь_с_прошлой"] = {
                "тип": "продолжение",
                "что_связывает": bind or prev_mid or (
                    f"scene_{prev_nums[0]:02d}" if prev_nums else ""
                ),
            }
    draft["scenes"] = exploded
    if glued:
        draft["cells"] = []
        normalize_skeleton_draft(draft)
        logger.info(
            "skeleton: explode_glued_vo_scenes {} glued → {} cells",
            glued,
            len(exploded),
        )
    return glued


def validate_skeleton_coverage(
    scenes: list[Any], expected_frame_numbers: list[int]
) -> None:
    """Жёсткая проверка покрытия: 1 VO-ячейка = 1 сцена, без дыр и двойников."""
    if not expected_frame_numbers:
        return
    expect = sorted(int(n) for n in expected_frame_numbers)
    # coverage читает только scenes + expect
    gaps: list[dict[str, str]] = []
    covered: dict[int, str] = {}
    prev_max = 0
    for i, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            raise ag.SceneDesignAgentError(f"scene_design/skeleton: scenes[{i}] не объект")
        sid = str(sc.get("id_scene") or f"scenes[{i}]")
        nums = _scene_frames(sc)
        if not nums:
            gaps.append(_gap(sid, "пустые кадры", "укажи кадры"))
            continue
        if len(nums) != 1:
            gaps.append(
                _gap(
                    sid,
                    f"кадры {nums}: склейка VO запрещена",
                    "кадры:[N] ровно один номер на сцену",
                )
            )
        if any(b != a + 1 for a, b in zip(nums, nums[1:], strict=False)):
            gaps.append(_gap(sid, f"не соседние кадры: {nums}", "только непрерывный диапазон"))
        if prev_max and nums[0] != prev_max + 1:
            gaps.append(
                _gap(sid, f"разрыв порядка после кадра {prev_max}", "сцены по порядку кадров")
            )
        prev_max = nums[-1]
        for n in nums:
            if n in covered:
                gaps.append(_gap(sid, f"кадр {n} дублируется", "каждый кадр ровно в одной сцене"))
            covered[n] = sid
    missing = [n for n in expect if n not in covered]
    if missing:
        gaps.append(_gap("coverage", f"не покрыты: {missing}", "добавь кадры"))
    extra = sorted(n for n in covered if n not in expect)
    if extra:
        gaps.append(_gap("coverage", f"лишние: {extra}", "убери лишние номера"))
    if gaps:
        msg = "; ".join(f"{g['адрес']}: {g['проблема']}" for g in gaps[:8])
        raise ag.SceneDesignAgentError(f"scene_design/skeleton: покрытие — {msg}")


def merge_by_id(draft: dict[str, Any], editor: dict[str, Any]) -> dict[str, Any]:
    """Заменить карточки/сущности по id из ответа редактора."""
    out = json.loads(json.dumps(draft, ensure_ascii=False))  # deep copy via json
    normalize_skeleton_draft(out)
    ed = dict(editor)
    # V3 editor → fixed_cells; legacy fixed_scenes всё ещё принимаем.
    fixed_cells = [c for c in (ed.get("fixed_cells") or []) if isinstance(c, dict)]
    if fixed_cells and not ed.get("fixed_scenes"):
        tmp = {
            "cells": fixed_cells,
            "items_seed": ed.get("fixed_items") or [],
            "characters_seed": ed.get("fixed_characters") or [],
            "locations_seed": ed.get("fixed_locations") or [],
        }
        normalize_skeleton_draft(tmp)
        ed["fixed_scenes"] = tmp.get("scenes") or []
        # Обновить cells в out по id_cell / кадру
        cells = [c for c in (out.get("cells") or []) if isinstance(c, dict)]
        by_cell = {
            str(c.get("id_cell") or "").strip(): i
            for i, c in enumerate(cells)
            if c.get("id_cell")
        }
        by_frame = {
            _cell_frame_number(c): i
            for i, c in enumerate(cells)
            if _cell_frame_number(c) is not None
        }
        for cell in fixed_cells:
            cid = str(cell.get("id_cell") or "").strip()
            num = _cell_frame_number(cell)
            if cid and cid in by_cell:
                cells[by_cell[cid]] = cell
            elif num is not None and num in by_frame:
                cells[by_frame[num]] = cell
            else:
                cells.append(cell)
                if cid:
                    by_cell[cid] = len(cells) - 1
        out["cells"] = cells

    scenes = list(out.get("scenes") or [])
    by_id = {
        str(s.get("id_scene") or "").strip(): i
        for i, s in enumerate(scenes)
        if isinstance(s, dict) and s.get("id_scene")
    }
    for sc in ed.get("fixed_scenes") or []:
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id_scene") or "").strip()
        if not sid:
            num = _cell_frame_number(sc)
            if num is not None:
                sid = f"scene_{num:02d}"
                sc = {**sc, "id_scene": sid}
        if sid in by_id:
            scenes[by_id[sid]] = sc
        elif sid:
            scenes.append(sc)
            by_id[sid] = len(scenes) - 1
    out["scenes"] = scenes

    def _merge_seed(key: str, fixed_key: str, id_field: str = "id") -> None:
        items = [x for x in (out.get(key) or []) if isinstance(x, dict)]
        idx = {
            str(x.get(id_field) or "").strip(): i
            for i, x in enumerate(items)
            if x.get(id_field)
        }
        fixed_list = [x for x in (ed.get(fixed_key) or []) if isinstance(x, dict)]
        provided: set[str] = set()
        for item in fixed_list:
            iid = str(item.get(id_field) or "").strip()
            if not iid:
                continue
            provided.add(iid)
            if iid in idx:
                items[idx[iid]] = item
            else:
                items.append(item)
                idx[iid] = len(items) - 1
        # Редактор вернул non-empty реестр → выкинуть сущности, которых нет
        # в ответе и на которые больше не ссылаются карточки (слияние двойников).
        if provided:
            referenced: set[str] = set()
            if key == "locations_seed":
                for sc in out.get("scenes") or []:
                    if isinstance(sc, dict):
                        mid = str(sc.get("место_id") or "").strip()
                        if mid:
                            referenced.add(mid)
            elif key == "characters_seed":
                for sc in out.get("scenes") or []:
                    if not isinstance(sc, dict):
                        continue
                    for p in sc.get("персонажи") or []:
                        if isinstance(p, dict):
                            pid = str(p.get("id") or "").strip()
                            if pid:
                                referenced.add(pid)
            elif key == "items_seed":
                for sc in out.get("scenes") or []:
                    if not isinstance(sc, dict):
                        continue
                    for p in sc.get("предметы") or []:
                        if isinstance(p, dict):
                            pid = str(p.get("id") or "").strip()
                            if pid:
                                referenced.add(pid)
            items = [
                x
                for x in items
                if str(x.get(id_field) or "").strip() in provided
                or str(x.get(id_field) or "").strip() in referenced
            ]
        out[key] = items

    _merge_seed("characters_seed", "fixed_characters")
    _merge_seed("locations_seed", "fixed_locations")
    _merge_seed("items_seed", "fixed_items")
    if ed.get("report"):
        out["report"] = ed.get("report")
    # fixed_cells → источник правды для scenes; иначе scenes (legacy) → cells.
    if not fixed_cells:
        out["cells"] = []
    normalize_skeleton_draft(out)
    return out


def _action_phases(scene: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (
        scene.get("цепь_действия")
        or scene.get("phases")
        or scene.get("фазы")
        or []
    )
    return [ph for ph in raw if isinstance(ph, dict)]


def _phase_blob(scene: dict[str, Any]) -> str:
    return _norm_cf(
        " ".join(
            str(
                ph.get("action")
                or ph.get("действие")
                or ph.get("subject")
                or ph.get("изменение")
                or ""
            )
            for ph in _action_phases(scene)
        )
    )


def validate_action_covers_skeleton_bits(
    action_scenes: list[Any],
    skeleton: dict[str, Any] | None,
) -> None:
    """Биты скелета покрыты кинематографическими фазами action.

    Не требуем дословный якорь VO в тексте фазы (action пишет видимое действие).
    На сцену скелета с N битами нужно ≥N фаз; плюс мягкое пересечение токенов
    глагол/изменение с фазами сцены (если токены есть).
    """
    if not isinstance(skeleton, dict):
        return
    acts = [s for s in action_scenes if isinstance(s, dict)]
    if not acts:
        return
    by_id = {
        str(s.get("id_scene") or "").strip(): s
        for s in acts
        if str(s.get("id_scene") or "").strip()
    }
    missing: list[str] = []
    for i, sc in enumerate(skeleton.get("scenes") or []):
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id_scene") or "").strip() or f"scene_{i+1:02d}"
        bits = [b for b in (sc.get("биты") or []) if isinstance(b, dict)]
        if not bits:
            continue
        act = by_id.get(sid)
        if act is None and i < len(acts):
            act = acts[i]
        if act is None:
            missing.append(f"{sid}: нет сцены action")
            continue
        phases = _action_phases(act)
        if len(phases) < len(bits):
            missing.append(
                f"{sid}: фаз {len(phases)} < битов {len(bits)}"
            )
            continue
        blob = _phase_blob(act)
        for bi, bit in enumerate(bits):
            toks = _significant_tokens(
                " ".join(
                    str(bit.get(k) or "")
                    for k in ("глагол", "изменение", "якорь")
                )
            )
            # Короткие/служебные биты без значимых токенов — хватает бюджета фаз.
            if len(toks) < 2:
                continue
            if not (toks & set(re.findall(r"[a-zа-яё0-9]{3,}", blob))):
                # Не валим весь прогон: кино-фаза может перефразировать VO.
                # Жёстко только бюджет фаз выше; токены — warning в лог.
                logger.warning(
                    "skeleton/action: {} бит[{}] без токен-пересечения "
                    "(toks={}) — допускаем при фазах≥битов",
                    sid,
                    bi,
                    sorted(toks)[:8],
                )
    if missing:
        raise ag.SceneDesignAgentError(
            "scene_design/action: биты скелета без фазы: " + ", ".join(missing[:12])
        )


def format_gaps_error(gaps: list[dict[str, str]]) -> str:
    lines = ["skeleton: разрывы не закрыты после редактора:"]
    for g in gaps[:40]:
        lines.append(f"- [{g.get('адрес')}] {g.get('проблема')} → {g.get('как_исправить')}")
    if len(gaps) > 40:
        lines.append(f"… и ещё {len(gaps) - 40}")
    return "\n".join(lines)


def _load_editor_prompt(project: Project) -> str:
    from app.project_root import find_project_root
    from app.services.prompt_library import step_dir

    # step_code = excel_gpt → папка prompts/05_excel_gpt (см. prompt_library.STEP_DIRS)
    excel = step_dir("excel_gpt")
    for base in (excel, find_project_root() / "templates" / "excel_gpt_agents"):
        p = Path(base) / "sd_skeleton_editor.md"
        if p.is_file():
            text = p.read_text(encoding="utf-8").strip()
            if text:
                return text
    raise RuntimeError("skeleton: нет промпта sd_skeleton_editor.md")


def _parse_editor_reply(text: str) -> dict[str, Any]:
    data = ag.extract_json_object(
        text,
        marker_keys=(
            "fixed_cells",
            "fixed_scenes",
            "error",
            "fixed_characters",
            "fixed_locations",
            "fixed_items",
        ),
    )
    if data is None:
        raise RuntimeError(
            f"skeleton editor: в ответе нет JSON (len={len(text or '')})"
        )
    err = str(data.get("error") or "").strip()
    if err:
        raise RuntimeError(f"skeleton editor: честный отказ — {err}")
    if not isinstance(data.get("fixed_cells"), list):
        data["fixed_cells"] = []
    if not isinstance(data.get("fixed_scenes"), list):
        data["fixed_scenes"] = []
    if not isinstance(data.get("fixed_characters"), list):
        data["fixed_characters"] = []
    if not isinstance(data.get("fixed_locations"), list):
        data["fixed_locations"] = []
    if not isinstance(data.get("fixed_items"), list):
        data["fixed_items"] = []
    return data


async def _gpt(prompt: str, context: str, *, project: Project, timeout: float) -> str:
    from app.services import gpt_client

    text = f"{prompt}\n\n---\n\n{context}"
    return await gpt_client.gpt_ask_fresh(
        text, timeout=timeout, project_id=project.id
    )


def write_skeleton_cells_payload(draft: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать срез для cells.slice_to_cells (добавить sk_bit/sk_thread поля)."""
    return draft


async def store_skeleton_cells(
    session: AsyncSession,
    project: Project,
    draft: dict[str, Any],
    full_vo: str,
) -> dict[str, int]:
    """Запись sk_* ячеек (карточки VO + реестры + биты + нити + предметы)."""
    normalize_skeleton_draft(draft)
    base = sd_cells.slice_to_cells(project, ag.SKELETON, draft, full_vo)
    extra: list[Any] = []
    for sc in draft.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id_scene") or "").strip()
        if not sid:
            continue
        for bit in sc.get("биты") or []:
            if not isinstance(bit, dict):
                continue
            try:
                order = int(bit.get("порядок") or 0)
            except (TypeError, ValueError):
                order = 0
            key = f"{sid}.bit{order}"
            for field in ("порядок", "глагол", "изменение", "якорь"):
                val = bit.get(field)
                if val is None or (isinstance(val, str) and not str(val).strip()):
                    continue
                extra.append(
                    sd_cells._cell(  # noqa: SLF001
                        project, ag.SKELETON, "sk_bit", key, field, val, seq=order
                    )
                )
        glav = sc.get("главное")
        if isinstance(glav, dict):
            th = str(glav.get("нить") or "").strip()
            if th:
                extra.append(
                    sd_cells._cell(
                        project, ag.SKELETON, "sk_thread", th, "открыта_в", sid, seq=0
                    )
                )
        for nt in sc.get("нити") or []:
            if isinstance(nt, str) and nt.strip():
                extra.append(
                    sd_cells._cell(
                        project,
                        ag.SKELETON,
                        "sk_thread",
                        nt.strip(),
                        "упоминание",
                        sid,
                        seq=0,
                    )
                )
            elif isinstance(nt, dict):
                name = str(nt.get("имя") or nt.get("нить") or "").strip()
                if not name:
                    continue
                for field, val in nt.items():
                    if field in ("имя", "нить", "name"):
                        continue
                    if val is None or (isinstance(val, str) and not str(val).strip()):
                        continue
                    extra.append(
                        sd_cells._cell(
                            project, ag.SKELETON, "sk_thread", name, str(field), val, seq=0
                        )
                    )
    return await sd_cells.store_cells(session, project, ag.SKELETON, base + extra)


async def run_skeleton(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
) -> dict[str, Any]:
    """Черновик → validate → editor (≤2) → ячейки + checkpoint."""
    cached = runner.load_checkpoint(project, ag.SKELETON)
    if isinstance(cached, dict) and (cached.get("scenes") or cached.get("cells")):
        normalize_skeleton_draft(cached)
        logger.info("[#{}] skeleton: checkpoint hit — GPT skip", project.id)
        return cached

    context = context_builder.build_shared_context(project, frames)
    full_vo = context_builder.full_voiceover(project, frames)
    timeout = float(getattr(settings, "scene_design_agent_attempt_timeout_s", 280.0) or 280.0)

    draft_prompt = ag.load_prompt(ag.SKELETON, project)
    logger.info("[#{}] skeleton: draft GPT…", project.id)
    draft_raw = await _gpt(draft_prompt, context, project=project, timeout=timeout)
    draft = ag.parse_agent_slice(ag.SKELETON, draft_raw, validate=False)
    normalize_skeleton_draft(draft)
    explode_glued_vo_scenes(draft, frames)
    heal_open_threads(draft)
    # coverage soft — полная матрица в validate_skeleton
    gaps = validate_skeleton(draft, frames, full_vo)
    if not gaps:
        logger.info("[#{}] skeleton: draft ok (gaps=0)", project.id)
    else:
        editor_prompt = _load_editor_prompt(project)
        for round_i in range(1, EDITOR_MAX_ROUNDS + 1):
            logger.info(
                "[#{}] skeleton: editor round {} (gaps={})",
                project.id,
                round_i,
                len(gaps),
            )
            editor_ctx = (
                f"{context}\n\n"
                f"# ЧЕРНОВИК (JSON)\n{json.dumps(draft, ensure_ascii=False)}\n\n"
                f"# РАЗРЫВЫ (JSON)\n{json.dumps(gaps, ensure_ascii=False)}"
            )
            reply = await _gpt(
                editor_prompt, editor_ctx, project=project, timeout=timeout
            )
            edited = _parse_editor_reply(reply)
            draft = merge_by_id(draft, edited)
            explode_glued_vo_scenes(draft, frames)
            heal_open_threads(draft)
            gaps = validate_skeleton(draft, frames, full_vo)
            if not gaps:
                logger.info(
                    "[#{}] skeleton: editor round {} fixed all",
                    project.id,
                    round_i,
                )
                break
            logger.warning(
                "[#{}] skeleton: editor round {} still gaps={}",
                project.id,
                round_i,
                len(gaps),
            )
        if gaps:
            raise RuntimeError(format_gaps_error(gaps))

    # финальная coverage на всякий случай
    expect = [int(fr.number) for fr in frames if fr.number is not None]
    validate_skeleton_coverage(draft.get("scenes") or [], expect)

    stats = await store_skeleton_cells(session, project, draft, full_vo)
    runner.save_checkpoint(project, ag.SKELETON, draft)
    await session.commit()
    logger.info("[#{}] skeleton: stored cells={} checkpoint ok", project.id, stats)
    return draft
