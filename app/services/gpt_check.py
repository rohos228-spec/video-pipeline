"""GPT-проверка артефактов пайплайна с поддержкой 3-х видов вердиктов:

* «одобрено» (плюс необязательная численная оценка `score: 0.7`),
* «перегенерация …» (с инструкцией-подсказкой),
* возвращённый GPT-ом файл — пересохраняем и считаем обновлённой версией.

Этот модуль НЕ управляет логикой ретраев и подтверждениями в TG —
это задача вызывающего шага. Здесь только:
  1) формирование полного «сэндвича» (промт + сопровождение + артефакт),
  2) отправка в ChatGPTBot,
  3) парсинг свободного ответа в структуру `GptCheckResult`,
  4) попытка скачать прикреплённый файл (если есть).

Контракт ответа GPT (см. promts/check_<kind>/default.md):
  - Если шаг прошёл проверку — GPT отвечает текстом, содержащим слово
    «одобрено» (или `approved`).
  - Если шаг ПРОВАЛЕН и GPT может предложить инструкцию для
    перегенерации — отвечает фразой, начинающейся с «перегенерация»
    (или `regenerate`). Дальше — текст-инструкция (что именно поменять).
  - Если у артефакта была численная оценка (например для видео) — GPT
    добавляет строку `score: 0.85` (или `оценка: 0.85`).
  - Если GPT хочет ЗАМЕНИТЬ артефакт (например прислал новый excel/текст)
    — он прикладывает файл как attachment. Тогда decision = `replace_artifact`.

Парсер регистронезависим и толерантен к окружающим словам —
«план одобрен» / «✅ Одобрено» / «approved by reviewer» все попадают в
`approved`. Однако «перегенерация» и его варианты приоритетнее
(если в ответе встречаются ОБА токена — выбираем `regenerate`,
потому что чаще всего «перегенерируй так, чтобы X был одобрен» означает
именно регенерацию).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger


class GptCheckDecision(str, Enum):
    """Финальный вердикт после `gpt_check_*`.

    Различие между `regenerate` и `regenerate_with_reference`:

    * `regenerate`              — просто повторить генерацию с ИСХОДНЫМ промтом
                                  (без референса, без подсказки) — «перегенерация».
    * `regenerate_with_reference` — перегенерировать ИСПОЛЬЗУЯ исходный артефакт как
                                  референс + новый промт из hint — «перегенерация на основе референса: ...».
      Используется в шагах 7 (картинки) и 9 (видео).
    """

    approved = "approved"
    regenerate = "regenerate"
    regenerate_with_reference = "regenerate_with_reference"
    replace_artifact = "replace_artifact"
    parse_error = "parse_error"
    timeout = "timeout"


@dataclass
class GptCheckResult:
    """Структурированный итог проверки.

    Поля:
      decision        — основной вердикт.
      raw_response    — сырой текст ответа GPT (для логов / TG).
      hint            — для regenerate: текст инструкции, идущий после
                        слова «перегенерация» (или весь ответ, если разделить
                        не получилось).
      replaced_path   — для replace_artifact: путь к скачанному файлу.
      score           — численная оценка GPT (0.0-1.0), если была.
                        Используется в шаге 9 для выбора «лучшего из 3х».
    """

    decision: GptCheckDecision
    raw_response: str
    hint: str = ""
    replaced_path: Path | None = None
    score: float | None = None


# ============================================================
# Парсер ответа GPT
# ============================================================


# Ключевые слова — регистронезависимо, с учётом эмодзи/пунктуации вокруг.
_APPROVED_TOKENS = (
    "одобрено",
    "одобряю",
    "одобрен",
    "approved",
    "approve",
)
_REGENERATE_TOKENS = (
    "перегенерация",
    "перегенерировать",
    "перегенерируй",
    "regenerate",
    "regen",
)
# Маркеры «на основе референса» — если встречаются в близкой окрестности от regen-токена,
# значит нужно прикрепить исходный артефакт как референс в outsee.
_REFERENCE_MARKERS = (
    "на основе референса",
    "на основе референси",  # опечатка-вариант
    "с референсом",
    "используя референс",
    "с использованием референса",
    "with reference",
    "using reference",
)

# Регулярка для score / оценки. Принимает форматы:
#   score: 0.85
#   score=0.85
#   оценка: 0.85
#   оценка — 0.85
#   "score": 0.85   (на случай если GPT отдал JSON-вариант)
_SCORE_RE = re.compile(
    r'(?i)(?:["\']?(?:score|оценка|рейтинг|rating)["\']?)\s*[:=\-–—]\s*([0-9]+(?:[.,][0-9]+)?)'
)


def _contains_token(text: str, tokens: tuple[str, ...]) -> tuple[bool, int]:
    """Ищет первое вхождение любого токена. Возвращает (нашёл, позиция_конца_токена_в_исходном_тексте).

    Поиск — case-insensitive по слову. Возвращаемая позиция — индекс в
    ОРИГИНАЛЬНОМ (не lower) тексте, конец совпадения.
    """
    lower = text.lower()
    best_pos = -1
    best_end = -1
    for tok in tokens:
        idx = lower.find(tok)
        if idx >= 0 and (best_pos < 0 or idx < best_pos):
            best_pos = idx
            best_end = idx + len(tok)
    return best_pos >= 0, best_end


def _extract_score(text: str) -> float | None:
    """Достаём численную оценку. Возвращаем нормализованное значение в [0, 1]
    (если GPT отдал 0-10 или 0-100, делим на максимум). None — если оценки нет."""
    m = _SCORE_RE.search(text)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    # Эвристика: 0..1 — берём как есть; 1.01..10 — делим на 10; >10..100 — на 100.
    if 0.0 <= val <= 1.0:
        return val
    if val <= 10.0:
        return val / 10.0
    if val <= 100.0:
        return val / 100.0
    return 1.0  # подозрительно большое — клампим


def parse_gpt_response(raw: str) -> tuple[GptCheckDecision, str, float | None]:
    """Парсит свободный текст GPT в (decision, hint, score).

    Правила (по приоритету):
    1. Пустой / только пробелы → parse_error, hint="empty response", score=None.
    2. Есть слово из _REGENERATE_TOKENS:
       а) Рядом (в окне ±100 символов) есть маркер из _REFERENCE_MARKERS →
          regenerate_with_reference. Hint = текст ПОСЛЕ regen-токена
          (включая маркер «на основе референса» и инструкцию).
       б) Иначе → regenerate. Hint = текст ПОСЛЕ regen-токена.
    3. Есть слово из _APPROVED_TOKENS → approved. Hint = "".
    4. Иначе → parse_error, hint=raw[:200] (для отладки).

    Score извлекается во всех случаях, если есть.
    """
    if not raw or not raw.strip():
        return GptCheckDecision.parse_error, "empty response", None

    text = raw.strip()
    score = _extract_score(text)

    regen_found, regen_end = _contains_token(text, _REGENERATE_TOKENS)
    if regen_found:
        # hint — всё после regenerate-токена, чуть подчистив пунктуацию.
        tail = text[regen_end:].lstrip(" :,—-–.").strip()
        if not tail:
            tail = text  # фолбэк: вернём весь ответ

        # Проверяем, есть ли маркер «на основе референса» в окрестности regen-токена
        # (±100 символов от конца токена) — это отличает «перегенерация на основе
        # референса: ...» от простого «перегенерация».
        nearby_start = max(0, regen_end - 50)
        nearby_end = min(len(text), regen_end + 100)
        nearby = text[nearby_start:nearby_end].lower()
        has_reference = any(m in nearby for m in _REFERENCE_MARKERS)
        if has_reference:
            return GptCheckDecision.regenerate_with_reference, tail, score
        return GptCheckDecision.regenerate, tail, score

    approved_found, _ = _contains_token(text, _APPROVED_TOKENS)
    if approved_found:
        return GptCheckDecision.approved, "", score

    # Не нашли явных маркеров — это «не понял что хочет gpt».
    snippet = text[:200]
    return GptCheckDecision.parse_error, snippet, score


# ============================================================
# Сборка «сэндвича» (промт + сопровождение + артефакт)
# ============================================================


def build_check_message(
    check_prompt: str,
    *,
    accompanying_text: str = "",
    artifact_inline_text: str = "",
) -> str:
    """Склеивает части в одно сообщение, которое будет отправлено в окно GPT.

    Порядок: системный промт → пустая строка → сопровождение (если есть) →
    пустая строка → inline-артефакт (если есть, для text-артефактов).

    Для file-артефактов inline-текст обычно пустой (файл идёт вложением).
    """
    parts: list[str] = [check_prompt.rstrip()]
    accomp = (accompanying_text or "").strip()
    if accomp:
        parts.append(accomp)
    art = (artifact_inline_text or "").strip()
    if art:
        parts.append(art)
    return "\n\n".join(parts)


# ============================================================
# Главные функции: gpt_check_*
# ============================================================


async def _try_download_attachment(
    chatgpt_bot: Any,
    *,
    target_path: Path,
    timeout: float,
) -> Path | None:
    """Пробует скачать прикреплённый файл из последнего ответа GPT.

    Возвращает Path к скачанному файлу или None, если файла нет.
    Внутренние ошибки `download_attachment_from_last_reply` (например
    «карточка не найдена») считаем как «GPT не приложил файл» — это не
    ошибка пайплайна, просто текстовый ответ.
    """
    if not hasattr(chatgpt_bot, "download_attachment_from_last_reply"):
        return None
    try:
        result = await chatgpt_bot.download_attachment_from_last_reply(
            target_path, timeout=timeout
        )
    except RuntimeError as e:
        # Это штатная сигнатура «файла нет» из ChatGPTBot.
        logger.info(
            "gpt_check: GPT не приложил файл в ответе ({})",
            type(e).__name__,
        )
        return None
    except Exception as e:  # noqa: BLE001
        # Любая другая ошибка — тоже трактуем как «файла нет», но логируем.
        logger.warning(
            "gpt_check: ошибка скачивания файла из ответа GPT: {}: {}",
            type(e).__name__, e,
        )
        return None
    if result is None or not Path(result).exists():
        return None
    return Path(result)


async def gpt_check_text_artifact(
    *,
    chatgpt_bot: Any,
    check_prompt: str,
    artifact_text: str,
    accompanying_text: str = "",
    new_conversation: bool = True,
    timeout: float = 1200.0,
    download_replacement_to: Path | None = None,
) -> GptCheckResult:
    """Отправляет TEXT-артефакт в GPT и парсит ответ.

    Параметры:
      chatgpt_bot           — экземпляр ChatGPTBot (ответственность вызывающего
                              за browser_session).
      check_prompt          — текст системного промта (читается из
                              prompts/check_<kind>/default.md).
      artifact_text         — текст артефакта (план, сценарий, разбивка…).
      accompanying_text     — необязательный текст-сопровождение в окно ввода.
      new_conversation      — открыть ли новый чат перед отправкой (по
                              умолчанию True; для шага 7 будет False).
      timeout               — сколько секунд ждать ответ GPT (по умолчанию
                              20 минут).
      download_replacement_to
                            — путь, куда сохранить файл если GPT приложил
                              обновлённый артефакт. None — не пытаться
                              скачивать.

    Возвращает GptCheckResult.
    """
    if new_conversation:
        await chatgpt_bot.new_conversation()

    msg = build_check_message(
        check_prompt,
        accompanying_text=accompanying_text,
        artifact_inline_text=artifact_text,
    )
    logger.info(
        "gpt_check_text: отправляю в GPT, размер сообщения = {} chars, "
        "timeout = {}s, new_conversation = {}",
        len(msg), timeout, new_conversation,
    )

    try:
        raw = await chatgpt_bot.ask(msg, timeout=timeout)
    except TimeoutError:
        logger.warning("gpt_check_text: таймаут {}s ожидания ответа GPT", timeout)
        return GptCheckResult(decision=GptCheckDecision.timeout, raw_response="")
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "gpt_check_text: исключение при вызове GPT: {}: {}",
            type(e).__name__, e,
        )
        return GptCheckResult(
            decision=GptCheckDecision.parse_error,
            raw_response=f"[GPT call error] {type(e).__name__}: {e}",
        )

    return await _finalize_result(
        chatgpt_bot=chatgpt_bot,
        raw=raw,
        download_replacement_to=download_replacement_to,
    )


async def gpt_check_file_artifact(
    *,
    chatgpt_bot: Any,
    check_prompt: str,
    artifact_path: Path,
    accompanying_text: str = "",
    new_conversation: bool = True,
    timeout: float = 1200.0,
    download_replacement_to: Path | None = None,
    extra_files: list[Path] | None = None,
) -> GptCheckResult:
    """Отправляет FILE-артефакт (excel/картинку/видео/аудио) + промт в GPT.

    Параметры аналогичны `gpt_check_text_artifact`. Дополнительно:
      artifact_path  — путь к файлу артефакта.
      extra_files    — необязательный список дополнительных файлов, которые
                       прикрепляются вместе с артефактом (например excel
                       промтов вместе с готовой картинкой в шаге 7).

    Возвращает GptCheckResult.
    """
    if new_conversation:
        await chatgpt_bot.new_conversation()

    if not artifact_path.exists():
        return GptCheckResult(
            decision=GptCheckDecision.parse_error,
            raw_response=f"[artifact not found] {artifact_path}",
        )

    msg = build_check_message(
        check_prompt,
        accompanying_text=accompanying_text,
        artifact_inline_text="",
    )
    files = [artifact_path]
    if extra_files:
        files.extend([f for f in extra_files if f.exists()])

    logger.info(
        "gpt_check_file: отправляю в GPT, артефакт={}, доп.файлов={}, "
        "timeout={}s, new_conversation={}",
        artifact_path.name, len(files) - 1, timeout, new_conversation,
    )

    try:
        if len(files) == 1:
            raw = await chatgpt_bot.ask_with_file(msg, files[0], timeout=timeout)
        else:
            raw = await chatgpt_bot.ask_with_files(msg, files, timeout=timeout)
    except TimeoutError:
        logger.warning("gpt_check_file: таймаут {}s ожидания ответа GPT", timeout)
        return GptCheckResult(decision=GptCheckDecision.timeout, raw_response="")
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "gpt_check_file: исключение при вызове GPT: {}: {}",
            type(e).__name__, e,
        )
        return GptCheckResult(
            decision=GptCheckDecision.parse_error,
            raw_response=f"[GPT call error] {type(e).__name__}: {e}",
        )

    return await _finalize_result(
        chatgpt_bot=chatgpt_bot,
        raw=raw,
        download_replacement_to=download_replacement_to,
    )


async def _finalize_result(
    *,
    chatgpt_bot: Any,
    raw: str,
    download_replacement_to: Path | None,
) -> GptCheckResult:
    """Общая логика: сначала пробуем скачать файл-замену, если её нет —
    парсим текст ответа."""
    if download_replacement_to is not None:
        replaced = await _try_download_attachment(
            chatgpt_bot,
            target_path=download_replacement_to,
            timeout=60.0,
        )
        if replaced is not None:
            logger.info(
                "gpt_check: GPT прислал файл-замену → сохранён в {}", replaced
            )
            # Извлечём score из текста ответа на всякий случай.
            _, _, score = parse_gpt_response(raw)
            return GptCheckResult(
                decision=GptCheckDecision.replace_artifact,
                raw_response=raw,
                replaced_path=replaced,
                score=score,
            )

    decision, hint, score = parse_gpt_response(raw)
    logger.info(
        "gpt_check: decision={}, score={}, raw_len={}",
        decision.value, score, len(raw or ""),
    )
    return GptCheckResult(
        decision=decision,
        raw_response=raw or "",
        hint=hint,
        score=score,
    )


# ============================================================
# Утилиты для шагов
# ============================================================


def load_check_prompt(kind_value: str, *, batch_snapshot_dir: Path | None = None) -> str:
    """Загружает текст системного промта для проверки артефакта данного
    HITLKind. Если есть batch-снэпшот — приоритет ему."""
    folder = f"check_{kind_value.replace('approve_', '')}"
    name = "default.md"
    here = Path(__file__).resolve().parent.parent.parent
    if batch_snapshot_dir is not None:
        snap = batch_snapshot_dir / folder / name
        if snap.exists():
            return snap.read_text(encoding="utf-8")
    p = here / "prompts" / folder / name
    if not p.exists():
        raise FileNotFoundError(
            f"gpt_check: не найден промт {p}. Создай файл или передай "
            f"batch_snapshot_dir."
        )
    return p.read_text(encoding="utf-8")
