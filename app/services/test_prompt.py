"""Сервис «Тестирование визуальных промтов».

Итеративный цикл:
  ChatGPT(system + visual) → txt
  Outsee(banana-pro, relax, prompt из txt) → картинка
  Юзер шлёт критику → ChatGPT(system + предыдущий txt + критика) → новый txt
  ... → ... (бесконечно, пока юзер не остановит)

Используется существующая инфраструктура:
  * app/bots/chatgpt.py: ChatGPTBot.ask_with_files + download_attachment_from_last_reply
  * app/bots/outsee.py: OutseeBot.generate_image(model_slug="nano-banana-pro", relax=True)

Параллельно может работать только ОДИН тестовый цикл — лочится по
test_prompt_projects.status in ('running_gpt', 'running_outsee').
"""
from __future__ import annotations

import contextlib
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestPromptProject

if TYPE_CHECKING:
    from aiogram import Bot


_RUNNING_STATUSES = {"running_gpt", "running_outsee"}


def slugify(name: str) -> str:
    base = re.sub(r"[^a-zа-я0-9]+", "-", name.lower(), flags=re.IGNORECASE)
    base = base.strip("-")[:40]
    return base or "test"


async def create_test_project(
    session: AsyncSession, name: str
) -> TestPromptProject:
    """Создаёт тестовый проект с уникальным slug. Папка не создаётся
    здесь — она появится при первой итерации (idempotent mkdir).
    """
    name = name.strip()
    if not name:
        raise ValueError("Пустое имя проекта")
    slug_base = slugify(name)
    slug = slug_base
    i = 1
    while (
        await session.execute(
            select(TestPromptProject).where(TestPromptProject.slug == slug)
        )
    ).scalar_one_or_none():
        i += 1
        slug = f"{slug_base}-{i}"
    proj = TestPromptProject(name=name, slug=slug, status="idle")
    session.add(proj)
    await session.flush()
    return proj


async def get_running_project(
    session: AsyncSession,
) -> TestPromptProject | None:
    """Возвращает проект, у которого сейчас активный шаг (GPT или
    outsee). None если нет ни одного — можно запускать новый.
    """
    rows = (
        await session.execute(
            select(TestPromptProject).where(
                TestPromptProject.status.in_(list(_RUNNING_STATUSES))
            )
        )
    ).scalars().all()
    return rows[0] if rows else None


def _build_gpt_first_prompt(system_prompt: str, visual_prompt: str) -> str:
    """Промт для ПЕРВОЙ итерации: системная инструкция + стартовый
    визуальный промт. Просим вернуть .txt файл (а не текст в чате) —
    так в download_attachment_from_last_reply можно скачать ровно его.
    """
    return (
        f"{system_prompt.strip()}\n\n"
        f"Вот визуальный промт для обработки:\n\n"
        f"---\n{visual_prompt.strip()}\n---\n\n"
        f"Верни ОБРАБОТАННЫЙ промт в виде вложения — .txt файл "
        f"(plain text, UTF-8). Только содержимое промта, без "
        f"пояснений в самом файле."
    )


def _build_gpt_critique_prompt(system_prompt: str, critique: str) -> str:
    """Промт для итераций после первой: к этому сообщению прикрепим
    предыдущий txt и попросим переписать с учётом критики юзера.
    """
    return (
        f"{system_prompt.strip()}\n\n"
        f"Во вложении — текущая версия визуального промта.\n"
        f"Юзер прислал комментарий, что нужно поправить:\n\n"
        f"---\n{critique.strip()}\n---\n\n"
        f"Перепиши промт с учётом этого комментария и верни ОБНОВЛЁННЫЙ "
        f"промт в виде нового .txt-вложения (plain text, UTF-8). "
        f"Только содержимое промта, без пояснений в самом файле."
    )


async def _gpt_get_new_prompt(
    *,
    out_txt_path: Path,
    user_message: str,
    attachments: list[Path] | None = None,
) -> str:
    """Открывает ChatGPT (новая сессия чата), отправляет user_message с
    приложенными файлами (если есть), просит вернуть .txt с
    обработанным промтом, скачивает его в out_txt_path. Возвращает
    содержимое txt.
    """
    # Импорт ленивый — чтобы тесты могли мокать.
    from app.bots.browser import browser_session
    from app.bots.chatgpt import ChatGPTBot

    out_txt_path.parent.mkdir(parents=True, exist_ok=True)

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        await gpt.new_conversation()
        if attachments:
            await gpt.ask_with_files(user_message, attachments, timeout=900)
        else:
            await gpt.ask(user_message, timeout=900)
        # Скачиваем сгенерированный .txt из ответа.
        await gpt.download_attachment_from_last_reply(out_txt_path)

    if not out_txt_path.exists() or out_txt_path.stat().st_size == 0:
        raise RuntimeError(
            f"ChatGPT не прислал .txt-вложение (или файл пустой): "
            f"{out_txt_path}"
        )
    return out_txt_path.read_text(encoding="utf-8", errors="replace")


async def _outsee_generate(
    *,
    prompt: str,
    out_image: Path,
    prompt_id_prefix: str,
) -> Path:
    """Запускает outsee.generate_image для Banana Pro в Relax-режиме,
    с уникальным prompt_id_prefix. Возвращает путь до картинки.
    """
    from app.bots.browser import browser_session
    from app.bots.outsee import OutseeBot

    out_image.parent.mkdir(parents=True, exist_ok=True)

    async with browser_session() as bs:
        bot = OutseeBot(bs)
        result = await bot.generate_image(
            prompt=prompt,
            out_path=out_image,
            aspect_ratio="16:9",
            model_slug="nano-banana-pro",
            relax=True,
            prompt_id_prefix=prompt_id_prefix,
        )
    if not result.success or not out_image.exists():
        raise RuntimeError(
            f"Outsee не вернул картинку для test_prompt: "
            f"success={result.success}, path_exists={out_image.exists()}"
        )
    return out_image


async def run_iteration(
    session: AsyncSession,
    project: TestPromptProject,
    *,
    critique: str | None = None,
    bot: Bot | None = None,
    chat_id: int | None = None,
) -> tuple[Path, Path]:
    """Запускает одну итерацию: GPT → txt → outsee → image.

    Если `critique` задан — это не первая итерация (доводка по
    критике), к GPT-запросу прикрепляется предыдущий txt из
    iter_<current_iter>/prompt.txt. Если critique=None — это первая
    итерация, GPT получает только system_prompt + visual_prompt.

    После завершения:
      * project.current_iter увеличен на 1
      * project.status = 'waiting_critique'
      * на диске: iter_<N>/prompt.txt и iter_<N>/image.jpg

    Возвращает (prompt_txt_path, image_path).

    Если bot+chat_id заданы — слёт прогресс-сообщения юзеру по ходу
    (чтобы он понимал что происходит).
    """
    if not project.visual_prompt or not project.system_prompt:
        raise RuntimeError(
            "Не заданы visual_prompt и/или system_prompt — задай их "
            "в меню проекта перед запуском."
        )

    # Лок: проверяем, что не запущен другой тестовый проект.
    other = await get_running_project(session)
    if other is not None and other.id != project.id:
        raise RuntimeError(
            f"Уже идёт другой тестовый цикл (проект #{other.id} "
            f"«{other.name}»). Дождись окончания или останови его."
        )

    prev_iter = project.current_iter
    new_iter = prev_iter + 1
    new_dir = project.iter_dir(new_iter)
    new_dir.mkdir(parents=True, exist_ok=True)

    out_txt = new_dir / "prompt.txt"
    out_img = new_dir / "image.jpg"

    # ---- 1. ChatGPT ----
    project.status = "running_gpt"
    await session.flush()
    if bot and chat_id is not None:
        with contextlib.suppress(Exception):
            await bot.send_message(
                chat_id,
                f"🧪 #{project.id} «{project.name}» — итерация {new_iter}\n"
                f"➡ Шаг 1/2: отправляю ChatGPT…",
            )

    if critique is None:
        # Первая итерация — без вложения.
        user_msg = _build_gpt_first_prompt(
            project.system_prompt, project.visual_prompt
        )
        attachments: list[Path] | None = None
    else:
        # Доводка по критике — к запросу прикрепляем предыдущий txt.
        prev_txt = project.iter_dir(prev_iter) / "prompt.txt"
        if not prev_txt.exists():
            raise RuntimeError(
                f"Нет предыдущего txt-промта в {prev_txt}, не могу "
                f"продолжить цикл"
            )
        # Сохраняем критику рядом с предыдущим txt — для истории.
        (project.iter_dir(prev_iter) / "critique.txt").write_text(
            critique, encoding="utf-8"
        )
        user_msg = _build_gpt_critique_prompt(
            project.system_prompt, critique
        )
        attachments = [prev_txt]

    try:
        new_prompt = await _gpt_get_new_prompt(
            out_txt_path=out_txt,
            user_message=user_msg,
            attachments=attachments,
        )
    except Exception as e:  # noqa: BLE001
        project.status = "error"
        meta = dict(project.meta or {})
        meta["last_error"] = f"GPT: {e}"
        project.meta = meta
        await session.flush()
        raise

    # ---- 2. Outsee ----
    project.status = "running_outsee"
    await session.flush()
    if bot and chat_id is not None:
        with contextlib.suppress(Exception):
            await bot.send_message(
                chat_id,
                f"➡ Шаг 2/2: отправляю в Banana Pro (Relax)…\n"
                f"Промт получен ({len(new_prompt)} символов).",
            )

    prompt_id_prefix = (
        f"[ID: test{project.id}-iter{new_iter}-{uuid.uuid4().hex[:8]}]"
    )
    try:
        await _outsee_generate(
            prompt=new_prompt,
            out_image=out_img,
            prompt_id_prefix=prompt_id_prefix,
        )
    except Exception as e:  # noqa: BLE001
        project.status = "error"
        meta = dict(project.meta or {})
        meta["last_error"] = f"Outsee: {e}"
        project.meta = meta
        await session.flush()
        raise

    # ---- 3. Финал ----
    project.current_iter = new_iter
    project.status = "waiting_critique"
    await session.flush()

    logger.info(
        "test_prompt: #{} «{}» итерация {} готова "
        "(txt={}, img={})",
        project.id, project.name, new_iter, out_txt, out_img,
    )
    return out_txt, out_img


def is_busy(project: TestPromptProject) -> bool:
    return project.status in _RUNNING_STATUSES
