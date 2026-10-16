
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bots import publishers as pubmod
from app.bots.publishers import PublishGate, PublishResult, Publisher
from app.models import Artifact, ArtifactKind, Base, Project, ProjectStatus
from app.orchestrator.steps import publish as step
from app.services import publications_store as ps
from app.settings import settings

PLATFORMS = [c.platform for c in pubmod.ALL_PUBLISHERS]


@pytest.fixture
async def maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'w16.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db(maker):
    async with maker() as s:
        yield s


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text):
        self.sent.append(text)


async def _project(db, tmp_path, slug="w16", status=ProjectStatus.publishing) -> Project:
    p = Project(slug=slug, title="t", topic="тема ролика", status=status, meta={})
    db.add(p)
    await db.flush()
    video = tmp_path / f"{slug}.mp4"
    video.write_bytes(b"SYNTHETIC-VIDEO")
    db.add(Artifact(project_id=p.id, kind=ArtifactKind.final_video, uuid=f"u-{slug}", path=str(video)))
    await db.flush()
    sha = await ps.sha256_of_file_async(video)
    csha = step.caption_sha256(step.caption_for(p))
    for pl in PLATFORMS:
        await ps.grant_approval(db, project_id=p.id, platform=pl, sha256=sha, caption_sha256=csha,
                                approved_by="operator:test")
    return p




async def _claim(session, owner, platform="tiktok", sha="abc", project_id=1, **kw):
    return await ps.claim(session, project_id=project_id, platform=platform, sha256=sha,
                          artifact_uuid="u", owner=owner, **kw)


async def test_two_parallel_workers_one_publication(maker):
    async def worker():
        async with maker() as s:
            return await _claim(s, ps.new_owner())

    res = await asyncio.gather(*[worker() for _ in range(4)])
    assert sorted(r.outcome for r in res).count(ps.ACQUIRED) == 1
    assert all(r.outcome in (ps.ACQUIRED, ps.BUSY) for r in res)
    async with maker() as s:
        rows = await ps.list_for(s, 1)
    assert len(rows) == 1 and rows[0]["status"] == ps.S_PLANNED


async def test_crash_between_click_and_record_never_retries(db):
    c = await _claim(db, "w1")
    assert c.outcome == ps.ACQUIRED
    assert await ps.mark_clicked(db, c.pub_id, "w1") is True
    later = await _claim(db, "w2", now=10**10)
    assert later.outcome == ps.NEEDS_HUMAN and later.status == ps.S_CLICKED
    assert await ps.mark_clicked(db, c.pub_id, "w2") is False


async def test_failure_before_click_is_retryable_but_bounded(db):
    for attempt in range(1, ps.MAX_PRE_CLICK_ATTEMPTS + 1):
        c = await _claim(db, f"w{attempt}")
        assert c.outcome == ps.ACQUIRED and c.attempts == attempt
        await ps.record_result(db, c.pub_id, f"w{attempt}", status=ps.S_FAILED, error_code="caption_field_missing")
    final = await _claim(db, "wx")
    assert final.outcome == ps.NEEDS_HUMAN


async def test_stale_planned_lease_is_taken_over_but_live_one_is_busy(db):
    c = await _claim(db, "w1", now=1000.0, lease_s=10)
    assert (await _claim(db, "w2", now=1005.0)).outcome == ps.BUSY
    c2 = await _claim(db, "w2", now=1011.0)
    assert c2.outcome == ps.ACQUIRED and c2.pub_id == c.pub_id and c2.attempts == 2


async def test_confirmed_without_url_or_id_is_not_confirmed(db):
    c = await _claim(db, "w1")
    await ps.mark_clicked(db, c.pub_id, "w1")
    st = await ps.record_result(db, c.pub_id, "w1", status=ps.S_CONFIRMED)
    assert st == ps.S_UNCONFIRMED
    assert (await _claim(db, "w2")).outcome == ps.NEEDS_HUMAN


async def test_confirmed_is_done_and_idempotent_per_sha(db):
    c = await _claim(db, "w1")
    await ps.mark_clicked(db, c.pub_id, "w1")
    await ps.record_result(db, c.pub_id, "w1", status=ps.S_CONFIRMED, post_url="https://x/1")
    assert (await _claim(db, "w2")).outcome == ps.DONE
    assert (await _claim(db, "w2", sha="other-file")).outcome == ps.ACQUIRED
    assert ps.idempotency_key(1, "tiktok", "abc") == "1:tiktok:abc"


def test_summarize_published_only_when_all_confirmed_with_url():
    rows = [{"platform": p, "status": ps.S_CONFIRMED, "post_url": "u", "post_id": None, "clicked_at": 1,
             "attempts": 1, "lease_until": None} for p in PLATFORMS]
    assert ps.summarize(rows, PLATFORMS)["all_confirmed"] is True
    rows[0]["post_url"] = None
    s = ps.summarize(rows, PLATFORMS)
    assert s["all_confirmed"] is False and PLATFORMS[0] in s["needs_human"]




class _Loc:
    def __init__(self, page, sel):
        self.page, self.sel, self.first = page, sel, self

    async def count(self):
        return 1

    async def set_input_files(self, *_):
        return None

    async def click(self):
        if self.sel == "BUTTON":
            self.page.clicks += 1
            if self.page.click_raises:
                raise RuntimeError("страница закрыта после клика")

    async def fill(self, *_):
        if self.page.fill_raises:
            raise RuntimeError("fill failed")


class _Page:
    url = "https://example.invalid/post/1"

    def __init__(self, fill_raises=False, click_raises=False):
        self.fill_raises, self.click_raises, self.clicks = fill_raises, click_raises, 0

    def locator(self, sel):
        return _Loc(self, "BUTTON" if "Post" in sel or "Опубл" in sel else sel)


def _pub(page, monkeypatch, **attrs):
    class P(pubmod.TikTokPublisher):
        success_selectors = ["div.done"]
        publish_button_selectors = ["button:has-text('Post')"]
        confirm_timeout_s = 0.0

    for k, v in attrs.items():
        setattr(P, k, v)
    pub = P(browser=None)

    async def _open():
        return page

    monkeypatch.setattr(pub, "_open", _open)
    return pub


async def test_caption_fill_failure_is_failed_before_click(monkeypatch, tmp_path):
    page = _Page(fill_raises=True)
    called = []

    async def bc():
        called.append(1)
        return True

    res = await _pub(page, monkeypatch).publish(tmp_path / "v.mp4", "cap", before_click=bc)
    assert (res.status, res.clicked, res.error_code) == ("failed", False, "caption_fill_failed")
    assert page.clicks == 0 and called == []


async def test_empty_caption_is_failed_before_click(monkeypatch, tmp_path):
    page = _Page()
    res = await _pub(page, monkeypatch).publish(tmp_path / "v.mp4", "   ")
    assert (res.status, res.clicked, res.error_code) == ("failed", False, "caption_empty")
    assert page.clicks == 0


async def test_caption_field_missing_is_failed_before_click(monkeypatch, tmp_path):
    page = _Page()
    pub = _pub(page, monkeypatch)

    async def none(*a, **k):
        return None

    async def first(page_, selectors, timeout_ms=0):
        return None if selectors is pub.caption_selectors else selectors[0]

    monkeypatch.setattr(pub, "_first_visible", first)
    res = await pub.publish(tmp_path / "v.mp4", "cap")
    assert res.status == "failed" and res.clicked is False and page.clicks == 0


async def test_exception_after_click_is_marked_clicked(monkeypatch, tmp_path):
    page = _Page(click_raises=True)
    res = await _pub(page, monkeypatch).publish(tmp_path / "v.mp4", "cap")
    assert res.status == "failed" and res.clicked is True and page.clicks == 1


async def test_before_click_refusal_means_no_click(monkeypatch, tmp_path):
    page = _Page()

    async def bc():
        return False

    res = await _pub(page, monkeypatch).publish(tmp_path / "v.mp4", "cap", before_click=bc)
    assert res.clicked is False and res.error_code == "click_not_allowed" and page.clicks == 0




def _fake_registry(monkeypatch, behaviors):
    clicks: list[str] = []

    def make(platform):
        class F(Publisher):
            pass
        F.platform = platform

        async def publish(self, video_path, caption, *, before_click=None):
            b = behaviors.get(platform, "confirmed")
            if b == "fail_pre":
                return PublishResult(platform, False, status="failed", error="до клика", error_code="x")
            if before_click is not None and not await before_click():
                return PublishResult(platform, False, status="failed", error="нет", error_code="click_not_allowed")
            clicks.append(platform)
            if b == "crash":
                raise RuntimeError("процесс упал после клика")
            if b == "unconfirmed":
                return PublishResult(platform, False, url="https://up", status="unconfirmed", clicked=True)
            return PublishResult(platform, True, url=f"https://{platform}/post", status="confirmed", clicked=True)

        F.publish = publish
        return F

    monkeypatch.setattr(pubmod, "ALL_PUBLISHERS", [make(p) for p in PLATFORMS])
    monkeypatch.setattr(step, "ALL_PUBLISHERS", pubmod.ALL_PUBLISHERS)

    @asynccontextmanager
    async def fake_browser(*a, **k):
        yield SimpleNamespace()

    async def nosleep(*_):
        return None

    monkeypatch.setattr(pubmod, "morelogin_browser", fake_browser)
    monkeypatch.setattr(pubmod.asyncio, "sleep", nosleep)
    return clicks


async def test_result_of_platform_1_survives_crash_on_platform_2(db, tmp_path, monkeypatch):
    clicks = _fake_registry(monkeypatch, {PLATFORMS[1]: "crash"})
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    p = await _project(db, tmp_path)
    with pytest.raises(RuntimeError):
        await step.run(db, p, Bot())
    sha = await ps.sha256_of_file_async(tmp_path / "w16.mp4")
    rows = {r["platform"]: r for r in await ps.list_for(db, p.id, sha)}
    assert rows[PLATFORMS[0]]["status"] == ps.S_CONFIRMED and rows[PLATFORMS[0]]["post_url"]
    assert rows[PLATFORMS[1]]["status"] == ps.S_CLICKED
    clicks.clear()
    await step.run(db, p, Bot())
    assert PLATFORMS[0] not in clicks and PLATFORMS[1] not in clicks
    assert sorted(clicks) == sorted(PLATFORMS[2:])
    assert p.status is ProjectStatus.assembled
    assert p.meta["publish_needs_human"]["platforms"] == [PLATFORMS[1]]
    assert set(p.meta["published_platforms"]) == set(PLATFORMS) - {PLATFORMS[1]}


async def test_all_confirmed_gives_published_with_urls(db, tmp_path, monkeypatch):
    _fake_registry(monkeypatch, {})
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    p = await _project(db, tmp_path)
    bot = Bot()
    await step.run(db, p, bot)
    assert p.status is ProjectStatus.published
    assert all(p.meta["published_platforms"][pl].startswith("https://") for pl in PLATFORMS)
    assert bot.sent


async def test_unconfirmed_everywhere_never_published_and_does_not_stick(db, tmp_path, monkeypatch):
    clicks = _fake_registry(monkeypatch, {pl: "unconfirmed" for pl in PLATFORMS})
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    p = await _project(db, tmp_path)
    await step.run(db, p, Bot())
    assert p.status is ProjectStatus.assembled and p.status is not ProjectStatus.published
    assert set(p.meta["publish_unconfirmed"]) == set(PLATFORMS)
    n = len(clicks)
    p.status = ProjectStatus.publishing
    await step.run(db, p, Bot())
    assert len(clicks) == n and p.status is ProjectStatus.assembled


async def test_failure_before_click_retries_only_that_platform(db, tmp_path, monkeypatch):
    behaviors = {PLATFORMS[2]: "fail_pre"}
    clicks = _fake_registry(monkeypatch, behaviors)
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    p = await _project(db, tmp_path)
    await step.run(db, p, Bot())
    assert p.status is ProjectStatus.publishing
    behaviors.pop(PLATFORMS[2])
    clicks.clear()
    await step.run(db, p, Bot())
    assert clicks == [PLATFORMS[2]] and p.status is ProjectStatus.published


async def test_second_worker_is_blocked_while_first_holds_lease(db, maker, tmp_path, monkeypatch):
    clicks = _fake_registry(monkeypatch, {})
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    p = await _project(db, tmp_path)
    await db.commit()
    sha = await ps.sha256_of_file_async(tmp_path / "w16.mp4")
    async with maker() as other:
        for pl in PLATFORMS:
            assert (await ps.claim(other, project_id=p.id, platform=pl, sha256=sha,
                                   artifact_uuid="u", owner="other")).outcome == ps.ACQUIRED
    await step.run(db, p, Bot())
    assert clicks == [] and p.status is ProjectStatus.publishing




async def test_disabled_flag_gives_publish_skipped_not_published(db, tmp_path, monkeypatch):
    clicks = _fake_registry(monkeypatch, {})
    monkeypatch.setattr(settings, "social_publish_enabled", False)
    p = await _project(db, tmp_path)
    await step.run(db, p, Bot())
    assert p.status is ProjectStatus.assembled and p.status is not ProjectStatus.published
    assert p.meta["publish_skipped"]["reason"] == "SOCIAL_PUBLISH_ENABLED=false"
    assert step.is_publish_skipped(p) is True and clicks == []
    sha = await ps.sha256_of_file_async(tmp_path / "w16.mp4")
    assert {r["status"] for r in await ps.list_for(db, p.id, sha)} == {ps.S_SKIPPED}
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    p.status = ProjectStatus.publishing
    await step.run(db, p, Bot())
    assert p.status is ProjectStatus.published and sorted(clicks) == sorted(PLATFORMS)


async def test_batch_progress_does_not_count_skipped_as_done(db, tmp_path, monkeypatch):
    from app.services.batches import batch_progress

    monkeypatch.setattr(settings, "social_publish_enabled", False)
    a = await _project(db, tmp_path, "b1")
    b = await _project(db, tmp_path, "b2", status=ProjectStatus.published)
    c = await _project(db, tmp_path, "b3", status=ProjectStatus.assembled)
    for x in (a, b, c):
        x.batch_id, x.batch_position = 7, x.id
    c.meta = {"publish_needs_human": {"platforms": ["tiktok"]}}
    await step.run(db, a, Bot())
    prog = await batch_progress(db, SimpleNamespace(id=7))
    assert prog["done"] == 1
    assert prog["publish_skipped"] == 1 and prog["publish_needs_human"] == 1
    assert prog["in_progress"] == 0
