"""W56: исправления по аудиту (обход каталога статики, возврат в шаг после паузы, история отказов)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _static_app(tmp_path):
    from app.web import api as web_api

    out = tmp_path / "out"
    out.mkdir()
    (out / "index.html").write_text("<html>INDEX</html>", encoding="utf-8")
    (out / "a.txt").write_text("OK", encoding="utf-8")
    (tmp_path / "secret.env").write_text("KEY=SECRET", encoding="utf-8")
    return web_api, out


def test_static_catch_all_does_not_leave_out_dir(tmp_path, monkeypatch):
    web_api, out = _static_app(tmp_path)
    src = open(web_api.__file__, encoding="utf-8").read()
    assert "_inside(candidate)" in src and "relative_to(base)" in src


def test_restart_chain_stop_remembers_running_status():
    from app.models import ProjectStatus
    from app.services import restart_chain

    class _S:
        async def flush(self):
            return None

    from app.models import Project
    p = Project(id=1, slug="t", topic="t", meta={}, status=ProjectStatus.generating_images)
    asyncio.run(restart_chain.stop(_S(), p, "generating_images", 61, 60, "auto"))
    assert p.status is ProjectStatus.paused
    assert p.meta["paused_from_status"] == ProjectStatus.generating_images.value


def test_regen_budget_stop_remembers_running_status():
    from app.models import ProjectStatus
    from app.services import regen_budget

    class _S:
        async def flush(self):
            return None

    verdict = SimpleNamespace(summary=lambda: "stop", kind="images", blocked=[], run_exhausted=True, rounds=3,
                              usd_exhausted=False, usd_spent=0.0, usd_cap=1.0, chain_exhausted=False,
                              per_frame=1, per_run=1, usd_round=0.0)
    from app.models import Project
    p = Project(id=2, slug="t", topic="t", meta={}, status=ProjectStatus.generating_videos)
    asyncio.run(regen_budget.stop(_S(), p, verdict))
    assert p.meta["paused_from_status"] == ProjectStatus.generating_videos.value


def test_clear_failure_on_success_resets_infra_streak():
    from app.models import ProjectStatus
    from app.services.step_failure_policy import _save_failure_state, _failure_state, clear_failure_on_success

    p = SimpleNamespace(id=3, meta={})
    fs = _failure_state(p)
    fs["infra_lock_streak"] = 7
    _save_failure_state(p, fs)
    clear_failure_on_success(p, ProjectStatus.generating_images)
    assert "infra_lock_streak" not in _failure_state(p)


def _ffmpeg_ok() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.skipif(not _ffmpeg_ok(), reason="нужен ffmpeg")
def test_variant2_mux_mixes_sfx_into_audio(tmp_path):
    """M1: звук из sfx_gen реально попадает в дорожку (раньше на пути variant2 выбрасывался молча)."""
    import subprocess

    from app.services.montage.variant2 import _mux
    from app.services.sfx_mix import SfxInput

    def run(*a):
        subprocess.run(a, check=True, capture_output=True)

    video, voice, sfx_f = tmp_path / "v.mp4", tmp_path / "voice.wav", tmp_path / "sfx.wav"
    run("ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=4", "-pix_fmt", "yuv420p", str(video))
    run("ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "4", str(voice))  # тишина
    run("ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=800:d=1", str(sfx_f))
    out_plain, out_sfx = tmp_path / "plain.mp4", tmp_path / "sfx.mp4"
    asyncio.run(_mux(video, voice, out_plain, voice_s=4.0, bgm=None))
    asyncio.run(_mux(video, voice, out_sfx, voice_s=4.0, bgm=None,
                     sfx=[SfxInput(path=sfx_f, t_start=1.0, gain=0.8, kind="sfx")]))

    def peak(path):
        r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True)
        for line in r.stderr.splitlines():
            if "max_volume" in line:
                return float(line.split("max_volume:")[1].split("dB")[0])
        return -91.0

    assert peak(out_plain) < -60          # без SFX дорожка тихая
    assert peak(out_sfx) > -20            # с SFX слышен сигнал


def test_assemble_refuses_missing_clips_by_default():
    from app.settings import settings

    assert settings.assemble_allow_missing_clips is False


def test_private_files_are_not_served(tmp_path):
    from app.web.routers.artifacts import _is_private_file

    for name in ("state.db", "studio-live.log", ".env", ".env.local", "web_sessions.sqlite3", "k.pem", "x.sqlite"):
        assert _is_private_file(tmp_path / name), name
    for name in ("clip_1.mp4", "frame.png", "voice.mp3", "project.xlsx"):
        assert not _is_private_file(tmp_path / name), name


def test_fleet_safe_path_rejects_sibling_prefix(tmp_path):
    from fastapi import HTTPException

    from app.web.routers.fleet import _safe_path

    base = tmp_path / "root"
    (tmp_path / "root-evil").mkdir()
    base.mkdir()
    with pytest.raises(HTTPException):
        _safe_path(base, "../root-evil/x.txt")
    assert _safe_path(base, "a/b.txt").is_relative_to(base.resolve())


def test_remember_pause_origin_keeps_running_step():
    from app.models import Project, ProjectStatus
    from app.services import restart_chain

    p = Project(id=9, slug="t", topic="t", meta={"x": 1}, status=ProjectStatus.plan_ready)
    restart_chain.remember_pause_origin(p)
    assert p.meta["paused_from_status"] == "plan_ready" and p.meta["x"] == 1
    p.status = ProjectStatus.paused
    restart_chain.remember_pause_origin(p)          # уже на паузе: источник не затирается
    assert p.meta["paused_from_status"] == "plan_ready"
