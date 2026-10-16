from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import settings as app_settings
from app.models import (
    Base, Frame, FrameStatus, HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus)
from app.orchestrator import auto_advance as aa
from app.services import regen_budget, restart_chain


@pytest.fixture(autouse=True)
def _caps(monkeypatch: pytest.MonkeyPatch) -> None:
    s = app_settings.settings
    monkeypatch.setattr(s, "regen_max_per_frame", 3)
    monkeypatch.setattr(s, "regen_max_per_run", 12)
    monkeypatch.setattr(s, "regen_max_usd_per_run", 0.0)
    monkeypatch.setattr(s, "regen_estimate_usd_per_item", 0.0)
    monkeypatch.setattr(s, "restart_chain_max_per_node", 0)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _no_fact():
    with patch("app.services.observation.record_fact", new=AsyncMock()) as m:
        yield m


_SEQ = [0]


def _project(status: ProjectStatus = ProjectStatus.generating_images, **meta) -> Project:
    _SEQ[0] += 1
    return Project(slug=f"rc{_SEQ[0]}", topic="t", status=status, hero_mode="no_hero", meta=dict(meta))


def _simulate_no_human_loop(p: Project) -> int:
    paid = 0
    for _cycle in range(aa.MAX_AUTO_REGEN_PER_STEP + 1):
        for rnd in range(11):
            if not regen_budget.charge(p, "images", [rnd % 4 + 1]).ok:
                return paid
            paid += 1
        regen_budget.clear(p, "images")
        regen_budget.charge(p, "vision:scenes", [1])
        regen_budget.clear(p, "vision:scenes")
        if aa._bump_retry_count(p, ProjectStatus.images_ready) > aa.MAX_AUTO_REGEN_PER_STEP:
            return paid
        paid += 1
    return paid


def test_c12_measurement_without_chain_cap_restarts_exceed_every_single_layer_cap() -> None:
    p = _project()
    paid = _simulate_no_human_loop(p)
    assert paid > 12 * 2, paid
    assert paid == 11 * 3 + 2


def test_c12_chain_cap_bounds_the_same_loop(monkeypatch) -> None:
    monkeypatch.setattr(app_settings.settings, "restart_chain_max_per_node", 20)
    p = _project()
    paid = _simulate_no_human_loop(p)
    assert paid == 20
    assert restart_chain.count(p, "generating_images") == 20
    regen_budget.clear(p)
    assert restart_chain.count(p, "generating_images") == 20
    v = regen_budget.charge(p, "images", [1])
    assert not v.ok and v.chain_exhausted and "общий потолок перезапусков" in v.summary()


def test_c12_vision_pass_clears_only_its_own_kind() -> None:
    p = _project()
    regen_budget.charge(p, "vision:scenes", [1])
    regen_budget.charge(p, "vision:videos", [2])
    assert regen_budget.clear(p, "vision:scenes")
    assert "vision:videos" in p.meta["regen_budget"] and "vision:scenes" not in p.meta["regen_budget"]


def test_c12_two_layers_share_one_node(monkeypatch) -> None:
    monkeypatch.setattr(app_settings.settings, "restart_chain_max_per_node", 3)
    p = _project()
    assert regen_budget.charge(p, "images", [1]).ok
    assert regen_budget.charge(p, "vision:scenes", [2]).ok
    assert restart_chain.charge(p, "generating_images", "step_failure")[0]
    assert not regen_budget.charge(p, "images", [3]).ok
    assert restart_chain.count(p, "generating_videos") == 0


@pytest.mark.asyncio
async def test_c12_chain_stop_pauses_and_escalates(_no_fact, session) -> None:
    p = _project()
    session.add(p)
    await session.flush()
    await restart_chain.stop(session, p, "generating_images", 60, 60, "regen_budget")
    assert p.status is ProjectStatus.paused
    assert "общий потолок" in p.meta["auto_paused_reason"]
    fact = _no_fact.await_args.args
    assert fact[0] == "runtime_check" and fact[1]["check"] == "regen_budget" and fact[1]["status"] == "rejected"


@pytest.mark.asyncio
async def test_c12_step_failure_counts_in_chain_and_stops(monkeypatch, session) -> None:
    from app.services.step_failure_policy import record_step_failure

    monkeypatch.setattr(app_settings.settings, "restart_chain_max_per_node", 2)
    p = _project(restart_chain={"generating_images": {"count": 1, "by": {"regen_budget": 1}}})
    session.add(p)
    await session.flush()
    with patch("app.services.step_failure_policy._soft_retry_without_wipe", new_callable=AsyncMock), \
            patch("app.services.step_failure_policy._fail_active_on_both", new_callable=AsyncMock), \
            patch("app.services.step_failure_policy._push_parked_runtime", new_callable=AsyncMock), \
            patch("app.services.run_sync.update_active_node_progress_text", new_callable=AsyncMock), \
            patch("app.project_db.run_on_replica_project", new_callable=AsyncMock):
        assert await record_step_failure(session, p, error=RuntimeError("boom 1")) == "retry"
        assert restart_chain.count(p, "generating_images") == 2
        p.status = ProjectStatus.generating_images
        assert await record_step_failure(session, p, error=RuntimeError("boom 2")) == "abandon"
    assert p.status is ProjectStatus.paused
    assert p.meta["step_failure"]["abandoned_reason"] == "restart_chain"
    assert "общий потолок" in p.meta["auto_paused_reason"]
    assert restart_chain.count(p, "generating_images") == 2



def _regen_transition():
    return aa.TRANSITIONS[ProjectStatus.images_ready]


@pytest.mark.asyncio
async def test_c11_manual_regens_do_not_trip_the_auto_regen_counter(session) -> None:
    p = _project(ProjectStatus.images_ready)
    session.add(p)
    await session.flush()
    res = SimpleNamespace(fix_hints=[])
    with patch.object(aa, "_prepare_node_run_for_status", new=AsyncMock()):
        for _ in range(5):
            p.status = ProjectStatus.images_ready
            await aa._apply_regen(session, p, None, _regen_transition(), res, bot=None, manual=True)
            assert p.status is ProjectStatus.generating_images
    assert "auto_retry_images_ready" not in p.meta
    assert "auto_paused_reason" not in p.meta
    assert restart_chain.count(p, "generating_images") == 5
    assert p.meta["restart_chain"]["generating_images"]["by"] == {"manual": 5}


@pytest.mark.asyncio
async def test_c11_auto_regen_still_pauses_after_two(session) -> None:
    p = _project(ProjectStatus.images_ready)
    session.add(p)
    await session.flush()
    res = SimpleNamespace(fix_hints=[])
    with patch.object(aa, "_prepare_node_run_for_status", new=AsyncMock()):
        for _ in range(3):
            p.status = ProjectStatus.images_ready
            await aa._apply_regen(session, p, None, _regen_transition(), res, bot=None)
    assert p.status is ProjectStatus.paused and "GPT просил regen" in p.meta["auto_paused_reason"]


@pytest.mark.asyncio
async def test_n16_manual_regen_respects_the_chain_cap(monkeypatch, session) -> None:
    monkeypatch.setattr(app_settings.settings, "restart_chain_max_per_node", 2)
    p = _project(ProjectStatus.images_ready)
    session.add(p)
    await session.flush()
    res = SimpleNamespace(fix_hints=[])
    with patch.object(aa, "_prepare_node_run_for_status", new=AsyncMock()) as prep:
        for _ in range(3):
            p.status = ProjectStatus.images_ready
            await aa._apply_regen(session, p, None, _regen_transition(), res, bot=None, manual=True)
    assert prep.await_count == 2
    assert p.status is ProjectStatus.paused and "общий потолок" in p.meta["auto_paused_reason"]


@pytest.mark.asyncio
async def test_c11_resume_releases_all_counters(session) -> None:
    from app.services.project_control import resume_project

    p = _project(ProjectStatus.paused, paused_from_status="generating_images", auto_retry_images_ready=3,
                 auto_paused_reason="x", auto_paused_fix_hints=["a"],
                 restart_chain={"generating_images": {"count": 60, "by": {}}},
                 restart_chain_stop={"node": "generating_images"},
                 regen_budget={"images": {"frames": {"1": 3}, "rounds": 12, "usd": 1.0}},
                 regen_budget_stop={"kind": "images"})
    session.add(p)
    await session.flush()
    assert await resume_project(session, p) == "generating_images"
    for key in ("auto_retry_images_ready", "auto_paused_reason", "restart_chain", "restart_chain_stop",
                "regen_budget", "regen_budget_stop", "auto_paused_fix_hints"):
        assert key not in p.meta, key


def test_automatic_paths_do_not_release_the_chain(monkeypatch) -> None:
    from app.services.step_failure_policy import clear_failure_on_success

    p = _project(restart_chain={"generating_images": {"count": 5, "by": {}}})
    clear_failure_on_success(p, ProjectStatus.generating_images)
    assert restart_chain.count(p, "generating_images") == 5



async def _frame_hitl(session, kind=HITLKind.approve_images, status=ProjectStatus.images_ready):
    p = _project(status)
    session.add(p)
    await session.flush()
    fr = Frame(project_id=p.id, number=1, voiceover_text="v", status=FrameStatus.image_generated, attrs={})
    session.add(fr)
    await session.flush()
    req = HITLRequest(project_id=p.id, frame_id=fr.id, kind=kind, payload={}, decision=HITLDecision.pending)
    session.add(req)
    await session.flush()
    return p, fr, req


@pytest.mark.asyncio
async def test_n17_rejected_frame_is_not_requeued_automatically(session, tmp_path) -> None:
    from app.orchestrator.steps.generate_images import requeue_failed_frames_without_png
    from app.services.hitl_apply import apply_hitl_side_effects

    p, fr, req = await _frame_hitl(session)
    await apply_hitl_side_effects(session, req, HITLDecision.rejected)
    assert fr.status is FrameStatus.failed and fr.attrs.get("rejected_by_human") is True
    n = requeue_failed_frames_without_png([fr], tmp_path, project_id=p.id)
    assert fr.status is FrameStatus.failed and not n
    assert p.status is ProjectStatus.images_ready and restart_chain.count(p, "generating_images") == 0
    other = Frame(project_id=p.id, number=2, voiceover_text="v", status=FrameStatus.failed, attrs={})
    assert requeue_failed_frames_without_png([other], tmp_path, project_id=p.id) == 1


@pytest.mark.asyncio
async def test_n16_manual_frame_regen_counts_and_respects_cap(monkeypatch, session) -> None:
    from app.services.hitl_apply import apply_hitl_side_effects

    monkeypatch.setattr(app_settings.settings, "restart_chain_max_per_node", 1)
    p, fr, req = await _frame_hitl(session)
    await apply_hitl_side_effects(session, req, HITLDecision.regenerate)
    assert fr.status is FrameStatus.image_prompt_ready and restart_chain.count(p, "generating_images") == 1
    fr.status = FrameStatus.image_generated
    await apply_hitl_side_effects(session, req, HITLDecision.regenerate)
    assert fr.status is FrameStatus.image_generated
    assert p.status is ProjectStatus.paused and "общий потолок" in p.meta["auto_paused_reason"]


@pytest.mark.asyncio
async def test_c14_approve_without_advance_does_not_start_the_next_paid_step(session) -> None:
    from app.services.hitl_apply import apply_hitl_side_effects

    p, fr, req = await _frame_hitl(session)
    await apply_hitl_side_effects(session, req, HITLDecision.approved, advance=False)
    assert fr.status is FrameStatus.image_approved and p.status is ProjectStatus.images_ready
    p2, fr2, req2 = await _frame_hitl(session)
    with patch("app.services.hitl_apply._maybe_advance_visual_ready", new=AsyncMock()) as adv:
        await apply_hitl_side_effects(session, req2, HITLDecision.approved)
        adv.assert_awaited_once()


def test_c14_hitl_approve_action_sends_advance_false() -> None:
    import inspect
    from app.services.action_layer import builtin_wave5 as w5

    assert '"advance"' in inspect.getsource(w5._decide)



@pytest.mark.asyncio
async def test_c4_deferred_spend_action_keeps_its_reserve() -> None:
    from app.services.action_layer import executor
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    assert registry.get("hitl.regenerate").deferred_spend and registry.get("massfactory.start").deferred_spend
    assert not registry.get("step.run_step").deferred_spend
    step = SimpleNamespace(idx=0)
    with patch.object(executor.budget, "measure_native_credits", new=AsyncMock(return_value=0.0)) as measure, \
            patch.object(executor.budget, "settle", new=AsyncMock()) as settle:
        got = await executor._settle_step("p1", step, registry.get("hitl.regenerate"), 30.0, None, None)
    assert got is None
    settle.assert_not_awaited()
    measure.assert_not_awaited()

@pytest.mark.asyncio
async def test_telegram_hero_regen_goes_through_the_chain_cap(monkeypatch, session) -> None:
    from app.services.hitl_apply import request_hero_regen

    monkeypatch.setattr(app_settings.settings, "restart_chain_max_per_node", 2)
    p = _project(ProjectStatus.hero_ready)
    session.add(p)
    await session.flush()
    for _ in range(2):
        p.status = ProjectStatus.hero_ready
        assert await request_hero_regen(session, p) is True
        assert p.status is ProjectStatus.generating_hero
    assert restart_chain.count(p, "generating_hero") == 2
    assert p.meta["restart_chain"]["generating_hero"]["by"] == {"manual": 2}
    p.status = ProjectStatus.hero_ready
    assert await request_hero_regen(session, p) is False
    assert p.status is ProjectStatus.paused and "общий потолок" in p.meta["auto_paused_reason"]


def test_telegram_hero_regen_branches_use_the_helper() -> None:
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app" / "telegram" / "bot.py").read_text(encoding="utf-8")
    start = src.index("regen_step_msg = \"\"")
    block = src[start:src.index("if decided_for_mirror is not None", start)]
    regen_blocks = re.findall(r'if action == "regen":(.*?)(?=\n\s+elif action == "approve":)', block, re.S)
    assert len(regen_blocks) == 2
    for b in regen_blocks:
        assert "request_hero_regen" in b and "project.status = ProjectStatus.generating_hero" not in b
