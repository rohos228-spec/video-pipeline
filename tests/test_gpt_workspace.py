"""GPT workspace: вложения, ask, ошибки, save."""

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

    gw._append_message(s["id"], "assistant", "закадровый текст для ролика")
    proj = tmp_path / "proj"
    r = gw.save_reply_as_voiceover(s["id"], project_data_dir=proj)
    assert (proj / "voiceover.txt").read_text(encoding="utf-8").startswith("закадровый")
    assert r["chars"] > 0


def test_attachment_urls_include_download() -> None:
    s = gw.create_session()
    att = gw.save_attachment(s["id"], "note.txt", b"hello")
    assert "download=1" in att["download_url"]
    assert att["url"].startswith("/api/files?path=")
    got = gw.get_session(s["id"])
    assert got["attachments"][0]["download_url"].endswith("download=1")


def test_copy_attachment_to_outputs() -> None:
    s = gw.create_session()
    gw.save_attachment(s["id"], "pic.png", b"\x89PNG" + b"x" * 40)
    out = gw.copy_attachment_to_outputs(s["id"], "pic.png")
    assert out["name"] == "pic.png"
    got = gw.get_session(s["id"])
    assert any(o["name"] == "pic.png" for o in got["outputs"])


def test_outputs_zip() -> None:
    s = gw.create_session()
    gw.save_attachment(s["id"], "a.txt", b"hello")
    gw.copy_attachment_to_outputs(s["id"], "a.txt")
    z = gw.build_outputs_zip(s["id"])
    assert z.exists() and z.suffix == ".zip"
    assert z.stat().st_size > 10


def test_sniff_png_bin_renames(tmp_path: Path) -> None:
    from app.services.gpt_api import ensure_correct_extension, sniff_file_extension

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    assert sniff_file_extension(png) == ".png"
    assert sniff_file_extension(b"\xff\xd8\xff\xe0" + b"x" * 20) == ".jpg"
    p = tmp_path / "reply.bin"
    p.write_bytes(png)
    fixed = ensure_correct_extension(p)
    assert fixed.name == "reply.png"
    assert fixed.is_file()
    assert not p.exists()


def test_save_attachment_bin_becomes_png() -> None:
    s = gw.create_session()
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    att = gw.save_attachment(s["id"], "sand.bin", png)
    assert att["name"].endswith(".png")
    assert not att["name"].endswith(".bin")

    assert gw._wants_file_return("верни файл обратно")
    assert gw._wants_file_return("send back the file please")
    assert not gw._wants_file_return("что на картинке?")
    assert gw._pure_file_return("верни файл hero.png")
    assert not gw._pure_file_return("верни файл и опиши что на нём")


def test_save_attachment_missing_session() -> None:
    with pytest.raises(FileNotFoundError, match="сессия не найдена"):
        gw.save_attachment("nosuch", "a.txt", b"hi")


@pytest.mark.asyncio
async def test_ask_studio_returns_bytes_without_gpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«Верни файл» — исходные байты с диска, GPT не вызывается."""
    import app.services.gpt_client as gc

    called = {"n": 0}

    class Boom:
        async def ask_with_files(self, *a, **k):
            called["n"] += 1
            raise AssertionError("GPT не должен вызываться для чистого возврата")

    monkeypatch.setattr(gc, "get_gpt_client", lambda: Boom())

    s = gw.create_session()
    raw = b"\x89PNG" + b"ORIGINAL-BYTES-XYZ" + b"x" * 20
    gw.save_attachment(s["id"], "hero.png", raw)
    out = await gw.ask(s["id"], "верни файл hero.png")
    assert called["n"] == 0
    names = [o["name"] for o in out["outputs"]]
    assert "hero.png" in names
    # байты идентичны исходному вложению
    got = next(o for o in out["outputs"] if o["name"] == "hero.png")
    assert Path(got["path"]).read_bytes() == raw
    assert "Studio вернула" in out["messages"][-1]["content"]
    assert out["messages"][-1].get("studio_returned") is True


@pytest.mark.asyncio
async def test_ask_return_plus_analyze_still_calls_gpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.gpt_client as gc

    captured: dict = {}

    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            captured["text"] = text
            captured["called"] = True
            return "На картинке персонаж в песке."

        async def download_attachment_from_last_reply(self, *a, **k):
            return None

    monkeypatch.setattr(gc, "get_gpt_client", lambda: FakeGpt())
    s = gw.create_session()
    gw.save_attachment(s["id"], "hero.png", b"\x89PNG" + b"x" * 40)
    out = await gw.ask(s["id"], "верни файл и опиши что на картинке")
    assert captured.get("called")
    assert "Studio уже положила" in captured["text"]
    assert "hero.png" in [o["name"] for o in out["outputs"]]
    assert "Исходники (Studio" in out["messages"][-1]["content"]


def test_save_attachment_empty_raises() -> None:
    s = gw.create_session()
    with pytest.raises(ValueError, match="пустой"):
        gw.save_attachment(s["id"], "a.txt", b"")


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
            captured["treat_txt_as_prompt"] = kwargs.get("treat_txt_as_prompt")
            return "Ты Капитан"

        async def download_attachment_from_last_reply(self, *a, **k):
            return None

    monkeypatch.setattr(gc, "get_gpt_client", lambda: FakeGpt())

    out = await gw.ask(s["id"], "как меня зовут?")
    assert captured["text"] == "как меня зовут?"
    assert len(captured["history"]) == 2
    assert captured["history"][0]["content"] == "зови меня Капитан"
    assert "Капитан" in out["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_ask_sends_txt_as_attachment_not_master(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Workspace: .txt — вложение, не master-промт пайплайна."""
    import app.services.gpt_client as gc

    s = gw.create_session()
    gw.save_attachment(s["id"], "secret.txt", "код OMEGA-1\n".encode("utf-8"))

    captured: dict = {}

    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            captured["text"] = text
            captured["files"] = [Path(p).name for p in files]
            captured["treat_txt_as_prompt"] = kwargs.get("treat_txt_as_prompt", True)
            captured["expect_file_download"] = kwargs.get("expect_file_download")
            return "OMEGA-1"

        async def download_attachment_from_last_reply(self, *a, **k):
            return None

    monkeypatch.setattr(gc, "get_gpt_client", lambda: FakeGpt())
    out = await gw.ask(s["id"], "какой код в файле?")
    assert captured["text"] == "какой код в файле?"
    assert captured["files"] == ["secret.txt"]
    assert captured["treat_txt_as_prompt"] is False
    assert captured["expect_file_download"] is False
    assert out["messages"][-2].get("attachment_names") == ["secret.txt"]
    assert "OMEGA-1" in out["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_ask_xlsx_sets_expect_download(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.gpt_client as gc

    s = gw.create_session()
    gw.save_attachment(s["id"], "t.xlsx", b"PK\x03\x04fake")

    captured: dict = {}

    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            captured["expect_file_download"] = kwargs.get("expect_file_download")
            captured["treat_txt_as_prompt"] = kwargs.get("treat_txt_as_prompt")
            return "ok"

        async def download_attachment_from_last_reply(self, *a, **k):
            raise RuntimeError("no xlsx")

    monkeypatch.setattr(gc, "get_gpt_client", lambda: FakeGpt())
    await gw.ask(s["id"], "проверь таблицу")
    assert captured["expect_file_download"] is True
    assert captured["treat_txt_as_prompt"] is False


@pytest.mark.asyncio
async def test_ask_api_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.gpt_client as gc
    from app.services.gpt_client import GptApiUnavailable

    s = gw.create_session()

    class Boom:
        async def ask_with_files(self, *a, **k):
            raise GptApiUnavailable("GPT API не настроен: test")

    monkeypatch.setattr(gc, "get_gpt_client", lambda: Boom())
    with pytest.raises(GptApiUnavailable):
        await gw.ask(s["id"], "привет")
    got = gw.get_session(s["id"])
    assert got["status"] == "error"
    assert any(m["role"] == "system" for m in got["messages"])
