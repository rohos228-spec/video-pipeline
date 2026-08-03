"""Контракт проверки GPT-ноды: TXT-отчёт + fallback JSON vp.check.v1.

Основной deliverable — check_report.txt. JSON vp.check.v1 остаётся
внутренним fallback (старые промты / analysis.json).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SCHEMA_ID = "vp.check.v1"
CHECK_REPORT_NAME = "check_report.txt"

Verdict = Literal["pass", "fail"]
ForwardMode = Literal["inherit", "explicit"]
FixTarget = Literal["source", "xlsx", "prompt", "none"]
CheckFixMode = Literal["fix", "report_only"]

# Хвост для промтов проверочных нод — модель обязана ответить только JSON.
RESPONSE_FOOTER = """
---
ФОРМАТ ОТВЕТА (строго): ОДИН JSON-объект vp.check.v1.
ЗАПРЕЩЕНО: markdown, текст до/после JSON, TSV/CSV таблиц, «исправленный Excel текстом».
Отчёт = этот JSON (система сама положит его в gpt_reply.txt + analysis.json).
Данные файла НЕ пиши в отчёт — файл передаётся отдельно (вход или rewrite_file).

{
  "schema": "vp.check.v1",
  "verdict": "pass" | "fail",
  "summary": "краткий отчёт: что проверил / что поправил",
  "checks": [{"id": "имя", "ok": true|false, "note": "…"}],
  "forward": {"mode": "inherit" | "explicit", "paths": ["относительный/путь"]},
  "fix": {
    "target": "source" | "xlsx" | "prompt" | "none",
    "instructions": "что сделано / что осталось",
    "rewrite_file": null
  }
}

Правила:
- Без правок: verdict=pass|fail, forward.mode=inherit, fix.target=none, rewrite_file=null
  → дальше уйдёт тот же входной файл + отчёт (если включено «Текст .txt» / «Проверка»).
- С правками в ЭТОЙ ноде: сохрани исправленный файл на диск и укажи
  fix.rewrite_file="относительный/путь/к/файлу.xlsx" (от корня проекта),
  forward.mode можно оставить inherit — приоритет у rewrite_file.
- Никогда не вставляй содержимое xlsx/TSV внутрь JSON или рядом с ним.
""".strip()

# Основной формат для тумблера «Проверка» — читаемый TXT.
CHECK_WRITEBACK_MARKER = "--- XLSX_WRITEBACK ---"

TXT_REPORT_FOOTER = """
---
ФОРМАТ ОТВЕТА (строго): один текстовый отчёт на русском.
НЕ JSON. Не пиши содержимое xlsx/TSV внутрь секций отчёта.

ВАЖНО ПРО ФАЙЛЫ:
- Во вложении — текстовый экспорт книги (TSV по листам `# Лист:`). Это и есть
  исходный project.xlsx для проверки. Бинарный .xlsx в рабочей директории
  модели недоступен — это нормально, НЕ ставь fail из‑за «нет бинарного xlsx».
- Проверяй полный лист «Общий план» / «Общий план ролика» по TSV во вложении.
- Если во вложении явно написано «обрезано» по нужному листу — тогда [error]
  про неполноту входа; иначе считай TSV полным снимком.

Шаблон (все секции обязательны, в этом порядке):

# ОТЧЁТ ПРОВЕРКИ
verdict: pass|fail
mode: fix|report_only
source_prompts: <nodeKey[, nodeKey…]>

## summary
2–4 предложения: итог.

## analysis
Что проверяли и по каким правилам из исходных промтов; что увидели в файле.

## findings
- [error] …
- [warn] …

## related
- <finding> → промт:<узел> | лист:… | строка:… | поле:…

## logic
Что логично / согласовано.
Что странно или противоречит.

## actions
Что сделано в этой ноде.
Что осталось.

## forward
file: original|fixed
path: <относительный путь от корня проекта или —>

Правила:
- mode=report_only → file: original, path: —, файл НЕ меняй.
- mode=fix без правок → file: original, path: —.
- mode=fix и нужна правка → после всего отчёта (после ## forward) добавь блок:

{marker}
# Лист: Общий план
<строки TSV через табуляцию — только изменённые листы>
и в ## forward укажи file: fixed, path: excel_gpt_uploads/<эта_нода>/project_fixed.xlsx
Система сама наложит TSV на копию книги. Остальные листы не трогай.
""".strip().format(marker=CHECK_WRITEBACK_MARKER)

_SECTION_RE = re.compile(
    r"(?m)^##\s+(summary|analysis|findings|related|logic|actions|forward|"
    r"scores|issues|regen_heroes|regen_frames|db_patch)\s*$"
)
_HEADER_VERDICT_RE = re.compile(r"(?im)^\s*verdict\s*:\s*(\S+)\s*$")
_HEADER_MODE_RE = re.compile(r"(?im)^\s*mode\s*:\s*(\S+)\s*$")
_HEADER_SOURCES_RE = re.compile(r"(?im)^\s*source_prompts\s*:\s*(.+?)\s*$")
_FORWARD_FILE_RE = re.compile(r"(?im)^\s*file\s*:\s*(\S+)\s*$")
_FORWARD_PATH_RE = re.compile(r"(?im)^\s*path\s*:\s*(.+?)\s*$")
_FINDING_RE = re.compile(r"(?m)^\s*[-*]\s*\[(error|warn|ok|pass|fail)\]\s*(.+?)\s*$")
_REGEN_LINE_RE = re.compile(r"(?im)^\s*regen\s*:\s*(.+?)\s*$")
_HERO_EXCEL_ID_RE = re.compile(r"\bc(\d{1,3})\b", re.IGNORECASE)
_HERO_FILE_ID_RE = re.compile(
    r"\b(c\d{1,3})\.(?:png|jpe?g|webp|gif)\b", re.IGNORECASE
)

# Подсказка в хвост отчёта для check после hero (vision → точечный переген).
HERO_REGEN_REPORT_HINT = """
## regen (обязательно при verdict: fail после ноды «Персонажи»)
Если какие-то reference-картинки плохие — перечисли их excel_id одной строкой:
regen: c01, c03
Только id персонажей из листа «Персонажи» / имён файлов characters/cXX.png.
Если все ок — строку regen не пиши.
""".strip()

# Порог pass для vision-check (оси 0..1). Regen только при critical.
VISION_PASS_THRESHOLD = 0.7
VISION_SCORE_AXES = (
    "style",
    "character",
    "format",
    "pose",
    "clones",
    "text",
    "angles",
    "quality",
    "hands",
)

# Единый хвост для vision-check (hero + scenes): scores + severity + regen.
VISION_CHECK_REPORT_HINT = """
## vision_check (обязательно при проверке PNG)
Сверяй каждое изображение с блоком «## База (source of truth)» во входе.
Не выдумывай поля — только БД + видимое на картинке.
Имена файлов: c01.png / frame_003_….png — только так ссылайся.

Оси score 0.0–1.0 (overall = взвешенное из агента, иначе среднее осей):
- style — единый визуальный стиль; сильный уход стиля = низкий score / critical
- character — identity vs База (лицо/тело/одежда)
- format — формат sheet/кадра (16:9 turnaround, белый фон hero и т.п.)
- pose — позиции/раскладка по формату; для не-человека/существа —
  допустимы иные позы, если промт/База это подразумевают
- clones — если в Базе несколько персонажей/клонов: различия (одежда,
  возраст, детали) должны быть читаемы на картинке и согласованы с промтом
- text — текст только в мире героя/сцены (вывеска, книга, этикетка);
  текст «в воздухе», подписи панелей, watermark вне контекста — брак
- angles — разные стороны (hero turnaround; клоны одного ракурса — плохо)
- quality — общий брак (лицо, глаза, артефакты, склейки)
- hands — руки/пальцы/конечности; грубый брак рук = critical

SEVERITY:
- critical → regen (только это)
- warning / minor → НЕ regen
pass = overall >= 0.70 И нет critical.

После обычных секций отчёта обязательно:

## scores
style: 0.85
character: 0.90
format: 0.90
pose: 0.85
clones: 0.80
text: 0.90
angles: 0.80
quality: 0.85
hands: 0.80
overall: 0.85

## issues
- [critical] c01: …
- [warning] frame_003_xxx.png: …

## regen_heroes
regen: c01

## regen_frames
frames: 3, 7s2

## db_patch
```json
{"characters":[{"id":"c02","внешность":"..."}],"ops":[{"frame_uuid":"<uuid>","fields":{"промт_картинки":"..."}}]}
```
Без critical — regen_* / db_patch не пиши. Без воды вне секций.
""".strip()

_FRAMES_LINE_RE = re.compile(r"(?im)^\s*frames\s*:\s*(.+?)\s*$")
_FRAME_TOKEN_RE = re.compile(
    r"^(?:frame[_-]?)?(\d{1,4})(?:[_-]?(?:s2|shot2|02))?$",
    re.IGNORECASE,
)
_FRAME_FILE_RE = re.compile(
    r"\bframe[_-]?(\d{1,4})(?:[_-]s2[_-][^.\s]+|[_-]?(?:s2|shot2|02))?[_-]?[^.\s]*\.(?:png|jpe?g|webp|gif)\b",
    re.IGNORECASE,
)
_DB_PATCH_FENCE_RE = re.compile(
    r"(?is)##\s*db_patch\s*\n\s*```(?:json)?\s*(\{.*?\})\s*```",
)
_DB_PATCH_BARE_RE = re.compile(
    r"(?is)##\s*db_patch\s*\n\s*(\{[^\n].*?\})\s*(?=\n##|\n# |\Z)",
)


def normalize_hero_excel_id(raw: str) -> str | None:
    """c1 / C01 / c01.png → c01."""
    s = (raw or "").strip()
    if not s:
        return None
    s = Path(s).stem if "." in s else s
    m = re.match(r"^c(\d{1,3})$", s, re.IGNORECASE)
    if not m:
        return None
    return f"c{int(m.group(1)):02d}"


def extract_hero_regen_ids(text: str) -> list[str]:
    """Список excel_id персонажей для перегена из check_report / reply.

    Сначала явная строка ``regen: c01, c03``, затем эвристика по findings/actions.
    """
    raw = text or ""
    found: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        nid = normalize_hero_excel_id(token)
        if nid and nid not in seen:
            seen.add(nid)
            found.append(nid)

    for m in _REGEN_LINE_RE.finditer(raw):
        for part in re.split(r"[,;/\s]+", m.group(1) or ""):
            part = part.strip().strip(".,;:)")
            if part:
                _add(part)
    if found:
        return found

    bodies = _section_bodies(raw) if looks_like_check_report_txt(raw) else {}
    if not bodies:
        # Сырой reply без секций: только явные cXX.png рядом с fail/regen.
        low = raw.lower()
        if any(w in low for w in ("regen", "переген", "fail", "error", "брак", "плохо")):
            for m in _HERO_FILE_ID_RE.finditer(raw):
                _add(m.group(1))
            for hm in _HERO_EXCEL_ID_RE.finditer(raw):
                _add(hm.group(0))
        return found

    for section_name in ("findings", "related", "actions", "summary"):
        section = bodies.get(section_name) or ""
        if section_name == "findings":
            for fm in _FINDING_RE.finditer(section):
                if fm.group(1).lower() in ("error", "fail", "warn"):
                    for m in _HERO_FILE_ID_RE.finditer(fm.group(2)):
                        _add(m.group(1))
                    for hm in _HERO_EXCEL_ID_RE.finditer(fm.group(2)):
                        _add(hm.group(0))
        else:
            low = section.lower()
            if any(
                w in low
                for w in ("regen", "переген", "плохо", "брак", "error", "fail", "не ок")
            ):
                for m in _HERO_FILE_ID_RE.finditer(section):
                    _add(m.group(1))
                for hm in _HERO_EXCEL_ID_RE.finditer(section):
                    _add(hm.group(0))
    return found


def append_hero_regen_hint(prompt: str) -> str:
    """Дописать правило regen: cXX в хвост check-промта (hero upstream)."""
    base = (prompt or "").rstrip()
    if "regen:" in base.lower() and "c01" in base.lower():
        return base
    if not base:
        return HERO_REGEN_REPORT_HINT
    return f"{base}\n\n{HERO_REGEN_REPORT_HINT}"


def append_vision_check_hint(prompt: str) -> str:
    """Дописать единый vision_check хвост (hero + scenes)."""
    base = (prompt or "").rstrip()
    low = base.lower()
    if (
        "vision_check" in low
        and "db_patch" in low
        and "## scores" in low
        and "critical" in low
    ):
        return base
    if not base:
        return VISION_CHECK_REPORT_HINT
    return f"{base}\n\n{VISION_CHECK_REPORT_HINT}"


def normalize_frame_regen_token(raw: str) -> dict[str, Any] | None:
    """``3`` / ``7s2`` / ``frame_012_s2.png`` → ``{number, shot}``."""
    s = (raw or "").strip().strip(".,;:)")
    if not s:
        return None
    # file-like: frame_003_abcd.png / frame_003_s2_uuid.png
    fm = re.search(
        r"frame[_-]?(\d{1,4})(?:[_-]s2|_s2_|[_-]?(?:s2|shot2))?",
        s,
        re.IGNORECASE,
    )
    if fm and re.search(r"\.(?:png|jpe?g|webp|gif)\b", s, re.IGNORECASE):
        num = int(fm.group(1))
        shot = 2 if re.search(r"(?:_s2_|s2|shot2)", s, re.IGNORECASE) else 1
        return {"number": num, "shot": shot}
    stem = Path(s).stem if "." in s else s
    stem = stem.strip()
    # 12s2 / 12_s2 / 12-shot2
    m2 = re.match(
        r"^(?:frame[_-]?)?(\d{1,4})(?:[_-]?(?:s2|shot2|02))$",
        stem,
        re.IGNORECASE,
    )
    if m2:
        return {"number": int(m2.group(1)), "shot": 2}
    m1 = re.match(r"^(?:frame[_-]?)?(\d{1,4})$", stem, re.IGNORECASE)
    if m1:
        return {"number": int(m1.group(1)), "shot": 1}
    return None


def extract_frame_regen_targets(text: str) -> list[dict[str, Any]]:
    """Список ``{number, shot}`` для перегена сцен из check_report."""
    raw = text or ""
    found: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()

    def _add(token: str) -> None:
        t = normalize_frame_regen_token(token)
        if not t:
            return
        key = (int(t["number"]), int(t["shot"]))
        if key in seen:
            return
        seen.add(key)
        found.append({"number": key[0], "shot": key[1]})

    for m in _FRAMES_LINE_RE.finditer(raw):
        for part in re.split(r"[,;/\s]+", m.group(1) or ""):
            part = part.strip()
            if part:
                _add(part)
    if found:
        return found

    # Heuristic: fail findings mentioning frame_NNN_*.png
    bodies = _section_bodies(raw) if looks_like_check_report_txt(raw) else {}
    chunks: list[str] = []
    if bodies:
        for name in ("findings", "related", "actions", "summary"):
            section = bodies.get(name) or ""
            if name == "findings":
                for fm in _FINDING_RE.finditer(section):
                    if fm.group(1).lower() in ("error", "fail", "warn"):
                        chunks.append(fm.group(2))
            else:
                low = section.lower()
                if any(
                    w in low
                    for w in ("regen", "переген", "плохо", "брак", "error", "fail")
                ):
                    chunks.append(section)
    else:
        chunks.append(raw)
    for chunk in chunks:
        for m in re.finditer(
            r"\bframe[_-]?\d{1,4}[^\s]*\.(?:png|jpe?g|webp|gif)\b",
            chunk,
            re.IGNORECASE,
        ):
            _add(m.group(0))
    return found


def extract_db_patch(text: str) -> dict[str, Any] | None:
    """JSON из секции ``## db_patch`` → ``{characters?, ops?}`` или None."""
    raw = text or ""
    blob: str | None = None
    m = _DB_PATCH_FENCE_RE.search(raw)
    if m:
        blob = m.group(1)
    else:
        m2 = _DB_PATCH_BARE_RE.search(raw)
        if m2:
            blob = m2.group(1)
    if not blob:
        # fallback: fenced JSON containing characters/ops near db_patch
        if "db_patch" not in raw.lower():
            return None
        for fm in _JSON_FENCE.finditer(raw):
            cand = fm.group(1)
            if '"characters"' in cand or '"ops"' in cand:
                blob = cand
                break
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    chars = data.get("characters")
    ops = data.get("ops")
    out: dict[str, Any] = {}
    if isinstance(chars, list) and chars:
        out["characters"] = chars
    if isinstance(ops, list) and ops:
        # normalize short form → target=frame
        norm_ops: list[dict[str, Any]] = []
        for op in ops:
            if not isinstance(op, dict):
                continue
            op2 = dict(op)
            if "frame_uuid" in op2 and "target" not in op2:
                op2["target"] = "frame"
            norm_ops.append(op2)
        if norm_ops:
            out["ops"] = norm_ops
    return out or None


_SCORE_LINE_RE = re.compile(
    r"(?im)^\s*(style|character|format|pose|clones|text|angles|quality|"
    r"hands|overall)\s*[:=]\s*"
    r"(0(?:\.\d+)?|1(?:\.0+)?|\.\d+)\s*$"
)
_ISSUE_LINE_RE = re.compile(
    r"(?im)^\s*[-*]\s*\[(critical|warning|minor|warn|error|fail)\]\s*(.+?)\s*$"
)


def _clamp_score(raw: float) -> float:
    if raw < 0.0:
        return 0.0
    if raw > 1.0:
        return 1.0
    return float(raw)


def extract_vision_scores(text: str) -> dict[str, float]:
    """Оси character/format/text/angles/overall из секции ``## scores``."""
    raw = text or ""
    found: dict[str, float] = {}
    # Prefer body under ## scores if present.
    bodies = _section_bodies(raw) if looks_like_check_report_txt(raw) else {}
    chunk = bodies.get("scores") or raw
    for m in _SCORE_LINE_RE.finditer(chunk):
        key = m.group(1).lower()
        try:
            found[key] = _clamp_score(float(m.group(2)))
        except ValueError:
            continue
    if "overall" not in found:
        axes = [found[a] for a in VISION_SCORE_AXES if a in found]
        if axes:
            found["overall"] = _clamp_score(sum(axes) / len(axes))
    return found


def _norm_issue_severity(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in ("critical", "error", "fail"):
        return "critical"
    if s in ("warning", "warn"):
        return "warning"
    if s == "minor":
        return "minor"
    return s or "warning"


def extract_vision_issues(text: str) -> list[dict[str, Any]]:
    """Список ``{severity, text}`` из ``## issues`` (и findings с severity-тегами)."""
    raw = text or ""
    out: list[dict[str, Any]] = []
    bodies = _section_bodies(raw) if looks_like_check_report_txt(raw) else {}
    chunks: list[str] = []
    if bodies.get("issues"):
        chunks.append(bodies["issues"])
    # Also accept severity tags in findings when scores section exists.
    if bodies.get("findings") and (
        bodies.get("scores") or extract_vision_scores(raw)
    ):
        chunks.append(bodies["findings"])
    if not chunks:
        # Bare ## issues without full report header.
        m = re.search(r"(?is)##\s*issues\s*\n(.*?)(?=\n##|\n# |\Z)", raw)
        if m:
            chunks.append(m.group(1))
        elif re.search(r"(?i)\[(critical|warning|minor)\]", raw):
            chunks.append(raw)
    seen: set[str] = set()
    for chunk in chunks:
        for m in _ISSUE_LINE_RE.finditer(chunk or ""):
            sev = _norm_issue_severity(m.group(1))
            body = (m.group(2) or "").strip()
            if not body:
                continue
            key = f"{sev}|{body.lower()}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"severity": sev, "text": body})
    return out


def has_critical_vision_issues(text: str) -> bool:
    return any(i.get("severity") == "critical" for i in extract_vision_issues(text))


def looks_like_scored_vision_report(text: str) -> bool:
    """Отчёт с scores и/или severity-issues — применяем порог + critical-only regen."""
    raw = text or ""
    if extract_vision_scores(raw):
        return True
    if re.search(r"(?i)\[(critical|warning|minor)\]", raw):
        return True
    if re.search(r"(?im)^\s*##\s*scores\s*$", raw):
        return True
    return False


def resolve_vision_check_gate(
    text: str,
    *,
    threshold: float = VISION_PASS_THRESHOLD,
) -> str | None:
    """pass|fail по scores+critical, или None если отчёт не scored-vision."""
    if not looks_like_scored_vision_report(text):
        return None
    scores = extract_vision_scores(text)
    critical = has_critical_vision_issues(text)
    if critical:
        return "fail"
    overall = scores.get("overall")
    if overall is None:
        # severity-only report: no critical → pass
        return "pass"
    return "pass" if float(overall) >= float(threshold) else "fail"


def apply_vision_score_gate(analysis: "CheckAnalysis", text: str) -> "CheckAnalysis":
    """Переписать verdict по scores/severity, если отчёт scored-vision."""
    resolved = resolve_vision_check_gate(text)
    if resolved is None:
        return analysis
    if analysis.verdict == resolved:
        return analysis
    old = analysis.verdict
    analysis.verdict = resolved  # type: ignore[assignment]
    note = (
        f"vision_gate: {old}→{resolved} "
        f"(threshold={VISION_PASS_THRESHOLD}, critical="
        f"{has_critical_vision_issues(text)})"
    )
    summary = (analysis.summary or "").strip()
    analysis.summary = f"{summary}\n{note}".strip() if summary else note
    return analysis


def extract_critical_hero_regen_ids(text: str) -> list[str]:
    """Hero ids для перегена: только critical (или legacy без scores)."""
    raw = text or ""
    if not looks_like_scored_vision_report(raw):
        return extract_hero_regen_ids(raw)
    if not has_critical_vision_issues(raw):
        return []
    found: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        nid = normalize_hero_excel_id(token)
        if nid and nid not in seen:
            seen.add(nid)
            found.append(nid)

    for issue in extract_vision_issues(raw):
        if issue.get("severity") != "critical":
            continue
        body = str(issue.get("text") or "")
        for m in _HERO_EXCEL_ID_RE.finditer(body):
            _add(m.group(0))
        for m in _HERO_FILE_ID_RE.finditer(body):
            _add(m.group(1))
    if found:
        return found
    return extract_hero_regen_ids(raw)


def extract_critical_frame_regen_targets(text: str) -> list[dict[str, Any]]:
    """Frame targets для перегена: только critical (или legacy без scores)."""
    raw = text or ""
    if not looks_like_scored_vision_report(raw):
        return extract_frame_regen_targets(raw)
    if not has_critical_vision_issues(raw):
        return []
    found: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()

    def _add(token: str) -> None:
        t = normalize_frame_regen_token(token)
        if not t:
            return
        key = (int(t["number"]), int(t["shot"]))
        if key in seen:
            return
        seen.add(key)
        found.append({"number": key[0], "shot": key[1]})

    for issue in extract_vision_issues(raw):
        if issue.get("severity") != "critical":
            continue
        body = str(issue.get("text") or "")
        for m in re.finditer(
            r"\bframe[_-]?\d{1,4}[^\s]*\.(?:png|jpe?g|webp|gif)\b",
            body,
            re.IGNORECASE,
        ):
            _add(m.group(0))
        for m in re.finditer(
            r"\b(\d{1,4})(?:[_-]?(?:s2|shot2))\b|\b(\d{1,4})\b",
            body,
            re.IGNORECASE,
        ):
            token = m.group(0)
            # avoid bare years / noise: only short nums near frame words or Ns2
            if "s2" in token.lower() or "shot" in token.lower():
                _add(token)
            elif re.search(r"(?i)frame|кадр", body):
                _add(token)
    if found:
        return found
    return extract_frame_regen_targets(raw)


_JSON_FENCE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.IGNORECASE | re.DOTALL,
)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class CheckItem:
    id: str
    ok: bool
    note: str = ""


@dataclass
class ForwardSpec:
    mode: ForwardMode = "inherit"
    paths: list[str] = field(default_factory=list)


@dataclass
class FixSpec:
    target: FixTarget = "none"
    instructions: str = ""
    rewrite_file: str | None = None


@dataclass
class CheckAnalysis:
    schema: str = SCHEMA_ID
    verdict: Verdict = "fail"
    summary: str = ""
    checks: list[CheckItem] = field(default_factory=list)
    forward: ForwardSpec = field(default_factory=ForwardSpec)
    fix: FixSpec = field(default_factory=FixSpec)
    raw_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.verdict == "pass" and self.raw_error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "verdict": self.verdict,
            "summary": self.summary,
            "checks": [asdict(c) for c in self.checks],
            "forward": asdict(self.forward),
            "fix": asdict(self.fix),
            **({"raw_error": self.raw_error} if self.raw_error else {}),
        }


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v or "").strip().lower()
    if s in ("1", "true", "yes", "ok", "pass", "одобрено"):
        return True
    if s in ("0", "false", "no", "fail", "не ок", "неок"):
        return False
    return default


def _norm_verdict(raw: Any) -> Verdict:
    s = str(raw or "").strip().lower()
    if s in ("pass", "ok", "одобрено", "approve", "approved", "true", "1"):
        return "pass"
    return "fail"


def _norm_forward_mode(raw: Any) -> ForwardMode:
    s = str(raw or "").strip().lower()
    if s == "explicit":
        return "explicit"
    return "inherit"


def _norm_fix_target(raw: Any) -> FixTarget:
    s = str(raw or "").strip().lower()
    if s in ("source", "xlsx", "prompt", "none"):
        return s  # type: ignore[return-value]
    return "none"


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Достать JSON-объект из сырого ответа модели."""
    raw = (text or "").strip()
    if not raw:
        return None
    # Прямой JSON
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # fenced ```json ... ```
    m = _JSON_FENCE.search(raw)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # первый {...}
    m2 = _JSON_OBJECT.search(raw)
    if m2:
        chunk = m2.group(0)
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            # урезать хвост до последней }
            last = chunk.rfind("}")
            if last > 0:
                try:
                    obj = json.loads(chunk[: last + 1])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    pass
    return None


def analysis_from_dict(data: dict[str, Any]) -> CheckAnalysis:
    checks_raw = data.get("checks") if isinstance(data.get("checks"), list) else []
    checks: list[CheckItem] = []
    for item in checks_raw:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or item.get("name") or "check").strip() or "check"
        checks.append(
            CheckItem(
                id=cid,
                ok=_as_bool(item.get("ok"), default=False),
                note=str(item.get("note") or item.get("message") or "").strip(),
            )
        )
    fwd_raw = data.get("forward") if isinstance(data.get("forward"), dict) else {}
    paths = fwd_raw.get("paths") if isinstance(fwd_raw.get("paths"), list) else []
    forward = ForwardSpec(
        mode=_norm_forward_mode(fwd_raw.get("mode")),
        paths=[str(p).strip().replace("\\", "/") for p in paths if str(p).strip()],
    )
    fix_raw = data.get("fix") if isinstance(data.get("fix"), dict) else {}
    rewrite = fix_raw.get("rewrite_file")
    fix = FixSpec(
        target=_norm_fix_target(fix_raw.get("target")),
        instructions=str(fix_raw.get("instructions") or "").strip(),
        rewrite_file=str(rewrite).strip() if rewrite else None,
    )
    schema = str(data.get("schema") or SCHEMA_ID).strip() or SCHEMA_ID
    verdict = _norm_verdict(data.get("verdict"))
    # если есть явные checks и все false при pass — всё равно уважаем verdict
    return CheckAnalysis(
        schema=schema,
        verdict=verdict,
        summary=str(data.get("summary") or "").strip(),
        checks=checks,
        forward=forward,
        fix=fix,
    )


def fail_analysis(reason: str) -> CheckAnalysis:
    return CheckAnalysis(
        verdict="fail",
        summary=reason,
        checks=[CheckItem(id="parse", ok=False, note=reason)],
        fix=FixSpec(target="source", instructions=reason),
        raw_error=reason,
    )


def looks_like_check_report_txt(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if "# отчёт проверки" in low or "# отчет проверки" in low:
        return True
    if _HEADER_VERDICT_RE.search(raw) and "## summary" in low:
        return True
    return False


def looks_like_check_payload(text: str) -> bool:
    """True, если текст — отчёт проверки (TXT/JSON), а не закадровый текст.

    Нужен guard при записи voiceover.txt: иначе ответ check-ноды
    попадает в закадр и ломает разбивку.
    """
    if looks_like_check_report_txt(text):
        return True
    raw = (text or "").strip()
    if not raw:
        return False
    obj = extract_json_object(raw)
    if not isinstance(obj, dict):
        return False
    if str(obj.get("schema") or "").strip() == SCHEMA_ID:
        return True
    if "verdict" in obj and ("checks" in obj or "fix" in obj or "forward" in obj):
        return True
    decision = str(obj.get("decision") or "").strip()
    if decision and (
        "criteria" in obj or "issues" in obj or "red_flags" in obj or "checks" in obj
    ):
        return True
    return False


def split_check_reply_and_writeback(text: str) -> tuple[str, str]:
    """Отделить TXT-отчёт от TSV writeback-хвоста.

    Returns (report_text, writeback_text). writeback может быть пустым.
    """
    raw = text or ""
    marker = CHECK_WRITEBACK_MARKER
    idx = raw.find(marker)
    if idx >= 0:
        return raw[:idx].rstrip(), raw[idx + len(marker) :].lstrip()
    # Без маркера: TSV `# Лист:` после секции forward
    m = re.search(r"(?im)^##\s+forward\s*$", raw)
    if m:
        rest = raw[m.end() :]
        sheet = re.search(r"(?im)^\s*#\s*Лист\s*:", rest)
        if sheet:
            cut = m.end() + sheet.start()
            return raw[:cut].rstrip(), raw[cut:].lstrip()
    return raw.strip(), ""


def _section_bodies(text: str) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(text or ""))
    bodies: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1).lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        bodies[name] = (text[start:end] or "").strip()
    return bodies


def parse_check_report_txt(text: str) -> CheckAnalysis | None:
    """Разобрать TXT-отчёт. None — если текст не похож на шаблон."""
    raw = (text or "").strip()
    if not looks_like_check_report_txt(raw):
        return None
    vm = _HEADER_VERDICT_RE.search(raw)
    if not vm:
        return fail_analysis("в TXT-отчёте нет поля verdict")
    verdict = _norm_verdict(vm.group(1))
    bodies = _section_bodies(raw)
    summary = bodies.get("summary") or ""
    findings = bodies.get("findings") or ""
    actions = bodies.get("actions") or ""
    forward_body = bodies.get("forward") or ""
    checks: list[CheckItem] = []
    for i, fm in enumerate(_FINDING_RE.finditer(findings), start=1):
        kind = fm.group(1).lower()
        note = fm.group(2).strip()
        ok = kind in ("ok", "pass")
        checks.append(CheckItem(id=f"{kind}_{i}", ok=ok, note=note))
    if not checks and findings.strip():
        for line in findings.splitlines():
            s = line.strip().lstrip("-*").strip()
            if s:
                checks.append(CheckItem(id="finding", ok=False, note=s))
    ff = _FORWARD_FILE_RE.search(forward_body)
    fp = _FORWARD_PATH_RE.search(forward_body)
    file_kind = (ff.group(1) if ff else "original").strip().lower()
    path_raw = (fp.group(1) if fp else "").strip()
    if path_raw in ("—", "-", "–", "none", "null", ""):
        path_raw = ""
    rewrite = path_raw if file_kind == "fixed" and path_raw else None
    forward = ForwardSpec(
        mode="explicit" if path_raw else "inherit",
        paths=[path_raw.replace("\\", "/")] if path_raw else [],
    )
    fix = FixSpec(
        target="source" if verdict == "fail" or rewrite else "none",
        instructions=actions[:1000],
        rewrite_file=rewrite,
    )
    if not summary and not checks:
        return fail_analysis("TXT-отчёт пустой (нет summary/findings)")
    return CheckAnalysis(
        schema=SCHEMA_ID,
        verdict=verdict,
        summary=summary or (checks[0].note if checks else verdict),
        checks=checks,
        forward=forward,
        fix=fix,
    )


def render_check_report_txt(
    analysis: CheckAnalysis,
    *,
    mode: CheckFixMode | str = "fix",
    source_prompts: list[str] | None = None,
) -> str:
    """Собрать канонический check_report.txt из CheckAnalysis."""
    mode_s = "report_only" if str(mode).strip().lower() == "report_only" else "fix"
    sources = ", ".join(str(x).strip() for x in (source_prompts or []) if str(x).strip()) or "—"
    findings_lines: list[str] = []
    for c in analysis.checks:
        tag = "ok" if c.ok else "error"
        note = (c.note or c.id or "").strip() or "—"
        findings_lines.append(f"- [{tag}] {note}")
    if not findings_lines:
        findings_lines.append("- [ok] замечаний нет" if analysis.verdict == "pass" else "- [error] см. summary")
    rewrite = (analysis.fix.rewrite_file or "").strip().replace("\\", "/")
    if rewrite:
        file_kind = "fixed"
        path_line = rewrite
    elif analysis.forward.paths:
        file_kind = "original"
        path_line = analysis.forward.paths[0]
    else:
        file_kind = "original"
        path_line = "—"
    related = "—\n"
    if analysis.checks:
        related = "\n".join(
            f"- {c.id} → промт:— | {c.note}" if c.note else f"- {c.id} → промт:— |"
            for c in analysis.checks[:12]
        ) + "\n"
    actions = (analysis.fix.instructions or "").strip() or (
        "правки не выполнялись" if mode_s == "report_only" else "см. findings"
    )
    analysis_body = (analysis.summary or "").strip() or "см. findings"
    return (
        "# ОТЧЁТ ПРОВЕРКИ\n"
        f"verdict: {analysis.verdict}\n"
        f"mode: {mode_s}\n"
        f"source_prompts: {sources}\n"
        "\n## summary\n"
        f"{(analysis.summary or analysis.verdict).strip()}\n"
        "\n## analysis\n"
        f"{analysis_body}\n"
        "\n## findings\n"
        + "\n".join(findings_lines)
        + "\n\n## related\n"
        f"{related}"
        "\n## logic\n"
        "см. analysis / findings\n"
        "\n## actions\n"
        f"{actions}\n"
        "\n## forward\n"
        f"file: {file_kind}\n"
        f"path: {path_line}\n"
    )


def write_check_report_txt(
    out_dir: Path,
    analysis: CheckAnalysis,
    *,
    mode: CheckFixMode | str = "fix",
    source_prompts: list[str] | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / CHECK_REPORT_NAME
    path.write_text(
        render_check_report_txt(analysis, mode=mode, source_prompts=source_prompts),
        encoding="utf-8",
    )
    return path


def parse_check_analysis(text: str, *, require_schema: bool = False) -> CheckAnalysis:
    """Разобрать ответ GPT: JSON vp.check.v1 или TXT-отчёт. Битый → fail."""
    report_text, _wb = split_check_reply_and_writeback(text or "")
    probe = report_text or (text or "")
    obj = extract_json_object(probe)
    if obj is not None:
        if require_schema:
            sid = str(obj.get("schema") or "").strip()
            if sid and sid != SCHEMA_ID:
                return fail_analysis(f"неверная schema: {sid!r}, ожидается {SCHEMA_ID}")
        if "verdict" not in obj:
            decision = str(obj.get("decision") or "").strip().lower()
            if decision in ("approved", "approve", "ok"):
                obj = {**obj, "verdict": "pass"}
            elif decision in ("rejected", "regen", "reject", "fail"):
                obj = {**obj, "verdict": "fail"}
            else:
                # возможно это не check-JSON — пробуем TXT
                txt = parse_check_report_txt(probe)
                if txt is not None:
                    return apply_vision_score_gate(txt, probe)
                return fail_analysis("в JSON нет поля verdict")
        return apply_vision_score_gate(analysis_from_dict(obj), probe)
    txt = parse_check_report_txt(probe)
    if txt is not None:
        return apply_vision_score_gate(txt, probe)
    return fail_analysis("нет TXT-отчёта и нет JSON vp.check.v1 в ответе")


def parse_gate_status(text: str) -> str:
    """pass|fail для planner / save_operator_result."""
    return parse_check_analysis(text).verdict


def write_analysis_json(out_dir: Path, analysis: CheckAnalysis) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "analysis.json"
    path.write_text(
        json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def default_check_report_format() -> str:
    """Дефолтный шаблон ответа модели (можно переопределить на ноде)."""
    return TXT_REPORT_FOOTER


def normalize_check_report_format(raw: str | None) -> str | None:
    """Пустой / whitespace → None (использовать дефолт)."""
    text = (raw or "").strip()
    return text if text else None


def append_txt_report_footer(
    prompt: str,
    *,
    report_format: str | None = None,
) -> str:
    """Добавить шаблон отчёта в конец промта.

    ``report_format`` — кастом с ноды; иначе дефолт TXT_REPORT_FOOTER.
    Если в промте уже есть явный шаблон (``# ОТЧЁТ`` / ``verdict:``+``## findings``)
    и кастом не задан — не дублируем. Кастом всегда дописываем (заменяет дефолт).
    """
    base = (prompt or "").rstrip()
    custom = normalize_check_report_format(report_format)
    footer = custom or TXT_REPORT_FOOTER
    if custom:
        # Пользовательский формат — всегда в хвосте (даже если в агенте был старый шаблон).
        if not base:
            return footer
        # Убрать прежний дефолтный хвост, если агент его случайно включил.
        if TXT_REPORT_FOOTER in base:
            base = base.replace(TXT_REPORT_FOOTER, "").rstrip()
        sep = "\n\n---\nФОРМАТ ОТВЕТА (шаблон этой ноды):\n"
        return f"{base}{sep}{footer}"
    marker = "# ОТЧЁТ ПРОВЕРКИ"
    if marker in base or (
        "source_prompts:" in base.lower() and "## findings" in base.lower()
    ):
        return base
    if not base:
        return footer
    return f"{base}\n\n{footer}"


def _load_footer_from_prompts() -> str:
    """Хвост из prompts/check_operator/_schema/response_footer.md, если есть."""
    try:
        from app.settings import settings

        path = Path(settings.prompts_dir) / "check_operator" / "_schema" / "response_footer.md"
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if SCHEMA_ID in text:
                return text
    except Exception:  # noqa: BLE001
        pass
    # fallback: рядом с репо
    try:
        root = Path(__file__).resolve().parents[2]
        path = root / "prompts" / "check_operator" / "_schema" / "response_footer.md"
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if SCHEMA_ID in text:
                return text
    except Exception:  # noqa: BLE001
        pass
    return RESPONSE_FOOTER


def append_response_footer(prompt: str) -> str:
    """Добавить хвост схемы, если его ещё нет."""
    footer = _load_footer_from_prompts()
    base = (prompt or "").rstrip()
    if SCHEMA_ID in base and "verdict" in base.lower():
        return base
    if not base:
        return footer
    return f"{base}\n\n{footer}"


# Ожидаемые артефакты по типу исходной рабочей ноды (относительно data_dir).
EXPECTED_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "topic": ("project.xlsx",),
    "plan": ("project.xlsx",),
    "script": ("voiceover.txt", "project.xlsx"),
    "split": ("project.xlsx",),
    "hero": ("characters/",),
    "items": ("characters/", "items/"),
    "image_prompts": ("project.xlsx",),
    "images": ("scenes/",),
    "hitl_images": ("scenes/",),
    "animation_prompts": ("project.xlsx",),
    "videos": ("videos/",),
    "hitl_videos": ("videos/",),
    "audio": ("audio/", "voiceover.txt"),
    "music": ("audio/",),
    "assemble": ("final/",),
    "storage": ("storage/",),
    "excel_gpt": ("project.xlsx",),
    "excel_feed": ("project.xlsx",),
}


# Типы вышестоящих нод, для которых важен целевой формат кадра/видео.
VISUAL_CHECK_TYPES: frozenset[str] = frozenset(
    {
        "image_prompts",
        "images",
        "hitl_images",
        "videos",
        "hitl_videos",
        "hero",
        "hitl_hero",
        "items",
        "assemble",
        "hitl_final",
        "publish",
    }
)


def format_target_hint(
    aspect_ratio: str | None = None,
    image_resolution: str | None = None,
    video_resolution: str | None = None,
) -> str:
    """Короткий блок «целевой формат проекта» для контекста агента-проверки.

    Разрешение/формат НЕ статичны в промтах — фактические значения проекта
    подставляются сюда, чтобы агент сверял с ними, а не с зашитым 9:16.
    """
    aspect = (aspect_ratio or "").strip() or "9:16"
    parts = [f"соотношение сторон = {aspect}"]
    if (image_resolution or "").strip():
        parts.append(f"разрешение картинок = {image_resolution.strip()}")
    if (video_resolution or "").strip():
        parts.append(f"разрешение видео = {video_resolution.strip()}")
    return "Целевой формат проекта: " + ", ".join(parts) + "."


def expected_artifacts_for_node_type(node_type: str) -> tuple[str, ...]:
    t = (node_type or "").strip().lower()
    if t.startswith("enrich_"):
        return EXPECTED_ARTIFACTS["excel_gpt"]
    return EXPECTED_ARTIFACTS.get(t, ())


def _check_operator_root() -> Path:
    try:
        root = Path(__file__).resolve().parents[2]
        path = root / "prompts" / "check_operator"
        if path.is_dir():
            return path
    except Exception:  # noqa: BLE001
        pass
    return Path("prompts") / "check_operator"


def list_check_operator_steps() -> list[str]:
    """Имена шагов с промтами в prompts/check_operator/<step>/default.md."""
    root = _check_operator_root()
    if not root.is_dir():
        return []
    steps: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        if (child / "default.md").is_file():
            steps.append(child.name)
    return steps


def resolve_check_operator_step(step: str) -> str:
    """Нормализовать тип/step_code ноды → имя папки prompts/check_operator/<step>."""
    step_key = (step or "").strip().lower().replace("-", "_")
    aliases = {
        # step_code рабочих нод → имя папки промта
        "img_pr": "image_prompts",
        "anim_pr": "animation_prompts",
        "img": "images",
        "video": "videos",
        "topic": "plan",
        # excel round-trip: слоты enrich_1..5 и excel_feed → общий excel_gpt
        "excel_feed": "excel_gpt",
        "enrich": "excel_gpt",
        # HITL-ноды проверяют выход рабочей ноды выше по стрелке
        "hitl_hero": "hero",
        "hitl_images": "images",
        "hitl_videos": "videos",
        "hitl_final": "assemble",
    }
    # enrich_1..5 (и любой enrich_*) → excel_gpt
    if step_key.startswith("enrich_"):
        step_key = "excel_gpt"
    return aliases.get(step_key, step_key)


def load_check_operator_prompt_body(step: str) -> str | None:
    """Только тело агента из default.md (без JSON/TXT footer)."""
    step_key = resolve_check_operator_step(step)
    path = _check_operator_root() / step_key / "default.md"
    if not path.is_file():
        return None
    body = path.read_text(encoding="utf-8").strip()
    return body or None


def load_check_operator_prompt(step: str) -> str | None:
    """Текст промта проверки + хвост схемы vp.check.v1.

    Резолвит любой реальный тип ноды пайплайна в файл
    `prompts/check_operator/<step>/default.md`. HITL-ноды проверяют те же
    артефакты, что и рабочая нода выше по стрелке, поэтому маппятся на неё;
    enrich-слоты и excel_feed — на общий контракт excel_gpt.
    """
    body = load_check_operator_prompt_body(step)
    if not body:
        return None
    return append_response_footer(body)
