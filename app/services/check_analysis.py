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

_SECTION_RE = re.compile(r"(?m)^##\s+(summary|analysis|findings|related|logic|actions|forward)\s*$")
_HEADER_VERDICT_RE = re.compile(r"(?im)^\s*verdict\s*:\s*(\S+)\s*$")
_HEADER_MODE_RE = re.compile(r"(?im)^\s*mode\s*:\s*(\S+)\s*$")
_HEADER_SOURCES_RE = re.compile(r"(?im)^\s*source_prompts\s*:\s*(.+?)\s*$")
_FORWARD_FILE_RE = re.compile(r"(?im)^\s*file\s*:\s*(\S+)\s*$")
_FORWARD_PATH_RE = re.compile(r"(?im)^\s*path\s*:\s*(.+?)\s*$")
_FINDING_RE = re.compile(r"(?m)^\s*[-*]\s*\[(error|warn|ok|pass|fail)\]\s*(.+?)\s*$")

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
                    return txt
                return fail_analysis("в JSON нет поля verdict")
        return analysis_from_dict(obj)
    txt = parse_check_report_txt(probe)
    if txt is not None:
        return txt
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


def append_txt_report_footer(prompt: str) -> str:
    """Добавить TXT-шаблон отчёта, если его ещё нет."""
    base = (prompt or "").rstrip()
    marker = "# ОТЧЁТ ПРОВЕРКИ"
    if marker in base or "source_prompts:" in base.lower() and "## findings" in base.lower():
        return base
    if not base:
        return TXT_REPORT_FOOTER
    return f"{base}\n\n{TXT_REPORT_FOOTER}"


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
