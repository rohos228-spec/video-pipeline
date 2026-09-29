"""Capabilities of existing registry nodes, not a new executor or workflow."""
from dataclasses import asdict, dataclass


READ_ACTIONS = ("read_events", "read_contract", "wait", "human_review")


@dataclass(frozen=True)
class NodeCapability:
    node_type: str
    result_kind: str
    executor: str
    phases: tuple[str, ...]
    effects: tuple[str, ...]
    actions: tuple[str, ...]
    managed: bool = False
    approval: tuple[str, ...] = ("scope", "budget", "provider_profile")

    def contract(self):
        return {**asdict(self), "semantic_quality_verified": False,
                "write_authority": "existing worker only",
                "arbitrary_shell_code_paths": False}


_JOB_PHASES = ("reserved", "submitted", "queued", "running", "completed",
               "downloading", "verifying", "verified", "failed", "cancelled", "unknown")
_CAPABILITIES = {
    "excel_gpt": NodeCapability("excel_gpt", "structured", "enrich_xlsx.run",
        ("running", "parse", "validate", "apply", "commit"), ("project_database",),
        READ_ACTIONS + ("retry_same", "retry_profile", "finish"), True),
    "images": NodeCapability("images", "image", "generate_images.run → kie_http",
        _JOB_PHASES, ("provider_job", "local_file", "artifact_row"), READ_ACTIONS, True),
    "videos": NodeCapability("videos", "video", "generate_videos.run → kie_http",
        _JOB_PHASES, ("provider_job", "local_file", "artifact_row"), READ_ACTIONS, True),
    "audio": NodeCapability("audio", "audio", "generate_audio.run → elevenlabs_api",
        ("reserved", "submitted", "verifying", "verified", "alignment", "failed", "unknown"),
        ("provider_generation", "local_file", "frame_timing", "artifact_row"), READ_ACTIONS, False),
    "assemble": NodeCapability("assemble", "assembly", "assemble.run → assembly.assemble",
        ("inputs", "processing", "subtitles", "verifying", "verified", "failed", "unknown"),
        ("local_file", "artifact_row"), READ_ACTIONS, False),
    "publish": NodeCapability("publish", "publication", "publish.run",
        ("input_validation", "dry_run", "blocked"), ("external_accounts",),
        ("read_events", "read_contract", "human_review"), False),
}


def capability_for(node_type: str) -> NodeCapability:
    from app.orchestrator.node_registry import is_work_node_type
    if node_type in _CAPABILITIES and is_work_node_type(node_type):
        return _CAPABILITIES[node_type]
    return NodeCapability(node_type, "unsupported", "", ("unknown",), (),
                          ("read_events", "read_contract", "human_review"))


def require_managed_capability(node_type: str):
    capability = capability_for(node_type)
    if not capability.managed:
        raise ValueError("node adapter has no accepted managed executor")
    if capability.result_kind == "video":
        import shutil
        if not shutil.which("ffprobe"):
            raise ValueError("video adapter requires ffprobe before generation")
    return capability
