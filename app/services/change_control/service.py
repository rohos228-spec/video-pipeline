from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.services.action_layer import store as al_store
from app.services.change_control import lints, shadow, targets, tiers
from app.services.change_control import store as cs
from app.services.change_control.errors import ChangeFailed, ChangeRefused, StaleBase
from app.services.change_control.gateway import ProjectGateway, ProjectNotFound
from app.services.change_control.store import sha

_TARGET_RE = re.compile(r"^project:\d+:[A-Za-z0-9_.\-]{1,120}:(prompt|options|step_params)$")
_LOCKS: dict[str, asyncio.Lock] = {}


def _lock(key: str) -> asyncio.Lock:
    return _LOCKS.setdefault(key, asyncio.Lock())


def _now() -> datetime:
    return datetime.utcnow()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _human(identity: str) -> None:
    from app.services.action_layer import approval
    try:
        approval._check_operator(approval.OperatorIdentity(str(identity or "")))
    except approval.ApprovalError as exc:
        raise ChangeRefused("operator_required", "only a human operator can apply, roll back or reject",
                            http=403) from exc



def proposal_view(row: cs.ChangeProposal, *, full: bool = True,
                  current_version: int | None = None) -> dict:
    cand = dict(row.candidate or {})
    if not full and isinstance(cand.get("text"), str):
        cand["text_chars"] = len(cand.pop("text"))
    return {
        "id": row.id, "tier": row.tier, "project_id": row.project_id, "node_key": row.node_key,
        "kind": row.kind, "target": row.target, "base_version": row.base_version,
        "base_digest": row.base_digest, "candidate": cand,
        "diff": row.diff if full else "", "digest": row.digest, "rationale": row.rationale,
        "proposer": row.proposer, "status": row.status, "error": row.error,
        "approved_by": row.approved_by, "approved_at": _iso(row.approved_at),
        "applied_version": row.applied_version, "shadow": row.shadow,
        "rejected_by": row.rejected_by or "", "reject_reason": row.reject_reason or "",
        "rejected_at": _iso(row.rejected_at),
        "stale": bool(current_version is not None and row.tier in tiers.APPLICABLE_TIERS
                      and row.status in (cs.ST_PROPOSED, cs.ST_APPROVED)
                      and current_version != row.base_version),
        "applicable": row.tier in tiers.APPLICABLE_TIERS,
        "can_apply": row.tier in tiers.APPLICABLE_TIERS
        and row.status in (cs.ST_PROPOSED, cs.ST_APPROVED),
        "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
        "applied_at": _iso(row.applied_at)}


def version_view(row: cs.ConfigVersion, *, full: bool = False) -> dict:
    content = dict(row.content or {})
    if not full and isinstance(content.get("text"), str):
        content["text_chars"] = len(content.pop("text"))
    return {"id": row.id, "target": row.target, "version": row.version,
            "parent_version": row.parent_version, "kind": row.kind,
            "project_id": row.project_id, "node_key": row.node_key, "content": content,
            "digest": row.digest, "source_proposal_id": row.source_proposal_id,
            "applied_by": row.applied_by, "applied_at": _iso(row.applied_at)}


def _proposal_digest(row_like: dict) -> str:
    return sha({k: row_like[k] for k in ("tier", "project_id", "node_key", "kind", "target",
                                          "base_version", "candidate")})



async def _record(event: str, *, actor: str, plan_id: str = "", action_id: str = "",
                  proposal_id: str = "", target: str = "", version: int | None = None,
                  tier: str = "", outcome: str = "", error: str = "") -> None:
    async with al_store.sessions()() as s:
        al_store.append_history(
            s, event=event, plan_id=plan_id, actor=actor, action_id=action_id,
            params={"proposal_id": proposal_id, "target": target, "version": version,
                    "tier": tier}, outcome=outcome, error=error)
        await s.commit()



async def _pointer(s, target: str) -> int:
    ptr = await s.get(cs.ConfigPointer, target)
    return ptr.current_version if ptr else 0


async def get_proposal(proposal_id: str) -> dict:
    async with cs.sessions()() as s:
        row = await s.get(cs.ChangeProposal, proposal_id)
        if row is None:
            raise ChangeRefused("proposal_not_found", "proposal not found", http=404)
        current = await _pointer(s, row.target)             if row.target and row.tier in tiers.APPLICABLE_TIERS else None
        view = proposal_view(row, current_version=current)
        if current is not None:
            view["current_version"] = current
        return view


async def list_proposals(*, project_id: int | None = None, status: str | None = None,
                         tier: str | None = None, node_key: str | None = None,
                         limit: int = 50, before_created: str | None = None) -> dict:
    q = select(cs.ChangeProposal)
    if project_id is not None:
        q = q.where(cs.ChangeProposal.project_id == project_id)
    if status:
        q = q.where(cs.ChangeProposal.status == status)
    if tier:
        q = q.where(cs.ChangeProposal.tier == tier)
    if node_key:
        q = q.where(cs.ChangeProposal.node_key == node_key)
    q = q.order_by(cs.ChangeProposal.created_at.desc(), cs.ChangeProposal.id).limit(
        max(1, min(int(limit), 200)))
    async with cs.sessions()() as s:
        rows = (await s.scalars(q)).all()
        wanted = {r.target for r in rows if r.target and r.tier in tiers.APPLICABLE_TIERS}
        pointers = {}
        if wanted:
            pointers = {p.target: p.current_version for p in (await s.scalars(
                select(cs.ConfigPointer).where(cs.ConfigPointer.target.in_(wanted)))).all()}
    return {"count": len(rows), "items": [
        proposal_view(r, full=False, current_version=(
            pointers.get(r.target, 0) if r.target in wanted else None)) for r in rows]}


async def reject(proposal_id: str, reason: str, *, actor: str, plan_id: str = "") -> dict:
    _human(actor)
    reason = (reason or "").strip()
    if not 3 <= len(reason) <= 500:
        raise ChangeRefused("bad_reason", "reason must be 3..500 chars")
    async with cs.sessions()() as s:
        row = await s.get(cs.ChangeProposal, proposal_id)
        if row is None:
            raise ChangeRefused("proposal_not_found", "proposal not found", http=404)
        status, target, tier, digest = row.status, row.target, row.tier, row.digest
        res = await s.execute(update(cs.ChangeProposal).where(
            cs.ChangeProposal.id == proposal_id,
            cs.ChangeProposal.status.in_((cs.ST_PROPOSED, cs.ST_DEVELOPER_REVIEW)),
        ).values(status=cs.ST_REJECTED, rejected_by=actor[:120], reject_reason=reason,
                 rejected_at=_now(), updated_at=_now()))
        if res.rowcount != 1:
            await s.rollback()
            raise ChangeRefused("status_not_rejectable",
                                f"proposal is {status}; only proposed or developer_review "
                                "can be rejected", http=409)
        al_store.append_history(
            s, event="change_rejected", plan_id=plan_id, actor=actor, action_id="change.reject",
            params={"proposal_id": proposal_id, "target": target, "tier": tier,
                    "digest": digest}, outcome="rejected", error=reason)
        await s.commit()
    return await get_proposal(proposal_id)


async def prompt_base(gateway: ProjectGateway, project_id: int, node_key: str) -> dict:
    nctx = await _load_node(gateway, project_id, node_key)
    if tiers.K_PROMPT_TEXT not in tiers.allowed_kinds(nctx.node_type):
        raise ChangeRefused("kind_not_allowed", f"{nctx.node_type!r} has no editable prompt")
    out = targets.describe_prompt_base(nctx)
    out.update(project_id=project_id, node_key=node_key, node_type=nctx.node_type, step=nctx.step)
    return out


async def list_versions(target: str, *, limit: int = 100) -> dict:
    if not _TARGET_RE.match(target or ""):
        raise ChangeRefused("bad_target", "target must look like "
                            "project:<id>:<node>:<prompt|options|step_params>")
    async with cs.sessions()() as s:
        rows = (await s.scalars(select(cs.ConfigVersion).where(
            cs.ConfigVersion.target == target).order_by(cs.ConfigVersion.version.desc())
            .limit(max(1, min(int(limit), 500))))).all()
        current = await _pointer(s, target)
    return {"target": target, "current_version": current,
            "items": [version_view(r) for r in rows]}



async def _load_node(gateway: ProjectGateway, project_id: int, node_key: str):
    try:
        state = await gateway.get(project_id)
    except ProjectNotFound as exc:
        raise ChangeRefused("project_not_found", str(exc), http=404) from exc
    return targets.resolve_node(state, project_id, node_key)


async def prepare_proposal(gateway: ProjectGateway | None, *, tier: str, kind: str,
                           rationale: str, candidate: dict, proposer: str,
                           project_id: int | None = None, node_key: str = "") -> dict:
    if tier not in tiers.TIERS:
        raise ChangeRefused("bad_tier", f"tier must be one of {list(tiers.TIERS)}")
    if kind not in tiers.KINDS_BY_TIER[tier]:
        raise ChangeRefused("kind_tier_mismatch",
                            f"kind {kind!r} does not belong to {tier}: "
                            f"{sorted(tiers.KINDS_BY_TIER[tier])}")
    rationale = (rationale or "").strip()
    if not rationale or len(rationale) > tiers.MAX_RATIONALE:
        raise ChangeRefused("bad_rationale", f"rationale must be 1..{tiers.MAX_RATIONALE} chars")
    proposer = str(proposer or "").strip()[:120]
    if not proposer:
        raise ChangeRefused("proposer_required", "proposer identity is required")
    if not isinstance(candidate, dict):
        raise ChangeRefused("bad_candidate", "candidate must be an object")
    out = {"tier": tier, "kind": kind, "rationale": rationale, "proposer": proposer,
           "project_id": project_id, "node_key": node_key, "base_digest": "",
           "status": cs.ST_PROPOSED, "nctx": None}
    if tier in (tiers.T0, tiers.T3):
        out["candidate"], out["diff"], out["target"], out["status"] = _record_only_candidate(
            tier, candidate)
        return out
    if gateway is None or not isinstance(project_id, int) or isinstance(project_id, bool)             or project_id < 1 or not node_key:
        raise ChangeRefused("project_required", "project_id and node_key are required")
    nctx = await _load_node(gateway, project_id, node_key)
    if kind not in tiers.allowed_kinds(nctx.node_type):
        raise ChangeRefused("kind_not_allowed",
                            f"{kind!r} is not allowed on a {nctx.node_type!r} node; allowed: "
                            f"{sorted(tiers.allowed_kinds(nctx.node_type))}")
    handler = targets.handler_for_kind(kind)
    out["candidate"] = handler.prepare(nctx, kind, candidate)
    live = handler.live(nctx)
    out["target"] = handler.target(nctx)
    out["base_digest"] = sha(handler.comparable(live))
    out["diff"] = handler.diff(nctx, out["candidate"], live)
    out["nctx"] = nctx
    return out


async def propose(gateway: ProjectGateway | None, *, tier: str, kind: str, rationale: str,
                  candidate: dict, proposer: str, project_id: int | None = None,
                  node_key: str = "", plan_id: str = "") -> dict:
    p = await prepare_proposal(gateway, tier=tier, kind=kind, rationale=rationale,
                               candidate=candidate, proposer=proposer, project_id=project_id,
                               node_key=node_key)
    tier, target, status = p["tier"], p["target"], p["status"]
    async with cs.sessions()() as s:
        base_version = await _pointer(s, target) if p["nctx"] is not None else 0
        digest = _proposal_digest({
            "tier": tier, "project_id": p["project_id"], "node_key": p["node_key"],
            "kind": p["kind"], "target": target, "base_version": base_version,
            "candidate": p["candidate"]})
        same = await s.scalar(select(cs.ChangeProposal).where(
            cs.ChangeProposal.digest == digest, cs.ChangeProposal.status == status))
        if same is not None:
            view = proposal_view(same)
            view["deduplicated"] = True
            return view
        if p["project_id"] is not None:
            open_count = await s.scalar(select(func.count()).select_from(cs.ChangeProposal).where(
                cs.ChangeProposal.project_id == p["project_id"],
                cs.ChangeProposal.status.in_((cs.ST_PROPOSED, cs.ST_APPROVED))))
            if (open_count or 0) >= tiers.MAX_OPEN_PER_PROJECT:
                raise ChangeRefused("too_many_open", "too many open proposals for this project",
                                    http=429)
        row = cs.ChangeProposal(
            tier=tier, project_id=p["project_id"], node_key=p["node_key"], kind=p["kind"],
            target=target, base_version=base_version, base_digest=p["base_digest"],
            candidate=p["candidate"], diff=p["diff"], digest=digest, rationale=p["rationale"],
            proposer=p["proposer"], status=status)
        s.add(row)
        await s.flush()
        al_store.append_history(
            s, event="change_proposed", plan_id=plan_id, actor=p["proposer"],
            action_id="change.propose",
            params={"proposal_id": row.id, "target": target, "tier": tier, "kind": p["kind"],
                    "digest": digest}, outcome=status)
        await s.commit()
        return proposal_view(row)


def _record_only_candidate(tier: str, candidate: dict):
    if tier == tiers.T0:
        if set(candidate) != {"text"} or not isinstance(candidate["text"], str) \
                or not 1 <= len(candidate["text"]) <= 4000:
            raise ChangeRefused("bad_candidate", "candidate must be {text: 1..4000 chars}")
        cand = {"text": candidate["text"]}
        diff, status = candidate["text"], cs.ST_PROPOSED
    else:
        allowed = {"summary", "diff", "path"}
        if not {"summary", "diff"} <= set(candidate) or set(candidate) - allowed:
            raise ChangeRefused("bad_candidate", "candidate must be {summary, diff, path?}")
        summary, diff_text = candidate["summary"], candidate["diff"]
        path = candidate.get("path", "")
        if (not isinstance(summary, str) or not 1 <= len(summary) <= 500
                or not isinstance(diff_text, str) or not 1 <= len(diff_text) <= tiers.MAX_DIFF_CHARS
                or not isinstance(path, str) or len(path) > 300):
            raise ChangeRefused("bad_candidate", "summary/diff/path have invalid size or type")
        if lints.new_secret_hits(diff_text, ""):
            raise ChangeRefused("lint_failed", "diff contains credential-like content",
                                errors=["credential-like content"])
        cand = {"summary": summary, "diff": diff_text, "path": path}
        diff, status = diff_text, cs.ST_DEVELOPER_REVIEW
    target = f"advice:{tier}" if tier == tiers.T0 else "developer_review"
    return cand, diff, target, status



def _busy(state: dict) -> bool:
    from app.models import ProjectStatus
    from app.services.project_state import is_running_status
    try:
        return is_running_status(ProjectStatus(state.get("status")))
    except ValueError:
        return False


async def _checks(gateway: ProjectGateway, proposal_id: str, expected_digest: str):
    async with cs.sessions()() as s:
        row = await s.get(cs.ChangeProposal, proposal_id)
        if row is None:
            raise ChangeRefused("proposal_not_found", "proposal not found", http=404)
        cur_version = await _pointer(s, row.target) if row.target else 0
        version_row = None
        if cur_version:
            version_row = await s.scalar(select(cs.ConfigVersion).where(
                cs.ConfigVersion.target == row.target, cs.ConfigVersion.version == cur_version))
    if expected_digest != row.digest:
        raise ChangeRefused("digest_mismatch", "expected_digest does not match the proposal",
                            http=409)
    if _proposal_digest({"tier": row.tier, "project_id": row.project_id,
                         "node_key": row.node_key, "kind": row.kind, "target": row.target,
                         "base_version": row.base_version, "candidate": row.candidate}) != row.digest:
        raise ChangeRefused("proposal_tampered", "stored proposal does not match its digest",
                            http=409)
    if row.tier not in tiers.APPLICABLE_TIERS:
        raise ChangeRefused("tier_not_applicable",
                            f"{row.tier} proposals are never applied by this layer", http=409)
    if row.status not in (cs.ST_PROPOSED, cs.ST_APPROVED):
        raise ChangeRefused("status_not_applicable", f"proposal is {row.status}", http=409)
    nctx = await _load_node(gateway, row.project_id, row.node_key)
    if _busy(nctx.state):
        raise ChangeRefused("project_busy", "the project is running a step", http=409)
    handler = targets.handler_for_target(row.target)
    live = handler.live(nctx)
    if cur_version != row.base_version:
        raise StaleBase(f"current version is {cur_version}, proposal is based on {row.base_version}")
    if version_row is None:
        if sha(handler.comparable(live)) != row.base_digest:
            raise StaleBase("the live value changed after the proposal was made")
    elif handler.comparable(version_row.content) != handler.comparable(live):
        raise StaleBase("the live value differs from the current version")
    errors = handler.validate(nctx, row.candidate, live)
    if errors:
        raise ChangeRefused("lint_failed", "; ".join(errors), errors=errors)
    return row, nctx, handler, live, cur_version


async def check_apply(gateway: ProjectGateway, proposal_id: str, expected_digest: str) -> str | None:
    try:
        await _checks(gateway, proposal_id, expected_digest)
    except ChangeRefused as exc:
        return str(exc)
    return None


async def _append_version(s, *, target: str, base: int, kind: str, content: dict,
                          project_id, node_key: str, proposal_id: str | None, by: str) -> int:
    ptr = await s.get(cs.ConfigPointer, target)
    if (ptr.current_version if ptr else 0) != base:
        raise StaleBase()
    new = base + 1
    s.add(cs.ConfigVersion(
        target=target, version=new, parent_version=base, kind=kind, project_id=project_id,
        node_key=node_key, content=content, digest=sha(content), source_proposal_id=proposal_id,
        applied_by=by[:120]))
    if ptr is None:
        s.add(cs.ConfigPointer(target=target, current_version=new))
    else:
        res = await s.execute(update(cs.ConfigPointer).where(
            cs.ConfigPointer.target == target, cs.ConfigPointer.current_version == base
        ).values(current_version=new, updated_at=_now()))
        if res.rowcount != 1:
            raise StaleBase()
    await s.flush()
    return new


async def _commit_versions(rows: list[dict]) -> list[int]:
    out: list[int] = []
    async with cs.sessions()() as s:
        try:
            for spec in rows:
                out.append(await _append_version(s, **spec))
            await s.commit()
        except (IntegrityError, StaleBase) as exc:
            await s.rollback()
            raise StaleBase() from exc
    return out


async def _set_proposal(proposal_id: str, **values: Any) -> None:
    async with cs.sessions()() as s:
        await s.execute(update(cs.ChangeProposal).where(cs.ChangeProposal.id == proposal_id)
                        .values(updated_at=_now(), **values))
        await s.commit()


async def _write_and_verify(gateway: ProjectGateway, handler, nctx, content: dict) -> str | None:
    try:
        await handler.write(gateway, nctx, content)
        state = await gateway.get(nctx.project_id)
    except Exception as exc:
        return f"write failed: {type(exc).__name__}: {exc}"[:400]
    after = targets.with_state(nctx, **state)
    if handler.comparable(handler.live(after)) != handler.comparable(content):
        return "value read back differs from the written value"
    return None


async def apply(gateway: ProjectGateway, proposal_id: str, expected_digest: str, *,
                approved_by: str, plan_id: str = "") -> dict:
    _human(approved_by)
    async with cs.sessions()() as s:
        row0 = await s.get(cs.ChangeProposal, proposal_id)
    key = row0.target if row0 is not None and row0.target else proposal_id
    async with _lock(key):
        try:
            row, nctx, handler, live, cur_version = await _checks(
                gateway, proposal_id, expected_digest)
        except ChangeRefused as exc:
            tier = row0.tier if row0 is not None else ""
            if exc.code == "lint_failed" and row0 is not None:
                await _set_proposal(proposal_id, status=cs.ST_FAILED, error=str(exc)[:2000])
            await _record("change_applied", actor=approved_by, plan_id=plan_id,
                          action_id="change.apply", proposal_id=proposal_id,
                          target=row0.target if row0 is not None else "", tier=tier,
                          outcome="stale_base" if exc.code == "stale_base" else "refused",
                          error=str(exc))
            raise
        view = proposal_view(row)
        results = await shadow.run_shadow_hooks(view)
        if results:
            await _set_proposal(proposal_id, shadow=results)
        if any(not r["ok"] for r in results):
            msg = "; ".join(f"{r['name']}: {r['note'] or 'not ok'}" for r in results if not r["ok"])
            await _set_proposal(proposal_id, status=cs.ST_FAILED, error=f"shadow_failed: {msg}"[:2000])
            await _record("change_applied", actor=approved_by, plan_id=plan_id,
                          action_id="change.apply", proposal_id=proposal_id, target=row.target,
                          tier=row.tier, outcome="refused", error=f"shadow_failed: {msg}")
            raise ChangeRefused("shadow_failed", msg, http=409)
        after = handler.after(nctx, row.candidate)
        await _set_proposal(proposal_id, status=cs.ST_APPROVED, approved_by=approved_by[:120],
                            approved_at=_now())
        specs = []
        base = cur_version
        if base == 0:
            specs.append(dict(target=row.target, base=0, kind=cs.VK_BASELINE, content=live,
                              project_id=row.project_id, node_key=row.node_key,
                              proposal_id=None, by="baseline"))
            base = 1
        specs.append(dict(target=row.target, base=base, kind=cs.VK_APPLY, content=after,
                          project_id=row.project_id, node_key=row.node_key,
                          proposal_id=row.id, by=approved_by))
        try:
            versions = await _commit_versions(specs)
        except StaleBase as exc:
            await _set_proposal(proposal_id, status=cs.ST_PROPOSED)
            await _record("change_applied", actor=approved_by, plan_id=plan_id,
                          action_id="change.apply", proposal_id=proposal_id, target=row.target,
                          tier=row.tier, outcome="stale_base", error=str(exc))
            raise
        applied_version = versions[-1]
        problem = await _write_and_verify(gateway, handler, nctx, after)
        if problem is None:
            await _set_proposal(proposal_id, status=cs.ST_APPLIED, applied_version=applied_version,
                                applied_at=_now(), error="")
            await _record("change_applied", actor=approved_by, plan_id=plan_id,
                          action_id="change.apply", proposal_id=proposal_id, target=row.target,
                          version=applied_version, tier=row.tier, outcome="applied")
            return {"ok": True, "proposal_id": row.id, "status": cs.ST_APPLIED,
                    "target": row.target, "version": applied_version,
                    "previous_version": applied_version - 1, "digest": row.digest}
        await _auto_rollback(gateway, row, handler, nctx, live, applied_version, problem,
                             approved_by, plan_id)


async def _auto_rollback(gateway, row, handler, nctx, live, applied_version, problem,
                         actor, plan_id) -> None:
    fresh = targets.with_state(nctx, **await _safe_state(gateway, nctx))
    restore_problem = await _write_and_verify(gateway, handler, fresh, live)
    if restore_problem is None:
        with contextlib.suppress(StaleBase):
            await _commit_versions([dict(
                target=row.target, base=applied_version, kind=cs.VK_AUTO_ROLLBACK, content=live,
                project_id=row.project_id, node_key=row.node_key, proposal_id=row.id,
                by="auto_rollback")])
        await _set_proposal(row.id, status=cs.ST_FAILED,
                            error=f"verification_failed (auto-rolled back): {problem}"[:2000])
        await _record("change_applied", actor=actor, plan_id=plan_id, action_id="change.apply",
                      proposal_id=row.id, target=row.target, version=applied_version,
                      tier=row.tier, outcome="auto_rolled_back", error=problem)
        raise ChangeFailed("verification_failed",
                           f"value was restored after a failed check: {problem}")
    await _set_proposal(row.id, status=cs.ST_FAILED,
                        error=f"rollback_failed, manual review needed: {problem}; "
                              f"{restore_problem}"[:2000])
    await _record("change_applied", actor=actor, plan_id=plan_id, action_id="change.apply",
                  proposal_id=row.id, target=row.target, version=applied_version, tier=row.tier,
                  outcome="rollback_failed", error=f"{problem}; {restore_problem}")
    raise ChangeFailed("rollback_failed", "the value could not be restored; manual review needed")


async def _safe_state(gateway: ProjectGateway, nctx) -> dict:
    try:
        return await gateway.get(nctx.project_id)
    except Exception:
        return nctx.state



async def _rollback_checks(gateway: ProjectGateway, target: str, expected_version: int):
    if not _TARGET_RE.match(target or ""):
        raise ChangeRefused("bad_target", "target must look like "
                            "project:<id>:<node>:<prompt|options|step_params>")
    async with cs.sessions()() as s:
        current = await _pointer(s, target)
        if current != expected_version:
            raise StaleBase(f"current version is {current}, expected {expected_version}")
        cur = await s.scalar(select(cs.ConfigVersion).where(
            cs.ConfigVersion.target == target, cs.ConfigVersion.version == current))
        if cur is None or cur.parent_version < 1:
            raise ChangeRefused("nothing_to_roll_back", "this is the baseline version", http=409)
        parent = await s.scalar(select(cs.ConfigVersion).where(
            cs.ConfigVersion.target == target, cs.ConfigVersion.version == cur.parent_version))
    nctx = await _load_node(gateway, cur.project_id, cur.node_key)
    if _busy(nctx.state):
        raise ChangeRefused("project_busy", "the project is running a step", http=409)
    handler = targets.handler_for_target(target)
    live = handler.live(nctx)
    if handler.comparable(cur.content) != handler.comparable(live):
        raise StaleBase("the live value differs from the current version")
    return cur, parent, nctx, handler, live


async def check_rollback(gateway: ProjectGateway, target: str, expected_version: int) -> str | None:
    try:
        await _rollback_checks(gateway, target, expected_version)
    except ChangeRefused as exc:
        return str(exc)
    return None


async def rollback(gateway: ProjectGateway, target: str, expected_version: int, *,
                   approved_by: str, reason: str = "", plan_id: str = "") -> dict:
    _human(approved_by)
    async with _lock(target):
        try:
            cur, parent, nctx, handler, live = await _rollback_checks(
                gateway, target, expected_version)
        except ChangeRefused as exc:
            await _record("change_rolled_back", actor=approved_by, plan_id=plan_id,
                          action_id="change.rollback", target=target, version=expected_version,
                          outcome="stale_base" if exc.code == "stale_base" else "refused",
                          error=str(exc))
            raise
        restore = dict(parent.content or {})
        try:
            (new_version,) = await _commit_versions([dict(
                target=target, base=cur.version, kind=cs.VK_ROLLBACK, content=restore,
                project_id=cur.project_id, node_key=cur.node_key,
                proposal_id=cur.source_proposal_id, by=approved_by)])
        except StaleBase as exc:
            await _record("change_rolled_back", actor=approved_by, plan_id=plan_id,
                          action_id="change.rollback", target=target, version=expected_version,
                          outcome="stale_base", error=str(exc))
            raise
        problem = await _write_and_verify(gateway, handler, nctx, restore)
        if problem is not None:
            fresh = targets.with_state(nctx, **await _safe_state(gateway, nctx))
            back = await _write_and_verify(gateway, handler, fresh, live)
            if back is None:
                with contextlib.suppress(StaleBase):
                    await _commit_versions([dict(
                        target=target, base=new_version, kind=cs.VK_AUTO_ROLLBACK,
                        content=dict(cur.content or {}), project_id=cur.project_id,
                        node_key=cur.node_key, proposal_id=cur.source_proposal_id,
                        by="auto_rollback")])
            await _record("change_rolled_back", actor=approved_by, plan_id=plan_id,
                          action_id="change.rollback", target=target, version=new_version,
                          outcome="auto_rolled_back" if back is None else "rollback_failed",
                          error=problem)
            raise ChangeFailed("verification_failed" if back is None else "rollback_failed",
                               f"rollback was not confirmed: {problem}")
        if cur.source_proposal_id:
            new_status = cs.ST_ROLLED_BACK if cur.kind == cs.VK_APPLY else (
                cs.ST_APPLIED if cur.kind == cs.VK_ROLLBACK else None)
            if new_status:
                await _set_proposal(cur.source_proposal_id, status=new_status)
        await _record("change_rolled_back", actor=approved_by, plan_id=plan_id,
                      action_id="change.rollback", proposal_id=cur.source_proposal_id or "",
                      target=target, version=new_version, tier="", outcome="rolled_back",
                      error=reason[:500])
        return {"ok": True, "target": target, "version": new_version,
                "restored_from_version": parent.version, "previous_current": cur.version,
                "proposal_id": cur.source_proposal_id}
