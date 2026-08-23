"""Сервис Мета-Агента: компиляция отказоустойчивых системных мастер-промптов.

Принимает человеческое описание задачи (на русском или английском) и категорию
ноды (step_code) -> возвращает готовый, проверенный системный .md промпт с
железобетонной JSON-схемой, плейсхолдерами и защитой от галлюцинаций.
"""

from __future__ import annotations

import re
from typing import Any
from loguru import logger

from app.services.gpt_client import get_gpt_client
from app.services.prompt_library import STEP_FOLDERS


META_AGENT_SYSTEM_PROMPT = """Ты — ведущий AI Prompt Architect и Compiler для продакшн-системы Video Pipeline Studio.

Твоя задача: взять пользовательскую идею/задачу на естественном языке и скомпилировать из неё ПРОФЕССИОНАЛЬНЫЙ, ОТКАЗОУСТОЙЧИВЫЙ СИСТЕМНЫЙ МАСТЕР-ПРОМПТ (.md).

СТРОГИЕ ПРАВИЛА КОМПИЛЯЦИИ:
1. Выдавай ТОЛЬКО готовый текст промпта в формате Markdown (.md). Никаких вводных слов ("Вот ваш промпт:", "Конечно, держите"), никаких заключительных рассуждений.
2. Промпт должен содержать чёткие секции:
   - РОЛЬ И ЦЕЛЬ (SYSTEM ROLE & OBJECTIVE)
   - ВХОДНЫЕ ДАННЫЕ И ПЛЕЙСХОЛДЕРЫ (INPUT CONTEXT)
   - ПРАВИЛА И СТРОГАЯ СТРУКТУРА ВЫХОДА (OUTPUT CONTRACT / JSON or TSV SCHEMA)
   - АНТИ-ГАЛЛЮЦИНАЦИИ И ЗАПРЕТЫ (STRICT CONSTRAINTS & NO-CHAT GUARD)
   - 1-2 ЭТАЛОННЫХ ПРИМЕРА ВХОД -> ВЫХОД (FEW-SHOT EXAMPLES)
3. Язык инструкций промпта: используй английский или русский в зависимости от типа шага (для генераторов картинок/видео Midjourney/Outsee/Veo — английские промпты; для Excel/аналитики/сценария — русские инструкции со строгим английским JSON).
4. Всегда внедряй правило No-Chat Guard: «Отвечай СТРОГО валидным JSON/TSV без вступительных фраз и без вежливых отступлений».
5. ЛАКОНИЧНОСТЬ И ТОЧНОСТЬ: Составляй ёмкий, сфокусированный и компактный системный мастер-промпт. Не растягивай Few-Shot примеры на десятки страниц — используй 1-2 кратких эталонных примера по 2-4 строки. Выдавай законченный, чистый результат без лишней воды.
"""

NODE_SPECIFIC_GUIDELINES: dict[str, str] = {
    "excel_gpt": """
СПЕЦИФИКА НОДЫ: «Работа с GPT / Анализ Excel и БД» (05_excel_gpt)
- Формат выхода: строго JSON-массив или TSV-таблица с привязкой к номерам строк/кадров ("number" или "frame_number": 1..N).
- Поддерживаемые поля: `place`, `main_action`, `accent`, `scene_sense`, `visual_type`, `characters`, `image_prompt`, `animation_prompt`, `mood`, `palette`, `continuity`.
- Обязательно требуй: сохранять исходный порядок и количество строк/кадров.
""",
    "scene_d": """
СПЕЦИФИКА НОДЫ: «Дизайн сцен и сквозное действие» (scene_design)
- Учитывай сквозное действие (match on action / continuity) между кадрами.
- Совместимость с каталогами камер (camera.md, camera_sets.md, action.md).
- Выход: JSON по кадрам со слоями описания (персонажи, окружение, свет, движение).
""",
    "hero_style": """
СПЕЦИФИКА НОДЫ: «Стиль персонажа / Hero Style» (04_hero_style)
- Формат: мастер-стиль для генератора персонажей (Outsee / Nano Banana 2 / Midjourney).
- Включай: кинематографическое освещение (rim light, volumetric shadows), оптику (35mm/85mm lens, f/1.8, shallow depth of field), текстуры кожи/материалов (8k resolution, subsurface scattering).
- Обязательно плейсхолдер `[character_description]`.
- Требования к ракурсу: чистый фон, 3/4 turn, четкие черты лица.
""",
    "hero": """
СПЕЦИФИКА НОДЫ: «Генерация персонажа / Hero» (04_hero)
- Создание референсного листа персонажа.
- Обязательно плейсхолдер `[character_description]`.
- Формат 16:9, несколько ракурсов одного и того же героя.
""",
    "items": """
СПЕЦИФИКА НОДЫ: «Предметы / Items / Props» (04b_items)
- Формат: Product Asset Turnaround Sheet в 16:9.
- Изоляция на нейтральном студийном фоне (#FFFFFF или #F4F4F7).
- 4 ракурса одного предмета: Front 3/4 Hero view, Side profile, Top angled, Close-up texture/controls.
- Обязательно плейсхолдер `[item_description]`.
- Запрет: никаких людей, лиц, рук, текста и водяных знаков.
""",
    "img_pr": """
СПЕЦИФИКА НОДЫ: «Промпты кадров / Image Prompts» (05_image_prompts)
- Генератор фотореалистичных промптов для каждого кадра на основе закадра.
- Плейсхолдеры: `{voiceover}`, `{frame_number}`, `{characters_list}`, `{project_topic}`.
- Директивы: композиция, операторский ракурс, глубина резкости, световая атмосфера.
""",
    "anim_pr": """
СПЕЦИФИКА НОДЫ: «Промпты анимации / Animation Prompts» (07_animation)
- Генератор подсказок движения камеры для видеогенераторов (Veo 3.1 / Kling 2.6).
- Директивы движения: smooth dolly in, slow cinematic pan, subtle parallax, micro-motions.
- Запреты: исключение артефактов морфинга и лишних быстрых скачков.
""",
    "plan": """
СПЕЦИФИКА НОДЫ: «Сценарий / План ролика» (01_plan)
- Структурирование вирусного/кинематографичного видео (хук, кульминация, развязка, тайминги).
""",
    "script": """
СПЕЦИФИКА НОДЫ: «Закадровый текст / Script» (02_script)
- Генерация плотного дикторского закадра с точным контролем длительности слов/символов под тайминг.
""",
    "split": """
СПЕЦИФИКА НОДЫ: «Разбивка на блоки / Split» (03_razbivka)
- Деление закадрового текста на ритмичные смысловые шоты (1–3 предложения на кадр).
""",
}


def sanitize_compiled_prompt(raw_text: str) -> str:
    """Удаляет мусорные обертки Markdown или вводные реплики LLM."""
    text = (raw_text or "").strip()
    # Если ответ обернут целиком в ```markdown ... ```
    if text.startswith("```markdown") and text.endswith("```"):
        text = text[len("```markdown"): -3].strip()
    elif text.startswith("```md") and text.endswith("```"):
        text = text[len("```md"): -3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    return text


async def compile_meta_prompt(
    step_code: str,
    user_intent: str,
    *,
    project_topic: str = "",
    target_name: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Генерирует системный промпт через LLM на основе пользовательского намерения."""
    normalized_step = (step_code or "excel_gpt").strip().lower()
    guideline = NODE_SPECIFIC_GUIDELINES.get(
        normalized_step,
        NODE_SPECIFIC_GUIDELINES.get("excel_gpt", "")
    )

    clean_intent = (user_intent or "").strip()
    if not clean_intent:
        clean_intent = "Создай эталонный сбалансированный мастер-промпт высокого качества."

    user_message = f"""СКОМПИЛИРУЙ СИСТЕМНЫЙ ПРОМПТ ДЛЯ НОДЫ: `{normalized_step}`

НАЗВАНИЕ/ПРЕСЕТ: {target_name or 'custom_preset'}
ТЕМА ПРОЕКТА: {project_topic or 'Не указана'}
ПОЛЬЗОВАТЕЛЬСКАЯ ЗАДАЧА / НАМЕРЕНИЕ:
\"\"\"{clean_intent}\"\"\"

{guideline}

Выдай готовый .md промпт:
"""

    gpt = get_gpt_client()
    logger.info("meta_agent: компиляция промпта для step={} intent='{}'...", normalized_step, clean_intent[:50])

    full_query = f"{META_AGENT_SYSTEM_PROMPT}\n\n---\n\n{user_message}"
    raw_response = await gpt.ask_fresh(full_query, timeout=120.0)

    compiled_text = sanitize_compiled_prompt(raw_response)
    if not compiled_text:
        raise RuntimeError("Мета-Агент вернул пустой результат компиляции.")

    # Быстрый анализ сгенерированного промпта
    has_json_schema = "json" in compiled_text.lower() or "{" in compiled_text
    has_guards = any(w in compiled_text.lower() for w in ["strict", "constraint", "no-chat", "запрещено", "only"])

    return {
        "step_code": normalized_step,
        "name": target_name or "custom_agent_prompt",
        "compiled_prompt": compiled_text,
        "stats": {
            "length_chars": len(compiled_text),
            "lines_count": len(compiled_text.splitlines()),
            "has_schema": has_json_schema,
            "has_guards": has_guards,
        }
    }