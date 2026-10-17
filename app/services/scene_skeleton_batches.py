"""«Каркас» после «Границ сцен»: сцена на фрагмент.

База персонажей — один запрос на все фрагменты границ. Сцены — три пачки,
разрезы только между этими фрагментами. Закадр сцены = фрагмент (пишет код).
Модель — с ноды (``bind_project_llm``), вызов через ``gpt_api.chat``
(группа script_frames_qc → vibecode через VPS).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.scene_boundaries import nws, nz, strip_fence

SCENE_BATCH_COUNT = 3
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


def fragment_ranges(n: int, parts: int) -> list[tuple[int, int]]:
    """Непрерывные куски [lo, hi) на n фрагментах. Режем только по границе фрагмента.

    Если фрагментов меньше, чем parts, пачек меньше: пустых нет.
    """
    if n <= 0:
        return []
    parts = max(1, min(int(parts), n))
    base, extra = divmod(n, parts)
    ranges: list[tuple[int, int]] = []
    lo = 0
    for i in range(parts):
        size = base + (1 if i < extra else 0)
        hi = lo + size
        ranges.append((lo, hi))
        lo = hi
    return ranges


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
    # 2026-10-11 fix1: full voiceover as chronology context, exactly as c60 (before ===УЖЕ_ЕСТЬ===)
    u += "===ВЕСЬ_ЗАКАДР (только для понимания хронологии, сцены не писать)===" + "\n" + "\n\n".join(frags) + "\n\n"
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


def _kept_reply(tag: str) -> str | None:
    """Один раз: уже лежащий ответ пачки не слать снова. Пачка из флага уходит заново."""
    flag = Path("data/_resend_skeleton_pack.flag")
    if not flag.is_file():
        return None
    try:
        spec = json.loads(flag.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    resend = [str(x) for x in (spec.get("resend") or [])]
    if any(tag == x or tag.startswith(x) for x in resend):
        return None
    src = Path(str(spec.get("from_dir") or "")) / f"{tag}.resp.txt"
    if not src.is_file() or src.stat().st_size <= 0:
        return None
    return src.read_text(encoding="utf-8")


async def _call(user: str, system: str, tag: str, trace_dir: Path) -> str:
    from app.services.gpt_api import chat
    from app.services.llm_override import current_text_model_id

    kept = _kept_reply(tag)
    if kept is not None:
        (trace_dir / f"{tag}.resp.txt").write_text(kept, encoding="utf-8")
        logger.info("scene_skeleton {}: берём уже лежащий ответ chars={}", tag, len(kept))
        return kept
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
        timeout=1200.0,
        max_retries=0,
    )
    txt = res.text or ""
    (trace_dir / f"{tag}.resp.txt").write_text(txt, encoding="utf-8")
    logger.info(
        "scene_skeleton {}: model={} chars={} sec={} finish={}",
        tag, res.model, len(txt), round(time.time() - t0, 1), res.finish_reason,
    )
    return txt


SCENE_ATTEMPTS = 5
SCENE_RETRY_S = 8.0


def _norm(s: Any) -> str:
    import unicodedata as _u

    s = _u.normalize("NFD", str(s or ""))
    s = "".join(ch for ch in s if not _u.combining(ch))
    s = s.replace("ё", "е").replace("Ё", "Е").lower()
    return " ".join(s.replace("\u2014", " — ").split())


def _split_var(name: Any) -> tuple[str, str, str | None]:
    """(base name, variation diff, parent id from «(вариация cNN)»)."""
    import re as _re

    raw = str(name or "")
    m = _re.search(r"\(\s*вариация\s+([^)\s]+)\s*\)", raw, _re.I)
    parent = m.group(1) if m else None
    raw = _re.sub(r"\(\s*вариация[^)]*\)", "", raw, flags=_re.I).strip()
    parts = _re.split(r"\s+[—–-]\s+", raw, maxsplit=1)
    base = parts[0].strip()
    diff = parts[1].strip() if len(parts) > 1 else ""
    return base, diff, parent


def _surname(n: str) -> str:
    w = _norm(n).split()
    return w[-1] if w else ""


def _is_var(c: dict) -> bool:
    b, d, p = _split_var(c.get("имя"))
    return bool(p or d or str(c.get("правила") or "").strip())


def _find_base(base: list, name: str) -> str | None:
    """Base card id for a person name: exact normalized, else unique surname match (names of 2+ words)."""
    nn = _norm(name)
    bases = [c for c in base if not _is_var(c)]
    for c in bases:
        if _norm(_split_var(c.get("имя"))[0]) == nn:
            return str(c["id"])
    sn = _surname(name)
    if not sn:
        return None
    hits = []
    for c in bases:
        cn = _norm(_split_var(c.get("имя"))[0])
        if len(cn.split()) >= 2 or len(nn.split()) >= 2:
            if _surname(cn) == sn:
                hits.append(str(c["id"]))
    return hits[0] if len(set(hits)) == 1 else None


def _next_id(by_id: dict, cid: str, prefix_guess: str) -> str:
    import re as _re

    m = _re.match(r"^(\D*)(\d+)", cid)
    pre, width = (m.group(1), len(m.group(2))) if m else (prefix_guess, 2)
    nums = [int(x.group(1)) for k in by_id if (x := _re.match(r"^\D*(\d+)", str(k)))]
    n = (max(nums) if nums else 0) + 1
    while f"{pre}{n:0{width}d}" in by_id:
        n += 1
    return f"{pre}{n:0{width}d}"


def _union_frags(dst: dict, src: dict) -> None:
    a = dst.get("фрагменты") or []
    b = src.get("фрагменты") or []
    if not isinstance(a, list):
        a = [a]
    if not isinstance(b, list):
        b = [b]
    merged: list = []
    for x in [*a, *b]:
        if x not in merged:
            merged.append(x)
    if merged:
        dst["фрагменты"] = merged


def _keep_existing(by_id: dict, mp: dict[str, str], cid: str, hit: str, src: dict) -> None:
    mp[cid] = hit
    old = by_id.get(hit)
    if isinstance(old, dict):
        _union_frags(old, src)


def _remap_ids(base: list, cards: list, prefix_guess: str) -> dict[str, str]:
    """Merge one batch's cards into ``base`` (in place). Returns batch-id -> merged-id map.

    Bases first (normalized name / surname), then variations (attached to base by the
    name before the dash and «(вариация cNN)» / «правила»); «правила» and the
    «(вариация cNN)» in names are rewritten with the merged ids.
    """
    mp: dict[str, str] = {}
    by_id = {str(c.get("id")): c for c in base}
    cards = [c for c in (cards or []) if isinstance(c, dict) and c.get("id")]
    items_mode = prefix_guess != "c"
    for c in [c for c in cards if items_mode or not _is_var(c)]:
        cid = str(c["id"])
        if items_mode:
            hit = next((str(x["id"]) for x in base if _norm(x.get("имя")) == _norm(c.get("имя"))), None)
        else:
            hit = _find_base(base, _split_var(c.get("имя"))[0])
        if hit:
            _keep_existing(by_id, mp, cid, hit, c)
            continue
        new = cid if cid not in by_id else _next_id(by_id, cid, prefix_guess)
        cc = dict(c)
        cc["id"] = new
        if not items_mode:
            cc["правила"] = ""
        base.append(cc)
        by_id[new] = cc
        mp[cid] = new
    if not items_mode:
        for c in [c for c in cards if _is_var(c)]:
            cid = str(c["id"])
            bname, diff, par = _split_var(c.get("имя"))
            raw_par = par or str(c.get("правила") or "").strip()
            parent = _find_base(base, bname) if bname else None
            if parent is None and raw_par:
                parent = mp.get(raw_par) or (raw_par if raw_par in by_id else None)
            if parent is None:  # orphan: treat as base
                parent = _next_id(by_id, cid, "c")
                by_id[parent] = {"id": parent, "имя": bname or str(c.get("имя")), "правила": ""}
                base.append(by_id[parent])
            pname = _split_var(by_id[parent].get("имя"))[0]
            hit = next(
                (
                    str(x["id"]) for x in base
                    if str(x.get("правила") or "").strip() == parent
                    and _norm(_split_var(x.get("имя"))[1]) == _norm(diff)
                ),
                None,
            )
            if hit:
                _keep_existing(by_id, mp, cid, hit, c)
                continue
            new = cid if cid not in by_id else _next_id(by_id, cid, "c")
            cc = dict(c)
            cc["id"] = new
            cc["правила"] = parent
            cc["имя"] = f"{pname} — {diff or 'вариация'} (вариация {parent})"
            base.append(cc)
            by_id[new] = cc
            mp[cid] = new
    return {k: v for k, v in mp.items() if k != v}


def _apply_map(obj: Any, mp: dict[str, str]) -> Any:
    import re as _re

    if not mp:
        return obj
    rx = _re.compile(r"(?<![\w])(" + "|".join(_re.escape(k) for k in sorted(mp, key=len, reverse=True)) + r")(?![\w])")
    return json.loads(rx.sub(lambda m: mp[m.group(1)], json.dumps(obj, ensure_ascii=False)))


async def _run_one_batch(bname, lo, hi, frags, system, trace_dir, chars0, items0, rep) -> list[tuple]:
    """Одна пачка целиком. Сбой сети — повтор той же пачки, без нарезки на фрагменты."""
    user = b_user(frags, lo, hi, [], chars0, items0)
    got = None
    for att in range(1, SCENE_ATTEMPTS + 1):
        try:
            raw = await _call(user, system, f"B_scenes_{bname}_a{att}", trace_dir)
        except Exception as e:  # noqa: BLE001
            logger.warning("scene_skeleton {} a{} call failed: {}", bname, att, e)
            if att < SCENE_ATTEMPTS:
                await asyncio.sleep(SCENE_RETRY_S * att)
                continue
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
    if got is None:
        raise RuntimeError(f"Каркас пачка {bname} ({lo + 1}-{hi}): нет разбираемого JSON")
    return [(bname, lo, hi) + got]


async def _await_each(coros):
    """Один запрос за раз. Пять длинных потоков сразу рвут сокет."""
    out = []
    for coro in coros:
        out.append(await coro)
    return out


PREPASS_REL = "prompts/scene_design/scene_skeleton_registry_prepass.md"


def _variation_rules(master: str) -> str:
    m = master or ""
    a = m.find("**Вариации.**")
    if a < 0:
        return ""
    b = m.find("`предметы`:", a)
    return m[a : b if b > a else len(m)].strip()


def _registry_user(frags: list[str], lo: int, hi: int) -> str:
    batch = [{"номер": i + 1, "закадр": frags[i]} for i in range(lo, hi)]
    return (
        "Ниже весь закадр, уже разрезанный прошлой нодой на фрагменты. "
        "Это одна пачка на весь фильм. "
        f"Номера фрагментов: {lo + 1}–{hi}. "
        "Базу персонажей и предметов составь по всем этим фрагментам. "
        "В поле «фрагменты» пиши эти номера.\n\n"
        "===ФРАГМЕНТЫ===\n"
        + json.dumps(batch, ensure_ascii=False, indent=1)
    )


async def _run_registry_batch(bname, lo, hi, frags, system, trace_dir) -> list[tuple]:
    """Одна пачка базы целиком. Сбой сети — повтор той же пачки, без нарезки."""
    user = _registry_user(frags, lo, hi)
    last: Exception | None = None
    for att in range(1, SCENE_ATTEMPTS + 1):
        try:
            raw = await _call(user, system, f"A_registry_{bname}_a{att}", trace_dir)
        except Exception as e:  # noqa: BLE001
            logger.warning("scene_skeleton registry {} a{} call failed: {}", bname, att, e)
            last = e
            if att < SCENE_ATTEMPTS:
                await asyncio.sleep(SCENE_RETRY_S * att)
            continue
        try:
            d = extract_json_obj(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("scene_skeleton registry {} a{} parse fail: {}", bname, att, e)
            last = e
            continue
        chars = [c for c in (d.get("база_персонажей") or []) if isinstance(c, dict)]
        items = [c for c in (d.get("предметы") or []) if isinstance(c, dict)]
        return [(bname, lo, hi, chars, items)]
    raise RuntimeError(f"Каркас: база персонажей, пачка {bname} ({lo + 1}-{hi}): {last}")


async def run_registry_prepass(frags: list[str], master: str, trace_dir: Path) -> tuple[list, list]:
    """Один запрос на все фрагменты границ. Одинаковых людей склеивает _remap_ids."""
    root = Path(__file__).resolve().parents[2]
    tpl = (root / PREPASS_REL).read_text(encoding="utf-8")
    system = SYSTEM_PREFIX + tpl.replace("{{VARIATION_RULES}}", _variation_rules(master))
    queue = [("r01", 0, len(frags))]
    if not queue:
        raise RuntimeError("Каркас: пре-проход базы персонажей: нет фрагментов")
    groups = await _await_each(
        _run_registry_batch(bn, lo, hi, frags, system, trace_dir)
        for bn, lo, hi in queue
    )
    results = [r for g in groups for r in g]
    results.sort(key=lambda r: r[1])
    chars: list = []
    items: list = []
    for bname, lo, hi, bchars, bitems in results:
        _remap_ids(chars, bchars, "c")
        _remap_ids(items, bitems, "p")
        logger.info(
            "scene_skeleton registry {}: frags {}-{} chars_in {} items_in {}",
            bname, lo + 1, hi, len(bchars), len(bitems),
        )
    if not chars:
        raise RuntimeError("Каркас: пре-проход базы персонажей не удался: пустая база персонажей")
    logger.info(
        "scene_skeleton registry: batches={} chars={} items={}",
        len(results), len(chars), len(items),
    )
    return chars, items


def speakers_of(s: dict) -> list[str]:
    import re as _re

    r = s.get("речь")
    txt = r.get("текст") if isinstance(r, dict) else (r if isinstance(r, str) else "")
    out = []
    for seg in _re.split(r"(?:^|\s)[—–-]\s+", str(txt or "")):
        m = _re.match(r"\s*([^:]{1,60}):", seg)
        if m:
            lab = m.group(1).strip()
            if lab and lab not in out:
                out.append(lab)
    return out


def _speaker_ok(label: str, pers: list, chars: list) -> bool:
    by_id = {str(c.get("id")): c for c in chars}
    ln = _norm(label)
    lw = set(ln.split())
    for pid in pers or []:
        c = by_id.get(str(pid))
        if not c:
            continue
        nm = _norm(_split_var(c.get("имя"))[0])
        if not nm:
            continue
        if nm == ln or nm in ln or ln in nm:
            return True
        nw = nm.split()
        if _surname(nm) in lw or (nw and nw[0] in lw):
            return True
        if any(len(w) >= 5 and any(x.startswith(w[:5]) for x in lw) for w in nw):
            return True
    return False


def speaker_problems(s: dict, chars: list) -> list[str]:
    pers = s.get("персонажи") or []
    if isinstance(pers, str):
        pers = [pers]
    return [lab for lab in speakers_of(s) if not _speaker_ok(lab, pers, chars)]


async def run_skeleton_batches(
    frags: list[str], master: str, trace_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    trace_dir.mkdir(parents=True, exist_ok=True)
    system = SYSTEM_PREFIX + (master or "")
    rep: dict[str, Any] = {"batches": [], "zakadr_overwritten": 0, "conflicts": [], "split_after_fail": [],
                           "id_remaps": [], "mode": "registry 1 batch, scenes 3 by boundary fragments",
                           "speaker_rounds": [], "speaker_fail": []}
    chars, items = await run_registry_prepass(frags, master, trace_dir)
    chars0 = json.loads(json.dumps(chars, ensure_ascii=False))
    items0 = json.loads(json.dumps(items, ensure_ascii=False))
    rep["registry"] = {
        "chars": len(chars0),
        "items": len(items0),
        "batches": 1,
        "scene_batches": SCENE_BATCH_COUNT,
    }
    queue = [
        (f"b{bi:02d}", lo, hi)
        for bi, (lo, hi) in enumerate(fragment_ranges(len(frags), SCENE_BATCH_COUNT), 1)
    ]
    groups = await _await_each(
        _run_one_batch(bn, lo, hi, frags, system, trace_dir, chars0, items0, rep)
        for bn, lo, hi in queue
    )
    results = [r for g in groups for r in g]
    results.sort(key=lambda r: r[1])
    by_num: dict[int, dict] = {}
    for bname, lo, hi, d, sc, att in results:
        sc, bad = _merge_batch(bname, lo, hi, d, sc, frags, chars, items, rep, by_num)
        rep["zakadr_overwritten"] += bad
        rep["batches"].append(
            {"batch": bname, "fragments": f"{lo + 1}-{hi}", "scenes_returned": len(sc),
             "attempt": att, "zakadr_not_verbatim": bad}
        )
    # Безымянный говорящий в «речь» id не получает. Повтор сцены из-за этого выключен.
    scenes = [by_num[i + 1] for i in range(len(frags)) if (i + 1) in by_num]
    flag = Path("data/_resend_skeleton_pack.flag")
    if flag.is_file():
        flag.unlink()
    return {"сцены": scenes, "база_персонажей": chars, "предметы": items}, rep


def _merge_batch(bname, lo, hi, d, sc, frags, chars, items, rep, by_num) -> tuple[list, int]:
    mp = _remap_ids(chars, d.get("база_персонажей"), "c")
    mp.update(_remap_ids(items, d.get("предметы"), "p"))
    if mp:
        rep["id_remaps"].append({"batch": bname, "map": mp})
        sc = _apply_map(sc, mp)
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
        by_num[i + 1] = s
    logger.info(
        "scene_skeleton batch {} frags {}-{} returned {} not_verbatim {} chars {} items {}",
        bname, lo + 1, hi, len(sc), bad, len(chars), len(items),
    )
    return sc, bad


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
