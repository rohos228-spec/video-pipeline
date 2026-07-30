"""Автономный харнес: verify проект → ops journal → soft repair (без wipe/audio/music)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.xlsx_v8_import import ROW_VIDEO_PROMPT_V8

# Шаги, которые харнес НИКОГДА не запускает.
HARNESS_FORBIDDEN_STEPS = frozenset({"audio", "music"})
# Soft repair кандидаты (если verify скажет missing/failed).
HARNESS_REPAIRABLE_STEPS = frozenset(
    {
        "plan",
        "script",
        "split",
        "hero",
        "items",
        "img_pr",
        "image_prompts",
        "img",
        "images",
        "anim_pr",
        "animation_prompts",
        "video",
        "videos",
        "assemble",
        "excel_gpt",
    }
)


@dataclass
class HarnessCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class HarnessReport:
    project_id: int
    run_id: str
    ok: bool
    status: str
    checks: list[HarnessCheck] = field(default_factory=list)
    next_action: str = "none"
    repair_steps: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "ok": self.ok,
            "status": self.status,
            "checks": [asdict(c) for c in self.checks],
            "next_action": self.next_action,
            "repair_steps": list(self.repair_steps),
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _db_path() -> Path:
    from app.settings import settings

    p = Path(getattr(settings, "db_path", "") or "")
    if p.is_file():
        return p
    return _repo_root() / "data" / "state.db"


def ops_dir_for_project(data_dir: Path) -> Path:
    d = data_dir / "ops"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_ops_event(data_dir: Path, event: dict[str, Any]) -> Path:
    path = ops_dir_for_project(data_dir) / "events.jsonl"
    row = dict(event)
    row.setdefault("at", _now())
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read_ops_telemetry(meta: dict[str, Any] | None) -> dict[str, Any]:
    m = meta if isinstance(meta, dict) else {}
    raw = m.get("ops_telemetry")
    return dict(raw) if isinstance(raw, dict) else {}


def write_ops_telemetry(meta: dict[str, Any], report: HarnessReport) -> dict[str, Any]:
    out = dict(meta) if isinstance(meta, dict) else {}
    out["ops_telemetry"] = {
        "harness_run_id": report.run_id,
        "updated_at": report.updated_at or _now(),
        "phase": "verify",
        "last_step": report.repair_steps[0] if report.repair_steps else None,
        "last_outcome": "verify_pass" if report.ok else "verify_fail",
        "artifact_ok": report.ok,
        "next_action": report.next_action,
        "checks": [asdict(c) for c in report.checks],
        "pointers": {
            "step_failure": (out.get("step_failure") or {}),
            "chrome_recovery": (out.get("chrome_recovery") or {}),
        },
    }
    return out


def _count_r48_filled(xlsx: Path) -> int:
    if not xlsx.is_file():
        return 0
    try:
        from openpyxl import load_workbook

        wb = load_workbook(xlsx, read_only=True, data_only=True)
        ws = None
        for name in wb.sheetnames:
            if name.strip().casefold() == "план":
                ws = wb[name]
                break
        if ws is None:
            wb.close()
            return 0
        n = 0
        for col in range(3, 20):
            v = ws.cell(ROW_VIDEO_PROMPT_V8, col).value
            if v and str(v).strip():
                n += 1
        wb.close()
        return n
    except Exception as e:  # noqa: BLE001
        logger.warning("harness R48 read failed: {}", e)
        return 0


def verify_project_disk(project_id: int, data_dir: Path, status: str) -> HarnessReport:
    """Синхронная проверка диска/БД без HTTP (для CLI и сервиса)."""
    run_id = uuid.uuid4().hex[:12]
    checks: list[HarnessCheck] = []
    repair: list[str] = []

    xlsx = data_dir / "project.xlsx"
    checks.append(
        HarnessCheck("project_xlsx", xlsx.is_file(), str(xlsx) if xlsx.is_file() else "missing")
    )
    if not xlsx.is_file():
        repair.append("plan")

    scenes = list((data_dir / "scenes").glob("*.png")) if (data_dir / "scenes").is_dir() else []
    videos = list((data_dir / "videos").glob("*.mp4")) if (data_dir / "videos").is_dir() else []
    heroes = (
        list((data_dir / "characters").glob("*.png"))
        if (data_dir / "characters").is_dir()
        else []
    )
    final = list((data_dir / "final").glob("*.mp4")) if (data_dir / "final").is_dir() else []

    checks.append(HarnessCheck("scenes_png", len(scenes) >= 1, f"n={len(scenes)}"))
    checks.append(HarnessCheck("videos_mp4", len(videos) >= 1, f"n={len(videos)}"))
    checks.append(HarnessCheck("heroes_png", True, f"n={len(heroes)}"))  # optional
    checks.append(
        HarnessCheck(
            "final_mp4",
            status != "assembled" or len(final) >= 1,
            f"n={len(final)} status={status}",
        )
    )
    if status == "assembled" and not final:
        repair.append("assemble")
    if not scenes:
        repair.append("img")
    if not videos:
        repair.append("video")

    r48 = _count_r48_filled(xlsx) if xlsx.is_file() else 0
    need_r48 = max(len(scenes), 1) if scenes else 0
    r48_ok = (not scenes) or (r48 >= min(need_r48, 9) if need_r48 else r48 >= 0)
    # If we have N scenes, expect N anim prompts when assembled/videos_ready+
    if scenes and status in {
        "animation_prompts_ready",
        "generating_video",
        "videos_ready",
        "generating_audio",
        "audio_ready",
        "generating_music",
        "music_ready",
        "assembling",
        "assembled",
        "published",
    }:
        r48_ok = r48 >= len(scenes)
        if not r48_ok:
            repair.append("anim_pr")
    checks.append(HarnessCheck("r48_anim", r48_ok, f"filled={r48} scenes={len(scenes)}"))

    # node_runs failed
    failed_n = 0
    try:
        db = sqlite3.connect(str(_db_path()))
        row = db.execute(
            "SELECT id FROM workflow_runs WHERE project_id=? ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if row:
            failed_n = db.execute(
                "SELECT COUNT(*) FROM node_runs WHERE workflow_run_id=? AND status='failed'",
                (row[0],),
            ).fetchone()[0]
        db.close()
    except Exception as e:  # noqa: BLE001
        checks.append(HarnessCheck("node_runs", False, str(e)))
    else:
        checks.append(HarnessCheck("node_runs_failed", failed_n == 0, f"failed={failed_n}"))

    # voiceover hygiene (optional)
    vo = data_dir / "voiceover.txt"
    if vo.is_file():
        text = vo.read_text(encoding="utf-8", errors="replace")
        polluted = "# Лист:" in text or "vp.check.v1" in text or "ОТЧЁТ ПРОВЕРКИ" in text
        checks.append(HarnessCheck("voiceover_clean", not polluted, "polluted" if polluted else "ok"))
        if polluted:
            repair.append("script")

    ok = all(c.ok for c in checks)
    # Filter forbidden
    repair = [s for s in repair if s not in HARNESS_FORBIDDEN_STEPS]
    next_action = "none" if ok else ("repair:" + ",".join(repair) if repair else "investigate")
    return HarnessReport(
        project_id=project_id,
        run_id=run_id,
        ok=ok,
        status=status,
        checks=checks,
        next_action=next_action,
        repair_steps=repair,
        updated_at=_now(),
    )


async def run_harness_verify(
    session: Any,
    project: Any,
    *,
    allow_repair: bool = False,
) -> HarnessReport:
    """Verify + optional soft repair (POST step run via project_steps)."""
    data_dir = Path(project.data_dir)
    status = str(getattr(project.status, "value", project.status) or "")
    report = verify_project_disk(int(project.id), data_dir, status)

    meta = dict(project.meta) if isinstance(project.meta, dict) else {}
    project.meta = write_ops_telemetry(meta, report)
    append_ops_event(
        data_dir,
        {
            "type": "harness_verify",
            "run_id": report.run_id,
            "ok": report.ok,
            "status": status,
            "checks": [asdict(c) for c in report.checks],
            "next_action": report.next_action,
        },
    )

    if allow_repair and report.repair_steps:
        step = report.repair_steps[0]
        if step in HARNESS_FORBIDDEN_STEPS:
            logger.warning("harness: refuse forbidden step {}", step)
        else:
            try:
                from app.services.project_steps import start_step

                code = {
                    "image_prompts": "img_pr",
                    "images": "img",
                    "animation_prompts": "anim_pr",
                    "videos": "video",
                }.get(step, step)
                if code in HARNESS_FORBIDDEN_STEPS:
                    raise RuntimeError(f"forbidden step {code}")
                logger.info("harness: soft repair step={} project={}", code, project.id)
                append_ops_event(
                    data_dir,
                    {"type": "harness_repair", "run_id": report.run_id, "step": code},
                )
                await start_step(
                    session,
                    project,
                    code,
                    require_node_fsm=False,
                    explicit_ui_start=True,
                )
                report.next_action = f"repaired:{code}"
            except Exception as e:  # noqa: BLE001
                logger.warning("harness repair failed: {}", e)
                report.next_action = f"repair_failed:{step}:{e}"
                append_ops_event(
                    data_dir,
                    {
                        "type": "harness_repair_failed",
                        "run_id": report.run_id,
                        "step": step,
                        "error": str(e),
                    },
                )

    await session.flush()
    return report


def harness_status_payload(project: Any) -> dict[str, Any]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    tel = read_ops_telemetry(meta)
    data_dir = Path(project.data_dir)
    events_path = data_dir / "ops" / "events.jsonl"
    last_events: list[dict[str, Any]] = []
    if events_path.is_file():
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in lines[-20:]:
            try:
                last_events.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return {
        "project_id": project.id,
        "status": str(getattr(project.status, "value", project.status)),
        "ops_telemetry": tel,
        "events_tail": last_events,
        "forbidden_steps": sorted(HARNESS_FORBIDDEN_STEPS),
    }
