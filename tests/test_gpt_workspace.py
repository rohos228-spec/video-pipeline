"""GPT workspace service: sessions / attachments / save."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import gpt_workspace as gw


@pytest.fixture(autouse=True)
def _workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gw.settings, "data_dir", tmp_path)
    yield


def test_create_list_get_delete() -> None:
    s = gw.create_session(title="Тест")
    assert s["title"] == "Тест"
    assert s["id"]
    listed = gw.list_sessions()
    assert any(x["id"] == s["id"] for x in listed)
    got = gw.get_session(s["id"])
    assert got["messages"] == []
    gw.delete_session(s["id"])
    with pytest.raises(FileNotFoundError):
        gw.get_session(s["id"])


def test_attachment_and_voiceover_save(tmp_path: Path) -> None:
    s = gw.create_session()
    att = gw.save_attachment(s["id"], "note.txt", b"hello")
    assert att["name"] == "note.txt"
    got = gw.get_session(s["id"])
    assert len(got["attachments"]) == 1

    # fake assistant message
    gw._append_message(s["id"], "assistant", "закадровый текст для ролика")
    proj = tmp_path / "proj"
    r = gw.save_reply_as_voiceover(s["id"], project_data_dir=proj)
    assert (proj / "voiceover.txt").read_text(encoding="utf-8").startswith("закадровый")
    assert r["chars"] > 0


@pytest.mark.asyncio
async def test_ask_passes_session_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повторный ask должен отдать прошлые реплики в history клиента."""
    import app.services.gpt_client as gc

    s = gw.create_session(title="mem")
    gw._append_message(s["id"], "user", "зови меня Капитан")
    gw._append_message(s["id"], "assistant", "Хорошо, Капитан")

    captured: dict = {}

    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            captured["text"] = text
            captured["history"] = list(kwargs.get("history") or [])
            return "Ты Капитан"

        async def download_attachment_from_last_reply(self, *a, **k):
            return None

    monkeypatch.setattr(gc, "get_gpt_client", lambda: FakeGpt())

    out = await gw.ask(s["id"], "как меня зовут?")
    assert captured["text"] == "как меня зовут?"
    assert len(captured["history"]) == 2
    assert captured["history"][0]["content"] == "зови меня Капитан"
    assert "Капитан" in out["messages"][-1]["content"]
