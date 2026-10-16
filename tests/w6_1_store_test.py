from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services import publications_store as ps
from tests.w6_1_support import PLATFORMS, Env

SHA, CSHA = "a" * 64, "c" * 64


@pytest.fixture
async def env():
    e = await Env().create()
    yield e
    await e.close()


@pytest.fixture
async def db(env):
    async with env.maker() as s:
        yield s


def _row(**kw):
    base = {"id": 1, "project_id": 1, "platform": "tiktok", "sha256": SHA, "status": ps.S_PLANNED, "post_url": None,
            "post_id": None, "attempts": 1, "error_code": None, "lease_until": None, "clicked_at": None,
            "artifact_uuid": "u", "account_id": None, "screenshot": None, "lease_owner": None}
    return {**base, **kw}




@pytest.mark.parametrize("row,expected", [
    (_row(status=ps.S_SKIPPED), ps.D_SKIPPED),
    (_row(status=ps.S_PLANNED), ps.D_INTENT),
    (_row(status=ps.S_CLICKED, clicked_at=1.0), ps.D_UNCONFIRMED),
    (_row(status=ps.S_UNCONFIRMED, clicked_at=1.0, post_url="https://up"), ps.D_UNCONFIRMED),
    (_row(status=ps.S_CONFIRMED, clicked_at=1.0, post_url="https://p/1"), ps.D_CONFIRMED),
    (_row(status=ps.S_CONFIRMED, clicked_at=1.0, post_id="77"), ps.D_CONFIRMED),
    (_row(status=ps.S_CONFIRMED, clicked_at=1.0), ps.D_UNCONFIRMED),
    (_row(status=ps.S_FAILED), ps.D_FAILED_BEFORE),
    (_row(status=ps.S_FAILED, clicked_at=1.0), ps.D_FAILED_AFTER),
    (_row(status=ps.S_REJECTED, clicked_at=1.0), ps.D_FAILED_AFTER),
])
def test_display_state_covers_all_six(row, expected):
    assert ps.display_state(row) == expected
    assert expected in ps.DISPLAY_LABELS_RU




async def test_approval_is_consumed_exactly_once(db):
    await ps.grant_approval(db, project_id=1, platform="tiktok", sha256=SHA, caption_sha256=CSHA, approved_by="op")
    assert (await ps.list_approvals(db, 1, SHA, CSHA))["tiktok"]["valid"] is True
    assert await ps.consume_approval(db, project_id=1, platform="tiktok", sha256=SHA, caption_sha256=CSHA) is True
    assert await ps.consume_approval(db, project_id=1, platform="tiktok", sha256=SHA, caption_sha256=CSHA) is False
    assert (await ps.list_approvals(db, 1, SHA, CSHA))["tiktok"]["valid"] is False


async def test_approval_does_not_cover_other_file_other_caption_other_platform(db):
    await ps.grant_approval(db, project_id=1, platform="tiktok", sha256=SHA, caption_sha256=CSHA, approved_by="op")
    assert await ps.consume_approval(db, project_id=1, platform="tiktok", sha256="b" * 64, caption_sha256=CSHA) is False
    assert await ps.consume_approval(db, project_id=1, platform="tiktok", sha256=SHA, caption_sha256="d" * 64) is False
    assert await ps.consume_approval(db, project_id=1, platform="vk_clips", sha256=SHA, caption_sha256=CSHA) is False
    assert (await ps.list_approvals(db, 1, SHA, "d" * 64))["tiktok"]["valid"] is False
    assert await ps.consume_approval(db, project_id=1, platform="tiktok", sha256=SHA, caption_sha256=CSHA) is True


async def test_grant_refused_on_confirmed_and_on_possible_click(db):
    c = await ps.claim(db, project_id=1, platform="tiktok", sha256=SHA, artifact_uuid="u", owner="w")
    await ps.mark_clicked(db, c.pub_id, "w")
    with pytest.raises(ps.PublicationRefused) as e:
        await ps.grant_approval(db, project_id=1, platform="tiktok", sha256=SHA, caption_sha256=CSHA, approved_by="op")
    assert e.value.code == "needs_human_first"
    await ps.record_result(db, c.pub_id, "w", status=ps.S_CONFIRMED, post_url="https://p/1")
    with pytest.raises(ps.PublicationRefused) as e:
        await ps.grant_approval(db, project_id=1, platform="tiktok", sha256=SHA, caption_sha256=CSHA, approved_by="op")
    assert e.value.code == "already_confirmed"




async def _unconfirmed(db, platform="tiktok", url="https://upload.page"):
    c = await ps.claim(db, project_id=1, platform=platform, sha256=SHA, artifact_uuid="u", owner="w")
    await ps.mark_clicked(db, c.pub_id, "w")
    await ps.record_result(db, c.pub_id, "w", status=ps.S_UNCONFIRMED, post_url=url)
    return c.pub_id


async def test_confirm_manual_needs_url_or_reason(db):
    await _unconfirmed(db)
    for kw in ({}, {"reason": "ок"}, {"reason": "   "}):
        with pytest.raises(ps.PublicationRefused) as e:
            await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="operator", **kw)
        assert e.value.code == "evidence_required"
    with pytest.raises(ps.PublicationRefused) as e:
        await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="operator", post_url="javascript:1")
    assert e.value.code == "bad_post_url"
    assert (await ps.list_for(db, 1, SHA))[0]["status"] == ps.S_UNCONFIRMED
    assert await ps.list_manual_actions(db, 1) == []


async def test_confirm_manual_with_url_makes_confirmed_and_logs_actor(db):
    await _unconfirmed(db)
    res = await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="operator:Иван",
                                  post_url="https://tiktok.com/@a/video/1", now=100.0)
    assert res["changed"] is True and res["state"] == ps.D_CONFIRMED and res["prev_status"] == ps.S_UNCONFIRMED
    row = (await ps.list_for(db, 1, SHA))[0]
    assert (row["status"], row["post_url"]) == (ps.S_CONFIRMED, "https://tiktok.com/@a/video/1")
    assert ps.display_state(row) == ps.D_CONFIRMED
    [act] = await ps.list_manual_actions(db, 1)
    assert (act["action"], act["actor"], act["prev_status"], act["platform"]) == ("confirm", "operator:Иван", "unconfirmed", "tiktok")


async def test_confirm_manual_with_reason_only_does_not_keep_upload_page_as_post_url(db):
    await _unconfirmed(db, url="https://upload.page")
    await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="operator",
                            reason="пост виден в кабинете, ссылку не дали")
    row = (await ps.list_for(db, 1, SHA))[0]
    assert row["status"] == ps.S_CONFIRMED and row["post_url"] is None and row["post_id"] == ps.MANUAL_CONFIRMED_ID
    assert ps.display_state(row) == ps.D_CONFIRMED
    assert ps.summarize([row], ["tiktok"])["all_confirmed"] is True


async def test_confirm_manual_is_idempotent_and_never_allows_a_second_click(db):
    await _unconfirmed(db)
    first = await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", post_url="https://p/1")
    again = await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", post_url="https://p/2")
    assert first["changed"] is True and again["changed"] is False
    assert len(await ps.list_manual_actions(db, 1)) == 1
    assert (await ps.list_for(db, 1, SHA))[0]["post_url"] == "https://p/1"
    assert (await ps.claim(db, project_id=1, platform="tiktok", sha256=SHA, artifact_uuid="u", owner="w2")).outcome == ps.DONE


async def test_manual_actions_refused_for_missing_record_and_live_lease(db):
    with pytest.raises(ps.PublicationRefused) as e:
        await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", post_url="https://p/1")
    assert e.value.code == "no_publication_record"
    await ps.claim(db, project_id=1, platform="tiktok", sha256=SHA, artifact_uuid="u", owner="w")
    for call in (ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", post_url="https://p/1"),
                 ps.mark_failed_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", reason="проверил, нет")):
        with pytest.raises(ps.PublicationRefused) as e:
            await call
        assert e.value.code == "busy"


async def test_confirm_manual_works_for_a_crash_after_click(db):
    c = await ps.claim(db, project_id=1, platform="tiktok", sha256=SHA, artifact_uuid="u", owner="w")
    await ps.mark_clicked(db, c.pub_id, "w")
    assert (await ps.list_for(db, 1, SHA))[0]["status"] == ps.S_CLICKED
    await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", reason="виден в кабинете")
    assert (await ps.list_for(db, 1, SHA))[0]["status"] == ps.S_CONFIRMED




async def test_mark_failed_requires_reason_and_closes_without_republish(db):
    await _unconfirmed(db)
    with pytest.raises(ps.PublicationRefused) as e:
        await ps.mark_failed_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", reason="")
    assert e.value.code == "reason_required"
    res = await ps.mark_failed_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op",
                                      reason="площадка показывает пустой кабинет")
    assert res["changed"] is True and res["allow_republish"] is False
    row = (await ps.list_for(db, 1, SHA))[0]
    assert row["status"] == ps.S_FAILED and row["error_code"] == ps.MANUAL_FAILED_CODE
    assert ps.display_state(row) == ps.D_FAILED_AFTER
    assert (await ps.claim(db, project_id=1, platform="tiktok", sha256=SHA, artifact_uuid="u", owner="w2")).outcome == ps.NEEDS_HUMAN
    assert ps.summarize([row], ["tiktok"])["manual_failed"] == ["tiktok"]
    [act] = await ps.list_manual_actions(db, 1)
    assert act["action"] == "mark_failed" and act["reason"].startswith("площадка")


async def test_mark_failed_with_republish_reopens_but_only_via_new_approval(db):
    await _unconfirmed(db)
    await ps.mark_failed_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op",
                                reason="поста нет, можно повторить", allow_republish=True)
    row = (await ps.list_for(db, 1, SHA))[0]
    assert row["clicked_at"] is None and row["attempts"] == 0 and ps.display_state(row) == ps.D_FAILED_BEFORE
    state = ps.derive_project_state([row], ["tiktok"], approved=set())
    assert state["state"] == ps.PS_AWAITING_APPROVAL
    await ps.grant_approval(db, project_id=1, platform="tiktok", sha256=SHA, caption_sha256=CSHA, approved_by="op")
    assert ps.derive_project_state([row], ["tiktok"], approved={"tiktok"})["state"] == ps.PS_WORKING


async def test_mark_failed_refused_on_confirmed(db):
    await _unconfirmed(db)
    await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", post_url="https://p/1")
    with pytest.raises(ps.PublicationRefused) as e:
        await ps.mark_failed_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", reason="передумал совсем")
    assert e.value.code == "already_confirmed"


async def test_manual_actions_log_is_append_only(db):
    await _unconfirmed(db)
    await ps.confirm_manual(db, project_id=1, platform="tiktok", sha256=SHA, actor="op", post_url="https://p/1")
    for sql in ("UPDATE publication_manual_actions SET actor='x'", "DELETE FROM publication_manual_actions"):
        with pytest.raises(Exception, match="append-only"):
            await db.execute(text(sql))
        await db.rollback()


def test_publications_ddl_for_migration_0012_is_unchanged():
    joined = " ".join(ps._DDL)
    assert "publish_approvals" not in joined and "publication_manual_actions" not in joined
    assert len(ps._DDL) == 3




def _rows(spec: dict[str, dict]):
    return [_row(id=i, platform=p, **kw) for i, (p, kw) in enumerate(spec.items(), 1)]


def _conf(**kw):
    return {"status": ps.S_CONFIRMED, "clicked_at": 1.0, "post_url": "https://p", **kw}


@pytest.mark.parametrize("spec,approved,expected", [
    ({p: _conf() for p in PLATFORMS}, set(), ps.PS_PUBLISHED),
    ({**{p: _conf() for p in PLATFORMS[1:]}, PLATFORMS[0]: {"status": ps.S_UNCONFIRMED, "clicked_at": 1.0}}, set(), ps.PS_NEEDS_HUMAN),
    ({**{p: _conf() for p in PLATFORMS[1:]}, PLATFORMS[0]: {"status": ps.S_FAILED, "clicked_at": 1.0}}, set(), ps.PS_NEEDS_HUMAN),
    ({**{p: _conf() for p in PLATFORMS[1:]}, PLATFORMS[0]: {"status": ps.S_CLICKED, "clicked_at": 1.0}}, set(), ps.PS_NEEDS_HUMAN),
    ({**{p: _conf() for p in PLATFORMS[1:]}, PLATFORMS[0]: {"status": ps.S_FAILED, "attempts": 1}}, set(), ps.PS_AWAITING_APPROVAL),
    ({**{p: _conf() for p in PLATFORMS[1:]}, PLATFORMS[0]: {"status": ps.S_FAILED, "attempts": 1}}, {PLATFORMS[0]}, ps.PS_WORKING),
    ({**{p: _conf() for p in PLATFORMS[1:]}, PLATFORMS[0]: {"status": ps.S_FAILED, "attempts": ps.MAX_PRE_CLICK_ATTEMPTS}}, {PLATFORMS[0]}, ps.PS_NEEDS_HUMAN),
    ({p: {"status": ps.S_SKIPPED, "attempts": 0} for p in PLATFORMS}, set(), ps.PS_AWAITING_APPROVAL),
    ({**{p: _conf() for p in PLATFORMS[1:]},
      PLATFORMS[0]: {"status": ps.S_FAILED, "clicked_at": 1.0, "attempts": 3, "error_code": ps.MANUAL_FAILED_CODE}}, set(), ps.PS_CLOSED),
    ({PLATFORMS[0]: {"status": ps.S_UNCONFIRMED, "clicked_at": 1.0}, **{p: _conf() for p in PLATFORMS[2:]}}, set(), ps.PS_NEEDS_HUMAN),
])
def test_project_state_is_derived_from_records(spec, approved, expected):
    rows = _rows(spec)
    assert ps.derive_project_state(rows, PLATFORMS, approved)["state"] == expected


def test_project_state_without_any_records_awaits_approval_and_busy_is_working():
    assert ps.derive_project_state([], PLATFORMS, set())["state"] == ps.PS_AWAITING_APPROVAL
    busy = _rows({PLATFORMS[0]: {"status": ps.S_PLANNED, "lease_until": 10**12}})
    assert ps.derive_project_state(busy, PLATFORMS, set())["state"] == ps.PS_WORKING


def test_publish_off_never_reports_publishing_for_an_approved_platform():
    rows = _rows({p: {"status": ps.S_FAILED, "attempts": 1} for p in PLATFORMS})
    approved = {PLATFORMS[0]}
    assert ps.derive_project_state(rows, PLATFORMS, approved)["state"] == ps.PS_WORKING
    assert ps.derive_project_state(rows, PLATFORMS, approved, publish_enabled=True)["state"] == ps.PS_WORKING
    off = ps.derive_project_state(rows, PLATFORMS, approved, publish_enabled=False)
    assert off["state"] == ps.PS_AWAITING_APPROVAL
    assert off["retry_approved"] == [] and off["approved_publish_off"] == [PLATFORMS[0]]
    assert PLATFORMS[0] not in off["awaiting_approval"] and set(off["awaiting_approval"]) == set(PLATFORMS[1:])
    only = ps.derive_project_state(rows[:1], PLATFORMS[:1], approved, publish_enabled=False)
    assert only["state"] == ps.PS_AWAITING_APPROVAL and only["awaiting_approval"] == []


def test_publish_off_keeps_busy_needs_human_and_published_states():
    busy = _rows({PLATFORMS[0]: {"status": ps.S_PLANNED, "lease_until": 10**12}})
    assert ps.derive_project_state(busy, PLATFORMS, set(), publish_enabled=False)["state"] == ps.PS_WORKING
    human = _rows({PLATFORMS[0]: {"status": ps.S_UNCONFIRMED, "clicked_at": 1.0}})
    assert ps.derive_project_state(human, PLATFORMS, {PLATFORMS[1]}, publish_enabled=False)["state"] == ps.PS_NEEDS_HUMAN
    done = _rows({p: _conf() for p in PLATFORMS})
    assert ps.derive_project_state(done, PLATFORMS, set(), publish_enabled=False)["state"] == ps.PS_PUBLISHED


def test_legacy_meta_results_are_not_retried():
    st = ps.derive_project_state([], PLATFORMS, set(), legacy={PLATFORMS[0]: True, PLATFORMS[1]: False})
    assert PLATFORMS[0] in st["confirmed"] and PLATFORMS[1] in st["needs_human"]
    assert st["state"] == ps.PS_NEEDS_HUMAN
