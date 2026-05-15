"""System prompts for each visual-lab phase.

Hard-coded here (not loaded from ``prompts/``) so the contract between
the Python code and ChatGPT stays in lock-step with the JSON schemas.

All prompts are bilingual (RU intro for context, EN JSON schema for
parseability). GPT-Vision via web works well with mixed Russian text +
explicit JSON schema in English.
"""

from __future__ import annotations

from app.services.visual_lab.criteria import CRITERIA, GROUPS
from app.services.visual_lab.limits import soft_limit


def _criteria_block() -> str:
    """Markdown table of all 20 criteria for the GPT system prompt."""
    lines = []
    for g in GROUPS:
        members = [c for c in CRITERIA if c.group == g.id]
        lines.append(f"\n### Group {g.id} — {g.name_ru} (weight {g.weight})")
        for c in members:
            lines.append(f"- **{c.id}**: {c.name_ru}. {c.description_ru}")
    return "\n".join(lines)


_CRITERIA_BLOCK = _criteria_block()


ANALYZE_SYSTEM_PROMPT = f"""\
Ты — эксперт-арт-директор по pixel art и cinematic animation. Тебе \
прислали ОДНУ сгенерированную картинку (антропоморфные коты, pixel art \
стиль) и текст промта, по которому она была сгенерирована. Также может \
быть прислан Excel-файл с историей предыдущих оценок (scores.xlsx) — \
используй его как контекст «куда движется проект».

Твоя задача — оценить ТОЛЬКО визуальное качество по 20 критериям ниже. \
НЕ оценивай сюжет, историю, логику сцены, композицию, правило третей, \
динамику.

20 ВИЗУАЛЬНЫХ КРИТЕРИЕВ (каждый от 1 до 10):
{_CRITERIA_BLOCK}

ФОРМАТ ОТВЕТА — строго ОДИН JSON-объект, без markdown, без префиксов, \
без пояснений вокруг. Только JSON. Схема:

{{
  "scores": {{
    "color_harmony": 1..10, "color_palette": 1..10,
    "light_quality": 1..10, "light_objects": 1..10, "light_character": 1..10,
    "detail_foreground": 1..10, "detail_midground": 1..10,
    "detail_background": 1..10, "spatial_depth": 1..10,
    "texture_objects": 1..10, "texture_surfaces": 1..10,
    "fur_quality": 1..10, "fur_detail": 1..10,
    "clothing_detail": 1..10, "clothing_physics": 1..10,
    "pixel_sharpness": 1..10, "pixel_size": 1..10, "outline_thickness": 1..10,
    "style_consistency": 1..10, "style_artifacts": 1..10
  }},
  "visual_pros": ["короткая строка про каждый плюс", ...],
  "visual_cons": ["короткая строка про каждый минус", ...],
  "criterion_explanations": {{
    "<criterion_id>": {{
      "score": 1..10,
      "what": "что конкретно видно (не общими словами)",
      "responsible_words": ["слово из промта, которое дало этот результат"],
      "missing_words": ["слова которых не хватает в промте"],
      "fix_suggestion": "что добавить/убрать чтобы поднять балл"
    }}
  }},
  "keyword_effects": {{
    "<word or phrase from the prompt>": {{
      "<criterion_id>": "+1.5" or "-0.8"
    }}
  }}
}}

Заполняй criterion_explanations МИНИМУМ для всех критериев со score < 6 \
и для всех со score >= 8. keyword_effects — для 3-5 самых влиятельных \
слов/фраз промта. Все 20 scores ОБЯЗАТЕЛЬНЫ.
"""


THINK_SYSTEM_PROMPT = """\
Ты — chain-of-thought рассуждалка для лаборатории промтов. Тебе \
прислали Excel-файл со всей историей итераций (scores.xlsx) + JSON-блок \
с предыдущими гипотезами, накопленными эффектами слов (knowledge base) \
и текущими 5 эталонными картинками (их scores).

Твоя задача — РАССУЖДАТЬ как опытный prompt engineer:
1. Найти системные слабые критерии (стабильно низкие баллы).
2. Найти корреляции между словами в промтах и оценками.
3. Сформулировать новые гипотезы (какое слово может улучшить какой критерий).
4. Расставить приоритеты тестирования (по слабейшим критериям).
5. Подтвердить/опровергнуть старые гипотезы.

ФОРМАТ ОТВЕТА — строго ОДИН JSON-объект, без markdown, без префиксов. \
Схема:

{
  "reasoning_summary": "2-4 абзаца твоего рассуждения (можно chain-of-thought, без markdown headers)",
  "key_observations": ["наблюдение 1", ...],
  "weakest_criteria": ["criterion_id", "criterion_id", ...],
  "new_hypotheses": [
    {
      "id": <int, продолжаем сквозную нумерацию>,
      "text": "Слово 'X' улучшит критерий Y, потому что ...",
      "type": "ADD_WORD" | "REMOVE_WORD" | "REPLACE_WORD" | "COMBO" | "OTHER",
      "test_word": "X" (или null),
      "replacement_for": "старое слово" (или null, только для REPLACE_WORD),
      "target_criteria": ["criterion_id", ...],
      "priority": 1..10,
      "status": "PROPOSED",
      "evidence": ""
    }
  ],
  "confirmed_hypotheses_ids": [int, ...],
  "rejected_hypotheses_ids": [int, ...],
  "antihypotheses": ["слово X скорее всего ВРЕДИТ критерию Y, потому что ..."]
}

Минимум 3 new_hypotheses, максимум 10. Приоритизируй гипотезы по \
слабейшим критериям (с самым низким средним баллом по истории).
"""


_BUILD_LIMIT = soft_limit()  # 4720 chars (4800 - 80 reserve for ID prefix)


BUILD_SYSTEM_PROMPT = f"""\
Ты — генератор оптимальных визуальных промтов для outsee.io / Banana \
Pro 2K, 16:9, Relax. На вход — текущий master_prompt, накопленная \
knowledge base (эффекты слов и комбо), последние гипотезы из think- \
фазы, и Excel со скорами всех итераций.

Твоя задача — собрать НОВЫЙ master_prompt, который:
- Включает стабильно-полезные слова из knowledge_base (STABLE_POSITIVE).
- Включает 1-2 новые гипотезы из think (приоритет 7+), для теста.
- НЕ включает STABLE_NEGATIVE слова и их синонимы.
- Сохраняет ядро base_visual_prompt (тема, сцена, персонаж).
- ⚠️ Длина итогового master_prompt ≤ {_BUILD_LIMIT} символов (включая \
пробелы, без [ID:...] префикса — он добавляется автоматически outsee).

ФОРМАТ ОТВЕТА — строго ОДИН JSON-объект:

{{
  "master_prompt": "полный текст нового промта (≤ {_BUILD_LIMIT} симв)",
  "word_rationale": [
    {{
      "word": "слово/фраза",
      "rationale": "почему оно здесь, на какой критерий влияет",
      "source_test_ids": [int, ...],
      "target_criteria": ["criterion_id", ...]
    }}
  ],
  "expected_gain": {{
    "<criterion_id>": +0.0
  }},
  "warnings": ["потенциальные конфликты или риски, если есть"]
}}

Если общая длина превысит {_BUILD_LIMIT} — сокращай поэтапно: сначала \
выкидываешь синонимы, потом второстепенные эпитеты, в последнюю \
очередь — описание сцены. ID-префикс [ID: ...] outsee добавит сам, \
оставь резерв.
"""


def analyze_user_prompt(
    *,
    prompt_used: str,
    iter_num: int,
    project_name: str,
) -> str:
    """User-facing message for the analyze phase. Attach: image, scores.xlsx."""
    return (
        f"Проект: {project_name!r}\n"
        f"Итерация: {iter_num}\n\n"
        f"Промт, по которому сгенерирована картинка ниже:\n"
        f"---\n{prompt_used}\n---\n\n"
        f"К сообщению приложены: текущая картинка (image) + scores.xlsx с "
        f"историей всех предыдущих итераций. Оцени картинку по 20 "
        f"критериям и верни строго JSON-объект по схеме из системного "
        f"промта."
    )


def think_user_prompt(
    *,
    project_name: str,
    iters_done: int,
    weakest_criteria_hint: list[str] | None = None,
    knowledge_summary: str = "",
) -> str:
    weak = (
        f"\nПредположительно слабые критерии (по моим расчётам): "
        f"{', '.join(weakest_criteria_hint or [])}"
        if weakest_criteria_hint
        else ""
    )
    return (
        f"Проект: {project_name!r}, итераций сделано: {iters_done}.{weak}\n\n"
        f"К сообщению приложены: scores.xlsx (вся история), knowledge_base.json "
        f"(накопленные эффекты слов и гипотезы), и 5 эталонных картинок из "
        f"reference/ (то качество, к которому мы стремимся).\n\n"
        f"Накопленная база знаний (краткая выжимка):\n{knowledge_summary}\n\n"
        f"Думай по схеме system prompt'а и верни JSON-объект."
    )


def build_user_prompt(
    *,
    project_name: str,
    current_master_prompt: str,
    base_visual_prompt: str,
    weakest_criteria: list[str],
    top_hypotheses: list[str],
) -> str:
    return (
        f"Проект: {project_name!r}.\n\n"
        f"Текущий master_prompt (последняя успешная итерация):\n"
        f"---\n{current_master_prompt}\n---\n\n"
        f"Базовый visual_prompt (тема, неизменное ядро):\n"
        f"---\n{base_visual_prompt}\n---\n\n"
        f"Слабейшие критерии сейчас: "
        f"{', '.join(weakest_criteria) if weakest_criteria else '—'}\n"
        f"Топ-3 гипотезы для теста этой итерацией:\n"
        + "\n".join(f"- {h}" for h in top_hypotheses)
        + "\n\nК сообщению приложены scores.xlsx и knowledge_base.json. "
        "Собери новый master_prompt по схеме system prompt'а."
    )
