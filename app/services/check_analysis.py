"""Контракт проверки GPT-ноды: vp.check.v1.

Проверочная нода (роль review/gate/compare) обязана вернуть JSON.
Парсер выставляет gateStatus и список файлов для передачи дальше.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SCHEMA_ID = "vp.check.v1"

Verdict = Literal["pass", "fail"]
ForwardMode = Literal["inherit", "explicit"]
FixTarget = Literal["source", "xlsx", "prompt", "none"]

# Хвост для промтов проверочных нод — модель обязана ответить только JSON.
RESPONSE_FOOTER = """
---
ФОРМАТ ОТВЕТА (строго): один JSON-объект, без markdown и без текста вокруг.
Схема vp.check.v1:
{
  "schema": "vp.check.v1",
  "verdict": "pass" | "fail",
  "summary": "кратко почему",
  "checks": [{"id": "имя", "ok": true|false, "note": "…"}],
  "forward": {"mode": "inherit" | "explicit", "paths": ["относительный/путь"]},
  "fix": {"target": "source" | "xlsx" | "prompt" | "none", "instructions": "…", "rewrite_file": null}
}
verdict=pass — пустить по стрелке «Ок»; fail — по «Не ок».
forward.mode=inherit — дальше те же файлы, что пришли на проверку;
explicit — только paths (относительно корня проекта).
""".strip()

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


def parse_check_analysis(text: str, *, require_schema: bool = False) -> CheckAnalysis:
    """Разобрать ответ GPT. Битый/пустой JSON → fail (не пускать дальше)."""
    obj = extract_json_object(text)
    if obj is None:
        return fail_analysis("нет JSON vp.check.v1 в ответе")
    if require_schema:
        sid = str(obj.get("schema") or "").strip()
        if sid and sid != SCHEMA_ID:
            return fail_analysis(f"неверная schema: {sid!r}, ожидается {SCHEMA_ID}")
    if "verdict" not in obj:
        # мягкий адаптер: auto_review JSON decision
        decision = str(obj.get("decision") or "").strip().lower()
        if decision in ("approved", "approve", "ok"):
            obj = {**obj, "verdict": "pass"}
        elif decision in ("rejected", "regen", "reject", "fail"):
            obj = {**obj, "verdict": "fail"}
        else:
            return fail_analysis("в JSON нет поля verdict")
    return analysis_from_dict(obj)


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


def append_response_footer(prompt: str) -> str:
    """Добавить хвост схемы, если его ещё нет."""
    base = (prompt or "").rstrip()
    if SCHEMA_ID in base and "verdict" in base.lower():
        return base
    if not base:
        return RESPONSE_FOOTER
    return f"{base}\n\n{RESPONSE_FOOTER}"


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


def expected_artifacts_for_node_type(node_type: str) -> tuple[str, ...]:
    t = (node_type or "").strip().lower()
    if t.startswith("enrich_"):
        return EXPECTED_ARTIFACTS["excel_gpt"]
    return EXPECTED_ARTIFACTS.get(t, ())
