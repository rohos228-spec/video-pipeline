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


def test_extract_image_urls_from_html() -> None:
    from app.services.gpt_api import collect_result_urls, extract_image_urls_from_html

    html = """
    <html><head>
      <meta property="og:image" content="https://cdn.example/hero.png" />
      <meta name="twitter:image" content="https://cdn.example/tw.jpg" />
    </head><body>
      <img src="/static/a.webp" />
      <img src="data:image/png;base64,aaa" />
    </body></html>
    """
    urls = extract_image_urls_from_html(html, "https://wiki.example/page/Lion")
    assert urls[0] == "https://cdn.example/hero.png"
    assert "https://cdn.example/tw.jpg" in urls
    assert "https://wiki.example/static/a.webp" in urls
    assert all(not u.startswith("data:") for u in urls)

    ordered = collect_result_urls(
        "see https://en.wikipedia.org/wiki/Lion and https://cdn.example/x.png"
    )
    assert ordered[0].endswith(".png")


@pytest.mark.asyncio
async def test_materialize_html_pulls_og_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import gpt_api as ga

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    html = (
        b"<!DOCTYPE html><html><head>"
        b'<meta property="og:image" content="https://cdn.example/lion.png" />'
        b"</head><body>Lion</body></html>"
    )

    async def fake_download(url: str, out_path: Path, *, timeout: float = 180.0) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if url.endswith(".png") or "lion.png" in url:
            out_path.write_bytes(png)
            return ga.finalize_downloaded_file(
                out_path, content_type="image/png", url=url
            )
        out_path.write_bytes(html)
        return ga.finalize_downloaded_file(
            out_path, content_type="text/html", url=url
        )

    monkeypatch.setattr(ga, "download_content", fake_download)
    saved = await ga.materialize_reply_assets(
        "вот https://en.wikipedia.org/wiki/Lion_(Dota_2)",
        tmp_path,
        prefix="gpt_test",
    )
    assert len(saved) == 1
    assert saved[0].suffix == ".png"
    assert saved[0].read_bytes().startswith(b"\x89PNG")
    assert not list(tmp_path.glob("*.html"))


def test_finalize_url_download_never_bin(tmp_path: Path) -> None:
    from app.services.gpt_api import finalize_downloaded_file, maybe_decode_base64_image
    import base64

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    p = tmp_path / "gpt_20260727_110549_url_0.bin"
    p.write_bytes(png)
    got = finalize_downloaded_file(p, content_type="image/png")
    assert got.suffix == ".png"
    assert got.name.endswith(".png")
    assert not p.exists()

    # Content-Type alone (байты неизвестны)
    p2 = tmp_path / "url_1.bin"
    p2.write_bytes(b"\x00" * 200)
    got2 = finalize_downloaded_file(p2, content_type="image/webp")
    assert got2.suffix == ".webp"

    # base64 PNG в теле
    p3 = tmp_path / "url_2.bin"
    p3.write_bytes(base64.b64encode(png))
    dec, ext = maybe_decode_base64_image(p3.read_bytes())
    assert ext == ".png"
    got3 = finalize_downloaded_file(p3)
    assert got3.suffix == ".png"
    assert got3.read_bytes().startswith(b"\x89PNG")

    # неизвестное → .dat, не .bin
    p4 = tmp_path / "url_3.bin"
    p4.write_bytes(bytes(range(256)) * 4)
    got4 = finalize_downloaded_file(p4)
    assert got4.suffix == ".dat"
    assert got4.suffix != ".bin"


def test_sniff_png_bin_renames(tmp_path: Path) -> None:
    from app.services.gpt_api import (
        ensure_correct_extension,
        sniff_file_extension,
        suggested_name_and_mime,
    )

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    assert sniff_file_extension(png) == ".png"
    assert sniff_file_extension(b"\xff\xd8\xff\xe0" + b"x" * 20) == ".jpg"
    p = tmp_path / "reply.bin"
    p.write_bytes(png)
    # пока на диске .bin — download-имя уже .png + image/png
    name, mime = suggested_name_and_mime(p)
    assert name == "reply.png"
    assert mime == "image/png"
    fixed = ensure_correct_extension(p)
    assert fixed.name == "reply.png"
    assert fixed.is_file()
    assert not p.exists()


def test_get_session_renames_existing_bin() -> None:
    """Старые сессии с .bin на диске — при get_session становятся .png."""
    s = gw.create_session()
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    d = gw._session_dir(s["id"])
    att = d / "attachments"
    att.mkdir(exist_ok=True)
    (att / "legacy.bin").write_bytes(png)
    gw._append_message(
        s["id"],
        "assistant",
        "Вот legacy.bin для тебя",
        attachment_names=["legacy.bin"],
    )
    got = gw.get_session(s["id"])
    att0 = got["attachments"][0]
    assert att0["name"] == "legacy.png"
    assert att0["display_name"] == "legacy.png"
    assert att0["kind"] == "image"
    assert att0["mime"] == "image/png"
    assert got["messages"][0]["attachment_names"] == ["legacy.png"]
    assert "legacy.png" in got["messages"][0]["content"]
    assert "legacy.bin" not in got["messages"][0]["content"]
    assert not (att / "legacy.bin").exists()
    assert (att / "legacy.png").is_file()


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
    assert not gw._wants_file_return("текст переработай и пришли мне файлом")
    assert not gw._pure_file_return("текст переработай и пришли мне файлом")
    assert gw._pure_file_return("верни файл hero.png")
    assert not gw._pure_file_return("верни файл и опиши что на нём")
    assert not gw._wants_file_return("верни файл и опиши что на нём")


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
            captured["files"] = [p.name for p in files]
            captured["called"] = True
            return "На картинке персонаж в песке."

        async def download_attachment_from_last_reply(self, *a, **k):
            return None

    monkeypatch.setattr(gc, "get_gpt_client", lambda: FakeGpt())
    s = gw.create_session()
    gw.save_attachment(s["id"], "hero.png", b"\x89PNG" + b"x" * 40)
    out = await gw.ask(s["id"], "верни файл и опиши что на картинке")
    assert captured.get("called")
    # при анализе/переработке исходники не копируем в Результаты
    assert "Studio уже положила" not in (captured.get("text") or "")
    assert "hero.png" in captured.get("files", [])
    assert "Исходники (Studio" not in out["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_ask_rework_text_to_file_calls_gpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«переработай и пришли файлом» — GPT, не studio-return исходников."""
    import app.services.gpt_client as gc

    called = {"n": 0}

    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            called["n"] += 1
            assert files and files[0].name.endswith(".txt")
            return "Переработанный текст для файла."

        async def download_attachment_from_last_reply(self, *a, **k):
            return None

    monkeypatch.setattr(gc, "get_gpt_client", lambda: FakeGpt())
    s = gw.create_session()
    gw.save_attachment(s["id"], "дело.txt", "сырой текст дела".encode("utf-8"))
    out = await gw.ask(s["id"], "текст переработай и пришли мне файлом")
    assert called["n"] == 1
    assert out["messages"][-1].get("studio_returned") is not True
    assert "Studio вернула исходные" not in out["messages"][-1]["content"]
    assert "Переработанный текст" in out["messages"][-1]["content"]
    # новый файл в Результаты, не копия исходника
    out_names = [o["name"] for o in out["outputs"] if not o["name"].startswith("reply_")]
    assert any(n.endswith(".txt") and "дело" in n.lower() for n in out_names)

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
