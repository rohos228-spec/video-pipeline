"""Анализ референсных изображений → запись стиля в формате агента + категории.

Поток: пачки изображений (vision, до 8 шт за вызов) → заметки по общим
критериям → синтез ОДНОЙ записи стиля (name/desc/category/prompt_core в
формате GEN_ASSISTANT: EN-ядро + RU + «Не …» негативы) → группировка
нескольких стилей в категории по общим критериям.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
BATCH_SIZE = 8  # лимит vision-входа gpt_api: до 8 изображений / 4MB каждое
MAX_IMAGES = 64

_NOTES_SYSTEM = """Ты — аналитик визуальных стилей для генерации изображений.
Тебе дают пачку референсных изображений ОДНОГО стиля.
Опиши только ОБЩИЕ признаки — то, что повторяется у большинства изображений пачки.

Критерии:
- palette: палитра (цвета, контраст, насыщенность)
- line: линия/штрих (контур, текстура, техника, медиум)
- light: свет и атмосфера
- composition: композиция и кадрирование
- subjects: персонажи/объекты (тип, пропорции, анатомия)
- mood: настроение
- forbidden: чего на референсах НЕТ и что сломает стиль (для негативов)

Ответ — СТРОГО валидный JSON без markdown и пояснений:
{"palette": "...", "line": "...", "light": "...", "composition": "...", "subjects": "...", "mood": "...", "forbidden": "..."}"""

_SYNTH_SYSTEM = """Ты — редактор библиотеки стилей. Ниже — JSON-заметки анализа нескольких пачек референсных изображений одного стиля.
Собери из них ОДНУ запись стиля для агента промптов. Оставляй только признаки, общие для большинства заметок.

Ответ — СТРОГО валидный JSON без markdown:
{
  "name": "короткое имя стиля (RU)",
  "desc": "одно предложение описания (RU)",
  "category": "предлагаемая категория (RU)",
  "prompt_core": "<EN-ядро: стиль, медиум, палитра, свет, композиция, субъекты>\n\nRU: <то же кратко по-русски>\n\nНе <негативы через запятую: всё, что ломает стиль>"
}

Правила prompt_core:
1. Начинается с EN-ядра (это уходит в генератор изображений).
2. Блок «Не …» обязателен — из критерия forbidden.
3. Без нумерации, без комментариев, без markdown."""

AGENT_MAX_CHARS = 8000
AGENT_MAX_BYTES = 8000
AGENT_PROMPT_MAX_CHARS = 4700

_AGENT_SYSTEM = """Ты — конструктор агентов для генерации изображений.
Тебе дают JSON-заметки анализа референсов одного визуального стиля и, возможно,
уточнение пользователя. Ты пишешь ГОТОВОГО АГЕНТА — системный текст, который
затем получит другая модель, чтобы по запросу пользователя выдавать промпты
для GPT Image 2 / Nano Banana Pro в этом стиле.

Формат ответа строго такой, без markdown и пояснений:
NAME: короткое имя стиля (RU, до 3 слов)
DESC: одно предложение описания (RU)
CATEGORY: предлагаемая категория (RU)
AGENT:
<дальше сплошной текст агента>

Правила текста агента:
1. Первая фраза: агент отвечает ОДНИМ готовым промптом и ничем больше — без
   пояснений, заголовков, markdown, списков, нумерации и квадратных скобок.
2. Дальше «ядро», которое агент обязан копировать в каждый промпт дословно:
   один английский абзац с медиумом, палитрой (точные hex, если цвета на
   референсах постоянные), линией/штрихом, светом, композицией, типографикой.
   Постоянный цвет фиксируй как «exactly hex #RRGGBB», а не словом.
3. Потом что агент дописывает сам: порядок английских фраз про конкретный
   кадр (герой, композиция, подписи в кавычках на языке запроса, финальная
   строка-негатив). Никаких слотов в квадратных скобках — только описание
   словами, что должно стоять на этом месте.
4. Бюджет содержания: сколько объектов и подписей допустимо, чтобы модель не
   путала буквы; напоминание брать факты только из запроса.
5. Один пример готового промпта целиком, на теме, которой нет в запросе, с
   пометкой, что содержание примера переносить нельзя.
6. Финальная проверка: нет скобок и служебных слов, ядро на месте, длина
   промпта не больше 4700 знаков.
7. Весь текст агента — не длиннее 7000 знаков, по-русски пиши коротко."""

_CATEGORIES_SYSTEM = """Ты — редактор библиотеки стилей. Дан JSON-список стилей (name/desc/category/prompt_core).
Сгруппируй их в категории по ОБЩИМ критериям: техника/медиум, палитра, настроение, назначение.
Один стиль — ровно в одной категории. Названия категорий короткие (RU).

Ответ — СТРОГО валидный JSON без markdown:
{"categories": [{"name": "...", "criteria": "почему эти стили вместе", "style_names": ["...", "..."]}]}"""


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """Первый сбалансированный {...} из ответа LLM → dict (или None)."""
    text = (raw or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def collect_image_paths(paths: list[str | Path]) -> list[Path]:
    """Валидация: существуют, картинки, не больше MAX_IMAGES."""
    out: list[Path] = []
    for p in paths:
        fp = Path(p)
        if not fp.is_file():
            raise ValueError(f"Файл не найден: {fp}")
        if fp.suffix.lower() not in IMG_EXTS:
            raise ValueError(f"Не изображение: {fp.name}")
        out.append(fp)
    if not out:
        raise ValueError("Нет изображений для анализа")
    if len(out) > MAX_IMAGES:
        raise ValueError(f"Слишком много изображений: {len(out)} > {MAX_IMAGES}")
    return out


def batch_paths(paths: list[Path], size: int = BATCH_SIZE) -> list[list[Path]]:
    return [paths[i : i + size] for i in range(0, len(paths), size)]


def _normalize_entry(data: dict[str, Any], *, name_hint: str | None) -> dict[str, str]:
    """Запись стиля в формате фронтовой библиотеки (GenStyleDef-подобной)."""
    name = str(data.get("name") or name_hint or "Новый стиль").strip()
    return {
        "name": name,
        "desc": str(data.get("desc") or "").strip(),
        "category": str(data.get("category") or "Без категории").strip(),
        "prompt_core": str(data.get("prompt_core") or data.get("promptCore") or "").strip(),
    }


async def analyze_style_images(
    paths: list[str | Path],
    *,
    name_hint: str | None = None,
) -> dict[str, Any]:
    """Изображения → запись стиля {name, desc, category, prompt_core} + сырые заметки."""
    images = collect_image_paths(paths)
    from app.services.gpt_client import get_gpt_client

    client = get_gpt_client()

    # 1) Пачки → заметки по общим критериям
    notes: list[dict[str, Any]] = []
    for i, batch in enumerate(batch_paths(images)):
        raw = await client.ask_with_files(
            "Проанализируй эту пачку референсов по критериям из system-промпта.",
            batch,
            system=_NOTES_SYSTEM,
            timeout=240,
            max_retries=1,
        )
        note = parse_json_object(raw)
        if note:
            notes.append(note)
        else:
            logger.warning("style_analyzer: пачка {} — нечитаемый ответ, пропуск", i + 1)
    if not notes:
        raise ValueError("LLM не смогла описать ни одной пачки — попробуйте меньше изображений")

    # 2) Синтез одной записи стиля
    synth_user = json.dumps(notes, ensure_ascii=False, indent=2)
    if name_hint:
        synth_user += f"\n\nПодсказка названия от пользователя: {name_hint}"
    raw = await client.ask_with_files(
        synth_user, [], system=_SYNTH_SYSTEM, timeout=240, max_retries=1
    )
    data = parse_json_object(raw)
    if not data:
        raise ValueError("LLM вернула нечитаемую запись стиля")
    entry = _normalize_entry(data, name_hint=name_hint)
    if not entry["prompt_core"]:
        raise ValueError("LLM вернула запись без prompt_core")
    logger.info("style_analyzer: «{}» ← {} изображений, {} пачек", entry["name"], len(images), len(notes))
    return {**entry, "images": len(images), "notes": notes}


def strip_markdown_fences(text: str) -> str:
    """Убирает ```-обёртки: агент должен быть сплошным текстом."""
    out = re.sub(r"^\s*```[a-zA-Z]*\s*\n?", "", (text or "").strip())
    out = re.sub(r"\n?```\s*$", "", out)
    return out.replace("```", "").strip()


def fit_agent_text(text: str) -> tuple[str, str | None]:
    """Агент должен влезать и в 8000 знаков, и в 8000 байт UTF-8.

    Режем по абзацам с конца: лучше короткий рабочий агент, чем обрубок фразы.
    """
    out = strip_markdown_fences(text)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    if len(out) <= AGENT_MAX_CHARS and len(out.encode("utf-8")) <= AGENT_MAX_BYTES:
        return out, None
    paras = out.split("\n\n")
    while paras and (
        len("\n\n".join(paras)) > AGENT_MAX_CHARS
        or len("\n\n".join(paras).encode("utf-8")) > AGENT_MAX_BYTES
    ):
        paras.pop()
    trimmed = "\n\n".join(paras).strip()
    if not trimmed:
        trimmed = out[:2000]
    return trimmed, "Агент был длиннее лимита — хвост обрезан, проверьте текст"


def parse_agent_reply(raw: str, *, name_hint: str | None = None) -> dict[str, str]:
    """Ответ вида NAME/DESC/CATEGORY/AGENT → запись стиля."""
    text = strip_markdown_fences(raw)
    if not text:
        raise ValueError("LLM вернула пустой ответ")

    def field(key: str) -> str:
        m = re.search(rf"^{key}\s*:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    m_agent = re.search(r"^AGENT\s*:\s*\n?", text, re.MULTILINE | re.IGNORECASE)
    agent = text[m_agent.end() :].strip() if m_agent else ""
    if not agent:
        # Ответ без разметки полей — считаем весь текст агентом.
        agent = text
    agent, warning = fit_agent_text(agent)
    if len(agent) < 200:
        raise ValueError("LLM вернула слишком короткого агента")
    return {
        "name": field("NAME") or (name_hint or "Новый стиль"),
        "desc": field("DESC"),
        "category": field("CATEGORY") or "Без категории",
        "agent": agent,
        "warning": warning or "",
    }


async def build_style_agent(
    paths: list[str | Path],
    *,
    user_request: str = "",
    name_hint: str | None = None,
) -> dict[str, Any]:
    """Референсы (+ пожелание пользователя) → готовый текст агента стиля."""
    images = collect_image_paths(paths)
    from app.services.gpt_client import get_gpt_client

    client = get_gpt_client()

    notes: list[dict[str, Any]] = []
    for i, batch in enumerate(batch_paths(images)):
        raw = await client.ask_with_files(
            "Проанализируй эту пачку референсов по критериям из system-промпта.",
            batch,
            system=_NOTES_SYSTEM,
            timeout=240,
            max_retries=1,
        )
        note = parse_json_object(raw)
        if note:
            notes.append(note)
        else:
            logger.warning("build_style_agent: пачка {} — нечитаемый ответ, пропуск", i + 1)
    if not notes:
        raise ValueError("LLM не смогла описать референсы — попробуйте другие изображения")

    user = json.dumps(notes, ensure_ascii=False, indent=2)
    if (user_request or "").strip():
        user += f"\n\nУточнение пользователя (учти его в агенте): {user_request.strip()}"
    if name_hint:
        user += f"\n\nПодсказка названия: {name_hint}"
    raw = await client.ask_with_files(user, [], system=_AGENT_SYSTEM, timeout=300, max_retries=1)
    entry = parse_agent_reply(raw, name_hint=name_hint)
    logger.info(
        "build_style_agent: «{}» ← {} изображений, {} знаков / {} байт",
        entry["name"],
        len(images),
        len(entry["agent"]),
        len(entry["agent"].encode("utf-8")),
    )
    return {
        **entry,
        "images": len(images),
        "chars": len(entry["agent"]),
        "bytes": len(entry["agent"].encode("utf-8")),
        "notes": notes,
    }


async def categorize_styles(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Список записей стилей → категории по общим критериям."""
    if not entries:
        raise ValueError("Пустой список стилей")
    from app.services.gpt_client import get_gpt_client

    client = get_gpt_client()
    slim = [
        {
            "name": e.get("name"),
            "desc": e.get("desc"),
            "category": e.get("category"),
            "prompt_core": str(e.get("prompt_core") or "")[:600],
        }
        for e in entries
    ]
    raw = await client.ask_with_files(
        json.dumps(slim, ensure_ascii=False, indent=2),
        [],
        system=_CATEGORIES_SYSTEM,
        timeout=240,
        max_retries=1,
    )
    data = parse_json_object(raw)
    cats = data.get("categories") if data else None
    if not isinstance(cats, list) or not cats:
        raise ValueError("LLM вернула нечитаемый список категорий")
    return {"categories": cats, "styles": len(entries)}
