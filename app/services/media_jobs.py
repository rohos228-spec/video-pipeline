"""Durable transport bookkeeping for existing media services.

No worker or scheduler here. Node identity comes only from the observed worker
context. Submit reservations are permanent; only a known job may be polled.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import JSON, String, UniqueConstraint, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, Project, WorkflowRun
from app.services.observation import _attempt, digest, record_fact
from app.services.artifact_verifier import contained_path


class MediaJob(Base):
    __tablename__ = "orchestrator_media_jobs"
    __table_args__ = (UniqueConstraint("execution_id", "node_key", "resource_key"),)
    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[int] = mapped_column(index=True)
    node_key: Mapped[str] = mapped_column(String(60))
    attempt_id: Mapped[str] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(30))
    model_id: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    resource_key: Mapped[str] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(24), default="reserved")
    provider_job_id: Mapped[str | None] = mapped_column(String(160), default=None)
    path: Mapped[str] = mapped_column(String(500))
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(100), default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class MediaRequest(Base):
    __tablename__ = "orchestrator_media_requests"
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    node_key: Mapped[str] = mapped_column(String(60))
    attempt_id: Mapped[str] = mapped_column(String(36))
    category: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(24), default="reserved")
    cost_status: Mapped[str] = mapped_column(String(30), default="unknown_tariff")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class MediaPolicyError(RuntimeError):
    """Protective stop; not a model quality or native pipeline defect."""

async def _verified_artifact_job(ctx, artifact):
    async with ctx.observer.master() as master:
        job = await master.scalar(select(MediaJob).where(
            MediaJob.execution_id==ctx.scope.execution_id, MediaJob.node_key==ctx.node_key,
            MediaJob.project_id==artifact.project_id, MediaJob.path==str(Path(artifact.path).resolve())))
        project = await master.get(Project, artifact.project_id)
    if not job or job.phase != "verified":
        raise MediaPolicyError("artifact has no verified job in this execution/node")
    from app.services.artifact_verifier import verify_artifact
    try:
        proof = await verify_artifact(artifact.path, root=project.data_dir, kind=job.kind)
    except (ValueError, OSError) as exc:
        raise MediaPolicyError("artifact verification path unavailable") from exc
    if proof["status"] != "passed" or proof.get("sha256") != job.verification.get("sha256"):
        raise MediaPolicyError("artifact verification changed before business apply")
    return job, proof


async def persist_artifact(session, artifact):
    """Original business transaction owns apply/rollback; job UUID deduplicates it."""
    from app.models import Artifact
    ctx = _attempt.get()
    if not ctx or ctx.scope.mode != "managed":
        session.add(artifact)
        return artifact
    await assert_entry(project_id=artifact.project_id, provider="kie")
    job, proof = await _verified_artifact_job(ctx, artifact)
    identity = uuid.uuid5(uuid.UUID(job.job_id), "artifact").hex
    meta = {**(artifact.meta or {}), "orchestrator": {
        "execution_id":job.execution_id, "node_key":job.node_key, "attempt_id":job.attempt_id,
        "applying_attempt_id":ctx.attempt_id,"job_id":job.job_id,
        "provider_job_id":job.provider_job_id,"verification":proof}}
    inserted = await session.execute(insert(Artifact).values(uuid=identity,
        project_id=artifact.project_id,frame_id=artifact.frame_id,kind=artifact.kind,
        path=job.path,meta=meta).on_conflict_do_nothing(index_elements=["uuid"]))
    actual = await session.scalar(select(Artifact).where(Artifact.uuid==identity))
    if (not actual or actual.project_id != artifact.project_id or actual.frame_id != artifact.frame_id
            or actual.kind != artifact.kind or actual.path != job.path):
        raise MediaPolicyError("artifact business identity conflict")
    ctx.observer.stage(session, ctx.scope, "artifact_saved", db_scope=ctx.db_scope,
        node_key=ctx.node_key,attempt_id=ctx.attempt_id,payload={
            "artifact_uuid":identity,"job_id":job.job_id,"transactional":True,
            "duplicate_delivery":inserted.rowcount==0,"verification":proof})
    return actual


async def require_verified_images(session, project):
    """Final managed gate inside original validator; never applies or regenerates."""
    bound = await assert_entry(project_id=project.id, provider="kie")
    if bound is None:
        return
    ctx, _ = bound
    from app.models import Artifact, ArtifactKind, Frame
    from app.generation_options import is_skippable_empty_prompt
    from app.services.scan_frames import newest_frame_image_path, frame_image_prompt_text
    from app.services.vo_shot_expand import is_coverage_leftover

    try:
        frames = (await session.scalars(select(Frame).where(Frame.project_id==project.id))).all()
        verified = []
        for frame in frames:
            if is_coverage_leftover(frame) or is_skippable_empty_prompt(frame_image_prompt_text(frame, list(frames))):
                continue
            path = newest_frame_image_path(project.data_dir / "scenes", frame.number)
            if path is None:
                raise MediaPolicyError("artifact verification missing required image")
            artifacts = (await session.scalars(select(Artifact).where(
                Artifact.project_id==project.id, Artifact.frame_id==frame.id,
                Artifact.kind==ArtifactKind.scene_image))).all()
            matching = [a for a in artifacts if a.path and Path(a.path).resolve()==path.resolve()]
            if len(matching) != 1:
                raise MediaPolicyError("artifact verification missing or ambiguous business record")
            artifact = matching[0]
            job, proof = await _verified_artifact_job(ctx, artifact)
            identity = uuid.uuid5(uuid.UUID(job.job_id), "artifact").hex
            binding = (artifact.meta or {}).get("orchestrator") or {}
            if (artifact.uuid != identity or binding.get("job_id") != job.job_id
                    or binding.get("execution_id") != ctx.scope.execution_id
                    or binding.get("node_key") != ctx.node_key
                    or binding.get("verification", {}).get("sha256") != proof["sha256"]):
                raise MediaPolicyError("artifact verification provenance conflict")
            verified.append(identity)
        await record_fact("media_node_verified", {"status":"passed", "artifact_uuids":verified,
                                                  "semantic_quality_verified":False})
    except MediaPolicyError as exc:
        await _hold(ctx, str(exc), "final-verification:"+ctx.node_key+":"+ctx.attempt_id)
        raise


def validate_media_config(raw, *, mode):
    expected = {"enabled", "provider", "origin", "download_origins", "model_ids",
                "max_bytes", "timeout_s", "poll_interval_s", "max_polls"}
    if not isinstance(raw, dict) or set(raw) != expected or type(raw["enabled"]) is not bool:
        raise ValueError("invalid media policy")
    if raw["provider"] not in {"kie", "elevenlabs"}:
        raise ValueError("unsupported media provider adapter")
    if mode != "synthetic" and raw["enabled"]:
        raise ValueError("media production transport has no authorized network profile")
    if not isinstance(raw["download_origins"], list) or not 1 <= len(raw["download_origins"]) <= 4:
        raise ValueError("explicit download origins required")
    for origin in [raw["origin"], *raw["download_origins"]]:
        u = urlsplit(origin)
        if (u.scheme != "http" or u.hostname not in {"127.0.0.1", "::1"}
                or u.username or u.password or u.query or u.fragment or u.path or not u.port):
            raise ValueError("media fixture requires exact loopback origin with port")
    if (not isinstance(raw["model_ids"], list) or not raw["model_ids"]
            or any(not isinstance(m, str) or not re.fullmatch(r"[\w.-]{1,100}", m) for m in raw["model_ids"])):
        raise ValueError("explicit model catalog ids required")
    for name, lo, hi in (("max_bytes", 1024, 256 * 1024 * 1024), ("timeout_s", 1, 1200), ("max_polls", 1, 100)):
        if type(raw[name]) is not int or not lo <= raw[name] <= hi:
            raise ValueError("invalid media limit")
    if type(raw["poll_interval_s"]) not in {int, float} or not 0.01 <= raw["poll_interval_s"] <= 60:
        raise ValueError("invalid polling interval")


def check_url(url, config, *, download=False, path=None):
    u = urlsplit(url)
    origin = f"{u.scheme}://{u.netloc}"
    allowed = config["download_origins"] if download else [config["origin"]]
    if (origin not in allowed or u.username or u.password or u.fragment
            or u.hostname not in {"127.0.0.1", "::1"} or u.scheme != "http"
            or (path is not None and u.path != path)):
        raise ValueError("media route/source denied")
    return url


async def assert_entry(*, project_id=None, provider):
    from app.services.actions_v1 import get_permission, check_managed_start
    ctx = _attempt.get()
    permission = get_permission(project_id) if project_id is not None else None
    if ctx is None or ctx.scope.mode != "managed":
        if permission is not None:
            raise MediaPolicyError("managed media requires observed worker context")
        return None
    if project_id is not None and project_id != ctx.scope.project_id:
        raise MediaPolicyError("media project outside execution scope")
    permission = get_permission(ctx.scope.project_id)
    if (permission is None or permission.execution_id != ctx.scope.execution_id
            or ctx.node_key not in permission.node_keys or ctx.node_key not in ctx.scope.node_keys):
        raise MediaPolicyError("media node outside execution scope")
    allowed = check_managed_start(project_id=ctx.scope.project_id, node_key=ctx.node_key)
    if allowed is None or not allowed.allowed:
        raise MediaPolicyError("media policy: " + (allowed.reason if allowed else "no permission"))
    from app.services.decision_config import runtime_config
    cfg = runtime_config()
    media = cfg.get("media")
    if not media or not media["enabled"] or media["provider"] != provider or cfg["mode"] != "synthetic":
        raise MediaPolicyError("media provider profile disabled or unsupported")
    from app.settings import settings
    if not str(settings.gpt_api_key).startswith("synthetic"):
        raise MediaPolicyError("media fixture requires synthetic application credentials")
    return ctx, cfg


async def _hold(ctx, reason, identity):
    from app.services.actions_v1 import ActionSupervisor
    await record_fact("media_policy_denied", {"reason": reason, "protective_stop": True,
                                             "job_id": identity})
    await ActionSupervisor(ctx.observer.master).set_held(
        project_id=ctx.scope.project_id, execution_id=ctx.scope.execution_id, held=True,
        initiator="media_policy", rationale=reason, idempotency_key="media-hold:" + identity,
        operator_request=False)


def _resource(path, root, kind):
    relative = contained_path(path, root).relative_to(root.resolve())
    pattern = r"(frame_\d+(?:_s2)?|clip_\d+(?:_s2)?|voice_full)_[a-fA-F0-9]{8,32}"
    stem = re.sub(pattern, r"\1", relative.stem)
    if stem == relative.stem and not re.fullmatch(r"(frame|clip)_\d+(?:_s2)?|voice_full", stem):
        raise MediaPolicyError("unrecognized media output identity")
    return str(relative.parent / (stem + relative.suffix))


async def open_job(*, provider, kind, model_id, payload, out_path):
    bound = await assert_entry(provider=provider)
    if bound is None:
        raise MediaPolicyError("managed media job requires context")
    ctx, cfg = bound
    from app.services.decision_config import DecisionRuntimeBinding
    from app.services.node_capabilities import capability_for
    async with ctx.observer.master() as s:
        run = await s.get(WorkflowRun, ctx.scope.workflow_run_id)
        project = await s.get(Project, ctx.scope.project_id)
        node = next((n for n in run.nodes_snapshot if n.get("id") == ctx.node_key), None) if run else None
        if not node or capability_for(node.get("type", "")).result_kind != kind:
            raise MediaPolicyError("media kind/node scope mismatch")
        if model_id not in cfg["media"]["model_ids"]:
            raise MediaPolicyError("media model outside explicit profile")
        binding = await s.get(DecisionRuntimeBinding, ctx.scope.execution_id)
        if binding and binding.config_hash != digest(cfg):
            raise MediaPolicyError("media configuration revision changed")
        if not binding:
            await s.execute(insert(DecisionRuntimeBinding).values(execution_id=ctx.scope.execution_id,
                config_hash=digest(cfg), config=cfg).on_conflict_do_nothing())
        root = project.data_dir.resolve()
        resource = _resource(out_path, root, kind)
        values = dict(job_id=str(uuid.uuid4()), execution_id=ctx.scope.execution_id,
            project_id=ctx.scope.project_id, node_key=ctx.node_key, attempt_id=ctx.attempt_id,
            provider=provider, model_id=model_id, kind=kind, resource_key=resource,
            input_hash=digest(payload), config_hash=digest(cfg), path=str(contained_path(out_path, root)),
            phase="reserved", verification={})
        await s.execute(insert(MediaJob).values(**values).on_conflict_do_nothing())
        row = await s.scalar(select(MediaJob).where(MediaJob.execution_id==ctx.scope.execution_id,
            MediaJob.node_key==ctx.node_key, MediaJob.resource_key==resource))
        await s.commit()
    if row.input_hash != digest(payload) or row.config_hash != digest(cfg) or row.model_id != model_id:
        raise MediaPolicyError("media input/config revision changed; no new submit")
    if row.phase in {"submitted", "unknown"} and not row.provider_job_id:
        await _hold(ctx, "unknown submit outcome; human review required", row.job_id)
        raise MediaPolicyError("unknown submit outcome; never resubmit")
    if row.phase in {"failed", "cancelled"}:
        await _hold(ctx, "terminal media result requires human review", row.job_id)
        raise MediaPolicyError("terminal media job cannot be resubmitted")
    return JobHandle(row, root, cfg)


class JobHandle:
    def __init__(self, row, root, config):
        self.row, self.root, self.config = row, root, config
        self.job_id, self.path = row.job_id, Path(row.path)
        self.provider_job_id, self.phase = row.provider_job_id, row.phase

    async def reserve(self, category):
        if category not in {"generation_submit", "status", "download", "upload"}:
            raise MediaPolicyError("unsupported media operation")
        ctx, cfg = await assert_entry(provider=self.row.provider)
        if ctx.scope.execution_id != self.row.execution_id or ctx.node_key != self.row.node_key:
            raise MediaPolicyError("media operation outside scope")
        if digest(cfg) != self.row.config_hash:
            raise MediaPolicyError("media configuration revision changed")
        request_id = str(uuid.uuid4())
        reservation = MediaRequest(request_id=request_id, job_id=self.job_id,
            execution_id=ctx.scope.execution_id, node_key=ctx.node_key,
            attempt_id=ctx.attempt_id, category=category)
        from app.services.actions_v1 import ActionSupervisor
        await ActionSupervisor(ctx.observer.master).charge_request(
            ctx.scope.execution_id, media_reservation=reservation)
        await record_fact("media_request_reserved", {"job_id": self.job_id,
            "category": category, "cost_status": "unknown_tariff"}, request_id=request_id)
        if ctx.scope.execution_id in ctx.observer.failures:
            raise MediaPolicyError("mandatory media audit unavailable; request not sent")
        return request_id

    async def update(self, *, phase, provider_job_id=None, verification=None, error=None):
        phases = {"queued", "running", "completed", "downloading", "verifying",
                  "verified", "failed", "cancelled", "unknown"}
        if phase not in phases:
            raise ValueError("unknown media phase")
        ctx = _attempt.get()
        async with ctx.observer.master() as s:
            row = await s.get(MediaJob, self.job_id)
            if row.phase in {"verified", "failed", "cancelled"}:
                if row.phase == phase:
                    return  # repeated delivery has no second application
                raise MediaPolicyError("terminal media transition denied")
            if provider_job_id:
                if row.provider_job_id and row.provider_job_id != provider_job_id:
                    raise MediaPolicyError("provider job identity changed")
                row.provider_job_id = str(provider_job_id)
            row.phase, row.updated_at = phase, datetime.utcnow()
            row.verification = verification if verification is not None else row.verification
            row.error = error
            await s.commit()
            self.phase, self.provider_job_id = row.phase, row.provider_job_id
        await record_fact("media_phase", {"job_id": self.job_id, "provider_job_id": self.provider_job_id,
            "phase": phase, "kind": self.row.kind, "verification": verification, "error": error})

    async def settle(self, request_id, status):
        if status not in {"received", "unknown", "rejected"}:
            raise ValueError("invalid media delivery status")
        ctx = _attempt.get()
        async with ctx.observer.master() as s:
            await s.execute(update(MediaRequest).where(MediaRequest.request_id==request_id,
                MediaRequest.job_id==self.job_id, MediaRequest.status=="reserved").values(status=status))
            await s.commit()


async def stage_reservation(session, reservation):
    """Called inside the SAME transaction as the common budget increment."""
    row = await session.get(MediaJob, reservation.job_id)
    if (row is None or row.execution_id != reservation.execution_id
            or row.node_key != reservation.node_key):
        raise MediaPolicyError("media reservation identity mismatch")
    if reservation.category == "generation_submit":
        changed = await session.execute(update(MediaJob).where(
            MediaJob.job_id==reservation.job_id, MediaJob.phase=="reserved",
            MediaJob.provider_job_id.is_(None)).values(phase="submitted").returning(MediaJob.job_id))
        if changed.first() is None:
            raise MediaPolicyError("duplicate submit denied")
    elif reservation.category == "status":
        if not row.provider_job_id or row.phase not in {"queued", "running", "completed", "unknown"}:
            raise MediaPolicyError("status requires known unfinished provider job")
    elif reservation.category == "download" and row.phase not in {"completed", "downloading"}:
        raise MediaPolicyError("download requires confirmed completed job")
    session.add(reservation)
