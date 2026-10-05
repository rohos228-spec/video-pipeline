"""Фаза G: атомарная сборка, проверка выхода, один approve_final, честный publish. Без сети."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)
from app.orchestrator.steps import assemble as asm
from app.settings import settings


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _project(session, slug="g4") -> Project:
    p = Project(slug=slug, title="t", topic="t", status=ProjectStatus.assembling)
    session.add(p)
    await session.flush()
    return p


def _wire(monkeypatch, *, content=b"NEWVIDEO", verdict="passed", fail_core=False):
    """Заглушки: сборка пишет во временный путь, проверка и HITL подменены."""
    import app.services.artifact_verifier as av
    import app.services.mass_factory as mf

    async def core(session, project, bot, *, out_path, **kw):
        out_path.write_bytes(content[: len(content) // 2] if fail_core else content)
        if fail_core:
            raise RuntimeError("ffmpeg exited with 1")
        return 10.0, 0.0

    async def verify(path, *, root, kind, expected=None):
        import hashlib
        data = path.read_bytes()
        res = {"status": verdict, "kind": kind, "sha256": hashlib.sha256(data).hexdigest()}
        if verdict != "passed":
            res["reason"] = "ValueError"
        return res

    sent: list[dict] = []

    async def send(bot, session, project, *, kind, video_path, caption, payload):
        req = HITLRequest(project_id=project.id, kind=kind, payload=payload)
        session.add(req)
        await session.flush()
        sent.append(payload)
        return req

    async def child(session, project):
        return None

    monkeypatch.setattr(asm, "_assemble_core", core)
    monkeypatch.setattr(av, "verify_artifact", verify)
    monkeypatch.setattr(asm, "send_hitl_video", send)
    monkeypatch.setattr(mf, "on_child_montage_complete", child)
    return sent


async def _approvals(session, project):
    return (await session.execute(select(HITLRequest).where(
        HITLRequest.project_id == project.id, HITLRequest.kind == HITLKind.approve_final))).scalars().all()


async def _finals(session, project):
    return (await session.execute(select(Artifact).where(
        Artifact.project_id == project.id, Artifact.kind == ArtifactKind.final_video))).scalars().all()


@pytest.mark.asyncio
async def test_crash_mid_build_keeps_previous_final(db, monkeypatch):
    p = await _project(db)
    final = p.data_dir / "final" / f"{p.slug}.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"OLDVIDEO")
    _wire(monkeypatch, fail_core=True)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        await asm._assemble_body(db, p, None)
    assert final.read_bytes() == b"OLDVIDEO"
    assert [f.name for f in final.parent.iterdir()] == [final.name]


@pytest.mark.asyncio
async def test_verification_failure_blocks_approve_final(db, monkeypatch):
    p = await _project(db)
    final = p.data_dir / "final" / f"{p.slug}.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"OLDVIDEO")
    sent = _wire(monkeypatch, verdict="failed")
    with pytest.raises(RuntimeError, match="проверка итогового видео"):
        await asm._assemble_body(db, p, None)
    assert sent == []
    assert await _approvals(db, p) == []
    assert await _finals(db, p) == []
    assert final.read_bytes() == b"OLDVIDEO"
    assert p.status is ProjectStatus.assembling


@pytest.mark.asyncio
async def test_unknown_verification_is_not_success(db, monkeypatch):
    p = await _project(db)
    sent = _wire(monkeypatch, verdict="unknown")
    with pytest.raises(RuntimeError):
        await asm._assemble_body(db, p, None)
    assert sent == []


@pytest.mark.asyncio
async def test_success_writes_final_and_one_approval_per_digest(db, monkeypatch):
    p = await _project(db)
    sent = _wire(monkeypatch)
    for _ in range(3):  # retries of the same output
        await asm._assemble_body(db, p, None)
    final = p.data_dir / "final" / f"{p.slug}.mp4"
    assert final.read_bytes() == b"NEWVIDEO"
    assert len(sent) == 1 and len(await _approvals(db, p)) == 1
    arts = await _finals(db, p)
    assert len(arts) == 1 and arts[0].meta["sha256"] == (await _approvals(db, p))[0].payload["sha256"]
    assert p.status is ProjectStatus.assembled
    assert [f.name for f in final.parent.iterdir()] == [final.name]


@pytest.mark.asyncio
async def test_new_digest_gets_new_approval(db, monkeypatch):
    p = await _project(db)
    _wire(monkeypatch, content=b"AAAA")
    await asm._assemble_body(db, p, None)
    _wire(monkeypatch, content=b"BBBB")
    await asm._assemble_body(db, p, None)
    assert len(await _approvals(db, p)) == 2


# ── auto approval requires the verifier ──────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_approve_final_needs_verification(db, monkeypatch):
    from app.orchestrator import auto_advance as aa
    import app.services.artifact_verifier as av

    p = await _project(db)
    p.status = ProjectStatus.assembled
    final = p.data_dir / "final" / f"{p.slug}.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"V")
    db.add(Artifact(project_id=p.id, kind=ArtifactKind.final_video, uuid="u1", path=str(final), meta={}))
    await db.flush()

    async def bad(*a, **k):
        return {"status": "failed"}

    monkeypatch.setattr(av, "verify_artifact", bad)
    assert await aa._final_verified(db, p) is False
    transition = aa.TRANSITIONS[ProjectStatus.assembled]
    await aa._apply_approve(db, p, None, transition)
    assert p.status is ProjectStatus.assembled  # not advanced

    async def good(*a, **k):
        return {"status": "passed", "sha256": "abc"}

    monkeypatch.setattr(av, "verify_artifact", good)
    assert await aa._final_verified(db, p) is True
    from loguru import logger

    logs: list[str] = []
    sink = logger.add(lambda m: logs.append(str(m)), level="WARNING")
    try:
        await aa._apply_approve(db, p, None, transition)
    finally:
        logger.remove(sink)
    assert not any("verification not passed" in m for m in logs)  # guard let it through
    # recorded digest that no longer matches the file blocks as well
    art = (await _finals(db, p))[0]
    art.meta = {"sha256": "other"}
    assert await aa._final_verified(db, p) is False


# ── publish: unconfirmed is not success ──────────────────────────────────────


class _Loc:
    def __init__(self, n):
        self.n = n
        self.first = self

    async def count(self):
        return self.n

    async def set_input_files(self, *_):
        return None

    async def click(self):
        return None

    async def fill(self, *_):
        return None


class _Page:
    url = "https://example.invalid/upload"

    def __init__(self, success=0):
        self.success = success

    def locator(self, sel):
        return _Loc(self.success if sel == "div.done" else 1)


async def _publish(pub_cls, page, tmp_path, monkeypatch):
    pub = pub_cls(browser=None)

    async def _open():
        return page

    monkeypatch.setattr(pub, "_open", _open)
    pub.confirm_timeout_s = 0.0
    return await pub.publish(tmp_path / "v.mp4", "cap")


@pytest.mark.asyncio
async def test_publish_without_success_marker_is_unconfirmed(tmp_path, monkeypatch):
    from app.bots.publishers import TikTokPublisher

    res = await _publish(TikTokPublisher, _Page(), tmp_path, monkeypatch)
    assert res.ok is False and res.status == "unconfirmed"


@pytest.mark.asyncio
async def test_publish_confirmed_only_with_marker(tmp_path, monkeypatch):
    from app.bots.publishers import TikTokPublisher

    class P(TikTokPublisher):
        success_selectors = ["div.done"]

    assert (await _publish(P, _Page(success=1), tmp_path, monkeypatch)).status == "confirmed"
    res = await _publish(P, _Page(success=0), tmp_path, monkeypatch)
    assert res.ok is False and res.status == "unconfirmed"


@pytest.mark.asyncio
async def test_publish_run_does_not_record_unconfirmed_as_success(db, tmp_path, monkeypatch):
    from app.bots.publishers import PublishResult
    from app.orchestrator.steps import publish as pub

    p = await _project(db, "g5")
    p.status = ProjectStatus.publishing
    p.meta = {}
    video = p.data_dir / "final" / "g5.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"V")
    db.add(Artifact(project_id=p.id, kind=ArtifactKind.final_video, uuid="u2", path=str(video)))
    await db.flush()
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    calls = []

    async def fake_everywhere(path, caption, *, skip_platforms=None):
        calls.append(set(skip_platforms or ()))
        return [
            PublishResult("tiktok", False, url="u", error="unconfirmed", status="unconfirmed"),
            PublishResult("yt_shorts", True, url="https://y/1", status="confirmed"),
            PublishResult("ig_reels", False, error="boom", status="failed"),
        ]

    monkeypatch.setattr(pub, "publish_everywhere", fake_everywhere)
    sent = []

    class Bot:
        async def send_message(self, chat, text):
            sent.append(text)

    await pub.run(db, p, Bot())
    assert p.meta["published_platforms"] == {"yt_shorts": "https://y/1"}
    assert set(p.meta["publish_unconfirmed"]) == {"tiktok"}
    assert p.status is ProjectStatus.publishing
    assert any("НЕ ПОДТВЕРЖДЕНО" in t for t in sent)
    # a retry never re-publishes to the unconfirmed platform
    await pub.run(db, p, Bot())
    assert calls[1] >= {"tiktok", "yt_shorts"}


def test_preview_stays_read_only_signature():
    import inspect
    from app.orchestrator.steps.publish import preview_publication

    assert list(inspect.signature(preview_publication).parameters) == ["session", "project"]
    assert SimpleNamespace  # keep import used
