"""Smoke + unit тесты на scripts/triage_prs.py (Phase H).

Не дёргает gh CLI — мокаем fetch_prs и проверяем логику suggestion'ов.
"""

from __future__ import annotations

from scripts.triage_prs import PRInfo


def _make_pr(**kwargs) -> PRInfo:
    defaults = dict(
        number=1,
        title="example",
        branch="devin/example",
        base="vetka-final",
        state="OPEN",
        author="someone",
        created_at="2026-05-22",
        updated_at="2026-05-22",
        age_days=0,
        is_draft=False,
    )
    defaults.update(kwargs)
    return PRInfo(**defaults)


def test_suggest_close_stale_over_60_days() -> None:
    pr = _make_pr(age_days=90, title="old feature")
    action, reason = pr.suggestion()
    assert action == "close-as-stale"
    assert "60" in reason or "stale" in reason


def test_suggest_rebase_over_30_days() -> None:
    pr = _make_pr(age_days=45, title="new feature")
    action, _ = pr.suggestion()
    assert action == "rebase-or-close"


def test_suggest_close_video_403_as_superseded() -> None:
    pr = _make_pr(title="fix(video): 403 on download", age_days=4)
    action, reason = pr.suggestion()
    assert action == "close-as-superseded"
    assert "403" in reason or "canonical" in reason


def test_suggest_close_visual_lab() -> None:
    pr = _make_pr(title="feat(visual_lab): итеративный анализатор", age_days=7)
    action, _ = pr.suggestion()
    assert action == "close-as-superseded"


def test_suggest_close_mass_creation() -> None:
    pr = _make_pr(title="feat(mass-gen): strict sequential", age_days=6)
    action, _ = pr.suggestion()
    assert action == "close-as-superseded"


def test_suggest_close_manual_walk() -> None:
    pr = _make_pr(title="outsee: manual-walk download", age_days=6)
    action, _ = pr.suggestion()
    assert action == "close-as-superseded"


def test_suggest_close_per_frame_hitl() -> None:
    pr = _make_pr(title="feat(videos): per-frame HITL approval", age_days=3)
    action, _ = pr.suggestion()
    assert action == "close-as-superseded"


def test_suggest_review_recent_feature() -> None:
    pr = _make_pr(title="step 11: bgm assembly", age_days=9)
    action, _ = pr.suggestion()
    assert action == "review"


def test_suggest_review_dev_env_agents_md() -> None:
    """PR #37: другой AGENTS.md — нужно сверить с нашим."""
    pr = _make_pr(
        title="Add AGENTS.md with Cursor Cloud dev environment instructions",
        age_days=0,
    )
    action, reason = pr.suggestion()
    assert action == "review-then-merge-or-close"
    assert "AGENTS.md" in reason or "dev environment" in reason


def test_suggest_close_audit_branch_pr() -> None:
    pr = _make_pr(
        title="audit fixes",
        branch="cursor/audit-fix-buttons-71d2",
        age_days=0,
    )
    action, _ = pr.suggestion()
    # cursor/audit-* — должны идти под close-and-delete-branch
    assert action == "close-and-delete-branch"
