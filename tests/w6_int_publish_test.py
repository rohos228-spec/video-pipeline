"""W6 интеграция W6-2 в W6-1: журнал, перенос по лимиту, ожидание человека, превью действия. Сети и площадок нет."""
from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from app.bots import publishers as pubmod
from app.bots.publishers import PublishResult
from app.models import ProjectStatus
from app.orchestrator.steps import publish as step
from app.services import publications_store as ps
from app.services import publish_accounts as pa
from app.settings import settings
from tests.w6_1_support import PLATFORMS, Bot, Env, fake_registry, grant, make_project, sha_of


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
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    monkeypatch.delenv(pa.ENV_JSON, raising=False)
    monkeypatch.setenv("DATA_DIR", "nonexistent-w6-int-dir")


def _human_stop(platform: str, code: str = "captcha"):
    async def publish(self, video_path, caption, *, before_click=None):
        return PublishResult(platform, False, status="failed", error_code=code, needs_human=True,
                             error=f"{platform}: {pa.refusal_text(code)}", account_label=f"{platform} (профиль …9999)",
                             screenshot=f"publications/1/{platform}.png")
    return publish


def test_store_hold_codes_match_account_codes():
    assert ps.HUMAN_HOLD_CODES == pa.NO_AUTO_RETRY_CODES


async def test_needs_human_result_waits_for_human_one_telegram_no_autoretry(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch)
    pubmod.ALL_PUBLISHERS[0].publish = _human_stop(PLATFORMS[0])
    p = await make_project(db, env.workdir)
    await grant(db, p)
    bot = Bot()
    await step.run(db, p, bot)
    sha = await sha_of(db, p)
    rows = {r["platform"]: r for r in await ps.list_for(db, p.id, sha)}
    r0 = rows[PLATFORMS[0]]
    assert r0["status"] == ps.S_FAILED and r0["error_code"] == "captcha" and not r0["clicked_at"]
    assert r0["screenshot"] == f"publications/1/{PLATFORMS[0]}.png" and "…9999" in r0["account_id"]
    assert sorted(clicks) == sorted(PLATFORMS[1:])
    assert p.status is ProjectStatus.assembled and PLATFORMS[0] in p.meta["publish_needs_human"]["platforms"]
    assert len(bot.sent) == 1 and pa.REFUSALS["captcha"] in bot.sent[0]
    assert not ps.is_auto_retryable(r0, time.time())
    # повторный запуск: ни клика по этой площадке, ни второго уведомления
    p.status = ProjectStatus.publishing
    await step.run(db, p, bot)
    assert sorted(clicks) == sorted(PLATFORMS[1:]) and len(bot.sent) == 1
    assert p.status is ProjectStatus.assembled


async def test_daily_limit_defers_without_needs_human_and_retries_after_time(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch)
    monkeypatch.setenv(pa.ENV_JSON, '{"platforms": {"%s": {"max_per_day": 1}}}' % PLATFORMS[0])
    p = await make_project(db, env.workdir)
    await grant(db, p)
    await ps.ensure_schema(db)
    now = time.time()
    await db.execute(text(
        "INSERT INTO publications (project_id, platform, sha256, status, attempts, clicked_at, created_at, updated_at) "
        "VALUES (999, :pl, 'other', 'confirmed', 1, :now, :now, :now)"), {"pl": PLATFORMS[0], "now": now - 60})
    await db.commit()
    assert len(await pa.recent_click_times(db, PLATFORMS[0])) == 1
    bot = Bot()
    await step.run(db, p, bot)
    assert PLATFORMS[0] not in clicks and sorted(clicks) == sorted(PLATFORMS[1:])
    row = next(r for r in await ps.list_for(db, p.id) if r["platform"] == PLATFORMS[0])
    assert row["status"] == ps.S_SKIPPED and row["error_code"] == "daily_limit" and not row["clicked_at"]
    meta = p.meta
    assert p.status is ProjectStatus.assembled and "publish_needs_human" not in meta
    marker = meta["publish_deferred"]
    assert set(marker["platforms"]) == {PLATFORMS[0]} and marker["platforms"][PLATFORMS[0]]["retry_after"] > now
    assert len(bot.sent) == 1 and pa.REFUSALS["daily_limit"] in bot.sent[0]
    # пока срок не вышел, проект сам в publishing не возвращается
    assert await step.publish_hold_reason(db, p) == "publish_deferred"
    # шаг, запущенный раньше срока, ничего не трогает
    p.status = ProjectStatus.publishing
    await step.run(db, p, bot)
    assert PLATFORMS[0] not in clicks and len(bot.sent) == 1
    # срок вышел и лимит снят: удержания нет, площадка публикуется (подтверждение «да» не тратилось)
    marker["platforms"][PLATFORMS[0]]["retry_after"] = now - 1
    p.meta = {**p.meta, "publish_deferred": marker}
    assert await step.publish_hold_reason(db, p) is None
    monkeypatch.delenv(pa.ENV_JSON)
    p.status = ProjectStatus.publishing
    await step.run(db, p, bot)
    assert PLATFORMS[0] in clicks
    assert p.status is ProjectStatus.published and "publish_deferred" not in p.meta


async def test_project_label_and_click_history_reach_publish_everywhere(db, env, monkeypatch):
    fake_registry(monkeypatch)
    seen = {}
    real = step.publish_everywhere

    async def spy(*a, **kw):
        seen.update(kw)
        return await real(*a, **kw)

    monkeypatch.setattr(step, "publish_everywhere", spy)
    p = await make_project(db, env.workdir)
    await grant(db, p)
    await step.run(db, p, Bot())
    assert seen["project_label"] == p.id and isinstance(seen["gate"], step._StoreGate)
    gate = step._StoreGate(db, p.id, await sha_of(db, p), "u", "c")
    assert isinstance(await gate.recent_clicks(PLATFORMS[0]), list)


def test_account_text_uses_per_platform_labels(monkeypatch):
    from app.services.action_layer import publish_action as pact
    monkeypatch.setenv(pa.ENV_JSON, '{"platforms": {"tiktok": {"profile_id": "prof-1111", "account_label": "TT основной"},'
                                    ' "yt_shorts": {"profile_id": "prof-2222"}}}')
    out = pact.account_text(["tiktok", "yt_shorts", "likee"])
    assert "TT основной" in out and "…2222" in out and "prof-2222" not in out
    assert "Likee: профиль не задан" in out


def test_effects_text_shows_file_and_caption_hashes():
    from app.services.action_layer import publish_action as pact
    pact._remember_facts(7, {"sha256": "a" * 64, "caption_sha256": "b" * 64})
    out = pact.effects_text({"project_id": 7, "platforms": ["tiktok"], "caption": "Тема"})
    assert "a" * 64 in out and "b" * 64 in out
    pact._remember_facts(7, None)
    out = pact.effects_text({"project_id": 7, "platforms": ["tiktok"], "caption": "Тема"})
    assert "не определён" in out and step.caption_sha256("Тема") in out
