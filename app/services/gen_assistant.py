"""Помощник генерации: LLM-агент промптов + обвязка-валидатор.

Поток: запрос + текст агента стиля + формат + N → активная текстовая LLM
(gpt_client) → строгий JSON-контракт → парсинг/валидация → ровно N промптов.
LLM недоступна или ответ нечитаемый → локальная сборка (fallback), ничего
не падает: фронт всегда получает count промптов.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

MIN_COUNT = 1
MAX_COUNT = 4
MAX_REQUEST_CHARS = 2000
MAX_AGENT_CHARS = 8000
MIN_PROMPT_CHARS = 40
MAX_PROMPT_CHARS = 8000
# Короткие ядра стиля (trash polka, pixel…) можно приклеить в начало.
# Длинный шаблон агента (~7k) приклеивать нельзя: срез по MAX_PROMPT_CHARS
# оставляет только YAML со слотами [ГЕРОЙ], а заполненный промпт отрезается.
PREPEND_CORE_MAX = 480

# Вариативные суффиксы для локальной добивки (ракурс/действие)
_VARIANT_HINTS = [
    "",
    "другой ракурс: крупнее, акцент на главном объекте",
    "другой ракурс: шире, больше окружения и воздуха",
    "другой ракурс: со спины / сбоку, иная композиция",
]

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


def write_agent_prompt_file(system: str, tmp_dir: Path) -> Path:
    """Мастер-промт агента как .md — gpt_client читает первый .md/.txt как master."""
    path = tmp_dir / "prompt_gen_assistant.md"
    path.write_text(system, encoding="utf-8")
    return path


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


def _local_variant(core: str, request: str, aspect: str, idx: int, total: int) -> str:
    """Локальная сборка одного промпта (зеркало фронтовой assembleGenPrompt)."""
    req = request.strip()
    if len(core.strip()) > PREPEND_CORE_MAX:
        # Длинный шаблон — не слать его в генератор: там примеры (початок) бьют запрос.
        base = (
            f"{req}. Photorealistic magazine poster infographic of this subject only, "
            f"not example objects from the style guide. Aspect ratio: {aspect}."
        )
    else:
        base = f"{core.strip()}\n\n{req}\n\nФормат кадра: {aspect}."
    hint = _VARIANT_HINTS[idx % len(_VARIANT_HINTS)]
    if total > 1 and hint:
        base += f"\nВариант {idx + 1} из {total}: {hint}."
    return base


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
    """Чистка + контроль количества. Возвращает (промпты, были_локальные_вставки)."""
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
        if len(core) > PREPEND_CORE_MAX and tokens and not any(t.lower() in p.lower() for t in tokens):
            continue
        if prepend_core and core_key not in p.lower():
            p = f"{core} — {p}"
        # дубли НЕ отбрасываем: если просят одинаковые/схожие — так и надо
        out.append(p[:MAX_PROMPT_CHARS])
        if len(out) >= count:
            break
    padded = len(out) < count
    while len(out) < count:
        out.append(_local_variant(core, request, aspect, len(out), count))
    return out[:count], padded


async def generate_prompts(
    *,
    request: str,
    agent_text: str,
    aspect: str = "9:16",
    count: int = 1,
) -> dict[str, Any]:
    """Агент + обвязка: валидация → LLM → парсинг → ровно count промптов."""
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
    if len(agent_text) > MAX_AGENT_CHARS:
        raise ValueError(f"Текст агента длиннее {MAX_AGENT_CHARS} символов — сократите")

    system = _SYSTEM_TEMPLATE.format(agent_text=agent_text, aspect=aspect)
    # Предмет — в начале master-файла, иначе 7k правил перевешивают короткий запрос.
    master = (
        f"ПРЕДМЕТ КАДРА (единственный герой; примеры из правил игнорировать):\n"
        f"{request}\n\n{system}\n\nN = {count}"
    )
    user = f"Предмет кадра: {request}\nN = {count}"

    source = "llm"
    warning: str | None = None
    raw_prompts: list[str] = []
    tmp_root: Path | None = None
    try:
        from app.services.gpt_client import get_gpt_client

        client = get_gpt_client()
        tmp_root = Path(tempfile.mkdtemp(prefix="gen_assistant_"))
        prompt_file = write_agent_prompt_file(master, tmp_root)
        logger.info(
            "gen_assistant: attach prompt_file={} chars={} request_chars={}",
            prompt_file.name,
            len(system),
            len(user),
        )
        # Файл, не system=: иначе master=— и vibecode chat/completions даёт 400.
        raw = await client.ask_with_files(user, [prompt_file], timeout=180, max_retries=1)
        raw_prompts = parse_prompts_reply(raw)
        # Не хватило вариантов — один добивающий запрос
        if 0 < len(raw_prompts) < count:
            missing = count - len(raw_prompts)
            logger.info("gen_assistant: LLM дала {} из {}, добиваем {}", len(raw_prompts), count, missing)
            raw2 = await client.ask_with_files(
                f"{user}\n\nПрошлый ответ дал только {len(raw_prompts)} промпт(ов). "
                f"Дай ещё {missing} НОВЫХ варианта (не повторяя прошлые), тем же JSON-контрактом.",
                [prompt_file],
                timeout=120,
                max_retries=1,
            )
            raw_prompts += parse_prompts_reply(raw2)
    except Exception as e:  # noqa: BLE001 — LLM/сеть/ключ: уходим в локальную сборку
        logger.warning("gen_assistant: LLM недоступна ({}), локальная сборка", e)
        source = "local"
        warning = f"LLM недоступна ({e}) — промпты собраны локально"
        raw_prompts = []
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)

    if not raw_prompts and source == "llm":
        source = "local"
        warning = "LLM вернула нечитаемый ответ — промпты собраны локально"

    prompts, padded = sanitize_prompts(
        raw_prompts, count=count, core=agent_text, request=request, aspect=aspect
    )
    if source == "llm" and padded:
        warning = "Часть промптов добита локальной сборкой (LLM дала меньше N)"
    logger.info(
        "gen_assistant: source={} count={} padded={} request={!r} out0_len={} out0_head={!r}",
        source,
        len(prompts),
        padded,
        request[:120],
        len(prompts[0]) if prompts else 0,
        (prompts[0][:180] if prompts else ""),
    )
    return {
        "prompts": prompts,
        "count": len(prompts),
        "source": source,
        "warning": warning,
        "aspect": aspect,
    }
