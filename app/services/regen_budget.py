"""Caps on regeneration loops: a failed result may be regenerated a bounded number of times.

Two loops redo work without a human: the post-step validation (``finalize_or_retry``: missing video/image
files, music, audio) and the vision check loop (check -> db_patch -> regen -> check). Each regeneration
of a frame/hero is a paid provider call, so both loops charge this budget before they requeue anything:

* ``REGEN_MAX_PER_FRAME`` (default 3): regenerations of one frame (or hero) before it needs a person;
* ``REGEN_MAX_PER_RUN`` (default 12): regeneration rounds of one step until it succeeds or a person releases it.
* ``REGEN_MAX_USD_PER_RUN`` (default 5.0): estimated USD of the regeneration rounds of one step. The attempt caps
  do not know the price of a round (a round of 12 frames costs far more than a round of one), so every round
  adds ``items x unit`` to the step bucket, where ``unit`` is ``REGEN_ESTIMATE_USD_PER_ITEM`` or, when that is
  0, ``spend_guard.MEDIA_SUBMIT_ESTIMATE_USD`` (the same per-submit estimate the spend guard reserves; text-only
  vision checks add ``spend_guard.DEFAULT_REQUEST_ESTIMATE_USD`` per round). A round that would push a bucket
  that has already spent something over the cap is refused; the very first round of a bucket is never refused
  by money (nothing to compare with, and refusing it would deadlock the step).
  ``0`` disables the corresponding cap.

When a cap is reached the loop does not requeue and does not pass the result on: the project is parked
(``paused``) with ``auto_paused_reason`` (the same field the auto-advance regen limit uses), an
observation fact ``runtime_check``/``regen_budget`` (status ``rejected``) is written for the diagnosis layer,
and the counters stay in ``project.meta['regen_budget']`` until ``reset`` (a human release) or until the step
finishes successfully (``clear``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from loguru import logger
from sqlalchemy.orm.attributes import flag_modified

META_KEY = "regen_budget"
PAUSE_REASON_KEY = "auto_paused_reason"

_KIND_TITLES = {
    "video": "видео", "images": "картинки", "music": "музыка", "audio": "озвучка",
    "vision:scenes": "проверка кадров", "vision:videos": "проверка клипов", "vision:hero": "проверка героев",
}


@dataclass
class Verdict:
    kind: str
    allowed: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    run_exhausted: bool = False
    per_frame: int = 0
    per_run: int = 0
    rounds: int = 0
    usd_exhausted: bool = False
    usd_spent: float = 0.0
    usd_round: float = 0.0
    usd_cap: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.blocked and not self.run_exhausted and not self.usd_exhausted

    def summary(self) -> str:
        title = _KIND_TITLES.get(self.kind, self.kind)
        if self.usd_exhausted:
            return (f"{title}: исчерпан денежный лимит перегенераций (потрачено по оценке ${self.usd_spent:.2f}, "
                    f"ещё один раунд ${self.usd_round:.2f}, лимит ${self.usd_cap:.2f}); дальше решает человек")
        if self.run_exhausted:
            return (f"{title}: исчерпан лимит перегенераций на прогон ({self.rounds} из {self.per_run}); "
                    f"дальше решает человек")
        names = ", ".join(self.blocked[:12])
        return (f"{title}: {names} уже перегенерировали {self.per_frame} раз(а) и результат всё ещё не принят; "
                f"автоповтор остановлен, нужен человек")


def limits() -> tuple[int, int]:
    try:
        from app.settings import settings
        return int(settings.regen_max_per_frame), int(settings.regen_max_per_run)
    except Exception:  # noqa: BLE001
        return 3, 12


def usd_limits() -> tuple[float, float]:
    """(cap, unit): the money cap per step run (0 = off) and the estimated USD of one regenerated item."""
    cap, unit = 5.0, 0.0
    try:
        from app.settings import settings
        cap, unit = float(settings.regen_max_usd_per_run), float(settings.regen_estimate_usd_per_item)
    except Exception:  # noqa: BLE001
        pass
    if unit <= 0:
        from app.services import spend_guard
        unit = float(spend_guard.MEDIA_SUBMIT_ESTIMATE_USD)
    return max(0.0, cap), unit


def estimate_round_usd(kind: str, item_count: int) -> float:
    """Estimated USD of one regeneration round: paid submits for the items, plus one request for vision checks."""
    _cap, unit = usd_limits()
    usd = unit * max(0, int(item_count))
    if kind.startswith("vision"):
        from app.services import spend_guard
        usd += float(spend_guard.DEFAULT_REQUEST_ESTIMATE_USD)
    elif item_count <= 0:
        usd = unit  # a whole-step round without frame numbers (music, audio) is one paid submit
    return round(usd, 4)


def _state(project) -> dict:
    meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
    state = meta.get(META_KEY)
    return state if isinstance(state, dict) else {}


def _save(project, state: dict) -> None:
    meta = dict(project.meta or {})
    if state:
        meta[META_KEY] = state
    else:
        meta.pop(META_KEY, None)
    project.meta = meta
    flag_modified(project, "meta")


def charge(project, kind: str, items: Iterable[object] = ()) -> Verdict:
    """Count one regeneration round of ``kind`` for ``items`` (frame numbers, hero ids, tokens).

    Items already at the per-frame cap are returned in ``blocked``; a spent run budget blocks the round.
    The counters are only advanced when the whole round is allowed.
    """
    per_frame, per_run = limits()
    names = [str(i) for i in items]
    state = {k: dict(v) if isinstance(v, dict) else v for k, v in _state(project).items()}
    bucket = dict(state.get(kind) or {})
    frames = dict(bucket.get("frames") or {})
    rounds = int(bucket.get("rounds") or 0)
    usd_cap, _unit = usd_limits()
    spent = float(bucket.get("usd") or 0.0)
    round_usd = estimate_round_usd(kind, len(names))
    verdict = Verdict(kind=kind, per_frame=per_frame, per_run=per_run, rounds=rounds,
                      usd_spent=round(spent, 4), usd_round=round_usd, usd_cap=usd_cap)
    if per_run and rounds >= per_run:
        verdict.run_exhausted = True
        verdict.blocked = names
        return verdict
    if usd_cap and spent > 0 and spent + round_usd > usd_cap:
        verdict.usd_exhausted = True
        verdict.blocked = names
        return verdict
    for name in names:
        if per_frame and int(frames.get(name) or 0) >= per_frame:
            verdict.blocked.append(name)
        else:
            verdict.allowed.append(name)
    if verdict.blocked:
        return verdict
    for name in names:
        frames[name] = int(frames.get(name) or 0) + 1
    bucket.update(frames=frames, rounds=rounds + 1, usd=round(spent + round_usd, 4))
    state[kind] = bucket
    verdict.rounds = rounds + 1
    verdict.usd_spent = round(spent + round_usd, 4)
    _save(project, state)
    return verdict


def clear(project, kind_prefix: str | None = None) -> bool:
    """Forget the counters of a step that finished (or all kinds when no prefix is given)."""
    state = dict(_state(project))
    gone = [k for k in state if kind_prefix is None or k == kind_prefix or k.startswith(kind_prefix + ":")]
    if not gone:
        return False
    for k in gone:
        state.pop(k, None)
    _save(project, state)
    return True


def reset(project, kind_prefix: str | None = None) -> bool:
    """Human release: counters and the pause reason set by this module are dropped, so the loop may run again."""
    changed = clear(project, kind_prefix)
    meta = dict(project.meta or {})
    if meta.get("regen_budget_stop"):
        meta.pop("regen_budget_stop", None)
        meta.pop(PAUSE_REASON_KEY, None)
        project.meta = meta
        flag_modified(project, "meta")
        changed = True
    return changed


async def stop(session, project, verdict: Verdict) -> None:
    """Park the project and record why. Never raises into the caller."""
    from app.models import ProjectStatus

    text = verdict.summary()
    meta = dict(project.meta or {})
    meta[PAUSE_REASON_KEY] = text
    meta["regen_budget_stop"] = {"kind": verdict.kind, "blocked": verdict.blocked[:24],
                                 "run_exhausted": verdict.run_exhausted, "rounds": verdict.rounds,
                                 "usd_exhausted": verdict.usd_exhausted, "usd_spent": verdict.usd_spent,
                                 "usd_cap": verdict.usd_cap}
    project.meta = meta
    flag_modified(project, "meta")
    project.status = ProjectStatus.paused
    try:
        await session.flush()
    except Exception:  # noqa: BLE001
        logger.exception("[#{}] regen_budget: flush failed", getattr(project, "id", "?"))
    logger.warning("[#{}] regen_budget: {}", getattr(project, "id", "?"), text)
    try:
        from app.services.observation import record_fact
        await record_fact("runtime_check", {
            "check": "regen_budget", "performed": True, "status": "rejected", "kind": verdict.kind,
            "blocked": verdict.blocked[:24], "run_exhausted": verdict.run_exhausted, "rounds": verdict.rounds,
            "per_frame_cap": verdict.per_frame, "per_run_cap": verdict.per_run,
            "usd_exhausted": verdict.usd_exhausted, "usd_spent": verdict.usd_spent,
            "usd_round": verdict.usd_round, "usd_cap": verdict.usd_cap, "summary": text})
    except Exception:  # noqa: BLE001
        logger.debug("regen_budget: observation fact skipped", exc_info=True)
