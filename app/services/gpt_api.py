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
_TEXT_SUFFIXES = frozenset({
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
    ".log",
    ".srt",
    ".vtt",
})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
# Лимит картинок и размера (base64 раздувает ~4/3) — чтобы не упереться в payload.
_MAX_VISION_IMAGES = 8
_MAX_VISION_BYTES = 4_000_000
# Мелкий бинарь (pdf/docx/zip/…) можно отдать как base64 — как «файл в чате».
_MAX_BINARY_INLINE_BYTES = 400_000


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
    """Текстовое представление файла для вложения в prompt (как attach в ChatGPT)."""
    import base64

    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
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
    elif suffix == ".pdf":
        # Без pypdf: вытаскиваем printable-куски + при малом размере — base64.
        try:
            raw = path.read_bytes()
        except OSError as e:
            return f"[pdf {path.name}: не прочитан ({e})]"
        printable = "".join(
            chr(b) if 32 <= b < 127 or b in (9, 10, 13) else " "
            for b in raw[:200_000]
        )
        printable = re.sub(r"[ \t]{2,}", " ", printable)
        printable = re.sub(r"\n{3,}", "\n\n", printable).strip()
        parts = [f"[pdf {path.name}: {len(raw)} байт]"]
        if printable and len(printable) > 40:
            parts.append(printable[:max_chars])
        if len(raw) <= _MAX_BINARY_INLINE_BYTES:
            parts.append(
                f"data:application/pdf;base64,{base64.b64encode(raw).decode('ascii')}"
            )
        body = "\n\n".join(parts)
    elif suffix in {".docx"}:
        # docx = zip+xml; вытащим word/document.xml текст грубо.
        try:
            import zipfile
            from xml.etree import ElementTree as ET

            with zipfile.ZipFile(path) as zf:
                xml = zf.read("word/document.xml")
            root = ET.fromstring(xml)
            texts = [
                (n.text or "")
                for n in root.iter()
                if n.tag.endswith("}t") and (n.text or "").strip()
            ]
            body = "\n".join(texts) or f"[docx {path.name}: пустой текст]"
        except Exception as e:  # noqa: BLE001
            body = f"[docx {path.name}: не разобрал ({e})]"
    else:
        # Прочий бинарь — метаданные; мелкий файл инлайним base64 (модель может вернуть).
        try:
            raw = path.read_bytes()
            size = len(raw)
        except OSError as e:
            return f"[файл {path.name}: не прочитан ({e})]"
        if size <= _MAX_BINARY_INLINE_BYTES:
            mime = {
                ".zip": "application/zip",
                ".mp4": "video/mp4",
                ".webm": "video/webm",
            }.get(suffix, "application/octet-stream")
            body = (
                f"[файл {path.name}: {suffix or 'bin'}, {size} байт]\n"
                f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
            )
        else:
            return (
                f"[файл {path.name}: {suffix or 'bin'}, {size} байт — "
                f"слишком большой для inline (>{_MAX_BINARY_INLINE_BYTES}), "
                f"только имя; скачай исходник кнопкой ↓ в Studio]"
            )
    if len(body) > max_chars and "base64," not in body:
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


def normalize_history(
    history: list[dict[str, Any]] | None,
    *,
    max_messages: int = 40,
    max_chars: int = 120_000,
) -> list[dict[str, str]]:
    """Оставить user/assistant реплики для multi-turn (хвост, лимит символов)."""
    if not history:
        return []
    cleaned: list[dict[str, str]] = []
    for raw in history:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = raw.get("content")
        if isinstance(content, list):
            # multimodal leftover — берём только текст
            text = "".join(
                str(p.get("text") or "")
                for p in content
                if isinstance(p, dict)
            ).strip()
        else:
            text = str(content or "").strip()
        if not text:
            continue
        cleaned.append({"role": role, "content": text})
    if max_messages > 0 and len(cleaned) > max_messages:
        cleaned = cleaned[-max_messages:]
    # с хвоста: уложиться в max_chars, выкидывая самые старые
    if max_chars > 0:
        total = 0
        kept_rev: list[dict[str, str]] = []
        for item in reversed(cleaned):
            n = len(item["content"])
            if kept_rev and total + n > max_chars:
                break
            kept_rev.append(item)
            total += n
        cleaned = list(reversed(kept_rev))
    return cleaned


def build_input(
    *,
    prompt: str,
    accompanying: str = "",
    input_paths: list[Path] | None = None,
    system: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> str | list[dict[str, Any]]:
    """Ввод для Responses API: строка или multimodal/multi-turn input[]."""
    prior = normalize_history(history)
    others, images = split_input_paths(input_paths)
    text = _compose_user_text(
        prompt=prompt,
        accompanying=accompanying,
        text_paths=others,
        image_names=[p.name for p in images],
    )
    if system:
        text = f"[Инструкция]\n{system}\n\n{text}"

    if not images and not prior:
        return text

    items: list[dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in prior]
    if not images:
        items.append({"role": "user", "content": text})
        return items

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
    items.append({"role": "user", "content": content})
    return items


def build_messages(
    *,
    prompt: str,
    accompanying: str = "",
    input_paths: list[Path] | None = None,
    system: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Собрать messages для chat/completions (текст + optional vision + история)."""
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for m in normalize_history(history):
        messages.append({"role": m["role"], "content": m["content"]})

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
    history: list[dict[str, Any]] | None = None,
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
                prompt=prompt,
                accompanying=accompanying,
                input_paths=input_paths,
                system=system,
                history=history,
            ),
            "stream": False,
        }
    else:
        body = {
            "model": use_model,
            "messages": build_messages(
                prompt=prompt,
                accompanying=accompanying,
                input_paths=input_paths,
                system=system,
                history=history,
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

_DATA_URI_RE = re.compile(
    r"data:(image/(?:png|jpeg|jpg|webp|gif)|application/(?:pdf|zip|octet-stream|"
    r"vnd\.openxmlformats-officedocument\.spreadsheetml\.sheet|"
    r"vnd\.ms-excel))"
    r";base64,([A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)


def sniff_file_extension(data: bytes) -> str | None:
    """Определить расширение по magic bytes (PNG/JPEG/…), не по имени/.bin."""
    if not data or len(data) < 12:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06"):
        head = data[:8000]
        if b"word/" in head:
            return ".docx"
        if b"xl/" in head or b"xl\\" in head:
            return ".xlsx"
        return ".zip"
    return None


_WEAK_SUFFIXES = frozenset({"", ".bin", ".dat", ".tmp", ".octet-stream", ".unknown"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_SNIFF_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _read_magic_head(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        if size <= 64_000:
            return path.read_bytes()
        with path.open("rb") as f:
            return f.read(16_384)
    except OSError:
        return b""


def needs_extension_fix(path: Path, sniffed: str | None) -> bool:
    """True если имя слабое (.bin) или не совпадает с magic (картинка)."""
    if not sniffed:
        return False
    cur = path.suffix.lower()
    if cur in _WEAK_SUFFIXES:
        return True
    if sniffed in _IMAGE_SUFFIXES and cur not in _IMAGE_SUFFIXES:
        return True
    return False


def suggested_name_and_mime(path: Path) -> tuple[str, str]:
    """Имя для Content-Disposition + MIME по magic (без обязательного rename на диске)."""
    import mimetypes

    head = _read_magic_head(path)
    sniffed = sniff_file_extension(head)
    name = path.name
    if sniffed and needs_extension_fix(path, sniffed):
        name = f"{path.stem}{sniffed}"
    mime, _ = mimetypes.guess_type(name)
    if not mime and sniffed:
        mime = _SNIFF_MIME.get(sniffed)
    if not mime:
        mime, _ = mimetypes.guess_type(str(path))
    return name, mime or "application/octet-stream"


def ensure_correct_extension(path: Path) -> Path:
    """Переименовать файл, если расширение .bin/пустое/не совпадает с magic."""
    if not path.is_file():
        return path
    sniffed = sniff_file_extension(_read_magic_head(path))
    if not needs_extension_fix(path, sniffed) or not sniffed:
        return path
    target = path.with_suffix(sniffed)
    if target.resolve() == path.resolve():
        return path
    n = 2
    while target.exists():
        target = path.with_name(f"{path.stem}_{n}{sniffed}")
        n += 1
    try:
        path.rename(target)
        logger.info("sniff rename {} → {}", path.name, target.name)
        return target
    except OSError as e:
        logger.warning("sniff rename failed {}: {}", path, e)
        return path


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


def extract_data_uri_files(text: str, out_dir: Path, *, prefix: str = "embed") -> list[Path]:
    """Вынуть data:image/...;base64,... из ответа модели в файлы."""
    import base64

    if not text:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for i, m in enumerate(_DATA_URI_RE.finditer(text)):
        mime = (m.group(1) or "application/octet-stream").lower()
        raw_b64 = re.sub(r"\s+", "", m.group(2) or "")
        if len(raw_b64) < 32:
            continue
        ext = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "application/pdf": ".pdf",
            "application/zip": ".zip",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/vnd.ms-excel": ".xls",
        }.get(mime, ".bin")
        try:
            data = base64.b64decode(raw_b64, validate=False)
        except Exception:  # noqa: BLE001
            continue
        if len(data) < 32:
            continue
        sniffed = sniff_file_extension(data)
        if sniffed:
            ext = sniffed
        path = out_dir / f"{prefix}_{i}{ext}"
        n = 2
        while path.exists():
            path = out_dir / f"{prefix}_{i}_{n}{ext}"
            n += 1
        path.write_bytes(data)
        saved.append(ensure_correct_extension(path))
    return saved


async def materialize_reply_assets(
    text: str,
    out_dir: Path,
    *,
    prefix: str = "file",
    timeout: float = 120.0,
    max_urls: int = 8,
) -> list[Path]:
    """Скачать URL и data-URI из ответа модели в out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    saved.extend(extract_data_uri_files(text, out_dir, prefix=f"{prefix}_embed"))

    for i, url in enumerate(collect_result_urls(text)[:max_urls]):
        url_path = url.split("?")[0]
        suffix = Path(url_path).suffix.lower()
        if suffix not in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".xlsx",
            ".xls",
            ".xlsm",
            ".txt",
            ".md",
            ".csv",
            ".json",
            ".pdf",
            ".zip",
            ".mp4",
            ".webm",
            "",
        }:
            suffix = suffix if suffix and len(suffix) <= 8 else ""
        dest = out_dir / f"{prefix}_url_{i}{suffix or '.bin'}"
        n = 2
        while dest.exists():
            dest = out_dir / f"{prefix}_url_{i}_{n}{suffix or '.bin'}"
            n += 1
        try:
            got = await download_content(url, dest, timeout=timeout)
            got = ensure_correct_extension(got)
            saved.append(got)
        except Exception as e:  # noqa: BLE001
            logger.warning("materialize_reply_assets: skip {}: {}", url[:120], e)
    return saved


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
    return ensure_correct_extension(out_path)


def copy_content(src: Path, out_path: Path) -> Path:
    """Скопировать локальный контент (когда файл уже на диске, не по URL)."""
    import shutil

    if not src.is_file():
        raise GptApiError("copy: источник не найден", context={"src": str(src)})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out_path)
    return out_path
