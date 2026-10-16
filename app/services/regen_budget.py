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
    chain_exhausted: bool = False
    chain_count: int = 0
    chain_cap: int = 0

    @property
    def ok(self) -> bool:
        return (not self.blocked and not self.run_exhausted and not self.usd_exhausted
                and not self.chain_exhausted)

    def summary(self) -> str:
        title = _KIND_TITLES.get(self.kind, self.kind)
        if self.chain_exhausted:
            from app.services import restart_chain
            return restart_chain.summary(restart_chain.node_for_kind(self.kind), self.chain_count,
                                         self.chain_cap, "regen_budget")
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
    except Exception:
        return 3, 12


def usd_limits() -> tuple[float, float]:
    cap, unit = 5.0, 0.0
    try:
        from app.settings import settings
        cap, unit = float(settings.regen_max_usd_per_run), float(settings.regen_estimate_usd_per_item)
    except Exception:
        pass
    if unit <= 0:
        from app.services import spend_guard
        unit = float(spend_guard.MEDIA_SUBMIT_ESTIMATE_USD)
    return max(0.0, cap), unit


def estimate_round_usd(kind: str, item_count: int) -> float:
    _cap, unit = usd_limits()
    usd = unit * max(0, int(item_count))
    if kind.startswith("vision"):
        from app.services import spend_guard
        usd += float(spend_guard.DEFAULT_REQUEST_ESTIMATE_USD)
    elif item_count <= 0:
        usd = unit
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
    from app.services import restart_chain
    chain_ok, chain_n, chain_cap = restart_chain.charge(project, restart_chain.node_for_kind(kind), "regen_budget")
    if not chain_ok:
        verdict.chain_exhausted = True
        verdict.chain_count, verdict.chain_cap = chain_n, chain_cap
        verdict.blocked = names
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
    state = dict(_state(project))
    gone = [k for k in state if kind_prefix is None or k == kind_prefix or k.startswith(kind_prefix + ":")]
    if not gone:
        return False
    for k in gone:
        state.pop(k, None)
    _save(project, state)
    return True


def reset(project, kind_prefix: str | None = None) -> bool:
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
    from app.models import ProjectStatus

    text = verdict.summary()
    meta = dict(project.meta or {})
    meta[PAUSE_REASON_KEY] = text
    meta["regen_budget_stop"] = {"kind": verdict.kind, "blocked": verdict.blocked[:24],
                                 "run_exhausted": verdict.run_exhausted, "rounds": verdict.rounds,
                                 "usd_exhausted": verdict.usd_exhausted, "usd_spent": verdict.usd_spent,
                                 "usd_cap": verdict.usd_cap, "chain_exhausted": verdict.chain_exhausted}
    if project.status != ProjectStatus.paused:
        meta["paused_from_status"] = project.status.value
    project.meta = meta
    flag_modified(project, "meta")
    project.status = ProjectStatus.paused
    try:
        await session.flush()
    except Exception:
        logger.exception("[#{}] regen_budget: flush failed", getattr(project, "id", "?"))
    logger.warning("[#{}] regen_budget: {}", getattr(project, "id", "?"), text)
    try:
        from app.services.observation import record_fact
        await record_fact("runtime_check", {
            "check": "regen_budget", "performed": True, "status": "rejected", "kind": verdict.kind,
            "blocked": verdict.blocked[:24], "run_exhausted": verdict.run_exhausted, "rounds": verdict.rounds,
            "per_frame_cap": verdict.per_frame, "per_run_cap": verdict.per_run,
            "usd_exhausted": verdict.usd_exhausted, "usd_spent": verdict.usd_spent,
            "usd_round": verdict.usd_round, "usd_cap": verdict.usd_cap,
            "restart_chain": verdict.chain_exhausted, "summary": text})
    except Exception:
        logger.debug("regen_budget: observation fact skipped", exc_info=True)
