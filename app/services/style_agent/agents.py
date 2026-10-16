"""Группа агентов: оркестратор (общие правила) + специалисты + синтез.

Каждый специалист: свой промпт-файл ``prompts/style_agent/<role>.md``,
анкета из ``schema.AGENT_FIELDS``, один повтор с фидбеком при невалидном
JSON. Все вызовы — через ``llm.ask_json`` (бюджет + журнал).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.style_agent import schema
from app.services.style_agent.budget import BudgetExceeded, IMAGES_PER_CALL, LlmBudget
from app.services.style_agent import llm
from app.services.style_analyzer import batch_paths, scrub_style_topic

PROMPTS_DIR = Path("prompts/style_agent")
ROLE_FILES = {
    "orchestrator": "orchestrator.md",
    "visual": "visual.md",
    "voice": "voice.md",
    "motion": "motion.md",
    "narrative": "narrative.md",
    "synth": "synth.md",
}
_TOPIC_SCRUB_KEYS = {"visual_core_en", "frame_style_lock_en", "subjects", "world", "composition_en"}
_TRANSCRIPT_MAX = 5000


def load_prompt(role: str, *, prompts_dir: Path | None = None) -> str:
    base = prompts_dir or PROMPTS_DIR
    fp = base / ROLE_FILES[role]
    try:
        return fp.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("style_agent: нет промпта {}", fp)
        return f"Роль: {role}. Ответ — один JSON-объект с ключами: " + ", ".join(schema.AGENT_FIELDS.get(role, {}))


def system_prompt(role: str, *, prompts_dir: Path | None = None) -> str:
    return load_prompt("orchestrator", prompts_dir=prompts_dir) + "\n\n---\n\n" + load_prompt(role, prompts_dir=prompts_dir)


def _compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _metrics_for(role: str, profile: dict[str, Any]) -> dict[str, Any]:
    fmt = profile.get("format") or {}
    base = {
        "duration_s": fmt.get("duration_s"),
        "aspect": fmt.get("aspect"),
        "resolution": fmt.get("resolution"),
        "fps": fmt.get("fps"),
    }
    if role == "visual":
        return {**base, "palette": profile.get("palette") or {}, "shots": (profile.get("motion") or {}).get("shots")}
    if role == "motion":
        motion = dict(profile.get("motion") or {})
        sp = profile.get("speech") or {}
        return {**base, "motion": motion, "pace_wpm": sp.get("pace_wpm"), "speech_ratio": sp.get("speech_ratio")}
    if role == "voice":
        sp = dict(profile.get("speech") or {})
        transcript = str(sp.pop("transcript", "") or "")[:_TRANSCRIPT_MAX]
        return {**base, "speech": sp, "music": profile.get("music") or {}, "audio": profile.get("audio") or {}, "transcript": transcript}
    if role == "narrative":
        sp = dict(profile.get("speech") or {})
        transcript = str(sp.pop("transcript", "") or "")[:_TRANSCRIPT_MAX]
        return {**base, "words": sp.get("words"), "pace_wpm": sp.get("pace_wpm"), "transcript": transcript}
    return base


def _merge_replies(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Несколько пачек листов одного ролика → один ответ (первое непустое; списки объединяем)."""
    out: dict[str, Any] = {}
    for part in parts:
        for k, v in part.items():
            if v is None:
                continue
            cur = out.get(k)
            if cur is None:
                out[k] = v
            elif isinstance(cur, list) and isinstance(v, list):
                seen = {str(x).lower() for x in cur}
                out[k] = cur + [x for x in v if str(x).lower() not in seen]
            elif isinstance(cur, dict) and isinstance(v, dict):
                keys = set(cur) | set(v)
                out[k] = {kk: round((float(cur.get(kk, 0)) + float(v.get(kk, 0))) / 2, 3) for kk in keys}
    return out


def _scrub(role: str, clean: dict[str, Any]) -> dict[str, Any]:
    for key in _TOPIC_SCRUB_KEYS:
        val = clean.get(key)
        if isinstance(val, str) and val:
            scrubbed = scrub_style_topic(val)
            if scrubbed and len(scrubbed) >= 0.4 * len(val):
                clean[key] = scrubbed
    return clean


async def _ask_role(
    role: str,
    user_text: str,
    *,
    files: list[Path] | None,
    budget: LlmBudget,
    label: str,
    project_id: int | None,
    log_path: Path | None,
    prompts_dir: Path | None,
    allow_retry: bool = True,
) -> tuple[dict[str, Any] | None, list[str]]:
    sys_prompt = system_prompt(role, prompts_dir=prompts_dir)
    data, raw = await llm.ask_json(
        user_text, files=files, system=sys_prompt, budget=budget, label=label, project_id=project_id, log_path=log_path
    )
    clean, problems = schema.validate_agent_reply(role, data)
    if problems and allow_retry and budget.can_reserve():
        feedback = (
            user_text
            + "\n\nПредыдущий ответ не прошёл проверку: "
            + "; ".join(problems[:6])
            + ". Верни только валидный JSON-объект по анкете."
        )
        if raw:
            feedback += "\nПредыдущий ответ:\n" + raw[:1500]
        data, raw = await llm.ask_json(
            feedback, files=files, system=sys_prompt, budget=budget, label=label + "/retry", project_id=project_id, log_path=log_path
        )
        clean, problems = schema.validate_agent_reply(role, data)
    if data is None:
        return None, problems or ["ответ не JSON"]
    return _scrub(role, clean), problems


async def run_specialist(
    role: str,
    profile: dict[str, Any],
    *,
    sheets: list[Path] | None,
    budget: LlmBudget,
    video_label: str,
    project_id: int | None = None,
    log_path: Path | None = None,
    prompts_dir: Path | None = None,
    max_batches: int = 1,
) -> dict[str, Any]:
    """Один специалист по одному ролику → {reply, problems, calls}."""
    metrics = _metrics_for(role, profile)
    text = (
        f"Ролик: {video_label}.\n"
        f"Измеренные метрики (JSON): {_compact(metrics)}\n\n"
        f"Заполни анкету роли «{role}». Ответ — один JSON-объект."
    )
    parts: list[dict[str, Any]] = []
    problems_all: list[str] = []
    calls_before = budget.used
    if sheets:
        batches = batch_paths(list(sheets), IMAGES_PER_CALL)[: max(1, int(max_batches))]
        for i, batch in enumerate(batches, start=1):
            suffix = f" Листы {i}/{len(batches)}: {', '.join(p.name for p in batch)}." if len(batches) > 1 else ""
            try:
                clean, problems = await _ask_role(
                    role,
                    text + suffix,
                    files=batch,
                    budget=budget,
                    label=f"{role}:{video_label}:{i}",
                    project_id=project_id,
                    log_path=log_path,
                    prompts_dir=prompts_dir,
                    allow_retry=(i == 1),
                )
            except BudgetExceeded:
                problems_all.append("лимит LLM исчерпан — часть листов не просмотрена")
                break
            problems_all.extend(problems)
            if clean:
                parts.append(clean)
    else:
        clean, problems = await _ask_role(
            role,
            text,
            files=None,
            budget=budget,
            label=f"{role}:{video_label}",
            project_id=project_id,
            log_path=log_path,
            prompts_dir=prompts_dir,
        )
        problems_all.extend(problems)
        if clean:
            parts.append(clean)
    return {
        "role": role,
        "reply": _merge_replies(parts) if parts else None,
        "problems": problems_all,
        "calls": budget.used - calls_before,
    }


async def run_synth(
    passport_draft: dict[str, Any],
    per_video: list[dict[str, Any]],
    *,
    budget: LlmBudget,
    project_id: int | None = None,
    log_path: Path | None = None,
    prompts_dir: Path | None = None,
) -> dict[str, Any]:
    """Синтез текстовых блоков для промптов Studio (1 вызов + ≤1 повтор)."""
    slim_videos = []
    for v in per_video:
        slim_videos.append(
            {
                "video": v.get("video"),
                "visual": (v.get("agents") or {}).get("visual"),
                "voice": (v.get("agents") or {}).get("voice"),
                "motion": (v.get("agents") or {}).get("motion"),
                "narrative": (v.get("agents") or {}).get("narrative"),
            }
        )
    draft_slim = {k: v for k, v in passport_draft.items() if k in ("sources", *schema.AXES)}
    text = (
        "Агрегированный паспорт (значения/уверенность/поддержка — не менять):\n"
        + _compact(draft_slim)
        + "\n\nОтветы специалистов по роликам:\n"
        + _compact(slim_videos)[:14000]
        + "\n\nЗаполни анкету synth. Ответ — один JSON-объект."
    )
    calls_before = budget.used
    clean, problems = await _ask_role(
        "synth", text, files=None, budget=budget, label="synth", project_id=project_id, log_path=log_path, prompts_dir=prompts_dir
    )
    return {"role": "synth", "reply": clean, "problems": problems, "calls": budget.used - calls_before}
