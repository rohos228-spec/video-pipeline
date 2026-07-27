"""Единый текстовый GPT-клиент (API) — замена ChatGPTBot без CDP.

Duck-typed под методы, которыми пользуются шаги пайплайна:
  ask_fresh / ask_with_files / ask_anim_pr_* / download_attachment_from_last_reply

Браузер (Chrome/CDP) для текста больше не нужен. Outsee/ElevenLabs —
отдельные боты и по-прежнему могут открывать CDP.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from app.settings import settings


class GptApiUnavailable(RuntimeError):
    """Нет ключа/базы GPT API — текстовый шаг не может идти через браузер."""


def gpt_text_via_api() -> bool:
    """True — весь текстовый GPT идёт через HTTP API."""
    return bool(settings.gpt_api_enabled)


def require_gpt_api() -> None:
    if not gpt_text_via_api():
        raise GptApiUnavailable(
            "GPT API не настроен: задай GPT_API_KEY (или GRSAI_API_KEY) "
            "и GPT_BASE_URL. Браузерный ChatGPT для текста отключён."
        )


class ApiGptClient:
    """Совместим с ChatGPTBot по текстовым методам (без Playwright)."""

    def __init__(self) -> None:
        self._last_reply: str = ""
        self._last_input_paths: list[Path] = []

    async def new_conversation(self) -> None:
        self._last_reply = ""
        self._last_input_paths = []

    async def ask_fresh(
        self,
        text: str,
        *,
        timeout: float = 600,
        project_id: int | None = None,
    ) -> str:
        return await self.ask_with_files(
            text,
            [],
            timeout=timeout,
            project_id=project_id,
            expect_file_download=False,
        )

    async def ask_with_files(
        self,
        text: str,
        files: list[Path],
        *,
        timeout: float = 600,
        project_id: int | None = None,
        expect_file_download: bool = False,
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        require_gpt_api()
        from app.services.gpt_api import chat
        from app.services.step_cancel import raise_if_cancelled
        from app.services.xlsx_text_writeback import WRITEBACK_HINT

        if project_id is not None:
            raise_if_cancelled(project_id)

        attachments = [Path(p) for p in (files or []) if p]
        for fp in attachments:
            if not fp.exists():
                raise FileNotFoundError(f"gpt_client: файл не найден {fp}")

        prompt_file: Path | None = None
        data_files: list[Path] = []
        for p in attachments:
            if prompt_file is None and p.suffix.lower() in {".txt", ".md"}:
                # Первый .txt/.md — мастер-промт (как вложение в браузере).
                prompt_file = p
            else:
                data_files.append(p)

        master = ""
        if prompt_file is not None:
            try:
                master = prompt_file.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                raise RuntimeError(f"gpt_client: не прочитал {prompt_file}: {e}") from e

        accompanying = (text or "").strip()
        if expect_file_download and any(
            p.suffix.lower() in {".xlsx", ".xlsm"} for p in data_files
        ):
            if WRITEBACK_HINT not in accompanying:
                accompanying = (
                    f"{accompanying}\n\n{WRITEBACK_HINT}".strip()
                    if accompanying
                    else WRITEBACK_HINT
                )

        hist = list(history or [])
        logger.info(
            "gpt_client/api: ask files=[{}] master={} chat_len={} expect_dl={} pid={} history={}",
            ", ".join(p.name for p in attachments) or "—",
            prompt_file.name if prompt_file else "—",
            len(accompanying),
            expect_file_download,
            project_id,
            len(hist),
        )

        result = await chat(
            prompt=master or accompanying,
            accompanying="" if not master else accompanying,
            input_paths=data_files if master else attachments,
            timeout=float(timeout),
            history=hist or None,
        )
        self._last_reply = result.text or ""
        self._last_input_paths = list(data_files or attachments)
        if project_id is not None:
            raise_if_cancelled(project_id)
        logger.info(
            "gpt_client/api: reply len={} model={}",
            len(self._last_reply),
            result.model,
        )
        return self._last_reply

    async def ask_anim_pr_initial(
        self,
        text: str,
        prompt_file: Path,
        *,
        timeout: float = 300,
        project_id: int | None = None,
    ) -> str:
        return await self.ask_with_files(
            text,
            [prompt_file],
            timeout=timeout,
            project_id=project_id,
            expect_file_download=False,
        )

    async def ask_anim_pr_batch(
        self,
        text: str,
        images: list[Path],
        *,
        timeout: float = 600,
        project_id: int | None = None,
    ) -> str:
        return await self.ask_with_files(
            text,
            list(images),
            timeout=timeout,
            project_id=project_id,
            expect_file_download=False,
        )

    async def download_attachment_from_last_reply(
        self,
        download_path: Path,
        *,
        timeout: float = 600,
        fallback_text: str | None = None,
        allow_reply_text_fallback: bool = False,
    ) -> Path:
        """Материализовать результат последнего ответа в download_path.

        Браузер качал .xlsx/.txt из чата. API: URL→файл, TSV→xlsx, либо текст.
        """
        from app.services.gpt_api import collect_result_urls, download_content
        from app.services.xlsx_text_writeback import writeback_project_xlsx

        target = Path(download_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        reply = fallback_text if fallback_text is not None else self._last_reply
        suffix = target.suffix.lower()

        downloaded: list[Path] = []
        for i, url in enumerate(collect_result_urls(reply or "")):
            url_suffix = Path(url.split("?")[0]).suffix.lower()
            if suffix in {".xlsx", ".xlsm", ".xls"} and url_suffix not in {
                ".xlsx",
                ".xlsm",
                ".xls",
                "",
            }:
                continue
            if suffix == ".txt" and url_suffix not in {".txt", ".md", ""}:
                continue
            dest = target.with_name(f".gpt_url_{target.stem}_{i}{url_suffix or suffix}")
            try:
                got = await download_content(url, dest, timeout=float(timeout))
                downloaded.append(got)
            except Exception as e:  # noqa: BLE001
                logger.warning("gpt_client: не скачал {}: {}", url, e)

        if suffix in {".xlsx", ".xlsm", ".xls"}:
            # Пишем во временный файл, не в живой project.xlsx.
            staging = target.with_name(f".gpt_dl_{target.stem}.xlsx")
            if staging.exists():
                staging.unlink()
            ref = next(
                (
                    p
                    for p in self._last_input_paths
                    if p.suffix.lower() in {".xlsx", ".xlsm"} and p.exists()
                ),
                None,
            )
            if ref is not None:
                import shutil

                shutil.copy2(ref, staging)
            wrote = writeback_project_xlsx(
                project_xlsx=staging if ref is not None else target,
                reply_text=reply or "",
                downloaded_paths=downloaded,
            )
            if wrote is None and not downloaded:
                if allow_reply_text_fallback and (reply or "").strip():
                    target.write_text(reply, encoding="utf-8")
                    return target
                raise RuntimeError(
                    "gpt_client: API не вернул xlsx/TSV для записи "
                    f"в {target.name}"
                )
            src = wrote or downloaded[0]
            if src.resolve() != target.resolve():
                import shutil

                shutil.copy2(src, target)
            if staging.exists() and staging.resolve() != target.resolve():
                try:
                    staging.unlink()
                except OSError:
                    pass
            return target

        # txt / прочее — текст ответа
        body = (reply or "").strip()
        if not body and downloaded:
            try:
                body = downloaded[0].read_text(encoding="utf-8", errors="replace")
            except OSError:
                body = ""
        if not body and not allow_reply_text_fallback:
            raise RuntimeError(f"gpt_client: пустой ответ для {target.name}")
        target.write_text(body or "", encoding="utf-8")
        return target


def get_gpt_client() -> ApiGptClient:
    """Фабрика: всегда API-клиент (браузерный текст отключён)."""
    require_gpt_api()
    return ApiGptClient()


async def gpt_ask_fresh(
    text: str,
    *,
    timeout: float = 600,
    project_id: int | None = None,
) -> str:
    client = get_gpt_client()
    return await client.ask_fresh(text, timeout=timeout, project_id=project_id)


async def gpt_ask_with_files(
    text: str,
    files: list[Path],
    *,
    timeout: float = 600,
    project_id: int | None = None,
    expect_file_download: bool = False,
) -> str:
    client = get_gpt_client()
    return await client.ask_with_files(
        text,
        files,
        timeout=timeout,
        project_id=project_id,
        expect_file_download=expect_file_download,
    )


# Для type hints в outsee_retry / auto_review
GptClientLike = Any
