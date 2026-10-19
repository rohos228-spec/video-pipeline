from __future__ import annotations

import pytest

from app.models import ProjectStatus
from app.orchestrator.steps import publish as step
from app.services import publications_store as ps
from app.settings import settings
from tests.w6_1_support import PLATFORMS, Bot, Env, add_final, fake_registry, grant, make_project, sha_of


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




async def test_without_human_approval_nothing_is_clicked_or_opened(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch)
    p = await make_project(db, env.workdir)
    bot = Bot()
    await step.run(db, p, bot)
    assert clicks == [] and clicks.opened == []
    assert p.status is ProjectStatus.assembled
    assert set(p.meta["publish_awaiting_approval"]["platforms"]) == set(PLATFORMS)
    assert await ps.list_for(db, p.id) == []
    assert len(bot.sent) == 1 and "ждёт вашего подтверждения" in bot.sent[0]
    p.status = ProjectStatus.publishing
    await step.run(db, p, bot)
    assert clicks == [] and len(bot.sent) == 1 and p.status is ProjectStatus.assembled


async def test_auto_mode_and_hitl_auto_approve_do_not_create_the_approval(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch)
    monkeypatch.setattr(settings, "hitl_auto_approve", True)
    p = await make_project(db, env.workdir)
    p.auto_mode = True
    await step.run(db, p, Bot())
    assert clicks == [] and p.status is ProjectStatus.assembled
    assert await ps.list_approvals(db, p.id, await sha_of(db, p)) == {}


async def test_each_platform_needs_its_own_yes(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch)
    p = await make_project(db, env.workdir)
    await grant(db, p, [PLATFORMS[0], PLATFORMS[2]])
    await step.run(db, p, Bot())
    assert sorted(clicks) == sorted([PLATFORMS[0], PLATFORMS[2]])
    rows = {r["platform"]: r["status"] for r in await ps.list_for(db, p.id)}
    assert rows == {PLATFORMS[0]: ps.S_CONFIRMED, PLATFORMS[2]: ps.S_CONFIRMED}
    assert p.status is ProjectStatus.assembled
    assert set(p.meta["publish_awaiting_approval"]["platforms"]) == set(PLATFORMS) - {PLATFORMS[0], PLATFORMS[2]}


async def test_approval_is_spent_by_the_click_and_cannot_cause_a_second_one(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch, {PLATFORMS[0]: "unconfirmed"})
    p = await make_project(db, env.workdir)
    await grant(db, p, [PLATFORMS[0]])
    await step.run(db, p, Bot())
    assert clicks == [PLATFORMS[0]]
    sha = await sha_of(db, p)
    assert (await ps.list_approvals(db, p.id, sha))[PLATFORMS[0]]["consumed_at"] is not None
    p.status = ProjectStatus.publishing
    await step.run(db, p, Bot())
    assert clicks == [PLATFORMS[0]]
    with pytest.raises(ps.PublicationRefused) as e:
        await grant(db, p, [PLATFORMS[0]])
    assert e.value.code == "needs_human_first"


async def test_rebuilt_video_has_a_new_sha_and_needs_a_new_yes(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch)
    p = await make_project(db, env.workdir)
    await grant(db, p)
    await add_final(db, p, env.workdir, data=b"REBUILT-VIDEO", uuid="u-new")
    await step.run(db, p, Bot())
    assert clicks == [] and p.status is ProjectStatus.assembled
    assert p.meta["publish_awaiting_approval"]["artifact_uuid"] == "u-new"


async def test_changed_caption_invalidates_the_approval(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch)
    p = await make_project(db, env.workdir)
    await grant(db, p)
    p.topic = "другая тема, подпись изменилась"
    await step.run(db, p, Bot())
    assert clicks == [] and p.status is ProjectStatus.assembled


async def test_gate_refuses_click_if_the_approval_was_spent_by_someone_else(db, env):
    p = await make_project(db, env.workdir)
    await grant(db, p, [PLATFORMS[0]])
    sha = await sha_of(db, p)
    csha = step.caption_sha256(step.caption_for(p))
    gate = step._StoreGate(db, p.id, sha, "u-w61", csha)
    assert await gate.claim(PLATFORMS[0]) is True
    assert await ps.consume_approval(db, project_id=p.id, platform=PLATFORMS[0], sha256=sha, caption_sha256=csha)
    assert await gate.before_click(PLATFORMS[0]) is False
    assert gate.not_claimed[PLATFORMS[0]] == "no_human_approval"
    assert (await ps.list_for(db, p.id))[0]["clicked_at"] is None




async def test_all_confirmed_gives_published_and_clears_markers(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch)
    p = await make_project(db, env.workdir)
    p.meta = {"publish_awaiting_approval": {"platforms": ["x"]}}
    await grant(db, p)
    await step.run(db, p, Bot())
    assert sorted(clicks) == sorted(PLATFORMS) and p.status is ProjectStatus.published
    assert not any(k in p.meta for k in ("publish_awaiting_approval", "publish_needs_human", "publish_closed_by_human"))


async def test_unconfirmed_leaves_publishing_with_visible_way_out_through_manual_confirm(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch, {PLATFORMS[1]: "unconfirmed"})
    p = await make_project(db, env.workdir)
    await grant(db, p)
    bot = Bot()
    await step.run(db, p, bot)
    assert p.status is ProjectStatus.assembled and p.status is not ProjectStatus.published
    assert p.meta["publish_needs_human"]["platforms"] == [PLATFORMS[1]]
    assert any("НЕ ПОДТВЕРЖДЕНО" in t and "ждёт проверки человека" in t for t in bot.sent)
    n = len(clicks)
    sha = await sha_of(db, p)
    await ps.confirm_manual(db, project_id=p.id, platform=PLATFORMS[1], sha256=sha, actor="operator",
                            post_url="https://example.invalid/post/1")
    rows = await ps.list_for(db, p.id, sha)
    approvals = await ps.list_approvals(db, p.id, sha)
    st = await step.apply_project_state(db, p, rows=rows, platforms=PLATFORMS, sha=sha, artifact_uuid="u-w61",
                                        approved={k for k, v in approvals.items() if v["valid"]})
    assert st["state"] == ps.PS_PUBLISHED and p.status is ProjectStatus.published
    assert len(clicks) == n
    assert "publish_needs_human" not in p.meta


async def test_crash_after_click_waits_for_human_and_never_retries(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch, {PLATFORMS[0]: "crash"})
    p = await make_project(db, env.workdir)
    await grant(db, p)
    with pytest.raises(RuntimeError):
        await step.run(db, p, Bot())
    sha = await sha_of(db, p)
    assert {r["platform"]: r["status"] for r in await ps.list_for(db, p.id, sha)}[PLATFORMS[0]] == ps.S_CLICKED
    clicks.clear()
    await step.run(db, p, Bot())
    assert PLATFORMS[0] not in clicks
    assert p.status is ProjectStatus.assembled and p.meta["publish_needs_human"]["platforms"] == [PLATFORMS[0]]


async def test_failure_before_click_keeps_the_approval_and_retries_that_platform_only(db, env, monkeypatch):
    behaviors = {PLATFORMS[2]: "fail_pre"}
    clicks = fake_registry(monkeypatch, behaviors)
    p = await make_project(db, env.workdir)
    await grant(db, p)
    await step.run(db, p, Bot())
    assert p.status is ProjectStatus.publishing
    behaviors.clear()
    clicks.clear()
    await step.run(db, p, Bot())
    assert clicks == [PLATFORMS[2]] and p.status is ProjectStatus.published


async def test_manual_not_published_closes_the_project_out_of_publishing(db, env, monkeypatch):
    fake_registry(monkeypatch, {pl: "unconfirmed" for pl in PLATFORMS})
    p = await make_project(db, env.workdir)
    await grant(db, p)
    await step.run(db, p, Bot())
    sha = await sha_of(db, p)
    for pl in PLATFORMS:
        await ps.mark_failed_manual(db, project_id=p.id, platform=pl, sha256=sha, actor="operator",
                                    reason="проверил кабинет: поста нет")
    rows = await ps.list_for(db, p.id, sha)
    st = await step.apply_project_state(db, p, rows=rows, platforms=PLATFORMS, sha=sha, artifact_uuid="u-w61", approved=set())
    assert st["state"] == ps.PS_CLOSED and p.status is ProjectStatus.assembled
    assert set(p.meta["publish_closed_by_human"]["platforms"]) == set(PLATFORMS)
    assert p.status is not ProjectStatus.published




async def test_disabled_flag_still_gives_publish_skipped_and_never_published(db, env, monkeypatch):
    clicks = fake_registry(monkeypatch)
    monkeypatch.setattr(settings, "social_publish_enabled", False)
    p = await make_project(db, env.workdir)
    await grant(db, p)
    await step.run(db, p, Bot())
    assert clicks == [] and p.status is ProjectStatus.assembled and step.is_publish_skipped(p)
    assert {r["status"] for r in await ps.list_for(db, p.id)} == {ps.S_SKIPPED}
    assert {step.pstore.display_state(r) for r in await ps.list_for(db, p.id)} == {ps.D_SKIPPED}


async def test_hold_reason_follows_the_markers_and_the_current_file(db, env, monkeypatch):
    p = await make_project(db, env.workdir)
    assert await step.publish_hold_reason(db, p) is None
    p.meta = {"publish_awaiting_approval": {"artifact_uuid": "u-w61", "platforms": ["tiktok"]}}
    assert await step.publish_hold_reason(db, p) == "publish_awaiting_approval"
    p.meta = {"publish_needs_human": {"platforms": ["tiktok"]}}
    assert await step.publish_hold_reason(db, p) == "publish_needs_human"
    p.meta = {"publish_closed_by_human": {"artifact_uuid": "u-w61"}}
    assert await step.publish_hold_reason(db, p) == "publish_closed_by_human"
    await add_final(db, p, env.workdir, data=b"REBUILT", uuid="u-new")
    assert await step.publish_hold_reason(db, p) is None
    p.meta = {"publish_skipped": {"artifact_uuid": "u-new"}}
    monkeypatch.setattr(settings, "social_publish_enabled", False)
    assert await step.publish_hold_reason(db, p) == "publish_skipped"
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    assert await step.publish_hold_reason(db, p) is None




async def test_facts_show_sha_post_url_status_and_actions(db, env, monkeypatch):
    fake_registry(monkeypatch, {PLATFORMS[1]: "unconfirmed"})
    p = await make_project(db, env.workdir)
    await grant(db, p, PLATFORMS[:2])
    await step.run(db, p, Bot())
    facts = await step.publication_facts(db, p)
    sha = await sha_of(db, p)
    assert facts["sha256"] == sha and facts["requires_human_approval"] is True and facts["publish_enabled"] is True
    assert facts["video"]["url"] == f"/api/artifacts/u-w61/file?project_id={p.id}"
    assert facts["caption"] == "тема ролика" and len(facts["caption_sha256"]) == 64
    by = {x["platform"]: x for x in facts["platforms"]}
    assert by[PLATFORMS[0]]["state"] == ps.D_CONFIRMED and by[PLATFORMS[0]]["post_url"].startswith("https://")
    assert by[PLATFORMS[0]]["can_confirm_manual"] is False
    assert by[PLATFORMS[1]]["state"] == ps.D_UNCONFIRMED and by[PLATFORMS[1]]["can_confirm_manual"] is True
    assert by[PLATFORMS[1]]["can_mark_failed"] is True and by[PLATFORMS[1]]["can_approve"] is False
    assert by[PLATFORMS[2]]["state"] == "not_started" and by[PLATFORMS[2]]["can_approve"] is True
    assert by[PLATFORMS[2]]["can_confirm_manual"] is False
    assert facts["derived"]["state"] == ps.PS_NEEDS_HUMAN


async def test_facts_with_publish_off_do_not_report_publishing_for_an_approved_platform(db, env, monkeypatch):
    fake_registry(monkeypatch)
    p = await make_project(db, env.workdir)
    await grant(db, p, PLATFORMS[:1])
    monkeypatch.setattr(settings, "social_publish_enabled", False)
    facts = await step.publication_facts(db, p)
    assert facts["publish_enabled"] is False
    assert facts["derived"]["state"] == ps.PS_AWAITING_APPROVAL
    assert facts["derived"]["approved_publish_off"] == [PLATFORMS[0]] and facts["derived"]["retry_approved"] == []
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    on = await step.publication_facts(db, p)
    assert on["derived"]["state"] == ps.PS_WORKING and on["derived"]["approved_publish_off"] == []


async def test_facts_is_read_only_and_works_without_a_final_video(db, env):
    from app.models import Project

    p = Project(slug="nofinal", title="t", topic="x", status=ProjectStatus.assembled, meta={})
    db.add(p)
    await db.flush()
    facts = await step.publication_facts(db, p)
    assert facts["sha256"] is None and facts["video"] is None and facts["derived"] is None
    assert all(x["state"] == "not_started" and x["can_approve"] is False for x in facts["platforms"])
