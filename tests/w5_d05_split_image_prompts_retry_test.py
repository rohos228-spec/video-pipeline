"""W5-3 / D05: безопасный повтор split и image_prompts (уровень U, без сети и без платных вызовов).

Что доказывают тесты:
* реестр: split и image_prompts допускают retry_same / retry_profile, а sfx_plan, sd_agent, sd_assemble совместимы с профилями;
  animation_prompts и медиа-ноды остаются закрытыми;
* типизация: повтор возможен только при полном сбое без записи (invalid_json / missing_payload); принятая локальная
  разбивка и частичное извлечение промтов инцидентом не становятся;
* повтор не теряет ручную работу (отказ ДО запроса к модели) и не повторяет уже завершённую разбивку;
* откат возвращает кадры, их uuid, поля, привязку артефактов и ключи meta;
* провал проверки после записи в управляемом запуске возвращает прежние кадры и удерживает запуск.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Artifact, ArtifactKind, Base, Frame, FrameStatus, Project, ProjectStatus,
)
from app.services import db_v2
from app.services import node_capabilities as nc
from app.services.diagnosis import derive_incidents
from app.services.node_capabilities import READ_ACTIONS, capability_for

RETRY = {"retry_same", "retry_profile", "finish"}


# ------------------------------------------------------------------ registry --------------------------------------

@pytest.mark.parametrize("node_type", ["split", "image_prompts"])
def test_split_and_image_prompts_admit_retry_and_profile(node_type):
    cap = capability_for(node_type)
    assert cap.managed and set(cap.actions) == set(READ_ACTIONS) | RETRY
    assert cap.profile_compatible and cap.volatile_attachment_names
    assert cap.native_json_retry is False and cap.recovery_continuation == "none"
    from app.orchestrator.node_spec import capability_spec_for
    spec = capability_spec_for(node_type)
    assert spec.forbidden_code is None and spec.recovery_cap == 1 and spec.limits and spec.verifies


def test_profile_compat_is_open_only_where_the_request_is_text_only_and_the_write_is_checked_first():
    open_nodes = {"plan", "script", "split", "image_prompts", "excel_gpt", "sfx_plan", "sd_agent", "sd_assemble"}
    from app.orchestrator.node_spec import registered_capability_specs
    actual = {t for t, s in registered_capability_specs().items() if s.profile_compatible}
    assert actual == open_nodes
    # animation_prompts: an image strip may be in the request and batches apply one by one -> stays retry_same only
    anim = capability_for("animation_prompts")
    assert not anim.profile_compatible and "retry_profile" not in anim.actions
    for media in ("images", "videos", "items", "music", "audio", "sfx_gen", "assemble", "hero", "publish"):
        assert not capability_for(media).profile_compatible, media


def test_only_the_text_request_shape_can_carry_a_profile():
    """The safety net behind the flag: a text-only Responses body takes the profile, an image body is refused."""
    from app.services import profiles_v1
    candidate = {"model_id": "gpt-5-6-sol", "provider": "kie", "kind": "text", "channel": "stable",
                 "reasoning_effort": "low", "max_output_tokens": 4096, "prompt_variant": "repair-json-only-v1",
                 "synthetic_only": False}
    with patch.object(profiles_v1, "validate_candidate", lambda c: None):
        text_body = {"model": "m", "input": [{"role": "user", "content": "prompt_split_20261001_205923.txt\nбез картинок"}]}
        out = profiles_v1.effective_body(text_body, candidate)
        assert out["input"][0]["content"].startswith("prompt_split_20261001_205923.txt")
        image_body = {"model": "m", "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "x"}, {"type": "input_image", "image_url": "data:image/png;base64,AA"}]}]}
        with pytest.raises(ValueError):
            profiles_v1.effective_body(image_body, candidate)


# ------------------------------------------------------------------ typing of failures ----------------------------

def _scope():
    return type("S", (), {"project_id": 2, "execution_id": "e", "node_keys": ["n"], "observer_state": "active",
                          "gaps": []})()


def _events(parse_payload, *, request_id="r1"):
    base = dict(project_id=2, execution_id="e", node_key="n", attempt_id="a", db_scope="project")
    return [
        {**base, "event_id": "s", "kind": "model_start", "request_id": request_id, "payload": {}, "id": 1},
        {**base, "event_id": "p", "kind": "parse_result", "request_id": request_id, "payload": parse_payload, "id": 2},
        {**base, "event_id": "x", "kind": "node_error", "request_id": None, "payload": {}, "id": 3},
    ]


async def _split_parse_payload(reply, spec, source):
    ctx = SimpleNamespace(scope=SimpleNamespace(project_id=2), last_request_id="r1", split_input_voiceover="a b c d")
    from app.services.observation import _attempt
    token = _attempt.set(ctx)
    try:
        with patch("app.services.observation.record_fact", new_callable=AsyncMock) as fact:
            await nc.record_split_parse(reply, spec, frames_source=source)
            return fact.call_args_list[0].args[1]
    finally:
        _attempt.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply, kind", [("model refused", "invalid_json"), ('{"ops": []}', "missing_payload")])
async def test_split_total_failure_is_typed_so_one_retry_is_possible(reply, kind):
    payload = await _split_parse_payload(reply, [], "none")
    assert payload["payload_required"] is True and payload["parsed"] is False and payload["payload_present"] is False
    types = {i["type"] for i in derive_incidents(_scope(), _events(payload), {})}
    assert kind in types and "split_degraded" not in types


@pytest.mark.asyncio
async def test_split_with_a_usable_answer_or_the_native_fallback_is_never_an_invalid_json_incident():
    spec = [{"закадр": "a b"}, {"закадр": "c d"}]
    for source in ("json_replace_frames", "dash_blocks", "local_voiceover_fallback"):
        payload = await _split_parse_payload("junk", spec, source)
        assert payload["payload_required"] is False
        types = {i["type"] for i in derive_incidents(_scope(), _events(payload), {})}
        assert not types & {"invalid_json", "missing_payload"}, source


def _attempt_ctx(**extra):
    base = dict(scope=SimpleNamespace(project_id=2, mode="managed", execution_id="e"), attempt_id="a1",
                last_request_id="r1", session=SimpleNamespace(info={}))
    base.update(extra)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply, kind", [("not json at all", "invalid_json"), ('{"ops": []}', "missing_payload")])
async def test_image_prompts_total_failure_is_recorded_once_and_typed(reply, kind):
    from app.services.observation import _attempt
    ctx = _attempt_ctx(image_prompt_batch_requests={1: "r1", 2: "r2"})
    token = _attempt.set(ctx)
    try:
        with patch("app.services.observation.record_fact", new_callable=AsyncMock) as fact:
            assert await nc.record_image_prompt_unusable() is False          # nothing noted: no fact
            nc.note_image_prompt_unusable(reply, batch_i=2)
            assert await nc.record_image_prompt_unusable() is True
            assert await nc.record_image_prompt_unusable() is False          # once per attempt
            assert fact.await_count == 1
            name, payload = fact.call_args.args[0], fact.call_args.args[1]
            assert name == "parse_result" and fact.call_args.kwargs["request_id"] == "r2"
    finally:
        _attempt.reset(token)
    assert payload["payload_required"] is True and payload["applied_before"] is False and payload["contract"] == "native_image_prompts"
    items = derive_incidents(_scope(), _events(payload, request_id="r2"), {})
    assert kind in {i["type"] for i in items}
    incident = next(i for i in items if i["type"] == kind)
    assert incident["evidence_incomplete"] is False, "a typed incident with a request id is retry-eligible evidence"


@pytest.mark.asyncio
async def test_image_prompts_native_second_attempt_leaves_no_open_incident():
    """The note is cleared at every native attempt; a later success never records the terminal fact."""
    from app.services.observation import _attempt
    ctx = _attempt_ctx()
    token = _attempt.set(ctx)
    try:
        with patch("app.services.observation.record_fact", new_callable=AsyncMock) as fact:
            nc.note_image_prompt_unusable("no ops", batch_i=1)
            nc.clear_image_prompt_unusable()
            assert await nc.record_image_prompt_unusable() is False
            fact.assert_not_awaited()
    finally:
        _attempt.reset(token)


def test_image_prompts_per_batch_facts_stay_non_incidents():
    """Partial extraction keeps working: a batch with zero ops is not an incident by itself (payload_required False)."""
    import inspect
    src = inspect.getsource(nc.record_image_prompt_parse).replace(" ", "")
    assert "'payload_required':False" in src


# ------------------------------------------------------------------ preflight (manual work) -----------------------

def _f(number, **kw):
    base = dict(number=number, image_prompt=None, animation_prompt=None, attrs={}, status=FrameStatus.planned)
    base.update(kw)
    return SimpleNamespace(**base)


def test_retry_preflight_refuses_frames_that_carry_work():
    bare = [_f(1), _f(2)]
    assert nc.split_retry_refusal(bare, {}) is None
    assert nc.split_retry_refusal([], {}) is None
    for worked in (_f(2, image_prompt="a prompt"), _f(2, animation_prompt="anim"), _f(2, attrs={"plan": "close"}),
                   _f(2, status=FrameStatus.image_generated)):
        reason = nc.split_retry_refusal([_f(1), worked], {})
        assert reason and "кадры: 2" in reason and "до запроса к модели" in reason
    # a completed split is skipped by the executor itself (no model request, no replacement): not a refusal
    assert nc.split_retry_refusal([_f(1, image_prompt="p"), _f(2)], {"split_completed": True}) is None
    # one frame is not a completed split
    assert nc.split_retry_refusal([_f(1, image_prompt="p")], {"split_completed": True}) is not None


# ------------------------------------------------------------------ DB: snapshot, rollback, preparation -----------

@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _project_with_work(session, slug="w5d05"):
    project = Project(slug=slug, title="t", topic="t", status=ProjectStatus.frames_ready,
                      meta={"split_completed": True, "split_params_applied": "fp-old", "keep": "me",
                            "scene_registry": [{"id": "s1"}]})
    session.add(project)
    await session.flush()
    created = await db_v2.replace_all_frames(session, project, [
        {"закадр": "Первый кадр про море", "длительность": 3}, {"закадр": "Второй кадр про берег"},
        {"закадр": "Третий кадр про закат"}])
    created[0].image_prompt = "P" * 250
    created[0].attrs = {"plan": "общий", "персонажи": ["A"]}
    created[1].status = FrameStatus.image_prompt_ready
    created[2].animation_prompt = "медленный наезд"
    session.add(Artifact(project_id=project.id, frame_id=created[0].id, kind=ArtifactKind.scene_image,
                         uuid="art-1", path="img/1.png"))
    await session.flush()
    return project


async def _frames(session, project):
    return (await session.execute(select(Frame).where(Frame.project_id == project.id).order_by(Frame.number))).scalars().all()


def _fingerprint(frames):
    return [(f.id, f.number, f.uuid, f.voiceover_text, f.image_prompt, f.animation_prompt, f.status, f.attrs,
             f.duration_seconds) for f in frames]


@pytest.mark.asyncio
async def test_rollback_returns_the_frames_their_artifacts_and_meta(db):
    project = await _project_with_work(db)
    before = _fingerprint(await _frames(db, project))
    snapshot = await nc.capture_split_snapshot(db, project)
    assert snapshot and len(snapshot["frames"]) == 3 and snapshot["artifact_links"]
    assert snapshot["meta"]["split_completed"] is True and "path" in snapshot
    assert json.loads(open(snapshot["path"], encoding="utf-8").read())["frames"][0]["image_prompt"] == "P" * 250

    # the replacement the node would commit (what split_frames.run does around replace_frames)
    await db_v2.replace_all_frames(db, project, [{"закадр": "новый один"}, {"закадр": "новый два"}])
    meta = dict(project.meta)
    meta.pop("scene_registry", None)
    meta.update(split_completed=True, split_params_applied="fp-new")
    project.meta = meta
    await db.flush()
    after = await _frames(db, project)
    assert [f.voiceover_text for f in after] == ["новый один", "новый два"]
    art = await db.scalar(select(Artifact).where(Artifact.uuid == "art-1"))
    assert art.frame_id is None, "replace_all_frames unlinks artifacts; the rollback must link them back"

    result = await nc.rollback_split(db, project, snapshot)
    assert result == {"rolled_back": True, "frames": 3, "artifact_links": 1}
    assert _fingerprint(await _frames(db, project)) == before
    art = await db.scalar(select(Artifact).where(Artifact.uuid == "art-1"))
    assert art.frame_id == before[0][0]
    assert project.meta["split_params_applied"] == "fp-old" and project.meta["keep"] == "me"
    assert project.meta["scene_registry"] == [{"id": "s1"}]
    assert len(await _frames(db, project)) == 3, "no frame of the rejected replacement is left"


@pytest.mark.asyncio
async def test_rollback_without_a_snapshot_changes_nothing(db):
    project = await _project_with_work(db, slug="w5d05b")
    before = _fingerprint(await _frames(db, project))
    assert await nc.rollback_split(db, project, None) == {"rolled_back": False, "reason": "no_prior_frames"}
    other = await nc.capture_split_snapshot(db, Project(id=999, slug="x", title="t", topic="t"))
    assert other is None
    assert _fingerprint(await _frames(db, project)) == before


@pytest.mark.asyncio
async def test_a_project_without_frames_has_nothing_to_snapshot(db):
    project = Project(slug="w5d05c", title="t", topic="t", status=ProjectStatus.splitting)
    db.add(project)
    await db.flush()
    assert await nc.capture_split_snapshot(db, project) is None


@pytest.mark.asyncio
async def test_prepare_refuses_a_supervised_retry_over_frames_with_work_before_any_request(db):
    from app.services.observation import _attempt
    project = await _project_with_work(db, slug="w5d05d")
    project.meta = {}                                   # the split is not marked completed
    await db.flush()
    ctx = _attempt_ctx(session=SimpleNamespace(info={"recovery_attempt_id": "a1"}))
    token = _attempt.set(ctx)
    try:
        with pytest.raises(nc.SplitRetryRefused, match="до запроса к модели"):
            await nc.prepare_split_replacement(db, project)
        assert getattr(ctx, "split_pre_snapshot", None) is None
    finally:
        _attempt.reset(token)


@pytest.mark.asyncio
async def test_prepare_in_a_managed_attempt_keeps_the_snapshot_and_in_other_scopes_does_nothing(db):
    from app.services.observation import _attempt
    project = await _project_with_work(db, slug="w5d05e")
    # observe scope or no attempt: native behaviour, nothing captured
    assert await nc.prepare_split_replacement(db, project) is None
    observe = _attempt_ctx(scope=SimpleNamespace(project_id=2, mode="observe", execution_id="e"))
    token = _attempt.set(observe)
    try:
        assert await nc.prepare_split_replacement(db, project) is None
    finally:
        _attempt.reset(token)
    # a managed first attempt (not a supervised retry) over a completed split: snapshot taken, no refusal
    managed = _attempt_ctx()
    token = _attempt.set(managed)
    try:
        snapshot = await nc.prepare_split_replacement(db, project)
        assert snapshot is managed.split_pre_snapshot and len(snapshot["frames"]) == 3
    finally:
        _attempt.reset(token)


@pytest.mark.asyncio
async def test_a_supervised_retry_over_a_completed_split_is_not_refused(db):
    """split_completed + >=2 frames: the executor skips the model and the replacement, so there is nothing to lose."""
    from app.services.observation import _attempt
    project = await _project_with_work(db, slug="w5d05f")
    ctx = _attempt_ctx(session=SimpleNamespace(info={"recovery_attempt_id": "a1"}))
    token = _attempt.set(ctx)
    try:
        assert (await nc.prepare_split_replacement(db, project))["frames"]
    finally:
        _attempt.reset(token)


# ------------------------------------------------------------------ verifier: rollback + hold ---------------------

@pytest.mark.asyncio
async def test_failed_post_write_check_gives_the_previous_frames_back_and_holds_the_run(db):
    from app.services.observation import _attempt
    project = await _project_with_work(db, slug="w5d05g")
    before = _fingerprint(await _frames(db, project))
    snapshot = await nc.capture_split_snapshot(db, project)
    # the committed replacement LOSES the voiceover tail -> verify_split_frames fails
    await db_v2.replace_all_frames(db, project, [{"закадр": "Первый кадр про море"}, {"закадр": "Второй кадр"}])
    ctx = _attempt_ctx(split_pre_snapshot=snapshot, split_input_voiceover="Первый кадр про море Второй кадр про берег Третий кадр про закат")
    ctx.scope.project_id = project.id
    ctx.observer = SimpleNamespace(master=object(), mark_gap=lambda *a, **k: None)
    held = AsyncMock()
    token = _attempt.set(ctx)
    try:
        with patch("app.services.observation.record_fact", new_callable=AsyncMock) as fact, \
                patch("app.services.actions_v1.ActionSupervisor") as supervisor:
            supervisor.return_value.set_held = held
            with pytest.raises(RuntimeError, match="native split verification not confirmed"):
                await nc.observe_split_result(db, project, "split")
    finally:
        _attempt.reset(token)
    assert _fingerprint(await _frames(db, project)) == before, "the previous frames are back"
    checks = [c.args[1] for c in fact.call_args_list if c.args[0] == "runtime_check"]
    assert checks and checks[0]["check"] == "откат разбивки к прежним кадрам" and checks[0]["status"] == "passed"
    held.assert_awaited_once()
    assert "прежние кадры возвращены" in held.await_args.kwargs["rationale"]
    assert held.await_args.kwargs["operator_request"] is False


@pytest.mark.asyncio
async def test_first_split_without_prior_frames_is_only_held_as_before(db):
    from app.services.observation import _attempt
    project = Project(slug="w5d05h", title="t", topic="t", status=ProjectStatus.splitting)
    db.add(project)
    await db.flush()
    await db_v2.replace_all_frames(db, project, [{"закадр": "один"}, {"закадр": "два"}])
    ctx = _attempt_ctx(split_input_voiceover="один два три четыре")
    ctx.scope.project_id = project.id
    ctx.observer = SimpleNamespace(master=object(), mark_gap=lambda *a, **k: None)
    held = AsyncMock()
    token = _attempt.set(ctx)
    try:
        with patch("app.services.observation.record_fact", new_callable=AsyncMock) as fact, \
                patch("app.services.actions_v1.ActionSupervisor") as supervisor:
            supervisor.return_value.set_held = held
            with pytest.raises(RuntimeError, match="not confirmed"):
                await nc.observe_split_result(db, project, "split")
    finally:
        _attempt.reset(token)
    assert [c.args[0] for c in fact.call_args_list] == ["node_result_verification"]
    assert len(await _frames(db, project)) == 2, "nothing to give back: the new frames stay, the run is held"
    held.assert_awaited_once()
