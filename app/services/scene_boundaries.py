"""Нода «Границы сцен» (группа script_frames_qc, шаг 1 перед «Каркасом»).

Один вызов claude-opus-5 (vibecode, Anthropic ``/v1/messages``, stream) возвращает
весь закадр дословно с [ ] вокруг фрагментов сцен. Проверка кодом: склейка
фрагментов = закадр (без учёта пробелов и знаков ударения), вне скобок нет
текста, скобки не вложены. Не прошло — один повтор. Промт и логика — как в
``_tmp_skel_m13_opus.py`` (шаг A).

Выход: ``data/videos/<slug>/scene_boundaries/fragments.json`` — фрагменты,
вырезанные из исходного закадра (дословно), + отчёт проверки. «Каркас»
читает их через :func:`load_fragments_for_vo` (одна сцена на фрагмент).
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

URL = "https://vibecode.moe/v1/messages"
MODEL = "claude-opus-5"
MAX_TOK = 64000
PROMPT_REL = "prompts/scene_design/scene_boundaries_agent.md"
OUT_DIRNAME = "scene_boundaries"
OUT_FILENAME = "fragments.json"
_SYS_MARK = "\n=== SYSTEM ===\n"
_USER_MARK = "\n=== USER ===\n"


def nws(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def nz(s: str) -> str:  # без пробелов и без combining-ударения
    return re.sub(r"[\s\u0301]+", "", s or "")


def prompt_path() -> Path:
    from app.project_root import find_project_root

    return find_project_root() / PROMPT_REL


def load_prompt() -> tuple[str, str]:
    """(system, user_template с {{VO}}) из prompts/scene_design/scene_boundaries_agent.md."""
    text = prompt_path().read_text(encoding="utf-8")
    if _SYS_MARK not in text or _USER_MARK not in text:
        raise RuntimeError(f"{PROMPT_REL}: нет маркеров === SYSTEM === / === USER ===")
    body = text.split(_SYS_MARK, 1)[1]
    system, user_t = body.split(_USER_MARK, 1)
    if user_t.endswith("\n"):
        user_t = user_t[:-1]
    if "{{VO}}" not in user_t:
        raise RuntimeError(f"{PROMPT_REL}: в блоке USER нет {{{{VO}}}}")
    return system, user_t


def strip_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def parse_seg(raw: str) -> tuple[list[str], str]:
    s = strip_fence(raw)
    s = s.replace("===ТЕКСТ_НАЧАЛО===", "").replace("===ТЕКСТ_КОНЕЦ===", "")
    frags = re.findall(r"\[(.*?)\]", s, re.S)
    outside = re.sub(r"\[(.*?)\]", "", s, flags=re.S)
    return list(frags), outside


def check_seg(vo: str, frags: list[str], outside: str) -> dict[str, Any]:
    j = "".join(frags)
    strict = nws(j) == nws(vo)
    no_stress = nz(j) == nz(vo)
    info: dict[str, Any] = {
        "strict_ws_equal": strict,
        "equal_ignoring_stress": no_stress,
        "outside_nonspace_chars": len(nws(outside)),
        "fragments": len(frags),
        "nested_brackets": any("[" in f for f in frags),
    }
    if not strict:
        a, b = nws(vo), nws(j)
        i = 0
        while i < min(len(a), len(b)) and a[i] == b[i]:
            i += 1
        info["first_diff_vo"] = a[max(0, i - 40): i + 60]
        info["first_diff_frag"] = b[max(0, i - 40): i + 60]
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        info["diff_chars"] = sum(
            max(i2 - i1, j2 - j1)
            for t, i1, i2, j1, j2 in sm.get_opcodes()
            if t != "equal"
        )
    return info


def project_to_source(vo: str, frags: list[str], key=nz) -> list[str]:
    """Режем ИСХОДНЫЙ закадр по границам фрагментов (по нормализованной длине)."""
    idx = [i for i, ch in enumerate(vo) if key(ch)]
    norm_total = len(idx)
    out, pos, cum = [], 0, 0
    for k, f in enumerate(frags):
        cum += len(key(f))
        if k == len(frags) - 1:
            end = len(vo)
        else:
            end = idx[min(cum, norm_total) - 1] + 1 if cum > 0 else 0
            while end < len(vo) and vo[end] == "\u0301":
                end += 1
        out.append(vo[pos:end].strip())
        pos = end
    return out


async def _stream_once(
    payload: dict, headers: dict, url: str = URL, read_timeout: float = 900.0
) -> tuple[str, dict]:
    import httpx

    parts: list[str] = []
    meta: dict[str, Any] = {"finish_reason": None, "usage": None, "model": None, "sse_lines": 0}
    t0 = time.time()
    timeout = httpx.Timeout(connect=30.0, read=read_timeout, write=120.0, pool=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            async with c.stream("POST", url, headers=headers, json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")
                    raise RuntimeError(f"HTTP {r.status_code}: {body[:600]}")
                async for line in r.aiter_lines():
                    if line:
                        meta["sse_lines"] += 1
                    if not line.startswith("data:"):
                        continue
                    d = line[5:].strip()
                    if not d or d == "[DONE]":
                        continue
                    try:
                        ev = json.loads(d)
                    except Exception:  # noqa: BLE001
                        continue
                    t = ev.get("type")
                    if t == "message_start":
                        m = ev.get("message") or {}
                        meta["model"] = m.get("model") or meta["model"]
                        if m.get("usage"):
                            meta["usage"] = dict(m["usage"])
                    elif t == "content_block_delta":
                        dl = ev.get("delta") or {}
                        if dl.get("type") == "text_delta":
                            parts.append(dl.get("text") or "")
                    elif t == "message_delta":
                        meta["finish_reason"] = (ev.get("delta") or {}).get("stop_reason") or meta["finish_reason"]
                        if ev.get("usage"):
                            meta["usage"] = {**(meta["usage"] or {}), **ev["usage"]}
                    elif t == "error":
                        raise RuntimeError("stream error event: " + json.dumps(ev, ensure_ascii=False)[:500])
    except (httpx.HTTPError, OSError) as e:
        txt = "".join(parts)
        dt = round(time.time() - t0, 1)
        if not txt.strip():
            raise RuntimeError(f"empty output after {dt}s; network {type(e).__name__}: {e}") from e
        raise RuntimeError(f"stream broken after {dt}s with {len(txt)} chars: {type(e).__name__}: {e}") from e
    return "".join(parts), meta


def _api_key() -> str:
    from app.settings import settings

    key = (settings.vibecode_api_key or "").strip()
    if not key:
        raise RuntimeError("VIBECODE_API_KEY пуст — задай ключ vibecode.moe в .env")
    return key


async def call_opus(user: str, system: str, tag: str, trace_dir: Path, tries: int = 4) -> str:
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / f"{tag}.req.txt").write_text(
        f"=== MODEL: {MODEL}\n=== URL: {URL}\n=== SYSTEM ===\n{system}\n=== USER ===\n{user}\n",
        encoding="utf-8",
    )
    key = _api_key()
    headers = {
        "x-api-key": key,
        "Authorization": f"Bearer {key}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": MAX_TOK,
        "stream": True,
        "system": system,
        "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
    }
    last: Exception | None = None
    for k in range(tries):
        t0 = time.time()
        try:
            txt, meta = await _stream_once(payload, headers)
            dt = round(time.time() - t0, 1)
            if not txt.strip():
                raise RuntimeError(f"empty output after {dt}s; finish={meta['finish_reason']}")
            logger.info(
                "scene_boundaries {}: ok chars={} sec={} finish={} model={} usage={}",
                tag, len(txt), dt, meta["finish_reason"], meta["model"], meta["usage"],
            )
            (trace_dir / f"{tag}.resp.txt").write_text(txt, encoding="utf-8")
            return txt
        except Exception as e:  # noqa: BLE001
            last = e
            msg = f"{type(e).__name__}: {e}"
            logger.warning("scene_boundaries {} try{} failed: {}", tag, k, msg[:500])
            (trace_dir / f"{tag}.err{k}.txt").write_text(msg, encoding="utf-8")
            if "HTTP 400" in msg and "max_tokens" in msg and payload["max_tokens"] > 32000:
                payload["max_tokens"] = 32000
                continue
            if "HTTP 4" in msg and not any(x in msg for x in ("429", "408")):
                raise
            if k < tries - 1:
                await asyncio.sleep(15 * (k + 1))
    assert last is not None
    raise last


async def run_boundaries(vo: str, trace_dir: Path) -> tuple[list[str], dict[str, Any]]:
    """Шаг A: разметка + проверка кодом, при провале один повтор."""
    system, user_t = load_prompt()
    user = user_t.replace("{{VO}}", vo)
    tries: list[dict[str, Any]] = []
    raws: list[str] = []
    best: tuple[list[str], int] | None = None
    for att in (1, 2):
        raw = await call_opus(user, system, f"A_seg_a{att}", trace_dir)
        raws.append(raw)
        frags, outside = parse_seg(raw)
        info = check_seg(vo, frags, outside)
        info["attempt"] = att
        tries.append(info)
        logger.info("scene_boundaries attempt {}: {}", att, json.dumps(info, ensure_ascii=False)[:800])
        if info["strict_ws_equal"] and not info["outside_nonspace_chars"] and not info["nested_brackets"]:
            best = (frags, att)
            break
        if best is None and info["equal_ignoring_stress"] and not info["outside_nonspace_chars"]:
            best = (frags, att)
    rep: dict[str, Any] = {
        "tries": tries,
        "passed_strict": bool(tries[-1]["strict_ws_equal"] and not tries[-1]["outside_nonspace_chars"]),
        "retried": len(tries) > 1,
    }
    if best is None:
        # последний шанс: границы по diff (отчёт = провал проверки)
        frags = parse_seg(raws[-1])[0]
        rep["fallback"] = "difflib boundary alignment"
        a = nz(vo)
        b = nz("".join(frags))
        sm = difflib.SequenceMatcher(None, b, a, autojunk=False)
        ends, cum = [], 0
        for f in frags:
            cum += len(nz(f))
            ends.append(cum)

        def mapb(x: int) -> int:
            for _t, i1, i2, j1, j2 in sm.get_opcodes():
                if i1 <= x <= i2:
                    return j1 + min(x - i1, j2 - j1)
            return len(a)

        mapped = [mapb(e) for e in ends]
        if mapped:
            mapped[-1] = len(a)
        lens = [mapped[0]] + [mapped[i] - mapped[i - 1] for i in range(1, len(mapped))] if mapped else []
        pseudo = ["x" * max(L, 0) for L in lens]
        src = project_to_source(vo, pseudo, key=nz) if pseudo else []
    else:
        frags, att = best
        rep["attempt_used"] = att
        src = project_to_source(vo, frags, key=nz)
    src = [s for s in src if s.strip()]
    rep["source_join_ok"] = nz("".join(src)) == nz(vo) and nws("".join(src)) == nws(vo)
    if best is not None:
        rep["fragments_differing_from_source_ws"] = sum(
            1 for f, s in zip(best[0], src, strict=False) if nws(f) != nws(s)
        )
    rep["check_passed"] = bool(
        best is not None
        and rep["source_join_ok"]
        and not any(t["nested_brackets"] for t in tries if t["attempt"] == best[1])
    )
    return src, rep


def fragment_stats(frags: list[str]) -> dict[str, Any]:
    L = [len(f) for f in frags]
    return {
        "fragments": len(L),
        "avg_chars": round(sum(L) / max(len(L), 1), 1),
        "max_chars": max(L) if L else 0,
        "over_300": sum(1 for x in L if x > 300),
        "over_350": sum(1 for x in L if x > 350),
    }


def output_path(project: Any) -> Path:
    return Path(project.data_dir) / OUT_DIRNAME / OUT_FILENAME


def save_output(project: Any, *, node_key: str, vo: str, frags: list[str], rep: dict[str, Any]) -> Path:
    path = output_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "node_key": node_key,
        "model": MODEL,
        "url": URL,
        "prompt": PROMPT_REL,
        "created_at": datetime.now(UTC).isoformat(),
        "vo_chars": len(vo),
        "vo_nz_len": len(nz(vo)),
        "stats": fragment_stats(frags),
        "check": rep,
        "fragments": frags,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_fragments_for_vo(project: Any, vo: str) -> list[str] | None:
    """Фрагменты «Границ сцен», если проверка прошла и они покрывают текущий закадр."""
    path = output_path(project)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    frags = [str(f) for f in (data.get("fragments") or []) if str(f).strip()]
    if not frags or not (data.get("check") or {}).get("check_passed"):
        return None
    if nz("".join(frags)) != nz(vo):
        logger.warning(
            "scene_boundaries: {} не совпадает с текущим закадром — Каркас идёт без фрагментов",
            path,
        )
        return None
    return frags
