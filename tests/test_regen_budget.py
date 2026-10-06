"""Caps on regeneration loops: post-step validation and the vision check loop stop honestly."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import settings as app_settings
from app.models import Project, ProjectStatus
from app.services import post_step_validate as psv
from app.services import regen_budget
from app.services import vision_check_loop as vcl
from app.services.excel_gpt_node import upload_dir


@pytest.fixture(autouse=True)
def _caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings.settings, "regen_max_per_frame", 3)
    monkeypatch.setattr(app_settings.settings, "regen_max_per_run", 12)
    # the money cap has its own tests below; the attempt-cap tests run with it off
    monkeypatch.setattr(app_settings.settings, "regen_max_usd_per_run", 0.0)
    monkeypatch.setattr(app_settings.settings, "regen_estimate_usd_per_item", 0.0)


def _project(slug: str = "rb", status: ProjectStatus = ProjectStatus.generating_videos) -> Project:
    return Project(slug=slug, topic="t", status=status, hero_mode="no_hero", meta={})


class _Sess:
    async def flush(self):
        return None

    async def execute(self, *_a, **_k):
        class _R:
            def scalars(self):
                return SimpleNamespace(all=lambda: [])

        return _R()

    async def delete(self, *_a, **_k):
        return None


def test_per_frame_cap_blocks_the_fourth_regeneration_of_a_frame() -> None:
    p = _project()
    for _ in range(3):
        assert regen_budget.charge(p, "video", [4, 5]).ok
    v = regen_budget.charge(p, "video", [4, 6])
    assert not v.ok and v.blocked == ["4"] and not v.run_exhausted
    # a refused round is not counted
    assert p.meta["regen_budget"]["video"]["frames"] == {"4": 3, "5": 3}
    assert p.meta["regen_budget"]["video"]["rounds"] == 3
    assert "перегенерировали 3" in v.summary()


def test_per_run_cap_counts_rounds_even_without_frame_numbers() -> None:
    p = _project()
    for _ in range(12):
        assert regen_budget.charge(p, "music").ok
    v = regen_budget.charge(p, "music")
    assert v.run_exhausted and not v.ok
    assert "12 из 12" in v.summary()


def test_zero_disables_a_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings.settings, "regen_max_per_frame", 0)
    monkeypatch.setattr(app_settings.settings, "regen_max_per_run", 0)
    p = _project()
    for _ in range(40):
        assert regen_budget.charge(p, "video", [1]).ok


def test_kinds_are_counted_separately_and_clear_forgets_one_step() -> None:
    p = _project()
    for _ in range(3):
        regen_budget.charge(p, "video", [1])
    assert regen_budget.charge(p, "images", [1]).ok
    assert regen_budget.clear(p, "video") is True
    assert regen_budget.clear(p, "video") is False
    assert "video" not in p.meta["regen_budget"] and "images" in p.meta["regen_budget"]


def test_clear_vision_prefix_covers_every_vision_kind() -> None:
    p = _project()
    regen_budget.charge(p, "vision:scenes", ["f1"])
    regen_budget.charge(p, "vision:hero", ["c01"])
    regen_budget.charge(p, "video", [1])
    assert regen_budget.clear(p, "vision")
    assert list(p.meta["regen_budget"]) == ["video"]


def test_stop_parks_the_project_with_a_reason_and_reset_releases_it() -> None:
    p = _project()
    for _ in range(3):
        regen_budget.charge(p, "video", [2])
    v = regen_budget.charge(p, "video", [2])
    asyncio.run(regen_budget.stop(_Sess(), p, v))
    assert p.status is ProjectStatus.paused
    assert "автоповтор остановлен" in p.meta["auto_paused_reason"]
    assert p.meta["regen_budget_stop"]["blocked"] == ["2"]
    assert regen_budget.reset(p) is True
    assert "auto_paused_reason" not in p.meta and "regen_budget" not in p.meta
    assert regen_budget.charge(p, "video", [2]).ok


def _stub_validation(monkeypatch: pytest.MonkeyPatch, results: list[psv.ValidationResult]):
    queued: list[list[int]] = []

    async def validate(session, project):
        return results.pop(0) if results else results_last[0]

    results_last = [psv.ValidationResult(ok=False, expected_frames=3, missing_frame_numbers=[2])]

    async def regen(session, project, numbers):
        queued.append(list(numbers))
        return 1

    monkeypatch.setattr(psv, "validate_after_videos", validate)
    monkeypatch.setattr(psv, "mark_frames_for_video_regen", regen)
    return queued


def test_finalize_or_retry_stops_after_the_cap_instead_of_looping(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _project()
    queued = _stub_validation(monkeypatch, [])

    async def run() -> list[bool]:
        out = []
        for _ in range(3):
            out.append(await psv.finalize_or_retry(
                _Sess(), p, step="video", ready_status=ProjectStatus.videos_ready,
                running_status=ProjectStatus.generating_videos))
            assert p.status is ProjectStatus.generating_videos  # still looping, as before
        out.append(await psv.finalize_or_retry(
            _Sess(), p, step="video", ready_status=ProjectStatus.videos_ready,
            running_status=ProjectStatus.generating_videos))
        return out

    assert asyncio.run(run()) == [False, False, False, False]
    assert queued == [[2], [2], [2]]  # the fourth round requeued nothing
    assert p.status is ProjectStatus.paused
    assert "видео" in p.meta["auto_paused_reason"]


def test_finalize_or_retry_success_clears_the_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _project()
    good = psv.ValidationResult(ok=True, expected_frames=3)
    bad = psv.ValidationResult(ok=False, expected_frames=3, missing_frame_numbers=[2])
    queued = _stub_validation(monkeypatch, [bad, bad, good, bad, bad, bad])

    async def step() -> bool:
        return await psv.finalize_or_retry(
            _Sess(), p, step="video", ready_status=ProjectStatus.videos_ready,
            running_status=ProjectStatus.generating_videos)

    results = [asyncio.run(step()) for _ in range(6)]
    assert results == [False, False, True, False, False, False]
    assert queued == [[2]] * 5  # a finished step starts the next run with a fresh budget
    assert p.status is ProjectStatus.generating_videos


def _vision_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, check_key: str) -> Project:
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project("vb", ProjectStatus.enrich_2_ready)
    p.meta = {
        "excel_gpt_nodes": {check_key: {"checkMode": True, "checkFix": False, "slotIndex": 2}},
        "gpt_operator_results": {check_key: {"gateStatus": "fail"}},
        "canvas_graph": {
            "nodes": [
                {"id": "n_img", "type": "images"},
                {"id": check_key, "type": "excel_gpt", "data": {"slotIndex": 2, "checkMode": True}},
            ],
            "edges": [{"source": "n_img", "target": check_key, "data": {"kind": "after"}}],
        },
    }
    p.data_dir.mkdir(parents=True, exist_ok=True)
    out = upload_dir(p, check_key)
    out.mkdir(parents=True, exist_ok=True)
    (out / "check_report.txt").write_text("# ОТЧЁТ ПРОВЕРКИ\nverdict: fail\n\nframes: 3\n", encoding="utf-8")

    async def _fake_prepare(*_a, **_k):
        return True

    monkeypatch.setattr("app.services.run_sync.prepare_node_for_step_start", _fake_prepare)
    return p


def test_vision_loop_stops_with_a_pause_when_a_frame_keeps_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    check_key = "n_check_img"
    p = _vision_project(tmp_path, monkeypatch, check_key)

    def one_round() -> bool:
        return asyncio.run(vcl.maybe_start_vision_check_loop_after_check(_Sess(), p, check_key))

    for n in (1, 2, 3):
        assert one_round() is True
        assert p.status is ProjectStatus.generating_images, n
        assert p.meta["vision_check_round"] == n
    assert one_round() is True  # stopped, not started: the caller must not chain on
    assert p.status is ProjectStatus.paused
    assert "f3" in p.meta["auto_paused_reason"] and "автоповтор остановлен" in p.meta["auto_paused_reason"]
    assert not vcl.vision_check_loop_active(p)  # loop meta cleared, nothing silently "passed"
    assert p.meta["regen_budget"]["vision:scenes"]["frames"] == {"f3": 3}


def test_vision_loop_hard_round_ceiling_also_stops_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings.settings, "regen_max_per_frame", 0)
    monkeypatch.setattr(app_settings.settings, "regen_max_per_run", 0)
    check_key = "n_check_img"
    p = _vision_project(tmp_path, monkeypatch, check_key)
    p.meta["vision_check_round"] = vcl.MAX_VISION_CHECK_ROUNDS
    started = asyncio.run(vcl.maybe_start_vision_check_loop_after_check(_Sess(), p, check_key))
    assert started is True
    assert p.status is ProjectStatus.paused
    assert "лимит перегенераций на прогон" in p.meta["auto_paused_reason"]


def test_vision_pass_resets_the_vision_counters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check_key = "n_check_img"
    p = _vision_project(tmp_path, monkeypatch, check_key)
    assert asyncio.run(vcl.maybe_start_vision_check_loop_after_check(_Sess(), p, check_key)) is True
    assert "vision:scenes" in p.meta["regen_budget"]
    out = upload_dir(p, check_key)
    (out / "check_report.txt").write_text(
        "# ОТЧЁТ ПРОВЕРКИ\nverdict: pass\n\n## summary\nвсё хорошо\n\n## findings\n- [ok] frame_003_x.png: ок\n",
        encoding="utf-8")
    p.meta["gpt_operator_results"][check_key] = {
        "gateStatus": "pass", "analysis": {"schema": "vp.check.v1", "verdict": "pass"}}
    assert asyncio.run(vcl.maybe_start_vision_check_loop_after_check(_Sess(), p, check_key)) is False
    assert "regen_budget" not in p.meta


def test_diagnosis_shows_the_stop_as_a_human_review_incident() -> None:
    from app.services.diagnosis import derive_incidents

    scope = SimpleNamespace(execution_id="execution", project_id=2, node_keys=["img"], observer_state="active", gaps=[])
    row = dict(id=1, event_id="e1", execution_id="execution", project_id=2, node_key="img", attempt_id="a1",
               request_id=None, kind="runtime_check", db_scope="project", created_at="2026-10-06 12:00:00",
               payload={"check": "regen_budget", "performed": True, "status": "rejected",
                        "summary": "видео: 2 уже перегенерировали 3 раз(а); автоповтор остановлен, нужен человек"})
    [item] = derive_incidents(scope, [row], {})
    assert item["type"] == "contract_rejected" and item["status"] == "human_review"
    assert "автоповтор остановлен" in item["fact"]
    assert item["recommendation"]["action"] == "human_review"


# -- money cap -----------------------------------------------------------------

def _money(monkeypatch: pytest.MonkeyPatch, cap: float, unit: float = 0.5) -> None:
    monkeypatch.setattr(app_settings.settings, "regen_max_usd_per_run", cap)
    monkeypatch.setattr(app_settings.settings, "regen_estimate_usd_per_item", unit)


def test_money_cap_stops_a_loop_that_attempt_counters_would_still_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    _money(monkeypatch, cap=3.0, unit=0.5)
    p = _project()
    frames = [1, 2, 3, 4]  # $2 a round: well inside 3 attempts per frame and 12 per run
    assert regen_budget.charge(p, "video", frames).ok
    v = regen_budget.charge(p, "video", frames)
    assert v.usd_exhausted and not v.ok and not v.run_exhausted
    assert v.usd_spent == 2.0 and v.usd_round == 2.0 and v.usd_cap == 3.0 and v.blocked == ["1", "2", "3", "4"]
    # a refused round changes nothing
    assert p.meta["regen_budget"]["video"]["usd"] == 2.0 and p.meta["regen_budget"]["video"]["rounds"] == 1
    assert "денежный лимит" in v.summary() and "$3.00" in v.summary()


def test_money_is_summed_over_rounds_and_a_cheaper_round_may_still_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    _money(monkeypatch, cap=3.0, unit=0.5)
    p = _project()
    assert regen_budget.charge(p, "video", [1, 2, 3, 4]).ok  # 2.0
    assert regen_budget.charge(p, "video", [5, 6]).ok        # 3.0, exactly at the cap is allowed
    assert not regen_budget.charge(p, "video", [7]).ok       # 3.5 > 3.0
    assert p.meta["regen_budget"]["video"]["usd"] == 3.0


def test_first_round_is_never_refused_by_money(monkeypatch: pytest.MonkeyPatch) -> None:
    _money(monkeypatch, cap=1.0, unit=0.5)
    p = _project()
    big = regen_budget.charge(p, "images", list(range(1, 11)))  # $5 > $1, but nothing was spent yet
    assert big.ok and p.meta["regen_budget"]["images"]["usd"] == 5.0
    assert regen_budget.charge(p, "images", [1]).usd_exhausted


def test_zero_disables_the_money_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _money(monkeypatch, cap=0.0, unit=100.0)
    p = _project()
    monkeypatch.setattr(app_settings.settings, "regen_max_per_frame", 0)
    monkeypatch.setattr(app_settings.settings, "regen_max_per_run", 0)
    for _ in range(20):
        assert regen_budget.charge(p, "video", [1, 2]).ok


def test_unit_defaults_to_the_spend_guard_media_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import spend_guard

    _money(monkeypatch, cap=100.0, unit=0.0)
    assert regen_budget.estimate_round_usd("video", 2) == round(2 * spend_guard.MEDIA_SUBMIT_ESTIMATE_USD, 4)
    assert regen_budget.estimate_round_usd("music", 0) == spend_guard.MEDIA_SUBMIT_ESTIMATE_USD
    assert regen_budget.estimate_round_usd("vision:scenes", 1) == round(
        spend_guard.MEDIA_SUBMIT_ESTIMATE_USD + spend_guard.DEFAULT_REQUEST_ESTIMATE_USD, 4)


def test_money_buckets_are_per_step_clear_and_reset_forget_them(monkeypatch: pytest.MonkeyPatch) -> None:
    _money(monkeypatch, cap=1.0, unit=0.5)
    p = _project()
    regen_budget.charge(p, "video", [1, 2])
    assert not regen_budget.charge(p, "video", [1]).ok
    assert regen_budget.charge(p, "images", [1]).ok  # another step has its own budget
    assert regen_budget.clear(p, "video")
    assert regen_budget.charge(p, "video", [1]).ok


def test_money_stop_parks_the_project_and_writes_the_fact(monkeypatch: pytest.MonkeyPatch) -> None:
    _money(monkeypatch, cap=1.0, unit=0.5)
    facts: list[tuple[str, dict]] = []

    async def fake_record(kind, payload, *a, **k):
        facts.append((kind, payload))

    monkeypatch.setattr("app.services.observation.record_fact", fake_record)
    p = _project()
    regen_budget.charge(p, "video", [1, 2])
    v = regen_budget.charge(p, "video", [3])
    asyncio.run(regen_budget.stop(_Sess(), p, v))
    assert p.status is ProjectStatus.paused
    assert "денежный лимит" in p.meta["auto_paused_reason"]
    stop = p.meta["regen_budget_stop"]
    assert stop["usd_exhausted"] is True and stop["usd_spent"] == 1.0 and stop["usd_cap"] == 1.0
    [(kind, payload)] = facts
    assert kind == "runtime_check" and payload["check"] == "regen_budget" and payload["status"] == "rejected"
    assert payload["usd_exhausted"] is True and payload["usd_cap"] == 1.0
    assert regen_budget.reset(p) is True
    assert "auto_paused_reason" not in p.meta and regen_budget.charge(p, "video", [3]).ok


def test_finalize_or_retry_stops_on_money_before_the_attempt_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _money(monkeypatch, cap=0.7, unit=0.5)
    p = _project()
    queued = _stub_validation(monkeypatch, [])

    async def run() -> list[bool]:
        return [await psv.finalize_or_retry(
            _Sess(), p, step="video", ready_status=ProjectStatus.videos_ready,
            running_status=ProjectStatus.generating_videos) for _ in range(3)]

    assert asyncio.run(run()) == [False, False, False]
    assert queued == [[2]]  # one regeneration, the second would be $1.00 > $0.70
    assert p.status is ProjectStatus.paused and "денежный лимит" in p.meta["auto_paused_reason"]


def test_diagnosis_shows_the_money_stop() -> None:
    from app.services.diagnosis import derive_incidents

    v = regen_budget.Verdict(kind="video", usd_exhausted=True, usd_spent=2.0, usd_round=1.0, usd_cap=2.5)
    scope = SimpleNamespace(execution_id="execution", project_id=2, node_keys=["img"], observer_state="active", gaps=[])
    row = dict(id=1, event_id="e1", execution_id="execution", project_id=2, node_key="img", attempt_id="a1",
               request_id=None, kind="runtime_check", db_scope="project", created_at="2026-10-06 12:00:00",
               payload={"check": "regen_budget", "performed": True, "status": "rejected", "usd_exhausted": True,
                        "summary": v.summary()})
    [item] = derive_incidents(scope, [row], {})
    assert item["type"] == "contract_rejected" and item["status"] == "human_review"
    assert "денежный лимит" in item["fact"] and "$2.50" in item["fact"]
