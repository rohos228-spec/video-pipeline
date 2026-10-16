from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import notification_log as nl
from app.settings import settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("VP_NOTIFICATION_LOG", raising=False)
    nl.reset_stats()


class FakeBot:
    def __init__(self, fail: Exception | None = None):
        self.calls: list[tuple] = []
        self.fail = fail

    async def _do(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        if self.fail is not None:
            raise self.fail
        return SimpleNamespace(message_id=77)

    async def send_message(self, *a, **kw):
        return await self._do("send_message", *a, **kw)

    async def send_photo(self, *a, **kw):
        return await self._do("send_photo", *a, **kw)

    async def send_document(self, *a, **kw):
        return await self._do("send_document", *a, **kw)

    async def send_video(self, *a, **kw):
        return await self._do("send_video", *a, **kw)


def _rows():
    return nl.list_rows(100)



def test_ddl_constant_creates_expected_columns_and_is_append_only(tmp_path):
    con = sqlite3.connect(tmp_path / "x.db")
    con.executescript(nl.NOTIFICATION_LOG_DDL)
    con.executescript(nl.NOTIFICATION_LOG_DDL)
    cols = [r[1] for r in con.execute("PRAGMA table_info(notification_log)")]
    assert cols == ["id", "ts", "project_id", "run_id", "channel", "kind", "text_redacted", "ok", "error"]
    con.execute("INSERT INTO notification_log (ts, channel, kind, ok) VALUES ('2026-01-01 00:00:00','telegram','k',1)")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE notification_log SET ok = 0")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM notification_log")



async def test_success_is_logged_and_result_returned():
    bot = FakeBot()
    res = await nl.notify(bot, "send_message", 5, "привет", kind="step_done", project_id=12, run_id="r1",
                          parse_mode="HTML")
    assert res.message_id == 77
    assert bot.calls == [("send_message", (5, "привет"), {"parse_mode": "HTML"})]
    (row,) = _rows()
    assert (row["project_id"], row["run_id"], row["channel"], row["kind"], row["ok"], row["error"]) == \
        (12, "r1", "telegram", "step_done", 1, None)
    assert row["text_redacted"] == "привет"
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", row["ts"])


async def test_caption_of_media_is_logged_and_kind_defaults_to_method():
    bot = FakeBot()
    await nl.notify(bot, "send_photo", 5, object(), caption="подпись", project_id=3)
    await nl.notify(bot, "send_document", 5, object())
    rows = sorted(_rows(), key=lambda r: r["id"])
    assert [(r["kind"], r["text_redacted"]) for r in rows] == [("send_photo", "подпись"), ("send_document", "")]


async def test_send_failure_is_logged_as_error_and_reraised():
    bot = FakeBot(fail=RuntimeError("Forbidden: bot was blocked"))
    with pytest.raises(RuntimeError, match="blocked"):
        await nl.notify(bot, "send_message", 5, "текст", kind="x", project_id=1)
    (row,) = _rows()
    assert row["ok"] == 0 and "RuntimeError" in row["error"] and "blocked" in row["error"]


async def test_cancellation_is_logged_and_propagates():
    import asyncio
    bot = FakeBot(fail=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await nl.notify(bot, "send_message", 5, "t", kind="x")
    (row,) = _rows()
    assert row["ok"] == 0 and row["error"] == "CancelledError"


async def test_secrets_are_redacted_and_text_is_truncated():
    bot = FakeBot()
    secret = "sk-abcdefghijklmnop1234"
    await nl.notify(bot, "send_message", 5, f"ключ {secret} " + "я" * 5000, kind="x")
    (row,) = _rows()
    assert secret not in row["text_redacted"]
    assert len(row["text_redacted"]) <= nl.TEXT_LIMIT
    assert secret in bot.calls[0][1][1]


async def test_log_failure_never_blocks_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(nl, "_db_path", lambda: tmp_path)
    bot = FakeBot()
    res = await nl.notify(bot, "send_message", 5, "доставь", kind="x")
    assert res.message_id == 77 and len(bot.calls) == 1
    st = nl.stats()
    assert st["skipped"] == 1 and st["written"] == 0 and st["last_error"]


async def test_log_failure_does_not_mask_send_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(nl, "_db_path", lambda: tmp_path)
    bot = FakeBot(fail=ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        await nl.notify(bot, "send_message", 5, "t", kind="x")


async def test_noop_bot_is_recorded_as_not_delivered():
    from app.telegram.noop_bot import NoopBot
    await nl.notify(NoopBot(), "send_message", 5, "никому", kind="step_done", project_id=2)
    (row,) = _rows()
    assert row["channel"] == nl.CHANNEL_OFF and row["ok"] == 0 and "не отправлено" in row["error"]


async def test_unknown_method_is_rejected():
    with pytest.raises(ValueError):
        await nl.notify(FakeBot(), "delete_message", 5, 1)


async def test_switch_off_env_skips_logging_but_still_sends(monkeypatch):
    monkeypatch.setenv("VP_NOTIFICATION_LOG", "0")
    bot = FakeBot()
    await nl.notify(bot, "send_message", 5, "t", kind="x")
    assert len(bot.calls) == 1 and _rows() == []



async def test_hitl_text_goes_through_notify_with_project_id(monkeypatch):
    from app.models import HITLKind
    from app.services import hitl

    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "123:fake")
    monkeypatch.setattr(settings, "telegram_owner_chat_id", 4242)

    class Sess:
        def add(self, obj):
            obj.id = 9

        async def flush(self):
            pass

    bot = FakeBot()
    project = SimpleNamespace(id=31)
    req = await hitl.send_hitl_text(bot, Sess(), project, HITLKind.approve_plan, "Заголовок", "Тело <b>")
    assert req.tg_message_id == 77
    name, args, kw = bot.calls[0]
    assert name == "send_message" and args[0] == 4242 and kw["parse_mode"] == "HTML" and "kind" not in kw
    (row,) = _rows()
    assert row["project_id"] == 31 and row["kind"] == "hitl_request" and row["ok"] == 1
    assert "Заголовок" in row["text_redacted"]



_DIRECT = re.compile(r"\.send_(?:message|photo|document|video|audio|voice|animation)\(")
_OUTSIDE_BOUNDARY: dict[str, int] = {}


def _direct_calls() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in (ROOT / "app").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in {"app/services/notification_log.py"}:
            continue
        n = len(_DIRECT.findall(path.read_text(encoding="utf-8", errors="replace")))
        if n:
            found[rel] = n
    return found


def test_no_direct_send_calls_inside_boundary():
    found = _direct_calls()
    inside = {k: v for k, v in found.items() if k not in _OUTSIDE_BOUNDARY}
    assert inside == {}, f"прямые bot.send_*: используйте notification_log.notify(): {inside}"


def test_outside_boundary_list_only_shrinks():
    found = _direct_calls()
    for rel, n in found.items():
        if rel in _OUTSIDE_BOUNDARY:
            assert n <= _OUTSIDE_BOUNDARY[rel], f"в {rel} прибавились прямые отправки ({n})"
    for rel in _OUTSIDE_BOUNDARY:
        assert rel in found, f"{rel} больше не содержит прямых отправок: удалите из _OUTSIDE_BOUNDARY"


def test_every_notify_call_site_names_kind_and_imports():
    sites = 0
    for path in (ROOT / "app").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "app/services/notification_log.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        calls = [m.start() for m in re.finditer(r"await notify\(", text)]
        if not calls:
            continue
        assert "from app.services.notification_log import notify" in text, rel
        for pos in calls:
            depth, i = 0, pos + len("await notify")
            while True:
                ch = text[i]
                depth += ch == "("
                depth -= ch == ")"
                i += 1
                if depth == 0:
                    break
            assert re.search(r"\bkind\s*=", text[pos:i]), f"{rel}: notify без kind= ({text[pos:pos + 80]!r})"
            sites += 1
    assert sites == 42
