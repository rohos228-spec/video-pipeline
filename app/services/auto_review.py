"""GPT-ревьюер артефактов для auto_mode проектов.

Логика «бутерброда»:

  1) Загружаем артефакт (текст плана / сценарий / путь к картинке /
     путь к видео).
  2) Загружаем чек-промт из prompts/check_<kind>/default.md
     (или из snapshot-а массового проекта, если есть).
  3) Шлём в ChatGPT (новый чат) пары `<чек-промт> + <артефакт>`.
     - Для текстовых артефактов — promt + текст inline.
     - Для картинок/видео — promt + файл через ChatGPTBot.ask_with_file.
  4) Парсим ответ как JSON (в коде, не доверяем GPT декларации).
  5) Code-side anti-bullshit:
     - все evidence_quote должны быть substring артефакта,
     - все числовые критерии (длина, временные интервалы) считает код,
     - GPT даёт scores, код выносит финальное решение.
  6) Возвращаем ReviewResult.

Не использует HITL-таблицу — это отдельный «контур качества». Решение
auto_review подставляется в HITL вместо клика юзера в auto_advance.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from app.models import HITLDecision, HITLKind
from app.services.auto_review_validators import (
    derive_final_decision,
    validate_evidence_quotes,
    validate_plan_numeric,
    validate_script_numeric,
)

# Корень с чек-промтами.
PROMPTS_ROOT = Path(__file__).resolve().parent.parent.parent / "prompts"


# HITLKind → имя папки с чек-промтами.
CHECK_FOLDER_BY_KIND: dict[HITLKind, str] = {
    HITLKind.approve_plan: "check_plan",
    HITLKind.approve_script: "check_script",
    HITLKind.approve_hero: "check_hero",
    HITLKind.approve_images: "check_images",
    HITLKind.approve_videos: "check_videos",
    HITLKind.approve_final: "check_final",
}


@dataclass
class ReviewResult:
    """Итог автоматической проверки."""

    decision: HITLDecision
    confidence: float
    fix_hints: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    raw_response: str = ""
    numeric_facts: dict = field(default_factory=dict)
    fabricated_evidence: list[str] = field(default_factory=list)
    parse_error: str | None = None


# ============================================================
# Загрузка чек-промтов
# ============================================================


def get_check_prompt_path(
    kind: HITLKind, *, batch_snapshot_dir: Path | None = None
) -> Path:
    """Путь к чек-промту. Если есть snapshot массового — берём оттуда."""
    folder = CHECK_FOLDER_BY_KIND.get(kind)
    if folder is None:
        raise ValueError(f"нет чек-папки для HITLKind={kind}")
    name = "default.md"
    if batch_snapshot_dir is not None:
        snap = batch_snapshot_dir / folder / name
        if snap.exists():
            return snap
    return PROMPTS_ROOT / folder / name


def load_check_prompt(
    kind: HITLKind, *, batch_snapshot_dir: Path | None = None
) -> str:
    p = get_check_prompt_path(kind, batch_snapshot_dir=batch_snapshot_dir)
    if not p.exists():
        raise FileNotFoundError(f"чек-промт не найден: {p}")
    return p.read_text(encoding="utf-8")


# ============================================================
# JSON-парсер ответа GPT
# ============================================================

# GPT часто оборачивает JSON в ```json ... ``` — снимаем.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_review_json(raw: str) -> tuple[dict, str | None]:
    """Достаёт JSON из ответа GPT, толерантно к обёрткам.

    Возвращает (parsed_or_empty, error_or_none).
    """
    if not raw:
        return {}, "empty response"
    # Сначала пробуем «как есть».
    candidates: list[str] = [raw.strip()]
    # Внутри markdown-фенса.
    for m in _JSON_FENCE_RE.finditer(raw):
        candidates.append(m.group(1).strip())
    # Между первой `{` и последней `}` — самый агрессивный фоллбэк.
    a, b = raw.find("{"), raw.rfind("}")
    if a >= 0 and b > a:
        candidates.append(raw[a : b + 1].strip())

    last_err: str | None = None
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj, None
        except json.JSONDecodeError as e:
            last_err = str(e)
            continue
    return {}, last_err or "no JSON found"


# ============================================================
# Главные функции review_*()
# ============================================================


def _build_full_prompt(check_prompt: str, artifact_text: str) -> str:
    """Склеивает чек-промт + артефакт. Артефакт прижат к концу промта."""
    return check_prompt.rstrip() + "\n\n" + artifact_text.strip()


def _coalesce_fix_hints(parsed: dict, extra: list[str]) -> list[str]:
    hints = []
    raw = parsed.get("fix_hints") or []
    if isinstance(raw, list):
        hints = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        hints = [raw.strip()] if raw.strip() else []
    # дополнительные технические фразы из anti-bullshit.
    hints.extend(extra)
    return hints[:10]


def _safe_confidence(v: Any) -> float:
    try:
        f = float(v)
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return 0.0


async def review_text(
    *,
    kind: HITLKind,
    artifact_text: str,
    chatgpt_bot: Any,  # ChatGPTBot (избегаем циклического импорта)
    batch_snapshot_dir: Path | None = None,
    extra_numeric: list[str] | None = None,
    extra_numeric_facts: dict | None = None,
    timeout: float = 240.0,
) -> ReviewResult:
    """Общий обработчик ТЕКСТОВЫХ артефактов (plan, script).

    Принимает уже инициализированный ChatGPTBot — звено снаружи отвечает
    за `browser_session()`-контекст.
    """
    check_prompt = load_check_prompt(kind, batch_snapshot_dir=batch_snapshot_dir)
    full_prompt = _build_full_prompt(check_prompt, artifact_text)

    raw = await chatgpt_bot.ask_fresh(full_prompt, timeout=timeout)
    logger.info("auto_review[{}]: raw response len={}", kind.value, len(raw or ""))

    parsed, perr = parse_review_json(raw)
    if perr:
        logger.warning("auto_review[{}]: JSON parse error: {}", kind.value, perr)

    gpt_decision = str(parsed.get("decision") or "").strip().lower()
    confidence = _safe_confidence(parsed.get("confidence"))
    criteria = parsed.get("criteria") if isinstance(parsed.get("criteria"), dict) else {}

    fab = validate_evidence_quotes(artifact_text, criteria)

    numeric_failures = list(extra_numeric or [])

    decision_str, reasons = derive_final_decision(
        gpt_decision=gpt_decision,
        confidence=confidence,
        criteria=criteria,
        fabricated_evidence=fab,
        numeric_failures=numeric_failures,
    )

    decision_map = {
        "approved": HITLDecision.approved,
        "regen": HITLDecision.regenerate,
        "rejected": HITLDecision.rejected,
    }
    decision = decision_map.get(decision_str, HITLDecision.regenerate)

    fix_hints = _coalesce_fix_hints(parsed, [f"[{r}]" for r in reasons])

    if perr and not parsed:
        # Если ничего не распарсилось — это сильный «regen»-сигнал.
        decision = HITLDecision.regenerate
        reasons.append(f"PARSE_ERROR: {perr}")

    return ReviewResult(
        decision=decision,
        confidence=confidence,
        fix_hints=fix_hints,
        reasons=reasons,
        raw_response=raw or "",
        numeric_facts=extra_numeric_facts or {},
        fabricated_evidence=fab,
        parse_error=perr,
    )


async def review_plan(
    *,
    plan_text: str,
    chatgpt_bot: Any,
    batch_snapshot_dir: Path | None = None,
    timeout: float = 240.0,
    product_name: str | None = None,
) -> ReviewResult:
    """Проверка общего плана.

    `product_name` — название постоянного продукта массового. Если задан
    и продукт не упомянут в плане — добавится numeric_failure и план
    уйдёт на регенерацию.
    """
    facts = validate_plan_numeric(plan_text, product_name=product_name)
    extra = []
    if not facts["has_all_intervals"]:
        missing = facts["missing_intervals"]
        extra.append(
            "PLAN_MISSING_INTERVALS: "
            + ", ".join(f"{a}-{b}" for a, b in missing)
        )
    if facts.get("product_required") and not facts.get("product_mentioned"):
        extra.append(f"PRODUCT_NOT_MENTIONED: «{product_name}»")
    return await review_text(
        kind=HITLKind.approve_plan,
        artifact_text=plan_text,
        chatgpt_bot=chatgpt_bot,
        batch_snapshot_dir=batch_snapshot_dir,
        extra_numeric=extra,
        extra_numeric_facts=facts,
        timeout=timeout,
    )


async def review_script(
    *,
    script_text: str,
    chatgpt_bot: Any,
    batch_snapshot_dir: Path | None = None,
    timeout: float = 240.0,
    product_name: str | None = None,
) -> ReviewResult:
    """Проверка закадрового текста."""
    facts = validate_script_numeric(script_text, product_name=product_name)
    extra = []
    if not facts["char_count_in_range"]:
        extra.append(
            f"SCRIPT_LEN_OUT_OF_RANGE: {facts['char_count']} not in "
            f"[{facts['char_min']}, {facts['char_max']}]"
        )
    if facts["banned_phrases_found"]:
        extra.append(
            "BANNED_PHRASES: " + ", ".join(facts["banned_phrases_found"])
        )
    if facts["repeated_sentence_starts"]:
        extra.append(
            "REPEATED_STARTS: "
            + ", ".join(facts["repeated_sentence_starts"][:5])
        )
    if facts.get("product_required") and not facts.get("product_mentioned"):
        extra.append(f"PRODUCT_NOT_MENTIONED: «{product_name}»")
    return await review_text(
        kind=HITLKind.approve_script,
        artifact_text=script_text,
        chatgpt_bot=chatgpt_bot,
        batch_snapshot_dir=batch_snapshot_dir,
        extra_numeric=extra,
        extra_numeric_facts=facts,
        timeout=timeout,
    )


async def review_image(
    *,
    kind: HITLKind,  # approve_hero | approve_images | approve_videos | approve_final
    image_path: Path,
    chatgpt_bot: Any,
    batch_snapshot_dir: Path | None = None,
    context_text: str = "",
    timeout: float = 240.0,
) -> ReviewResult:
    """Проверка картинки / видео (vision-чек).

    Для PR #2 этот код есть, но из конвейера он пока НЕ вызывается —
    auto_advance для hero/images/videos/final работает в режиме
    «auto-approve без vision-чека». Когда подтвердишь, что хочешь
    включить vision — переключим один флаг.
    """
    check_prompt = load_check_prompt(kind, batch_snapshot_dir=batch_snapshot_dir)
    prompt = check_prompt.rstrip()
    if context_text:
        prompt += "\n\nКОНТЕКСТ:\n" + context_text.strip()

    raw = await chatgpt_bot.ask_with_file(prompt, [image_path], timeout=timeout)
    logger.info("auto_review[{}]: image raw len={}", kind.value, len(raw or ""))

    parsed, perr = parse_review_json(raw)
    gpt_decision = str(parsed.get("decision") or "").strip().lower()
    confidence = _safe_confidence(parsed.get("confidence"))
    criteria = parsed.get("criteria") if isinstance(parsed.get("criteria"), dict) else {}

    # Для картинок substring-чек не делаем — там observation, а не цитата.
    fab: list[str] = []
    decision_str, reasons = derive_final_decision(
        gpt_decision=gpt_decision,
        confidence=confidence,
        criteria=criteria,
        fabricated_evidence=fab,
        numeric_failures=[],
    )
    decision_map = {
        "approved": HITLDecision.approved,
        "regen": HITLDecision.regenerate,
        "rejected": HITLDecision.rejected,
    }
    decision = decision_map.get(decision_str, HITLDecision.regenerate)
    fix_hints = _coalesce_fix_hints(parsed, [f"[{r}]" for r in reasons])
    if perr and not parsed:
        decision = HITLDecision.regenerate
        reasons.append(f"PARSE_ERROR: {perr}")
    return ReviewResult(
        decision=decision,
        confidence=confidence,
        fix_hints=fix_hints,
        reasons=reasons,
        raw_response=raw or "",
        parse_error=perr,
    )
