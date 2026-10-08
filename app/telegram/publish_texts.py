"""Тексты уведомлений о публикации для оператора (W6-1, E01/E02). Чистые функции, без сети и без бота.

Правила текста: что произошло, что не произошло, что делать человеку. «Опубликовано» пишется только для
подтверждённых площадок; неподтверждённое и сбой после клика всегда сопровождаются «автоповтора не будет».
"""
from __future__ import annotations

from typing import Iterable


def _sha_short(sha256: str | None) -> str:
    return (sha256 or "")[:12] or "—"


def publish_summary_text(project_id: int, results: Iterable, *, skipped: Iterable[str] = (),
                         not_claimed: dict[str, str] | None = None) -> str:
    """Итог одного запуска публикации по площадкам (results - PublishResult из publishers)."""
    lines: list[str] = [f"Публикация проекта #{project_id}:"]
    for r in results:
        if r.ok:
            lines.append(f"- {r.platform}: опубликовано {r.url or ''}".rstrip())
        elif r.status == "unconfirmed":
            lines.append(f"- {r.platform}: НЕ ПОДТВЕРЖДЕНО. Проверьте площадку вручную и подтвердите в панели "
                         f"«Публикации». Автоповтора не будет. {r.url or ''}".rstrip())
        elif r.clicked:
            lines.append(f"- {r.platform}: СБОЙ ПОСЛЕ КЛИКА. Проверьте площадку вручную, автоповтора не будет: "
                         f"{r.error or ''}".rstrip())
        else:
            lines.append(f"- {r.platform}: сбой до клика, ничего не опубликовано: {r.error or ''}".rstrip())
    skipped = sorted(set(skipped))
    if skipped:
        lines.append(f"(пропущены, итог уже есть или нет вашего подтверждения: {', '.join(skipped)})")
    if not_claimed:
        lines.append("(заняты другим запуском или ждут человека: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(not_claimed.items())) + ")")
    return "\n".join(lines)


def awaiting_approval_text(project_id: int, platforms: Iterable[str], sha256: str | None) -> str:
    """Публикация не начата: на площадки нет подтверждения человека для этого файла."""
    names = ", ".join(sorted(platforms)) or "—"
    return (f"Проект #{project_id}: публикация НЕ начата, ждёт вашего подтверждения.\n"
            f"Площадки: {names}. Файл (sha256): {_sha_short(sha256)}.\n"
            "Откройте панель «Публикации», проверьте площадки, подпись и файл и подтвердите каждую площадку. "
            "Подтверждение действует только для этого файла и этой подписи; пересборка видео требует нового.")


def needs_human_text(project_id: int, platforms: Iterable[str]) -> str:
    names = ", ".join(sorted(platforms)) or "—"
    return (f"Проект #{project_id}: публикация ждёт проверки человека.\n"
            f"Площадки: {names}. Автоповтора не будет, чтобы не создать дубль.\n"
            "Откройте площадку, проверьте, есть ли пост, и нажмите в панели «Публикации» "
            "«Подтвердить вручную» или «Не опубликовано».")


def manual_action_text(project_id: int, platform: str, action: str, actor: str, *, all_published: bool) -> str:
    what = {"confirm": "подтверждено вручную как опубликованное",
            "mark_failed": "помечено вручную как НЕ опубликованное",
            "mark_failed_republish": "помечено вручную как НЕ опубликованное, повторная публикация разрешена "
                                     "(нужно новое подтверждение)"}.get(action, action)
    tail = " Все площадки подтверждены: проект опубликован." if all_published else ""
    return f"Проект #{project_id}, {platform}: {what} ({actor}). Клик по площадке не повторялся.{tail}"
