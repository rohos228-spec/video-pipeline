"""Ревью #352: скачивание уже оплаченного результата не режется бюджетом."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from app.services.style_agent.budget import LlmBudget


@pytest.mark.asyncio
async def test_exhausted_budget_still_saves_paid_download(
    monkeypatch, tmp_path: Path
) -> None:
    from app.bots import outsee_http
    from app.services.spend_guard import use_spend_budget

    dest = tmp_path / "paid.png"
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/curl" if name == "curl" else None)

    class _Proc:
        returncode = 0

        async def communicate(self):
            dest.write_bytes(b"x" * 80)
            return b"", b""

    async def _fake_curl(*_args, **_kwargs):
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_curl)

    with use_spend_budget(LlmBudget(max_calls=0)):
        ok = await outsee_http._download_via_curl(
            "https://cdn.example/already-paid.png", dest
        )

    assert ok is True
    assert dest.is_file()
    assert dest.stat().st_size >= 64
