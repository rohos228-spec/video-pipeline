"""GPT текстом через HTTP API (OpenAI-совместимый `/v1/chat/completions`).

Замена браузерного ChatGPT (CDP) для excel_gpt / проверочных нод:
  * без Playwright/Chrome — прямой httpx-запрос к шлюзу (GRSAI / OpenAI-совм.);
  * полная обработка ошибок: 401/403 (ключ), 429 (лимит, ретрай), 5xx/сеть/таймаут
    (ретрай с экспоненциальной паузой), пустой/битый ответ;
  * приложенные файлы (xlsx/txt) сворачиваются в текстовый контекст;
  * скачивание/копирование контента: `download_content` тянет файл по URL,
    `collect_result_urls` достаёт ссылки из ответа модели.

Настройки — `settings.gpt_*` (ключ/база можно переиспользовать из GRSAI_*).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.settings import settings

# Коды, при которых повтор бессмысленен (не ретраим).
_FATAL_STATUS = frozenset({400, 401, 403, 404, 422})
# Коды, при которых повтор имеет смысл (лимиты / временные сбои шлюза).
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

_URL_RE = re.compile(r"https?://[^\s\)\]\}<>\"']+", re.IGNORECASE)
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv", ".json", ".yaml", ".yml"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
# Лимит картинок и размера (base64 раздувает ~4/3) — чтобы не упереться в payload.
_MAX_VISION_IMAGES = 8
_MAX_VISION_BYTES = 4_000_000


class GptApiError(Exception):
    """Ошибка GPT API с контекстом для логов/повторов."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context = context or {}

    @property
    def retryable(self) -> bool:
        return bool(self.context.get("retryable"))


@dataclass
class GptChatResult:
    text: str
    model: str
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def gpt_api_enabled() -> bool:
    return bool(settings.gpt_api_enabled)


def _headers() -> dict[str, str]:
    key = settings.gpt_api_effective_key
    if not key:
        raise GptApiError(
            "GPT_API_KEY пуст (и GRSAI_API_KEY тоже) — задай ключ в .env",
            context={"error_kind": "no_key"},
        )
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _chat_url(model: str) -> str:
    base = settings.gpt_api_effective_base_url
    if not base:
        raise GptApiError("GPT_BASE_URL пуст — задай базу шлюза", context={"error_kind": "no_base"})
    path = (settings.gpt_chat_path or "/v1/chat/completions").strip()
    if not path.startswith("/"):
        path = "/" + path
    # kie.ai: путь зависит от модели (/{model}/v1/chat/completions).
    if "{model}" in path:
        path = path.replace("{model}", model)
    return f"{base}{path}"


# ─────────────────────────── файлы → контекст ───────────────────────────


def xlsx_to_text(path: Path, *, max_rows: int = 400, max_cols: int = 40) -> str:
    """Свернуть xlsx в читаемый TSV-контекст (непустые строки листов)."""
    try:
        from openpyxl import load_workbook
    except Exception as e:  # noqa: BLE001
        return f"[xlsx: openpyxl недоступен: {e}]"
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        return f"[xlsx: не удалось открыть {path.name}: {e}]"
    out: list[str] = []
    for ws in wb.worksheets:
        out.append(f"# Лист: {ws.title}")
        rows_written = 0
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c) for c in row[:max_cols]]
            if not any(c.strip() for c in cells):
                continue
            out.append("\t".join(cells))
            rows_written += 1
            if rows_written >= max_rows:
                out.append("… (обрезано)")
                break
    import contextlib

    with contextlib.suppress(Exception):
        wb.close()
    return "\n".join(out).strip() or "[xlsx: пустой]"


def file_to_context(path: Path, *, max_chars: int = 60_000) -> str:
    """Текстовое представление файла для вложения в prompt."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        body = xlsx_to_text(path)
    elif suffix in _TEXT_SUFFIXES:
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            body = f"[не удалось прочитать {path.name}: {e}]"
    elif suffix in _IMAGE_SUFFIXES:
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        return f"[изображение {path.name}: {suffix}, {size} байт — см. vision-вложение]"
    else:
        # бинарь (mp4/…) — только метаданные, не тянем в текст.
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        return f"[файл {path.name}: {suffix or 'bin'}, {size} байт — бинарный, не разворачиваю]"
    if len(body) > max_chars:
        body = body[:max_chars] + "\n… (обрезано)"
    return f"===== {path.name} =====\n{body}"


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIXES


def image_to_data_url(path: Path) -> str:
    """data:image/...;base64,... для multimodal / vision."""
    import base64

    suffix = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    data = path.read_bytes()
    if len(data) > _MAX_VISION_BYTES:
        raise GptApiError(
            f"картинка слишком большая для vision: {path.name} ({len(data)} байт)",
            context={"error_kind": "media_too_large", "retryable": False},
        )
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def split_input_paths(
    input_paths: list[Path] | None,
) -> tuple[list[Path], list[Path]]:
    """(text_or_other_files, image_files) с лимитом vision."""
    files = [p for p in (input_paths or []) if p and p.is_file()]
    images = [p for p in files if is_image_path(p)][:_MAX_VISION_IMAGES]
    others = [p for p in files if p not in images]
    return others, images


def is_responses_mode() -> bool:
    """API формата Responses (input/output) вместо chat/completions?"""
    mode = (settings.gpt_api_mode or "auto").strip().lower()
    if mode == "responses":
        return True
    if mode == "chat":
        return False
    return "responses" in (settings.gpt_chat_path or "").lower()


def _compose_user_text(
    *,
    prompt: str,
    accompanying: str = "",
    text_paths: list[Path] | None = None,
    image_names: list[str] | None = None,
) -> str:
    parts: list[str] = []
    if (prompt or "").strip():
        parts.append(prompt.strip())
    if (accompanying or "").strip():
        parts.append("## Сопроводительный текст\n" + accompanying.strip())
    files = list(text_paths or [])
    if files:
        parts.append("## Приложенные файлы")
        for p in files:
            parts.append(file_to_context(p))
    if image_names:
        parts.append(
            "## Изображения (vision)\n"
            + "\n".join(f"- {n}" for n in image_names)
        )
    return "\n\n".join(parts) or "(пусто)"


def build_input(
    *,
    prompt: str,
    accompanying: str = "",
    input_paths: list[Path] | None = None,
    system: str | None = None,
) -> str | list[dict[str, Any]]:
    """Ввод для Responses API: строка или multimodal input[] с картинками."""
    others, images = split_input_paths(input_paths)
    text = _compose_user_text(
        prompt=prompt,
        accompanying=accompanying,
        text_paths=others,
        image_names=[p.name for p in images],
    )
    if system:
        text = f"[Инструкция]\n{system}\n\n{text}"
    if not images:
        return text
    content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    for img in images:
        try:
            content.append(
                {"type": "input_image", "image_url": image_to_data_url(img)}
            )
        except GptApiError as e:
            logger.warning("gpt_api vision skip {}: {}", img.name, e)
            content.append(
                {
                    "type": "input_text",
                    "text": f"[не удалось вложить изображение {img.name}: {e}]",
                }
            )
    return [{"role": "user", "content": content}]


def build_messages(
    *,
    prompt: str,
    accompanying: str = "",
    input_paths: list[Path] | None = None,
    system: str | None = None,
) -> list[dict[str, Any]]:
    """Собрать messages для chat/completions (текст + optional vision)."""
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})

    others, images = split_input_paths(input_paths)
    text = _compose_user_text(
        prompt=prompt,
        accompanying=accompanying,
        text_paths=others,
        image_names=[p.name for p in images],
    )
    if not images:
        messages.append({"role": "user", "content": text})
        return messages

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for img in images:
        try:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_to_data_url(img)},
                }
            )
        except GptApiError as e:
            logger.warning("gpt_api vision skip {}: {}", img.name, e)
            content.append(
                {
                    "type": "text",
                    "text": f"[не удалось вложить изображение {img.name}: {e}]",
                }
            )
    messages.append({"role": "user", "content": content})
    return messages


# ─────────────────────────── HTTP вызов ───────────────────────────


def _check_provider_envelope(payload: dict[str, Any]) -> None:
    """Некоторые шлюзы (kie.ai) отдают ошибку как HTTP 200 с {code,msg,data}.

    Если code не успешный и нет choices — поднимаем понятную ошибку.
    """
    if not isinstance(payload, dict) or payload.get("choices"):
        return
    code = payload.get("code")
    if code is None:
        return
    try:
        code_int = int(code)
    except (TypeError, ValueError):
        return
    if code_int in (0, 200):
        return
    msg = str(payload.get("msg") or payload.get("message") or "ошибка провайдера")
    retryable = code_int in _RETRY_STATUS or code_int >= 500
    hint = ""
    low = msg.lower()
    if "not authorized" in low or "apikey" in low or code_int in (401, 403):
        hint = " — ключ не авторизован на эту модель (проверь GPT_API_KEY/модель у провайдера)"
    raise GptApiError(
        f"GPT провайдер code={code_int}: {msg}{hint}",
        context={"provider_code": code_int, "retryable": retryable},
    )


def _parse_responses(payload: dict[str, Any]) -> tuple[str, str]:
    """(text, status) из ответа Responses API (kie.ai codex, gpt-5.6)."""
    _check_provider_envelope(payload)
    status = str(payload.get("status") or "")
    text_parts: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for c in content:
                    if (
                        isinstance(c, dict)
                        and c.get("type") in ("output_text", "text")
                        and c.get("text")
                    ):
                        text_parts.append(str(c["text"]))
    text = "".join(text_parts).strip()
    if not text:
        ot = payload.get("output_text")
        if isinstance(ot, str):
            text = ot.strip()
    if not text:
        raise GptApiError(
            "GPT(responses): пустой output",
            context={"status": status, "payload": payload},
        )
    return text, status


def _parse_choice(payload: dict[str, Any]) -> tuple[str, str]:
    """(text, finish_reason) из ответа chat/completions."""
    _check_provider_envelope(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise GptApiError("GPT: пустой choices в ответе", context={"payload": payload})
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    # content может быть строкой или списком частей (vision/tools)
    if isinstance(content, list):
        text = "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict)
        )
    else:
        text = str(content or "")
    finish = str(first.get("finish_reason") or "")
    if not text.strip():
        raise GptApiError(
            "GPT: пустой content в ответе",
            context={"finish_reason": finish, "payload": payload},
        )
    return text, finish


async def chat(
    *,
    prompt: str,
    accompanying: str = "",
    input_paths: list[Path] | None = None,
    system: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> GptChatResult:
    """Вызвать GPT chat/completions с ретраями и полной обработкой ошибок."""
    headers = _headers()
    use_model = (model or settings.gpt_model or "gpt-5.5").strip()
    url = _chat_url(use_model)
    use_timeout = float(timeout if timeout is not None else settings.gpt_timeout_s)
    retries = int(max_retries if max_retries is not None else settings.gpt_max_retries)

    responses_mode = is_responses_mode()
    if responses_mode:
        body: dict[str, Any] = {
            "model": use_model,
            "input": build_input(
                prompt=prompt, accompanying=accompanying, input_paths=input_paths, system=system
            ),
            "stream": False,
        }
    else:
        body = {
            "model": use_model,
            "messages": build_messages(
                prompt=prompt, accompanying=accompanying, input_paths=input_paths, system=system
            ),
        }
    if temperature is not None:
        body["temperature"] = temperature

    attempt = 0
    last_exc: Exception | None = None
    while attempt <= retries:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=use_timeout) as client:
                resp = await client.post(url, headers=headers, json=body)
            status = resp.status_code
            if status in _FATAL_STATUS:
                low = resp.text.lower()
                hint = ""
                if "apikey error" in low:
                    hint = (
                        f" — ключ не авторизован на модель {use_model!r} "
                        "(добавь модель в whitelist ключа grsai)"
                    )
                elif "model not register" in low or "not register" in low:
                    hint = f" — модель {use_model!r} не существует у провайдера (проверь GPT_MODEL)"
                raise GptApiError(
                    f"GPT HTTP {status}: {resp.text[:400]}{hint}",
                    context={"status_code": status, "retryable": False, "model": use_model},
                )
            if status in _RETRY_STATUS or status >= 500:
                raise GptApiError(
                    f"GPT HTTP {status}: {resp.text[:300]}",
                    context={"status_code": status, "retryable": True, "model": use_model},
                )
            if status >= 400:
                raise GptApiError(
                    f"GPT HTTP {status}: {resp.text[:400]}",
                    context={"status_code": status, "retryable": False, "model": use_model},
                )
            try:
                payload = resp.json()
            except Exception as e:  # noqa: BLE001
                raise GptApiError(
                    f"GPT: ответ не JSON: {resp.text[:300]}",
                    context={"retryable": True, "model": use_model},
                ) from e
            text, finish = (
                _parse_responses(payload) if responses_mode else _parse_choice(payload)
            )
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            logger.info(
                "gpt_api.chat OK model={} attempt={} finish={} chars={}",
                use_model,
                attempt,
                finish,
                len(text),
            )
            return GptChatResult(
                text=text, model=use_model, finish_reason=finish, usage=usage, raw=payload
            )
        except httpx.TimeoutException:
            last_exc = GptApiError(
                f"GPT timeout {use_timeout:.0f}s (попытка {attempt}/{retries + 1})",
                context={"error_kind": "timeout", "retryable": True, "model": use_model},
            )
            logger.warning(str(last_exc))
        except httpx.HTTPError as e:
            last_exc = GptApiError(
                f"GPT сетевая ошибка: {type(e).__name__}: {e}",
                context={"error_kind": "network", "retryable": True, "model": use_model},
            )
            logger.warning(str(last_exc))
        except GptApiError as e:
            if not e.retryable:
                raise
            last_exc = e
            logger.warning("gpt_api.chat retryable: {}", e)

        if attempt <= retries:
            backoff = min(2.0 * (2 ** (attempt - 1)), 30.0)
            await asyncio.sleep(backoff)

    raise last_exc or GptApiError("GPT: неизвестная ошибка", context={"model": use_model})


# ─────────────────────────── контент (download/copy) ───────────────────────────


def collect_result_urls(text: str) -> list[str]:
    """Достать http(s)-ссылки на контент из ответа модели (картинки/файлы)."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        u = m.group(0).rstrip(".,;")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def download_content(
    url: str, out_path: Path, *, timeout: float = 180.0
) -> Path:
    """Скачать файл по URL в out_path (картинки/видео/xlsx из ответа GPT)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code >= 400:
                raise GptApiError(
                    f"download HTTP {r.status_code}: {url}",
                    context={"status_code": r.status_code, "url": url},
                )
            out_path.write_bytes(r.content)
    except httpx.HTTPError as e:
        raise GptApiError(
            f"download сетевая ошибка: {type(e).__name__}: {e}",
            context={"url": url},
        ) from e
    if not out_path.is_file() or out_path.stat().st_size < 1:
        raise GptApiError("download: пустой файл", context={"url": url, "path": str(out_path)})
    return out_path


def copy_content(src: Path, out_path: Path) -> Path:
    """Скопировать локальный контент (когда файл уже на диске, не по URL)."""
    import shutil

    if not src.is_file():
        raise GptApiError("copy: источник не найден", context={"src": str(src)})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out_path)
    return out_path
