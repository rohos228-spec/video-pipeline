"""Шлюз бюджета вокруг HTTP «Границ сцен». LLM и сеть подменены.

С включённым шлюзом порядок и одновременность запусков те же, что без него.
Потолок даёт ``waiting_approval`` / ``budget_exhausted``, не ``failed``.
Уже записанные сцены и ``fragments.json`` не стираются и не перезаписываются.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import json
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Scene,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.orchestrator.validators.vo_length import check_vo_lengths
from app.services import scene_boundaries as sb
from app.services import spend_guard
from app.services.step_failure_policy import record_step_failure

_VO = "Альфа бежит по двору. Бета смотрит в окно."
_BAD = "совсем без скобок"
_GOOD = "[Альфа бежит по двору.] [Бета смотрит в окно.]"
_GATE_MARK = "app/services/spend_guard.py"


class _Trace:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.inflight = 0
        self.max_inflight = 0
        self._lock = asyncio.Lock()

    async def enter(self, name: str) -> None:
        async with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.events.append(("start", name))

    async def leave(self, name: str) -> None:
        async with self._lock:
            self.events.append(("end", name))
            self.inflight -= 1

    def signature(self) -> tuple[list[tuple[str, str]], int]:
        return list(self.events), self.max_inflight


class _Hold:
    def __init__(self, need: int) -> None:
        self.need = need
        self.started = 0
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()

    def mark(self) -> None:
        self.started += 1
        if self.started >= self.need:
            self.all_started.set()


class _Box:
    def __init__(self) -> None:
        self.scripts: dict[str, str] = {}
        self.hold: _Hold | None = None
        self.trace: _Trace | None = None


class _Stream:
    def __init__(self, name: str, text: str, box: _Box) -> None:
        self.name = name
        self.text = text
        self.box = box
        self.status_code = 200

    async def __aenter__(self) -> _Stream:
        if self.box.trace is not None:
            await self.box.trace.enter(self.name)
        if self.box.hold is not None:
            self.box.hold.mark()
            await self.box.hold.release.wait()
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        if self.box.trace is not None:
            await self.box.trace.leave(self.name)
        return False

    async def aiter_lines(self):
        payload = json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": self.text},
            },
            ensure_ascii=False,
        )
        yield "data: " + payload
        yield 'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}'

    async def aread(self) -> bytes:
        return b""


class _Client:
    box = _Box()

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def stream(self, _method: str, _url: str, headers: dict | None = None, json: object = None):
        del json
        name = str((headers or {}).get("x-batch") or "batch")
        return _Stream(name, self.box.scripts[name], self.box)


def _from_gate() -> bool:
    return any(_GATE_MARK in frame for frame in traceback.format_stack())


def _install_http(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def _open_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящий paid_call, но без БД, диска и рубильника."""

    from app.services import disk_guard, kill_switch

    async def guard_native(_sessions: object) -> None:
        return None

    def disk_check() -> SimpleNamespace:
        return SimpleNamespace(allowed=True)

    def caps() -> dict[str, float]:
        return {"daily": 0.0, "project": 0.0, "run": 0.0}

    async def record_block(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(kill_switch, "guard_native", guard_native)
    monkeypatch.setattr(disk_guard, "check", disk_check)
    monkeypatch.setattr(spend_guard, "caps", caps)
    monkeypatch.setattr(spend_guard, "_record_block", record_block)


def _disable_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.asynccontextmanager
    async def passthrough(*_args: object, **_kwargs: object):
        yield None

    monkeypatch.setattr(spend_guard, "paid_call", passthrough)


def _track_gate_pauses(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Семафор, очередь и sleep шлюза. Чужой sleep (ретрай call_opus) только пишется."""

    hits: list[object] = []
    other_sleeps: list[float] = []
    real_semaphore = asyncio.Semaphore
    real_queue = asyncio.Queue

    def semaphore(*args: object, **kwargs: object):
        if _from_gate():
            hits.append("Semaphore")
        return real_semaphore(*args, **kwargs)

    def queue(*args: object, **kwargs: object):
        if _from_gate():
            hits.append("Queue")
        return real_queue(*args, **kwargs)

    async def sleep(delay: float = 0, result: object = None) -> object:
        del result
        if _from_gate():
            hits.append(("sleep", delay))
        else:
            other_sleeps.append(delay)
        return None

    monkeypatch.setattr(asyncio, "Semaphore", semaphore)
    monkeypatch.setattr(asyncio, "Queue", queue)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    sleep.outside = other_sleeps  # type: ignore[attr-defined]
    return hits


def _lengths(frags: list[str]) -> list[tuple[int, str, int, str]]:
    return [(item.index, item.kind, item.length, item.fragment) for item in check_vo_lengths(frags)]


def _scripted_kie(prompts: list[tuple[str, str]], trace: _Trace):
    async def kie(user: str, system: str) -> tuple[str, dict]:
        prompts.append((system, user))
        name = f"a{len(prompts)}"
        await trace.enter(name)
        try:
            text, meta = await sb._stream_once(
                {"batch": name},
                {"x-batch": name},
                url="http://llm.invalid/messages",
            )
        finally:
            await trace.leave(name)
        return text, meta

    return kie


async def _run_boundaries(trace_dir: Path) -> tuple[list[str], dict, list[tuple[str, str]], _Trace]:
    prompts: list[tuple[str, str]] = []
    trace = _Trace()
    _Client.box.scripts = {"a1": _BAD, "a2": _GOOD}
    _Client.box.hold = None
    _Client.box.trace = None
    sb_module_kie = _scripted_kie(prompts, trace)
    # Подмена только в этом прогоне: call_opus остаётся настоящим.
    original = sb._kie_once
    sb._kie_once = sb_module_kie
    try:
        frags, rep = await sb.run_boundaries(_VO, trace_dir)
    finally:
        sb._kie_once = original
        spend_guard.reset_inflight()
    return frags, rep, prompts, trace


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        yield opened
    await engine.dispose()


def test_gate_source_has_no_semaphore_queue_or_sleep() -> None:
    source = "\n".join(
        inspect.getsource(fn)
        for fn in (spend_guard.paid_call, spend_guard.paid_gate, spend_guard._paid_gate)
    )
    forbidden = {"Semaphore", "Queue", "PriorityQueue", "LifoQueue", "sleep"}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            raise AssertionError(node.attr)
        if isinstance(node, ast.Name) and node.id in forbidden:
            raise AssertionError(node.id)


async def test_gate_does_not_change_batch_order_or_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_http(monkeypatch)
    hits = _track_gate_pauses(monkeypatch)
    system, user_t = sb.load_prompt()
    expected_user = user_t.replace("{{VO}}", _VO)

    async def once(enabled: bool) -> tuple[list[str], dict, list[tuple[str, str]], _Trace, list[float]]:
        if enabled:
            _open_budget(monkeypatch)
        else:
            _disable_gate(monkeypatch)
        sleep_attr = asyncio.sleep
        outside = sleep_attr.outside  # type: ignore[attr-defined]
        outside.clear()
        frags, rep, prompts, trace = await _run_boundaries(tmp_path / ("on" if enabled else "off"))
        return frags, rep, prompts, trace, list(outside)

    gated = await once(True)
    plain = await once(False)

    assert gated[3].signature() == plain[3].signature()
    assert gated[3].max_inflight == 1
    assert [name for _kind, name in gated[3].events] == ["a1", "a1", "a2", "a2"]
    assert gated[4] == plain[4]
    assert hits == []
    for prompts in (gated[2], plain[2]):
        assert prompts == [(system, expected_user), (system, expected_user)]
    assert gated[0] == plain[0]
    assert gated[1]["check_passed"] is True
    assert plain[1]["check_passed"] is True
    assert _lengths(gated[0]) == _lengths(plain[0])
    assert sb.fragment_stats(gated[0]) == sb.fragment_stats(plain[0])


async def test_open_gate_lets_batches_overlap_like_the_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_http(monkeypatch)
    hits = _track_gate_pauses(monkeypatch)

    async def wave(enabled: bool) -> _Trace:
        if enabled:
            _open_budget(monkeypatch)
        else:
            _disable_gate(monkeypatch)
        trace = _Trace()
        hold = _Hold(3)
        _Client.box.scripts = {f"b{i}": _GOOD for i in range(3)}
        _Client.box.hold = hold
        _Client.box.trace = trace
        tasks = [
            asyncio.create_task(
                sb._stream_once(
                    {"batch": i},
                    {"x-batch": f"b{i}"},
                    url="http://llm.invalid/messages",
                )
            )
            for i in range(3)
        ]
        try:
            await asyncio.wait_for(hold.all_started.wait(), timeout=2)
            assert trace.max_inflight == 3
            hold.release.set()
            done = await asyncio.gather(*tasks)
        finally:
            hold.release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            spend_guard.reset_inflight()
        assert [text for text, _meta in done] == [_GOOD, _GOOD, _GOOD]
        return trace

    gated = await wave(True)
    plain = await wave(False)
    assert gated.signature() == plain.signature()
    assert gated.max_inflight == 3
    assert hits == []


async def test_exhausted_budget_parks_task_and_keeps_ready_scenes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: AsyncSession,
) -> None:
    _install_http(monkeypatch)
    _open_budget(monkeypatch)
    hits = _track_gate_pauses(monkeypatch)
    real_gate = spend_guard.paid_gate
    seen = {"n": 0}

    async def limited(**kwargs: object):
        seen["n"] += 1
        if seen["n"] > 1:
            raise spend_guard.PaidCallBlocked(
                "spend_cap_run",
                "потолок за запуск",
                provider="vibecode",
                what="anthropic messages stream",
            )
        return await real_gate(**kwargs)

    monkeypatch.setattr(spend_guard, "paid_gate", limited)

    project = Project(
        slug="sb-gate",
        topic="двор",
        status=ProjectStatus.scene_designing,
        auto_mode=False,
        meta={},
    )
    session.add(project)
    await session.flush()
    ready = Scene(
        project_id=project.id,
        sort_key=1,
        title="Двор",
        place="двор",
        meaning="Альфа уже на месте",
    )
    other = Scene(
        project_id=project.id,
        sort_key=2,
        title="Окно",
        place="дом",
        meaning="Бета смотрит",
    )
    session.add_all([ready, other])
    workflow = Workflow(name="sb")
    session.add(workflow)
    await session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        project_id=project.id,
        status=WorkflowRunStatus.running,
    )
    session.add(run)
    await session.flush()
    node = NodeRun(
        workflow_run_id=run.id,
        node_key="boundaries",
        node_type="enrich",
        status=NodeRunStatus.running,
        meta={"kept": True},
    )
    session.add(node)
    await session.flush()

    folder = SimpleNamespace(data_dir=tmp_path / "video")
    saved = sb.save_output(
        folder,
        node_key="boundaries",
        vo=_VO,
        frags=["Альфа бежит по двору.", "Бета смотрит в окно."],
        rep={"check_passed": True},
    )
    before_file = saved.read_bytes()

    async def snapshot() -> list[tuple]:
        rows = (
            await session.execute(
                select(Scene).where(Scene.project_id == project.id).order_by(Scene.sort_key)
            )
        ).scalars().all()
        return [
            (row.id, row.sort_key, row.title, row.place, row.meaning, row.scene_type)
            for row in rows
        ]

    before_rows = await snapshot()
    with pytest.raises(spend_guard.BudgetExhausted) as raised:
        await _run_boundaries(tmp_path / "trace")

    action = await record_step_failure(session, project, error=raised.value)
    assert action == "waiting_approval"
    assert project.status is ProjectStatus.paused
    assert project.status is not ProjectStatus.failed
    assert project.meta["approval_status"] == "waiting_approval"
    assert project.meta["approval_reason"] == "budget_exhausted"
    await session.refresh(node)
    assert node.status is NodeRunStatus.waiting_approval
    assert node.error is None
    assert node.meta["status"] == "waiting_approval"
    assert node.meta["reason"] == "budget_exhausted"
    assert node.meta["kept"] is True
    assert saved.read_bytes() == before_file
    assert await snapshot() == before_rows
    assert seen["n"] == 2
    assert hits == []
