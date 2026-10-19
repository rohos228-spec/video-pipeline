from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0"
       b"\xa0\x00\x00\x00\x00IEND\xaeB`\x82")
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom\x00\x00\x00\x08free"

N_FRAMES = 30
SCENE_SIZES = (3, 2, 1)
SHOT2_FRAMES = (2, 4, 8, 12, 20)
IMAGE_FRAMES = tuple(n for n in range(1, 25) if n != 14)
VIDEO_FRAMES = tuple(range(1, 13))
SHOT2_IMAGE_FRAMES = (2, 4, 8)
SHOT2_VIDEO_FRAMES = (4,)
NODES = ("n_excel_gpt_fw_script", "n_excel_gpt_fw_action", "n_excel_gpt_fw_shots",
         "n_excel_gpt_fw_qc", "n_excel_gpt_fw_report")
PROMPT_STEPS_COPIED = ("01_plan", "02_script", "03_razbivka", "05_excel_gpt")
MAIN_TEXT = "# main\nОсновной вариант мастер-промта для оценки планировщика.\n"
DRAFT_TEXT = "# draft\nЧерновик мастер-промта для оценки планировщика.\n"


@dataclass
class SeedInfo:
    project_id: int
    frame_ids: dict[int, int] = field(default_factory=dict)
    proposals: list[dict] = field(default_factory=list)
    targets: dict[str, int] = field(default_factory=dict)
    queue: list[dict] = field(default_factory=list)
    data_dir: Path | None = None
    prompts_root: Path | None = None


def scene_layout(n_frames: int = N_FRAMES) -> list[str]:
    roles: list[str] = []
    i = 0
    while len(roles) < n_frames:
        size = SCENE_SIZES[i % len(SCENE_SIZES)]
        roles += ["p"] + ["c"] * (size - 1)
        i += 1
    return roles[:n_frames]


def queue_ops() -> list[dict]:
    return [
        {"type": "image_regen", "frame_number": 3, "shot": 1},
        {"type": "image_ai_change", "frame_number": 3, "shot": 1, "instruction": "сделай небо темнее"},
        {"type": "image_regen", "frame_number": 4, "shot": 1},
        {"type": "video_regen", "frame_number": 8, "shot": 1},
    ]


def write_media(data_dir: Path) -> None:
    scenes, videos = data_dir / "scenes", data_dir / "videos"
    for d in (scenes, videos):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    for n in IMAGE_FRAMES:
        (scenes / f"frame_{n:03d}_seed.png").write_bytes(PNG)
    for n in SHOT2_IMAGE_FRAMES:
        (scenes / f"frame_{n:03d}_s2_seed.png").write_bytes(PNG)
    for n in VIDEO_FRAMES:
        (videos / f"clip_{n:03d}_seed.mp4").write_bytes(MP4)
    for n in SHOT2_VIDEO_FRAMES:
        (videos / f"clip_{n:03d}_s2_seed.mp4").write_bytes(MP4)


def seed_prompts(dest: Path) -> Path:
    from app.services import prompt_library as pl
    src = Path(pl.PROMPTS_ROOT)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for folder in PROMPT_STEPS_COPIED:
        if (src / folder).is_dir():
            shutil.copytree(src / folder, dest / folder)
        else:
            (dest / folder).mkdir()
    (dest / "02_script" / "main.md").write_text(MAIN_TEXT, encoding="utf-8")
    (dest / "02_script" / "draft.md").write_text(DRAFT_TEXT, encoding="utf-8")
    (dest / "05_excel_gpt" / "main.md").write_text(MAIN_TEXT, encoding="utf-8")
    pl.PROMPTS_ROOT = dest
    return dest


async def _seed_project(s, project_id: int | None, data_dir_hint: Path | None):
    from sqlalchemy import delete, select
    from app.models import Frame, Project, ProjectStatus, Workflow, WorkflowRun

    project = await s.get(Project, project_id) if project_id else None
    if project is None:
        project = Project(topic="Оценка планировщика", slug=f"eval-seed-{project_id or 'new'}")
        if project_id:
            project.id = project_id
        s.add(project)
    project.title = "Оценка планировщика"
    project.status = ProjectStatus.frames_ready
    project.image_generator, project.image_resolution = "nano_banana_2", "2k"
    project.aspect_ratio, project.image_quality = "9_16", "medium"
    project.video_generator, project.video_resolution = "veo_3_fast", "1080p"
    project.prompt_overrides = {"script": "main", "excel_gpt": "main"}
    project.meta = {"montage_board": {"pending_ops": queue_ops()}}
    await s.flush()
    pid = project.id
    await s.execute(delete(Frame).where(Frame.project_id == pid))
    roles = scene_layout()
    for n in range(1, N_FRAMES + 1):
        attrs: dict = {}
        if roles[n - 1] == "c":
            attrs["camera_subdivide"] = {"role": "shot"}
        if n in SHOT2_FRAMES:
            attrs["image_prompt_shot2"] = f"финальный план кадра {n}"
        s.add(Frame(id=frame_id_of(pid, n), project_id=pid, number=n,
                    voiceover_text=f"Реплика кадра {n}.", uuid=frame_uuid_of(pid, n),
                    sort_key=float(n), attrs=attrs))
    nodes = [{"id": nid, "type": "excel_gpt", "data": {"type": "excel_gpt"}} for nid in NODES]
    run = await s.scalar(select(WorkflowRun).where(WorkflowRun.project_id == pid))
    if run is None:
        wf = Workflow(name="eval seed", nodes=nodes, edges=[])
        s.add(wf)
        await s.flush()
        s.add(WorkflowRun(workflow_id=wf.id, project_id=pid, nodes_snapshot=nodes, edges_snapshot=[]))
    else:
        run.nodes_snapshot = nodes
    await s.flush()
    ids = {r.number: r.id for r in (await s.execute(
        select(Frame.number, Frame.id).where(Frame.project_id == pid))).all()}
    return project, ids


def frame_id_of(pid: int, number: int) -> int:
    return 1000 + 100 * pid + number


def frame_uuid_of(pid: int, number: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"eval-seed-{pid}-{number}"))


def proposal_specs(pid: int) -> list[dict]:
    from app.services.change_control import store as cs

    shots = f"project:{pid}:n_excel_gpt_fw_shots:prompt"
    qc_opts = f"project:{pid}:n_excel_gpt_fw_qc:options"
    script_params = f"project:{pid}:n_excel_gpt_fw_script:step_params"
    spec = [
        ("T2", "prompt_text", "n_excel_gpt_fw_shots", shots, 1,
         {"text": "Правка промпта шотов, версия 2."}, "applied", 2),
        ("T2", "prompt_text", "n_excel_gpt_fw_shots", shots, 2,
         {"text": "Правка промпта шотов, версия 3."}, "applied", 3),
        ("T1", "node_options", "n_excel_gpt_fw_qc", qc_opts, 1,
         {"values": {"checkFix": True}}, "proposed", None),
        ("T0", "suggestion", "", "", 0, {"text": "Сократить закадр в кадрах 10-12."}, "proposed", None),
        ("T1", "step_params", "n_excel_gpt_fw_script", script_params, 0,
         {"values": {"duration_seconds": 60}}, "proposed", None),
    ]
    out: list[dict] = []
    for i, (tier, kind, node, target, bver, cand, status, applied) in enumerate(spec):
        digest = cs.sha({"tier": tier, "project_id": pid, "node_key": node, "kind": kind,
                         "target": target, "base_version": bver, "candidate": cand})
        out.append({"n": i + 1, "id": uuid.uuid5(uuid.NAMESPACE_DNS, f"eval-seed-proposal-{pid}-{i}").hex,
                    "digest": digest, "status": status, "target": target, "tier": tier, "kind": kind,
                    "node": node, "base": bver, "candidate": cand, "applied": applied})
    return out


def expected_vars(pid: int = 2) -> dict:
    out: dict = {}
    for n in range(1, N_FRAMES + 1):
        out[f"frame.{n}"] = frame_id_of(pid, n)
        out[f"uuid.{n}"] = frame_uuid_of(pid, n)
    for p in proposal_specs(pid):
        out[f"proposal.{p['n']}.id"] = p["id"]
        out[f"proposal.{p['n']}.digest"] = p["digest"]
    out["target.shots_prompt"] = f"project:{pid}:n_excel_gpt_fw_shots:prompt"
    out["version.shots_prompt"] = 3
    out["target.qc_options"] = f"project:{pid}:n_excel_gpt_fw_qc:options"
    out["version.qc_options"] = 1
    return out


def fake_attachments(project_id: int, specs: list[dict] | None) -> list:
    from app.services.action_layer.attachments import synthetic_ref
    return [synthetic_ref(project_id, str(sp["kind"]), str(sp["name"]), int(sp.get("size", 1024)),
                          att_id=uuid.uuid5(uuid.NAMESPACE_DNS, f"eval-attachment-{project_id}-{i}-{sp['name']}").hex)
            for i, sp in enumerate(specs or [])]


async def _seed_changes(s, pid: int) -> tuple[list[dict], dict[str, int]]:
    from sqlalchemy import delete, select
    from app.services.change_control import store as cs

    await s.execute(delete(cs.ChangeProposal).where(cs.ChangeProposal.project_id == pid))
    await s.execute(delete(cs.ConfigPointer).where(cs.ConfigPointer.target.like(f"project:{pid}:%")))
    base = datetime(2026, 10, 1, 12, 0, 0)
    out = proposal_specs(pid)
    for p in out:
        i = p["n"] - 1
        s.add(cs.ChangeProposal(
            id=p["id"], tier=p["tier"], project_id=pid, node_key=p["node"], kind=p["kind"],
            target=p["target"], base_version=p["base"], base_digest=cs.sha({"v": p["base"]}),
            candidate=p["candidate"], diff="", rationale=f"предложение {i + 1}", proposer="planner:eval",
            status=p["status"], applied_version=p["applied"], digest=p["digest"],
            created_at=base + timedelta(minutes=i), updated_at=base + timedelta(minutes=i)))
    shots, qc_opts = expected_vars(pid)["target.shots_prompt"], expected_vars(pid)["target.qc_options"]
    targets = {shots: 3, qc_opts: 1}
    for target, current in targets.items():
        s.add(cs.ConfigPointer(target=target, current_version=current,
                               updated_at=base + timedelta(hours=1)))
        for v in range(1, current + 1):
            if await s.scalar(select(cs.ConfigVersion.id).where(
                    cs.ConfigVersion.target == target, cs.ConfigVersion.version == v)):
                continue
            s.add(cs.ConfigVersion(target=target, version=v, parent_version=v - 1, kind="apply",
                                   project_id=pid, node_key=target.split(":")[2],
                                   content={"text": f"версия {v}"}, digest=cs.sha({"v": v, "t": target}),
                                   applied_by="eval", applied_at=base + timedelta(minutes=10 * v)))
    return [{k: p[k] for k in ("n", "id", "digest", "status", "target", "tier")} for p in out], targets


async def seed_realistic(project_id: int | None = 2, *, factory=None,
                         prompts_dir: Path | None = None) -> SeedInfo:
    from app.services.action_layer import store
    factory = factory or store.sessions()
    async with factory() as s:
        project, ids = await _seed_project(s, project_id, None)
        proposals, targets = await _seed_changes(s, project.id)
        await s.commit()
        pid, data_dir = project.id, project.data_dir
    write_media(data_dir)
    root = seed_prompts(prompts_dir) if prompts_dir is not None else None
    return SeedInfo(project_id=pid, frame_ids=ids, proposals=proposals, targets=targets,
                    queue=queue_ops(), data_dir=data_dir, prompts_root=root)
