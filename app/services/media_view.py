"""Read-only panel projection. No polling, scheduling or provider access."""
import shutil
from collections import Counter
from pathlib import Path
from sqlalchemy import select
from app.models import Artifact, Project, WorkflowRun
from app.services.artifact_verifier import contained_path
from app.services.media_jobs import MediaJob, MediaRequest
from app.services.node_capabilities import capability_for


async def context_for_nodes(master, scope, *, project_session=None):
    run = await master.get(WorkflowRun, scope.workflow_run_id)
    project = await master.get(Project, scope.project_id)
    nodes = {n["id"]:n for n in (run.nodes_snapshot if run else [])}
    jobs = (await master.scalars(select(MediaJob).where(
        MediaJob.execution_id==scope.execution_id, MediaJob.project_id==scope.project_id))).all()
    requests = (await master.scalars(select(MediaRequest).where(
        MediaRequest.execution_id==scope.execution_id))).all()
    artifacts = (await (project_session or master).scalars(select(Artifact).where(
        Artifact.project_id==scope.project_id))).all()
    by_job = {}
    for artifact in artifacts:
        evidence=(artifact.meta or {}).get("orchestrator") or {}
        if evidence.get("execution_id")!=scope.execution_id:
            continue
        try:
            path=contained_path(artifact.path, project.data_dir)
            if not path.is_file():
                continue
        except (ValueError, OSError, AttributeError):
            continue
        if evidence.get("verification",{}).get("status")=="passed":
            by_job[evidence.get("job_id")] = artifact.uuid
    result = {}
    for key in scope.node_keys:
        capability=capability_for(str(nodes.get(key,{}).get("type") or ""))
        if capability.result_kind=="structured":
            continue
        blockers=[]
        if capability.result_kind in {"video","audio","assembly"} and not shutil.which("ffprobe"):
            blockers.append("Недоступен ffprobe: результат нельзя подтвердить")
        if capability.result_kind=="assembly" and not shutil.which("ffmpeg"):
            blockers.append("Недоступен FFmpeg: сборка не выполняется")
        if not capability.managed:
            blockers.append("Управляемое исполнение этого типа ещё не принято")
        result[key]={"result_kind":capability.result_kind,
            "limited_management":capability.managed and not blockers,
            "synthetic_only":True,"production_profile_configured":False,
            "blockers":blockers,"allowed_actions":list(capability.actions),
            "requests":dict(Counter(r.category for r in requests if r.node_key==key)),
            "jobs":[{"job_id":j.job_id,"provider_job_id":j.provider_job_id,"phase":j.phase,
                "model_id":j.model_id,"attempt_id":j.attempt_id,"error":j.error,
                "verification":j.verification,"progress":None,
                "artifact_uuid":by_job.get(j.job_id) if j.phase=="verified" else None}
                for j in jobs if j.node_key==key]}
        if capability.result_kind == "publication":
            from app.orchestrator.steps.publish import preview_publication
            result[key]["publication"] = await preview_publication(project_session or master, project)
    return result
