"""Единый контракт обмена LLM ↔ video-pipeline (маркеры, validate, accompany).

SoT для ночного харнеса / GPT API — не плодить третий диалект проверки.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.check_analysis import CHECK_WRITEBACK_MARKER
from app.services.voiceover_sanitize import VOICEOVER_END, VOICEOVER_START
from app.services.xlsx_text_writeback import WRITEBACK_HINT

ReplyKind = Literal["text", "xlsx_writeback", "check_txt", "check_json", "voiceover"]

SHEET_HEADER_PREFIX = "# Лист:"
ROW_MARK_PREFIX = "@row="
CHECK_REPORT_HEADER = "# ОТЧЁТ ПРОВЕРКИ"
CHECK_SCHEMA_ID = "vp.check.v1"

# Реэкспорт канонических маркеров
XLSX_WRITEBACK_MARKER = CHECK_WRITEBACK_MARKER
VOICEOVER_START_MARK = VOICEOVER_START
VOICEOVER_END_MARK = VOICEOVER_END


@dataclass(frozen=True)
class ReplyValidation:
    ok: bool
    kind: ReplyKind
    reason: str = ""
    has_writeback: bool = False
    has_voiceover_block: bool = False
    has_check_txt: bool = False
    has_check_json: bool = False


def looks_like_xlsx_tsv(text: str) -> bool:
    t = text or ""
    return SHEET_HEADER_PREFIX.lower() in t.lower() or (
        ROW_MARK_PREFIX in t and "\t" in t
    )


def looks_like_voiceover_block(text: str) -> bool:
    t = text or ""
    return VOICEOVER_START in t and VOICEOVER_END in t


def looks_like_check_txt(text: str) -> bool:
    t = (text or "").lstrip()
    return t.startswith(CHECK_REPORT_HEADER) or (
        "verdict:" in t.lower() and "## summary" in t.lower()
    )


def looks_like_check_json(text: str) -> bool:
    t = (text or "").lstrip()
    return CHECK_SCHEMA_ID in t and ("{" in t[:200] or '"verdict"' in t)


def validate_model_reply(text: str, *, kind: ReplyKind) -> ReplyValidation:
    """Проверка ответа модели по ожидаемому kind (мягкая — для логов/retry)."""
    raw = text or ""
    has_wb = XLSX_WRITEBACK_MARKER in raw
    has_vo = looks_like_voiceover_block(raw)
    has_txt = looks_like_check_txt(raw)
    has_json = looks_like_check_json(raw)

    if kind == "text":
        return ReplyValidation(
            ok=bool(raw.strip()),
            kind=kind,
            reason="" if raw.strip() else "пустой ответ",
        )

    if kind == "voiceover":
        if has_vo:
            return ReplyValidation(ok=True, kind=kind, has_voiceover_block=True)
        return ReplyValidation(
            ok=False,
            kind=kind,
            reason="нет блока <<<VOICEOVER>>>…<<<END>>>",
            has_voiceover_block=False,
        )

    if kind == "xlsx_writeback":
        if looks_like_xlsx_tsv(raw) or has_wb:
            return ReplyValidation(
                ok=True,
                kind=kind,
                has_writeback=has_wb or looks_like_xlsx_tsv(raw),
            )
        return ReplyValidation(ok=False, kind=kind, reason="нет TSV (# Лист: / @row=)")

    if kind == "check_txt":
        if has_txt or has_json:
            return ReplyValidation(
                ok=True,
                kind=kind,
                has_check_txt=has_txt,
                has_check_json=has_json,
                has_writeback=has_wb,
            )
        return ReplyValidation(ok=False, kind=kind, reason="нет отчёта проверки TXT/JSON")

    if kind == "check_json":
        if has_json:
            return ReplyValidation(ok=True, kind=kind, has_check_json=True)
        return ReplyValidation(ok=False, kind=kind, reason="нет vp.check.v1")

    return ReplyValidation(ok=False, kind=kind, reason=f"неизвестный kind {kind}")


def build_api_accompany(
    base: str,
    *,
    expect_xlsx_writeback: bool = False,
    expect_voiceover: bool = False,
) -> str:
    """Собрать accompany с едиными hint-ами (без дублирования маркеров)."""
    parts: list[str] = []
    b = (base or "").strip()
    if b:
        parts.append(b)
    if expect_xlsx_writeback and WRITEBACK_HINT not in b:
        parts.append(WRITEBACK_HINT)
    if expect_voiceover and VOICEOVER_START not in b:
        from app.services.voiceover_sanitize import VOICEOVER_OUTPUT_FORMAT

        parts.append(VOICEOVER_OUTPUT_FORMAT)
    return "\n\n".join(parts).strip()


def check_fix_needs_writeback_retry(reply_text: str) -> bool:
    """True если check+fix без пригодного TSV (делегирует operator helper)."""
    from app.services.gpt_operator_client import _needs_check_writeback_retry

    return _needs_check_writeback_retry(reply_text)
