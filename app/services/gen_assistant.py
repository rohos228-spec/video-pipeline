"""Помощник генерации: LLM-агент промптов + обвязка-валидатор.

Поток: запрос + текст агента стиля + формат + N → активная текстовая LLM
(gpt_client) → строгий JSON-контракт → парсинг/валидация.
Без готового промпта от LLM генерация не стартует.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from loguru import logger

_LLM_LOCK = asyncio.Lock()

MIN_COUNT = 1
MAX_COUNT = 4
MAX_REQUEST_CHARS = 2000
MAX_AGENT_CHARS = 16000
MIN_PROMPT_CHARS = 40
MAX_PROMPT_CHARS = 8000
# Короткие ядра стиля (trash polka, pixel…) можно приклеить в начало.
# Длинный шаблон агента (~7k) приклеивать нельзя: срез по MAX_PROMPT_CHARS
# оставляет только YAML со слотами [ГЕРОЙ], а заполненный промпт отрезается.
PREPEND_CORE_MAX = 480

# Вариативные суффиксы для локальной добивки (ракурс/действие)
_STUB_MARKS = (
    "not example objects from the style guide",
    "subject of this image (the only topic)",
    "depict this request as one finished scene",
    "агент отвечает одним готовым промптом",
    "каждый промпт начинай с дословно скопированного ядра",
    "после ядра допиши",
    "агент собран из разбора референсов",
)
_STUB_MARK = _STUB_MARKS[0]


def is_stub_agent_text(text: str) -> bool:
    """Локальная обёртка / fallback — не текст, который написала LLM."""
    low = (text or "").lower()
    return bool(low.strip()) and any(mark in low for mark in _STUB_MARKS)

_SYSTEM_TEMPLATE = """Ты — агент визуальных промптов для генерации изображений.

ПРАВИЛА АГЕНТА ниже — это инструкция, КАК собрать промпт. Их НЕЛЬЗЯ копировать
в выход: ни YAML, ни markdown-заголовки агента, ни таблицу слотов.
Примеры предметов в правилах (початок, двигатель, кроссовок, гриб, зуб…)
НЕ являются заданием. Герой кадра = ТОЛЬКО запрос пользователя (блок
«ПРЕДМЕТ КАДРА»). Если пользователь просит не початок — початок в кадре запрещён.

АЛГОРИТМ (в ответ — только финальные промпты):
ШАГ 1 — АНАЛИЗ ЗАПРОСА. Главный предмет = формулировка пользователя. Построй
его детальное описание: форма, материалы, цвета, состояние, окружение, свет,
камера. Чего нет в запросе — додумай, не подменяя героя примером из правил.
ШАГ 2 — ПРОМПТЫ. Примени правила агента (композиция, палитра, сетка, типографика)
к ЭТОМУ герою. Заполни все слоты молча. На выходе — готовый текст для генератора
картинок (обычно английский visual prompt). Формат кадра {aspect}.

ЖЁСТКИЕ ПРАВИЛА:
1. В задании указано число N — в ответе РОВНО N промптов. Не больше и не меньше.
2. Каждый промпт РАЗВЁРНУТЫЙ. Предмет из запроса назван явно.
3. По умолчанию промпты различаются ракурсом/деталями (герой тот же). Если в
   запросе «одинаковые»/«схожие» — делай одинаковые.
4. Формат ответа — СТРОГО валидный JSON без markdown:
   {{"prompts": ["промпт 1", "промпт 2", ...]}}
5. Внутри промптов нет YAML, нет «name: infographic», нет квадратных скобок-слотов
   вроде [ГЕРОЙ], нет копипасты правил агента.

ПРАВИЛА АГЕНТА (применить, не копировать):
{agent_text}"""


_UNFILLED_SLOT_RE = re.compile(r"\[(?:ГЕРОЙ|ЭТАЖ[^\]]*|ЦВЕТ|СЕКЦИЯ|КАК ВСКРЫТ[^\]]*)\]")
_REQUEST_STOP = frozenset(
    {
        "этот",
        "чтобы",
        "только",
        "сделай",
        "сделать",
        "нужно",
        "просто",
        "картинка",
        "постер",
        "инфографика",
        "промпт",
        "пожалуйста",
    }
)


def request_tokens(request: str) -> list[str]:
    """Значимые слова запроса: они обязаны попасть в промпт для картинки."""
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{4,}", request or "")
    return [w for w in words if w.lower() not in _REQUEST_STOP]


def looks_like_agent_echo(prompt: str, core: str) -> bool:
    """LLM вернула правила агента вместо готового промпта для картинки."""
    p = (prompt or "").strip()
    if not p:
        return True
    head = p[:120].lstrip()
    if head.startswith("---") and "name:" in head.lower():
        return True
    if _UNFILLED_SLOT_RE.search(p):
        return True
    core = (core or "").strip()
    if len(core) > PREPEND_CORE_MAX:
        core_head = re.sub(r"\s+", " ", core[:100]).strip()
        body = re.sub(r"\s+", " ", p[:240])
        if core_head and core_head[:50] in body:
            return True
    return False


_CORE_RE = re.compile(
    r"(?:скопированного\s+ядра|ядра)[^\n]{0,80}:\s*\n+(.+?)(?:\n+После\s+ядра|\n+В\s+финальной|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_NEG_RE = re.compile(
    r"(?:негативе\s+обязательно\s*:|NEGATIVE\s*\n)\s*(.+?)(?:\n|\Z)",
    re.IGNORECASE,
)


def extract_agent_core(agent_text: str) -> str:
    """Ядро стиля из длинного агента — без инструкции и без темы референсов."""
    from app.services.style_analyzer import scrub_style_topic

    text = (agent_text or "").strip()
    if not text:
        return ""
    raw = ""
    m = _CORE_RE.search(text)
    if m:
        core = re.sub(r"\s+", " ", m.group(1)).strip()
        if len(core) >= 40:
            raw = core[:4000]
    if not raw and (
        len(text) <= PREPEND_CORE_MAX
        and not text.lower().startswith("агент отвечает")
        and not any(
            mark in text.lower() for mark in ("\n#", "```", "шаблон prompt", "чек-лист")
        )
    ):
        raw = text
    if not raw:
        for para in re.split(r"\n\s*\n", text):
            p = para.strip()
            if len(p) < 80:
                continue
            if p.startswith(("#", "---", "|")) or p.lower().startswith("агент отвечает"):
                continue
            raw = re.sub(r"\s+", " ", p)[:4000]
            break
    return scrub_style_topic(raw) or raw


def extract_agent_negatives(agent_text: str) -> str:
    m = _NEG_RE.search(agent_text or "")
    return (m.group(1).strip() if m else "")[:500]


def local_visual_prompt(
    *,
    request: str,
    agent_text: str,
    aspect: str,
    ref_labels: list[str] | None = None,
) -> str:
    """Готовый visual-промпт: визуал стиля + предмет из запроса. Без темы референсов."""
    core = extract_agent_core(agent_text)
    if not core:
        core = (
            "Follow the selected style: same medium, palette, lighting, "
            "composition and typography. One finished image, no collage."
        )
    req = (request or "").strip()
    parts = [core]
    if req:
        parts.append(
            f"Subject of this image (the only topic): {req}. "
            "Depict this request as one finished scene in the style above. "
            "Ignore any topics or objects that came from style references. "
            "If the request is a list, benefits, steps or facts, show them as "
            "clear labels and diagrams. Do not copy the user's wording as a "
            "lonely caption — build the actual picture."
        )
    labels = [str(x).strip() for x in (ref_labels or []) if str(x).strip()]
    if labels:
        parts.append("Use attached references only for look, not for topic: " + ", ".join(labels))
    parts.append(
        "Aspect ratio: 9:16. Vertical, rule of thirds, читаемый силуэт для shorts."
        if (aspect or "").strip() == "9:16"
        else f"Aspect ratio: {(aspect or '9:16').strip()}."
    )
    neg = extract_agent_negatives(agent_text)
    if neg:
        parts.append(f"Avoid: {neg}")
    return "\n\n".join(parts)


def is_unfilled_prompt(prompt: str, request: str = "") -> bool:
    """Сырой запрос / локальная заглушка — в генератор картинки слать нельзя."""
    p = (prompt or "").strip()
    req = (request or "").strip()
    if not p:
        return True
    low = p.lower()
    if any(mark in low for mark in _STUB_MARKS):
        return True
    if req:
        head = req[:80].lower()
        if head and p.lower().startswith(head) and len(p) <= len(req) + 200:
            return True
    return False


def parse_prompts_reply(raw: str) -> list[str]:
    """Разбор ответа LLM: строгий JSON → нумерованный список → абзацы."""
    text = (raw or "").strip()
    if not text:
        return []
    # 1) JSON (ищем первый сбалансированный блок {...})
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = json.loads(m.group(0))
            items: Any = data.get("prompts") if isinstance(data, dict) else None
            if isinstance(items, list):
                return [str(p) for p in items]
        except (json.JSONDecodeError, AttributeError):
            pass
    # 2) Нумерованный / маркированный список
    parts = [p.strip() for p in re.split(r"(?m)^\s*(?:\d+[.)]|[-•*])\s+", text) if p.strip()]
    if len(parts) >= 2:
        return parts
    # 3) Абзацы через пустую строку
    return [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()]


def sanitize_prompts(
    prompts: list[str],
    *,
    count: int,
    core: str,
    request: str,
    aspect: str,
) -> tuple[list[str], bool]:
    """Чистка. Возвращает (готовые промпты, не хватило до count). Без локальных заглушек."""
    core = core.strip()
    core_key = core[:40].lower()
    prepend_core = bool(core_key) and len(core) <= PREPEND_CORE_MAX
    tokens = request_tokens(request)
    out: list[str] = []
    for p in prompts:
        raw = str(p).strip()
        if len(core) <= PREPEND_CORE_MAX:
            p = re.sub(r"\s+", " ", raw).strip()
        else:
            p = raw
        if len(p) < MIN_PROMPT_CHARS:
            continue
        if looks_like_agent_echo(p, core):
            continue
        if is_unfilled_prompt(p, request):
            continue
        if (
            len(core) > PREPEND_CORE_MAX
            and len(p) < 400
            and tokens
            and not any(t.lower() in p.lower() for t in tokens)
        ):
            continue
        if prepend_core and core_key not in p.lower():
            p = f"{core} — {p}"
        out.append(p[:MAX_PROMPT_CHARS])
        if len(out) >= count:
            break
    incomplete = len(out) < count
    return out, incomplete


async def generate_prompts(
    *,
    request: str,
    agent_text: str,
    aspect: str = "9:16",
    count: int = 1,
    ref_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Агент + обвязка. Без готового промпта — ValueError, картинка не стартует."""
    request = (request or "").strip()
    agent_text = (agent_text or "").strip()
    aspect = (aspect or "9:16").strip() or "9:16"
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1
    count = max(MIN_COUNT, min(MAX_COUNT, count))

    if not request:
        raise ValueError("Пустой запрос: напишите, что должно быть в кадре")
    if len(request) > MAX_REQUEST_CHARS:
        raise ValueError(f"Запрос длиннее {MAX_REQUEST_CHARS} символов — сократите")
    if not agent_text:
        raise ValueError("Не выбран стиль: текст агента пуст")
    if is_stub_agent_text(agent_text):
        raise ValueError("Агент не написан LLM — заглушка запрещена, генерация не запущена")
    if len(agent_text) > MAX_AGENT_CHARS:
        raise ValueError(f"Текст агента длиннее {MAX_AGENT_CHARS} символов — сократите")

    system = _SYSTEM_TEMPLATE.format(agent_text=agent_text, aspect=aspect)
    master = (
        f"ПРЕДМЕТ КАДРА (единственный герой; примеры из правил игнорировать):\n"
        f"{request}\n\n{system}\n\nN = {count}"
    )
    user = f"Предмет кадра: {request}\nN = {count}"
    labels = [str(x).strip() for x in (ref_labels or []) if str(x).strip()]
    if labels:
        listed = "\n".join(labels)
        ref_block = (
            f"\n\nРЕФЕРЕНСЫ (файлы уже приложены к генератору в этом порядке):\n{listed}\n"
            "Если в запросе есть @imageN — оставь этот тег в выходном промпте."
        )
        master += ref_block
        user += f"\n{listed}"
    retry_user = (
        f"{user}\n\nПрошлый ответ нельзя слать в генератор картинки "
        f"(копия правил, YAML, слоты вроде [ГЕРОЙ], пустой JSON или сырой запрос). "
        f'Верни СТРОГО JSON {{"prompts": ["готовый visual prompt"]}} — ровно {count} '
        f"развёрнутых промпта(ов) для картинки, без YAML и без копирования правил."
    )

    raw_prompts: list[str] = []
    last_err = ""
    try:
        from app.services.gpt_api import chat

        logger.info(
            "gen_assistant: chat system_chars={} request_chars={}",
            len(master),
            len(user),
        )
        async with _LLM_LOCK:
            result = await chat(
                prompt=user,
                system=master,
                timeout=180,
                max_retries=4,
                auto_pack=False,
            )
            raw_prompts = parse_prompts_reply(result.text or "")
            good, incomplete = sanitize_prompts(
                raw_prompts, count=count, core=agent_text, request=request, aspect=aspect
            )
            if incomplete:
                logger.warning(
                    "gen_assistant: usable={}/{} after first reply, retry",
                    len(good),
                    count,
                )
                result2 = await chat(
                    prompt=retry_user,
                    system=master,
                    timeout=120,
                    max_retries=3,
                    auto_pack=False,
                )
                extra = parse_prompts_reply(result2.text or "")
                raw_prompts = [*good, *extra] if good else extra
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        last_err = str(e)
        logger.warning("gen_assistant: LLM недоступна ({})", e)
        raw_prompts = []

    prompts, incomplete = sanitize_prompts(
        raw_prompts, count=count, core=agent_text, request=request, aspect=aspect
    )
    if not prompts:
        detail = last_err or "пустой или неготовый ответ"
        raise ValueError(
            f"Агент недоступен ({detail}) — промпт не собран, генерация не запущена"
        )
    source = "llm"
    warning = (
        f"Агент собрал {len(prompts)} из {count} — генерация только по готовым"
        if incomplete
        else None
    )
    logger.info(
        "gen_assistant: source={} count={} incomplete={} request={!r} out0_len={} out0_head={!r}",
        source,
        len(prompts),
        incomplete,
        request[:120],
        len(prompts[0]),
        prompts[0][:180],
    )
    return {
        "prompts": prompts,
        "count": len(prompts),
        "source": source,
        "warning": warning,
        "aspect": aspect,
    }
