"""«Каркас» после «Границ сцен»: сцена на фрагмент, пачки по 8.

Повторяет шаг B ``_tmp_skel_m13_opus.py``: фрагменты → пачки по 8 с
контекстом соседей и предыдущих сцен, база персонажей/предметов копится;
закадр сцены = фрагмент (пишет код). Модель — с ноды (``bind_project_llm``),
вызов через ``gpt_api.chat`` (claude-* → vibecode /v1/messages).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.scene_boundaries import nws, nz, strip_fence

B_BATCH = 8
SYSTEM_PREFIX = (
    "Ты возвращаешь только валидный JSON, без пояснений и без markdown. "
    "Закадр копируешь дословно из входа.\n\n"
)


def extract_json_obj(text: str) -> dict:
    s = strip_fence(text)
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        for i in range(end, start, -1):
            if s[i] != "}":
                continue
            try:
                obj = json.loads(s[start : i + 1])
                if isinstance(obj, dict):
                    return obj
            except Exception:  # noqa: BLE001
                continue
    raise ValueError("no JSON object in model reply")


def compact_scene(s: dict) -> dict:
    return {
        "номер": s.get("номер"),
        "закадр": s.get("закадр"),
        "место": s.get("место"),
        "персонажи": s.get("персонажи"),
        "предметы": s.get("предметы"),
        "сцена": s.get("сцена"),
    }


def base_block(chars: list, items: list) -> str:
    return (
        "база_персонажей=" + json.dumps(chars, ensure_ascii=False) + "\n"
        "предметы=" + json.dumps(items, ensure_ascii=False)
    )


def b_user(frags: list[str], lo: int, hi: int, prev_scenes: list, chars: list, items: list) -> str:
    batch = [{"номер": i + 1, "закадр": frags[i]} for i in range(lo, hi)]
    before = frags[max(0, lo - 2) : lo]
    after = frags[hi : hi + 2]
    u = (
        "Разбивка закадра на сцены уже сделана. Ниже фрагменты закадра — каждый фрагмент это ровно одна сцена. "
        f"Напиши ровно {hi - lo} сцен(ы): по одной на каждый фрагмент, с теми же номерами, в том же порядке. "
        "Поле «закадр» каждой сцены — точная копия текста фрагмента, символ в символ (включая знаки ударения). "
        "Не объединяй и не дроби фрагменты, не переписывай закадр.\n"
        "Остальные поля (место, персонажи, предметы, речь, сцена) заполняй по системному промту. "
        "Соседние фрагменты и предыдущие сцены даны только для контекста и преемственности — сцены для них не пиши.\n\n"
        "Персонажи и предметы: если человек или предмет уже есть в базе — используй его id. Новым давай следующие свободные id. "
        "В «база_персонажей» и «предметы» ответа верни карточки всех id, которые использованы в твоих сценах "
        "(старые — без изменений, новые — полностью).\n\n"
    )
    u += "===ФРАГМЕНТЫ_ДЛЯ_СЦЕН===\n" + json.dumps(batch, ensure_ascii=False, indent=1) + "\n\n"
    u += "===КОНТЕКСТ_ДО (сцены не писать)===\n" + ("\n\n".join(before) if before else "(начало фильма)") + "\n\n"
    u += "===КОНТЕКСТ_ПОСЛЕ (сцены не писать)===\n" + ("\n\n".join(after) if after else "(конец фильма)") + "\n\n"
    if prev_scenes:
        u += (
            "===ПРЕДЫДУЩИЕ_СЦЕНЫ (для преемственности)===\n"
            + json.dumps([compact_scene(s) for s in prev_scenes[-6:]], ensure_ascii=False)
            + "\n"
            + "Краткий список всех предыдущих сцен: "
            + json.dumps(
                [
                    {"номер": s.get("номер"), "место": s.get("место"), "персонажи": s.get("персонажи")}
                    for s in prev_scenes[:-6]
                ],
                ensure_ascii=False,
            )
            + "\n\n"
        )
    u += "===УЖЕ_ЕСТЬ===\n" + base_block(chars, items) + "\n\n"
    u += "Верни один JSON-объект формы {\"сцены\": [...], \"база_персонажей\": [...], \"предметы\": [...]}."
    return u


def merge_base(dst: list, src: list) -> list:
    seen = {str(c.get("id")) for c in dst}
    conflicts = []
    for c in src or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        cid = str(c["id"])
        if cid in seen:
            old = next(x for x in dst if str(x.get("id")) == cid)
            if (old.get("имя") or "") != (c.get("имя") or ""):
                conflicts.append({"id": cid, "old": old.get("имя"), "new": c.get("имя")})
            continue
        dst.append(c)
        seen.add(cid)
    return conflicts


async def _call(user: str, system: str, tag: str, trace_dir: Path) -> str:
    from app.services.gpt_api import chat
    from app.services.llm_override import current_text_model_id

    model = current_text_model_id() or "?"
    (trace_dir / f"{tag}.req.txt").write_text(
        f"=== MODEL: {model}\n=== SYSTEM ===\n{system}\n=== USER ===\n{user}\n",
        encoding="utf-8",
    )
    t0 = time.time()
    res = await chat(
        prompt=user,
        system=system,
        auto_pack=False,
        timeout=900.0,
        max_retries=2,
    )
    txt = res.text or ""
    (trace_dir / f"{tag}.resp.txt").write_text(txt, encoding="utf-8")
    logger.info(
        "scene_skeleton {}: model={} chars={} sec={} finish={}",
        tag, res.model, len(txt), round(time.time() - t0, 1), res.finish_reason,
    )
    return txt


async def run_skeleton_batches(
    frags: list[str], master: str, trace_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    trace_dir.mkdir(parents=True, exist_ok=True)
    system = SYSTEM_PREFIX + (master or "")
    scenes: list[dict] = []
    chars: list[dict] = []
    items: list[dict] = []
    rep: dict[str, Any] = {"batches": [], "zakadr_overwritten": 0, "conflicts": [], "split_after_fail": []}
    queue = [
        (f"b{bi:02d}", lo, min(lo + B_BATCH, len(frags)))
        for bi, lo in enumerate(range(0, len(frags), B_BATCH), 1)
    ]
    while queue:
        bname, lo, hi = queue.pop(0)
        user = b_user(frags, lo, hi, scenes, chars, items)
        got = None
        failed_net = False
        for att in (1, 2):
            try:
                raw = await _call(user, system, f"B_scenes_{bname}_a{att}", trace_dir)
            except Exception as e:  # noqa: BLE001
                logger.warning("scene_skeleton {} a{} call failed: {}", bname, att, e)
                if hi - lo > 1:
                    failed_net = True
                    break
                raise
            try:
                d = extract_json_obj(raw)
                sc = [x for x in d.get("сцены") or d.get("scenes") or [] if isinstance(x, dict)]
            except Exception as e:  # noqa: BLE001
                logger.warning("scene_skeleton {} a{} parse fail: {}", bname, att, e)
                continue
            empty = sum(1 for x in sc if not str(x.get("сцена") or "").strip())
            if len(sc) != hi - lo or empty:
                logger.warning(
                    "scene_skeleton {} a{}: сцен {} != фрагментов {} или пустых «сцена» {}",
                    bname, att, len(sc), hi - lo, empty,
                )
                got = got or (d, sc, att)
                continue
            got = (d, sc, att)
            break
        if failed_net:
            mid = (lo + hi) // 2
            rep["split_after_fail"].append({"batch": bname, "fragments": f"{lo + 1}-{hi}"})
            queue[0:0] = [(bname + "s1", lo, mid), (bname + "s2", mid, hi)]
            continue
        if got is None:
            raise RuntimeError(f"Каркас пачка {bname} ({lo + 1}-{hi}): нет разбираемого JSON")
        d, sc, att = got
        bad = 0
        bynum: dict[int, dict] = {}
        for s in sc:
            try:
                bynum[int(s.get("номер"))] = s
            except Exception:  # noqa: BLE001
                pass
        for i in range(lo, hi):
            s = bynum.get(i + 1) or (sc[i - lo] if i - lo < len(sc) else {"сцена": "", "место": "", "_missing": True})
            s = dict(s)
            if nws(s.get("закадр") or "") != nws(frags[i]):
                bad += 1
                s["_закадр_модели"] = s.get("закадр")
            s["закадр"] = frags[i]
            s["номер"] = i + 1
            s["фрагмент"] = i + 1
            s["батч"] = bname
            scenes.append(s)
        rep["conflicts"] += merge_base(chars, d.get("база_персонажей"))
        rep["conflicts"] += merge_base(items, d.get("предметы"))
        rep["zakadr_overwritten"] += bad
        rep["batches"].append(
            {"batch": bname, "fragments": f"{lo + 1}-{hi}", "scenes_returned": len(sc),
             "attempt": att, "zakadr_not_verbatim": bad}
        )
        logger.info(
            "scene_skeleton batch {} frags {}-{} returned {} not_verbatim {} chars {} items {}",
            bname, lo + 1, hi, len(sc), bad, len(chars), len(items),
        )
    return {"сцены": scenes, "база_персонажей": chars, "предметы": items}, rep


def coverage(vo: str, scenes: list[dict]) -> dict[str, Any]:
    j = "".join(str(s.get("закадр") or "") for s in scenes)
    empty = [s.get("номер") for s in scenes if not str(s.get("сцена") or "").strip()]
    return {
        "scenes": len(scenes),
        "join_equal_ignoring_stress": nz(j) == nz(vo),
        "empty_scene_text": empty,
    }


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(_as_text(x) for x in v if _as_text(x))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def scene_attrs(s: dict) -> dict[str, Any]:
    """Поля каркаса → attrs кадра (как offline chain apply_to_db)."""
    out: dict[str, Any] = {}
    action = str(s.get("сцена") or "").strip()
    if action:
        out["главное_действие"] = action
        out["main_action"] = action
        out["shot01_action"] = action
        out["сцена"] = action
    place = _as_text(s.get("место"))
    if place:
        out["место"] = place
        out["place"] = place
    ppl = _as_text(s.get("персонажи"))
    if ppl:
        out["персонажи_сцены"] = ppl
        out["characters"] = ppl
    props = _as_text(s.get("предметы"))
    if props:
        out["предметы"] = props
        out["shot01_props"] = props
    speech = s.get("речь")
    if speech not in (None, "", []):
        out["речь"] = speech
    return out


def output_path(project: Any) -> Path:
    return Path(project.data_dir) / "scene_skeleton" / "skeleton.json"
