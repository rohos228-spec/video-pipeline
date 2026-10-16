from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bots import publishers as pubmod
from app.bots.publishers import PublishResult, Publisher
from app.models import Artifact, ArtifactKind, Base, Project, ProjectStatus
from app.orchestrator.steps import publish as step
from app.services import publications_store as ps
from tests._stand_workdir import make_workdir, remove_workdir

PLATFORMS = [c.platform for c in pubmod.ALL_PUBLISHERS]


class Env:
    def __init__(self) -> None:
        self.workdir = make_workdir("w61_")
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.workdir / 'w61.db'}")
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create(self) -> "Env":
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return self

    async def close(self) -> None:
        await self.engine.dispose()
        remove_workdir(self.workdir)


class Bot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat, text):
        self.sent.append(text)


async def make_project(db, workdir, slug="w61", *, topic="тема ролика", status=ProjectStatus.publishing,
                       data=b"SYNTHETIC-VIDEO", uuid=None) -> Project:
    p = Project(slug=slug, title="t", topic=topic, status=status, meta={})
    db.add(p)
    await db.flush()
    await add_final(db, p, workdir, data=data, uuid=uuid or f"u-{slug}")
    return p


async def add_final(db, project, workdir, *, data: bytes, uuid: str, name: str | None = None) -> str:
    video = workdir / (name or f"{project.slug}-{uuid}.mp4")
    video.write_bytes(data)
    db.add(Artifact(project_id=project.id, kind=ArtifactKind.final_video, uuid=uuid, path=str(video)))
    await db.flush()
    return await ps.sha256_of_file_async(video)


async def sha_of(db, project) -> str:
    final = await step._latest_final(db, project)
    return await ps.sha256_of_file_async(final.path)


async def grant(db, project, platforms=None, *, by="operator:test") -> None:
    sha = await sha_of(db, project)
    csha = step.caption_sha256(step.caption_for(project))
    for pl in (platforms if platforms is not None else PLATFORMS):
        await ps.grant_approval(db, project_id=project.id, platform=pl, sha256=sha, caption_sha256=csha,
                                approved_by=by)


class Clicks(list):

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[int] = []


def fake_registry(monkeypatch, behaviors: dict[str, str] | None = None):
    behaviors = behaviors if behaviors is not None else {}
    clicks = Clicks()

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
    opened = clicks.opened

    @asynccontextmanager
    async def fake_browser(*a, **k):
        opened.append(1)
        yield SimpleNamespace()

    async def nosleep(*_):
        return None

    monkeypatch.setattr(pubmod, "morelogin_browser", fake_browser)
    monkeypatch.setattr(pubmod.asyncio, "sleep", nosleep)
    return clicks
