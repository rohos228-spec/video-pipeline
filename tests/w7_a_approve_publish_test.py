"""W7-A: вид HITL approve_publish - «да» на публикацию из Telegram.

Бот - заменитель (ничего не уходит в сеть), площадки и браузер - фейковый реестр, файл ролика синтетический,
рабочая папка и БД вне репозитория (tests/_stand_workdir.py). Класс доказательства: U. Реальным Telegram не
проверялось.
"""
from __future__ import annotations

import html
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import HITLDecision, HITLKind, HITLRequest, ProjectStatus
from app.orchestrator.steps import publish as step
from app.services import hitl as hitl_svc
from app.services import notification_log as nl
from app.services import operator_audit as oa
from app.services import publications_store as ps
from app.settings import settings
from tests.w6_1_support import PLATFORMS, Bot, Env, add_final, fake_registry, make_project, sha_of

OWNER = 4242
STRANGER = 999
CAPTION = "Тема ролика про тест"


class TgBot:
    """Заменитель aiogram.Bot: запоминает отправленное, номера сообщений идут по порядку."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edits: list[dict] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return SimpleNamespace(message_id=1000 + len(self.sent))

    async def edit_message_reply_markup(self, **kw):
        self.edits.append(kw)

    def with_buttons(self) -> list[dict]:
        return [m for m in self.sent if m.get("reply_markup") is not None]


@pytest.fixture
async def env():
    e = await Env().create()
    yield e
    await e.close()


@pytest.fixture
async def db(env):
    async with env.maker() as s:
        yield s


@pytest.fixture(autouse=True)
def _flags(monkeypatch, env):
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    monkeypatch.setattr(settings, "telegram_enabled", True, raising=False)
    monkeypatch.setattr(settings, "telegram_bot_token", "123:test", raising=False)
    monkeypatch.setattr(settings, "telegram_owner_chat_id", OWNER)
    monkeypatch.setattr(settings, "hitl_auto_approve", False)
    # аудит и журнал уведомлений пишут в рабочую папку вне репозитория
    monkeypatch.setattr(settings, "sqlite_path", env.workdir / "state.db")
    monkeypatch.delenv("VP_OPERATOR_AUDIT", raising=False)
    monkeypatch.delenv("VP_NOTIFICATION_LOG", raising=False)
    oa.reset_stats()
    oa._ensured.clear()
    nl._ensured.clear()
    assert settings.telegram_active


async def _requests(db, project_id, decision=None):
    q = select(HITLRequest).where(HITLRequest.project_id == project_id, HITLRequest.kind == HITLKind.approve_publish)
    if decision is not None:
        q = q.where(HITLRequest.decision == decision)
    return list((await db.execute(q.order_by(HITLRequest.id))).scalars().all())


async def _setup(db, env, monkeypatch, *, behaviors=None, topic=CAPTION):
    clicks = fake_registry(monkeypatch, behaviors)
    p = await make_project(db, env.workdir, topic=topic)
    bot = TgBot()
    await step.run(db, p, bot)
    await db.commit()
    return p, bot, clicks


def _by_platform(reqs):
    return {(r.payload or {})["platform"]: r for r in reqs}


async def _valid(db, p):
    sha = await sha_of(db, p)
    csha = step.caption_sha256(step.caption_for(p))
    return {k for k, v in (await ps.list_approvals(db, p.id, sha, csha)).items() if v["valid"]}


# ── создание запроса и сообщение ────────────────────────────────────────────────


async def test_awaiting_approval_sends_one_request_per_platform_with_caption_sha_and_buttons(db, env, monkeypatch):
    p, bot, clicks = await _setup(db, env, monkeypatch)
    reqs = await _requests(db, p.id, HITLDecision.pending)
    assert set(_by_platform(reqs)) == set(PLATFORMS) and len(reqs) == len(PLATFORMS)
    sha = await sha_of(db, p)
    msgs = bot.with_buttons()
    assert len(msgs) == len(PLATFORMS)
    for platform, req in _by_platform(reqs).items():
        m = next(x for x in msgs if f"Площадка: {platform}\n" in x["text"])
        assert CAPTION in m["text"] and sha[:12] in m["text"] and sha not in m["text"]
        assert req.tg_message_id and m["chat_id"] == OWNER
        buttons = [b for row in m["reply_markup"].inline_keyboard for b in row]
        assert [b.text for b in buttons] == ["Разрешить публикацию", "Не публиковать"]
        assert [b.callback_data for b in buttons] == [f"hpub:{req.id}:yes", f"hpub:{req.id}:no"]
        assert all(len(b.callback_data.encode()) <= 64 for b in buttons)
    # само по себе сообщение ничего не разрешает
    assert clicks == [] and clicks.opened == [] and await _valid(db, p) == set()
    assert p.status is ProjectStatus.assembled


async def test_caption_is_verbatim_even_with_markup_characters(db, env, monkeypatch):
    topic = "Тема <b>& «кавычки»</b>\nвторая строка"
    p, bot, _ = await _setup(db, env, monkeypatch, topic=topic)
    m = bot.with_buttons()[0]
    assert m["parse_mode"] == "HTML" and html.escape(topic) in m["text"]
    assert html.unescape(m["text"]).count(topic) == 1 and "<b>Проект #" in m["text"]


async def test_long_caption_goes_verbatim_in_chunks_with_buttons_only_on_the_last(db, env, monkeypatch):
    topic = "&<" * 1100  # 2200 символов, после экранирования заметно длиннее лимита Telegram
    p, bot, _ = await _setup(db, env, monkeypatch, topic=topic)
    assert all(len(m["text"]) <= 4096 for m in bot.sent)
    assert len(bot.with_buttons()) == len(PLATFORMS)
    first_chunks = []
    for m in bot.sent:
        first_chunks.append(m)
        if m.get("reply_markup") is not None:
            break
    assert len(first_chunks) >= 2 and all(m.get("reply_markup") is None for m in first_chunks[:-1])
    joined = html.unescape("".join(re.sub(r"<[^>]+>", "", m["text"]) for m in first_chunks))
    assert topic in joined  # ни одна сущность &amp; / &lt; не порвана на границе кусков


async def test_no_telegram_no_request_and_panel_path_is_untouched(db, env, monkeypatch):
    monkeypatch.setattr(settings, "telegram_enabled", False, raising=False)
    fake_registry(monkeypatch)
    p = await make_project(db, env.workdir)
    bot = Bot()
    await step.run(db, p, bot)
    assert await _requests(db, p.id) == []
    assert p.status is ProjectStatus.assembled and "publish_awaiting_approval" in p.meta


async def test_rerun_for_the_same_file_does_not_duplicate_requests(db, env, monkeypatch):
    p, bot, _ = await _setup(db, env, monkeypatch)
    p.status = ProjectStatus.publishing
    await step.run(db, p, bot)
    await db.commit()
    assert len(await _requests(db, p.id)) == len(PLATFORMS)
    assert len(bot.with_buttons()) == len(PLATFORMS)


async def test_failed_send_leaves_no_orphan_request(db, env, monkeypatch):
    fake_registry(monkeypatch)
    p = await make_project(db, env.workdir)

    class Broken(TgBot):
        async def send_message(self, chat_id, text, **kw):
            if kw.get("reply_markup") is not None:
                raise RuntimeError("telegram down")
            return await super().send_message(chat_id, text, **kw)

    await step.run(db, p, Broken())
    assert await _requests(db, p.id) == []
    assert p.status is ProjectStatus.assembled  # шаг не сломан, «да» остаётся в панели


# ── автоодобрение «да» не создаёт ──────────────────────────────────────────────


async def test_auto_mode_and_hitl_auto_approve_do_not_decide_or_grant(db, env, monkeypatch):
    monkeypatch.setattr(settings, "hitl_auto_approve", True)
    clicks = fake_registry(monkeypatch)
    p = await make_project(db, env.workdir)
    p.auto_mode = True
    bot = TgBot()
    await step.run(db, p, bot)
    await db.commit()
    assert len(await _requests(db, p.id, HITLDecision.pending)) == len(PLATFORMS)
    from app.orchestrator.auto_advance import maybe_auto_advance
    await maybe_auto_advance(db, p, bot, force=True)
    await db.commit()
    assert len(await _requests(db, p.id, HITLDecision.pending)) == len(PLATFORMS)
    assert await _valid(db, p) == set() and clicks == [] and clicks.opened == []
    # (статус после maybe_auto_advance не проверяем: у синтетического проекта нет данных шагов, и его
    # собственный clamp_status_to_data опускает статус; важно, что «да» и решения по запросам не появились)


# ── «Разрешить публикацию» ────────────────────────────────────────────────────


async def test_owner_yes_grants_exactly_one_platform_with_telegram_actor_and_audit(db, env, monkeypatch):
    p, bot, clicks = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))["youtube"] if "youtube" in PLATFORMS else \
        _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    platform = req.payload["platform"]
    res = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)
    assert res.ok and res.code == "ok" and res.hide_buttons and platform in res.text
    assert await _valid(db, p) == {platform}
    sha = await sha_of(db, p)
    appr = (await ps.list_approvals(db, p.id, sha))[platform]
    assert appr["approved_by"] == f"telegram:{OWNER}"
    await db.refresh(req)
    assert req.decision is HITLDecision.approved
    rows = [r for r in oa.list_rows(50, p.id) if r["method"] == "TELEGRAM"]
    assert len(rows) == 1 and rows[0]["actor"] == f"telegram:{OWNER}" and rows[0]["outcome"] == "ok"
    assert rows[0]["status_code"] == 200 and rows[0]["project_id"] == p.id
    assert rows[0]["route_template"] == "telegram:approve_publish" and str(req.id) in rows[0]["path"]
    # «да» только записано; шаг публикации поставлен в очередь, кликов ещё нет
    assert clicks == [] and p.status is ProjectStatus.publishing


async def test_yes_then_publish_step_clicks_only_the_approved_platform_once(db, env, monkeypatch):
    p, bot, clicks = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[1]]
    assert (await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)).ok
    await step.run(db, p, bot)
    await db.commit()
    assert list(clicks) == [PLATFORMS[1]]
    assert await _valid(db, p) == set()  # «да» потрачено перед кликом


async def test_second_press_does_not_create_a_second_yes_even_after_the_first_was_spent(db, env, monkeypatch):
    p, bot, clicks = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    assert (await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)).ok
    await step.run(db, p, bot)  # «да» потрачено кликом
    await db.commit()
    assert list(clicks) == [PLATFORMS[0]] and await _valid(db, p) == set()
    again = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)
    assert not again.ok and again.code == "already_decided"
    assert await _valid(db, p) == set() and list(clicks) == [PLATFORMS[0]]  # второго «да» и клика нет
    # и кнопка «Не публиковать» на том же уже обработанном сообщении тоже ничего не делает
    no = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:no", OWNER)
    assert not no.ok and no.code == "already_decided"
    rows = [r for r in oa.list_rows(50, p.id) if r["method"] == "TELEGRAM"]
    assert [r["status_code"] for r in rows] == [409, 409, 200]


async def test_double_press_before_spending_gives_one_yes(db, env, monkeypatch):
    p, bot, _ = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[2]]
    first = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)
    sha = await sha_of(db, p)
    stamp = (await ps.list_approvals(db, p.id, sha))[PLATFORMS[2]]["approved_at"]
    second = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)
    assert first.ok and not second.ok and second.code == "already_decided"
    assert (await ps.list_approvals(db, p.id, sha))[PLATFORMS[2]]["approved_at"] == stamp  # «да» не перевыдано


# ── чужие пользователи ────────────────────────────────────────────────────────


async def test_stranger_is_rejected_for_both_buttons_and_the_request_stays_open(db, env, monkeypatch):
    p, bot, _ = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    for action in ("yes", "no"):
        res = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:{action}", STRANGER)
        assert not res.ok and res.code == "forbidden" and not res.hide_buttons
    assert await _valid(db, p) == set()
    await db.refresh(req)
    assert req.decision is HITLDecision.pending
    sha = await sha_of(db, p)
    assert await ps.list_for(db, p.id, sha) == []  # площадка не закрыта чужим
    rows = [r for r in oa.list_rows(50) if r["method"] == "TELEGRAM"]
    assert len(rows) == 2 and all(r["actor"] == f"telegram:{STRANGER}" and r["outcome"] == "denied"
                                  and r["status_code"] == 403 for r in rows)
    # владелец после этого может решить
    assert (await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)).ok


async def test_missing_user_and_garbage_callback_are_refused(db, env, monkeypatch):
    p, bot, _ = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    assert (await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", None)).code == "forbidden"
    assert (await hitl_svc.handle_publish_approval_callback(db, "hpub:abc:yes", OWNER)).code == "bad_callback"
    assert (await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:maybe", OWNER)).code == "bad_callback"
    assert (await hitl_svc.handle_publish_approval_callback(db, "hpub:99999:yes", OWNER)).code == "not_found"
    assert await _valid(db, p) == set()


# ── устаревшее сообщение ──────────────────────────────────────────────────────


async def test_rebuilt_video_makes_the_old_message_stale_and_a_new_one_is_sent(db, env, monkeypatch):
    p, bot, clicks = await _setup(db, env, monkeypatch)
    old = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    old_sha = await sha_of(db, p)
    new_sha = await add_final(db, p, env.workdir, data=b"REBUILT-VIDEO", uuid="u-rebuilt")
    assert new_sha != old_sha
    res = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{old.id}:yes", OWNER)
    assert not res.ok and res.code == "stale_artifact" and res.hide_buttons
    assert await _valid(db, p) == set() and (await ps.list_approvals(db, p.id, old_sha)) == {}
    await db.refresh(old)
    assert old.decision is HITLDecision.rejected and old.payload["superseded"] == "stale_artifact"
    # шаг публикации на новом файле просит новое «да»; старые ожидающие запросы закрыты как устаревшие
    p.status = ProjectStatus.publishing
    await step.run(db, p, bot)
    await db.commit()
    pending = await _requests(db, p.id, HITLDecision.pending)
    assert len(pending) == len(PLATFORMS) and {r.payload["sha256"] for r in pending} == {new_sha}
    assert clicks == []
    assert (await hitl_svc.handle_publish_approval_callback(db, f"hpub:{pending[0].id}:yes", OWNER)).ok
    assert await _valid(db, p) == {pending[0].payload["platform"]}


async def test_changed_caption_makes_the_message_stale(db, env, monkeypatch):
    p, bot, _ = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    p.topic = "Совсем другая подпись"
    await db.commit()
    res = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)
    assert not res.ok and res.code == "stale_caption" and res.hide_buttons
    assert await _valid(db, p) == set()
    await db.refresh(req)
    assert req.decision is HITLDecision.rejected
    assert (await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)).code == "already_decided"


async def test_stale_message_cannot_close_a_platform_either(db, env, monkeypatch):
    p, bot, _ = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    await add_final(db, p, env.workdir, data=b"REBUILT-2", uuid="u-rebuilt-2")
    res = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:no", OWNER)
    assert res.code == "stale_artifact"
    assert await ps.list_for(db, p.id) == []


async def test_project_not_ready_gives_no_yes_and_keeps_the_request(db, env, monkeypatch):
    p, bot, _ = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    p.status = ProjectStatus.generating_hero
    await db.commit()
    res = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:yes", OWNER)
    assert not res.ok and res.code == "project_not_ready" and not res.hide_buttons
    assert await _valid(db, p) == set()
    await db.refresh(req)
    assert req.decision is HITLDecision.pending


# ── «Не публиковать» ──────────────────────────────────────────────────────────


async def test_no_closes_the_platform_for_this_file_without_a_yes(db, env, monkeypatch):
    p, bot, clicks = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    res = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:no", OWNER)
    assert res.ok and res.hide_buttons and "закрыта человеком" in res.text
    sha = await sha_of(db, p)
    row = next(r for r in await ps.list_for(db, p.id, sha) if r["platform"] == PLATFORMS[0])
    assert row["status"] == ps.S_FAILED and row["error_code"] == ps.MANUAL_FAILED_CODE and not row["clicked_at"]
    actions = await ps.list_manual_actions(db, p.id)
    assert [(a["platform"], a["action"], a["actor"]) for a in actions] == [(PLATFORMS[0], "mark_failed", f"telegram:{OWNER}")]
    assert await _valid(db, p) == set()
    # даже если потом «да» придёт из другого места, закрытая площадка сама не публикуется
    assert not ps.is_auto_retryable(row, 0.0)
    audit = [r for r in oa.list_rows(50, p.id) if r["method"] == "TELEGRAM"]
    assert len(audit) == 1 and audit[0]["actor"] == f"telegram:{OWNER}" and audit[0]["outcome"] == "ok"
    assert clicks == []


async def test_no_on_every_platform_ends_as_closed_by_human_not_published(db, env, monkeypatch):
    p, bot, clicks = await _setup(db, env, monkeypatch)
    for req in await _requests(db, p.id, HITLDecision.pending):
        assert (await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:no", OWNER)).ok
    assert p.status is ProjectStatus.assembled and p.status is not ProjectStatus.published
    assert p.meta.get("publish_closed_by_human") and not p.meta.get("publish_awaiting_approval")
    assert clicks == [] and await _valid(db, p) == set()
    assert await _requests(db, p.id, HITLDecision.pending) == []


async def test_no_after_yes_was_already_spent_by_panel_is_refused_not_silent(db, env, monkeypatch):
    """Площадка уже подтверждена: «Не публиковать» не может её «закрыть»; запрос не остаётся одобренным."""
    p, bot, clicks = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]
    sha = await sha_of(db, p)
    await ps.claim(db, project_id=p.id, platform=PLATFORMS[0], sha256=sha, artifact_uuid="u", owner="o")
    pub = next(r for r in await ps.list_for(db, p.id, sha) if r["platform"] == PLATFORMS[0])
    assert await ps.mark_clicked(db, pub["id"], "o")
    await ps.record_result(db, pub["id"], "o", status=ps.S_CONFIRMED, post_url="https://x/post")
    res = await hitl_svc.handle_publish_approval_callback(db, f"hpub:{req.id}:no", OWNER)
    assert not res.ok and res.code == "already_confirmed"
    await db.refresh(req)
    assert req.decision is HITLDecision.rejected and req.payload["refused"] == "already_confirmed"


# ── обработчик бота (заменитель CallbackQuery) ───────────────────────────────


class FakeCb:
    def __init__(self, data: str, user_id: int) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[tuple] = []
        self.markup_removed = 0
        self.replies: list[str] = []
        outer = self

        class Msg:
            async def edit_reply_markup(self, reply_markup=None):
                outer.markup_removed += 1

            async def answer(self, text, **kw):
                outer.replies.append(text)

        self.message = Msg()

    async def answer(self, text="", show_alert=False, **kw):
        self.answers.append((text, show_alert))


async def test_bot_handler_uses_the_server_logic_and_hides_buttons_only_when_decided(db, env, monkeypatch):
    from app.telegram import bot as bot_mod

    p, bot, _ = await _setup(db, env, monkeypatch)
    req = _by_platform(await _requests(db, p.id))[PLATFORMS[0]]

    @asynccontextmanager
    async def scope():
        yield db

    monkeypatch.setattr(bot_mod, "session_scope", scope)
    foreign = FakeCb(f"hpub:{req.id}:yes", STRANGER)
    await bot_mod.on_publish_approval_callback(foreign)
    assert foreign.answers and foreign.answers[0][1] is True
    assert foreign.markup_removed == 0 and foreign.replies == [] and await _valid(db, p) == set()

    owner = FakeCb(f"hpub:{req.id}:yes", OWNER)
    await bot_mod.on_publish_approval_callback(owner)
    assert owner.answers and owner.answers[0][1] is False
    assert owner.markup_removed == 1 and len(owner.replies) == 1
    assert await _valid(db, p) == {PLATFORMS[0]}

    again = FakeCb(f"hpub:{req.id}:yes", OWNER)
    await bot_mod.on_publish_approval_callback(again)
    assert again.answers[0][1] is True and await _valid(db, p) == {PLATFORMS[0]}
    handlers = [h for h in bot_mod.dp.callback_query.handlers if h.callback is bot_mod.on_publish_approval_callback]
    assert len(handlers) == 1


def test_enum_kind_is_added_and_old_hitl_prefix_is_not_taken():
    assert HITLKind.approve_publish.value == "approve_publish"
    assert not "hpub:1:yes".startswith("hitl:")
