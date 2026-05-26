"""anim_pr: фаза 1 — только текст+файл; фаза 2 — только фото."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bots import chatgpt as cg


def test_anim_pr_doc_and_image_suffixes_disjoint() -> None:
    assert not cg._ANIM_PR_DOC_SUFFIXES & cg._ANIM_PR_IMAGE_SUFFIXES


@pytest.mark.asyncio
async def test_anim_pr_initial_rejects_png(tmp_path: Path) -> None:
    png = tmp_path / "prompt.png"
    png.write_bytes(b"x")

    class _Gpt:
        async def ask_with_files(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return "ok"

        async def _clear_composer_attachments(self) -> int:
            return 0

    gpt = _Gpt()
    with pytest.raises(ValueError, match="не картинка"):
        await cg.ChatGPTBot.ask_anim_pr_initial(gpt, "hi", png)  # type: ignore[arg-type]
