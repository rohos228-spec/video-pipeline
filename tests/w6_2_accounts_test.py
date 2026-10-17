from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from app.bots import morelogin
from app.bots import publishers as pubmod
from app.services import publish_accounts as pa
from tests._stand_workdir import make_workdir, remove_workdir


@pytest.fixture
def workdir():
    d = make_workdir("w62_")
    yield d
    remove_workdir(d)


def _cfg(platforms: dict, **extra) -> pa.AccountsConfig:
    return pa.parse_config({"platforms": platforms, **extra})




def test_no_config_is_empty_legacy_mode(workdir):
    cfg = pa.load_accounts(env={}, data_dir=workdir)
    assert cfg.platforms == {} and cfg.configured is False and cfg.source == "none"
    assert pa.select_profile("tiktok", cfg, legacy_profile_id="legacy-1111") == "legacy-1111"
    assert pa.select_profile("tiktok", cfg, legacy_profile_id="") is None


def test_profile_chosen_by_platform_without_fallback_to_foreign_profile():
    cfg = _cfg({"tiktok": {"profile_id": "prof-tt-0001", "account_label": "ТикТок основной"},
                "vk_clips": {"profile_id": "prof-vk-0002"}})
    assert pa.select_profile("tiktok", cfg) == "prof-tt-0001"
    assert pa.select_profile("vk_clips", cfg) == "prof-vk-0002"
    assert pa.select_profile("likee", cfg, legacy_profile_id="legacy-9999") is None


def test_file_and_env_merge_env_wins(workdir):
    (workdir / "publish_accounts.json").write_text(json.dumps(
        {"platforms": {"tiktok": {"profile_id": "file-0001", "max_per_day": 3}}}), encoding="utf-8")
    env = {pa.ENV_JSON: json.dumps({"platforms": {"tiktok": {"profile_id": "env-0002"}}})}
    cfg = pa.load_accounts(env=env, data_dir=workdir)
    a = cfg.platforms["tiktok"]
    assert a.profile_id == "env-0002" and a.max_per_day == 3
    assert cfg.source == "publish_accounts.json+PUBLISH_ACCOUNTS_JSON"


def test_secret_keys_rejected_without_echoing_values():
    with pytest.raises(pa.AccountsConfigError) as e:
        _cfg({"tiktok": {"profile_id": "p-1", "password": "SuperSecret123"}})
    assert "password" in str(e.value) and "SuperSecret123" not in str(e.value)
    with pytest.raises(pa.AccountsConfigError):
        pa.parse_config({"platforms": {}, "api_key": "x"})


def test_bad_config_reports_keys_not_values(workdir):
    (workdir / "publish_accounts.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(pa.AccountsConfigError):
        pa.load_accounts(env={}, data_dir=workdir)
    with pytest.raises(pa.AccountsConfigError, match="Неизвестная площадка"):
        _cfg({"myspace": {}})
    with pytest.raises(pa.AccountsConfigError, match="неизвестные поля"):
        _cfg({"tiktok": {"profil_id": "x"}})
    with pytest.raises(pa.AccountsConfigError, match="post_url_regex"):
        _cfg({"tiktok": {"post_url_regex": "(["}})


def test_profile_id_is_masked_in_repr_and_label():
    cfg = _cfg({"tiktok": {"profile_id": "very-secret-profile-4321"}})
    a = cfg.platforms["tiktok"]
    assert "very-secret" not in repr(a) and "very-secret" not in a.label()
    assert a.label().endswith("4321)") or "4321" in a.label()


def test_success_markers_default_empty():
    a = _cfg({"tiktok": {"profile_id": "p-1"}}).platforms["tiktok"]
    assert a.success_selectors == [] and a.success_url_markers == [] and a.post_url_regex == ""




def test_caption_hashtags_suffix_and_limit_reject():
    a = pa.PlatformAccount(platform="tiktok", hashtags=["ai", "#video", "#AI"], caption_suffix="Сделано с ИИ")
    r = pa.compose_caption("Привет #video", a)
    assert r.text == "Привет #video\n\nСделано с ИИ\n\n#ai"
    a.caption_limit = 10
    r = pa.compose_caption("Очень длинная подпись для проверки", a)
    assert r.error_code == "platform_limit" and "лимит" in r.error


def test_caption_truncate_keeps_hashtags():
    a = pa.PlatformAccount(platform="ig_reels", hashtags=["#reels"], caption_limit=40, on_overflow="truncate",
                           hashtag_separator=" ")
    r = pa.compose_caption("слово " * 30, a)
    assert r.truncated and len(r.text) <= 40 and r.text.endswith("#reels") and "…" in r.text
    a.caption_limit = 5
    assert pa.compose_caption("слово " * 30, a).error_code == "platform_limit"


def test_caption_empty_and_title():
    assert pa.compose_caption("  ", pa.PlatformAccount(platform="tiktok")).error_code == "caption_empty"
    a = pa.PlatformAccount(platform="yt_shorts", title_limit=10)
    assert pa.make_title("Заголовок очень длинный\nвторая строка", a) == "Заголовок…"




def test_daily_limit_rolling_24h():
    now = 1_000_000.0
    d = pa.daily_limit_decision([now - 100, now - 200], 2, now)
    assert not d.allowed and d.code == "daily_limit" and d.used == 2
    assert d.retry_after == pytest.approx(now - 200 + pa.DAY_S)
    assert pa.daily_limit_decision([now - 100, now - pa.DAY_S - 5], 2, now).allowed
    assert pa.daily_limit_decision(None, 2, now).allowed
    assert pa.daily_limit_decision([now] * 9, None, now).allowed


def test_window_decision_normal_and_overnight():
    base = time.mktime((2026, 10, 8, 12, 0, 0, 0, 0, -1))
    assert pa.window_decision((10, 22), base).allowed
    out = pa.window_decision((13, 22), base)
    assert not out.allowed and out.code == "outside_window" and out.retry_after == pytest.approx(base + 3600)
    night = time.mktime((2026, 10, 8, 23, 0, 0, 0, 0, -1))
    assert pa.window_decision((22, 6), night).allowed and not pa.window_decision((22, 6), base).allowed


def test_screenshot_path_has_no_secrets_or_traversal():
    assert pa.screenshot_relpath(12, "tiktok") == "publications/12/tiktok.png"
    rel = pa.screenshot_relpath("../../etc/pass wd", "tik/tok")
    assert rel == "publications/etc_pass_wd/tik_tok.png"


async def test_recent_click_times_counts_journal(workdir):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services import publications_store as ps
    eng = create_async_engine(f"sqlite+aiosqlite:///{workdir / 'j.db'}")
    try:
        async with AsyncSession(eng) as s:
            now = time.time()
            for i, plat in enumerate(["tiktok", "tiktok", "vk_clips"]):
                c = await ps.claim(s, project_id=i + 1, platform=plat, sha256="h", artifact_uuid=None, owner="o")
                await ps.mark_clicked(s, c.pub_id, "o", now=now - 60)
            await ps.claim(s, project_id=9, platform="tiktok", sha256="h", artifact_uuid=None, owner="o")
            times = await pa.recent_click_times(s, "tiktok", now=now)
            assert len(times) == 2
            assert not pa.daily_limit_decision(times, 2, now).allowed
    finally:
        await eng.dispose()




async def test_start_profile_parses_cdp_and_masks_failures(monkeypatch):
    async def ok(path, pid, host=None):
        return {"data": {"debuggerAddress": "127.0.0.1:9333"}}
    monkeypatch.setattr(morelogin, "_http_post", ok)
    assert await morelogin.start_profile("p") == "http://127.0.0.1:9333"

    async def bad(path, pid, host=None):
        return {"__status": 500, "__text": "boom"}
    monkeypatch.setattr(morelogin, "_http_post", bad)
    with pytest.raises(morelogin.MoreLoginError) as e:
        await morelogin.start_profile("very-secret-profile-4321")
    assert e.value.code == "profile_unavailable" and "very-secret" not in str(e.value) and "4321" in str(e.value)

    async def down(path, pid, host=None):
        raise ConnectionRefusedError("x")
    monkeypatch.setattr(morelogin, "_http_post", down)
    with pytest.raises(morelogin.MoreLoginError) as e2:
        await morelogin.start_profile("p")
    assert e2.value.code == "profile_unavailable"


def test_resolve_profile_by_platform(monkeypatch):
    cfg = _cfg({"tiktok": {"profile_id": "prof-tt-0001"}})
    assert morelogin.resolve_profile(platform="tiktok", accounts=cfg) == "prof-tt-0001"
    assert morelogin.resolve_profile("explicit") == "explicit"
    with pytest.raises(morelogin.MoreLoginError) as e:
        morelogin.resolve_profile(platform="likee", accounts=cfg)
    assert e.value.code == "profile_missing"
    monkeypatch.setattr(morelogin.settings, "morelogin_profile_id", None)
    with pytest.raises(morelogin.MoreLoginError) as e2:
        morelogin.resolve_profile()
    assert e2.value.code == "profile_missing"


async def test_browser_not_stopped_by_default(monkeypatch):
    calls = []

    class _Browser:
        async def close(self):
            calls.append("disconnect")

    class _PW:
        chromium = SimpleNamespace(connect_over_cdp=lambda url: _conn(url))

        async def stop(self):
            calls.append("pw.stop")

    async def _conn(url):
        calls.append(f"cdp:{url}")
        return _Browser()

    class _Starter:
        async def start(self):
            return _PW()

    async def start(pid):
        return "http://127.0.0.1:1"

    async def stop(pid):
        calls.append("stop_profile")

    monkeypatch.setattr(morelogin, "async_playwright", lambda: _Starter())
    monkeypatch.setattr(morelogin, "start_profile", start)
    monkeypatch.setattr(morelogin, "stop_profile", stop)
    monkeypatch.delenv("MORELOGIN_STOP_ON_EXIT", raising=False)
    async with morelogin.morelogin_browser("p1"):
        pass
    assert "stop_profile" not in calls and "disconnect" in calls
    calls.clear()
    async with morelogin.morelogin_browser("p1", stop_on_exit=True):
        pass
    assert "stop_profile" in calls


def test_youtube_has_no_uc_stub_and_url_override():
    assert "/channel/UC/" not in pubmod.YouTubeShortsPublisher.upload_url
    p = pubmod.YouTubeShortsPublisher(browser=None)
    p.account = pa.PlatformAccount(platform="yt_shorts", upload_url="https://studio.youtube.com/channel/UCreal/videos/upload")
    assert p._upload_url().endswith("/UCreal/videos/upload")
    assert "studio.youtube.com" in p._hosts()
