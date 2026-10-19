from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.services import timeline_ru as tr


def test_node_change_transitions_are_russian():
    title, detail, _sev = tr.describe({
        "source": "observation_events", "kind": "node_change",
        "payload": {"transitions": [{"from": "pending", "to": "queued", "initiator": "api"},
                                    {"from": "running", "to": "done", "initiator": "worker"}]}})
    text = f"{title} {detail}"
    for english in ("pending", "queued", "running", "done", "worker", "api"):
        assert english not in text, text
    assert "в очереди" in text and "готово" in text and "воркер" in text


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="нужны ffmpeg и ffprobe")
def test_provider_mp3_sfx_is_verified_by_ffprobe_not_as_wav(tmp_path):
    from app.services.sfx_plan import _sfx_wav_record

    mp3 = tmp_path / "sfx_01_door.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", "-q:a", "5",
                    str(mp3)], check=True)
    frame = SimpleNamespace(id=1, project_id=7, uuid="u" * 32, number=3)
    rec = {"idx": 1, "path": str(mp3), "frame_number": 3, "provider": "elevenlabs"}
    out = _sfx_wav_record(rec, {3: frame})
    assert out["provider"] == "elevenlabs" and out["duration_seconds"] > 1.0 and out["sha256"]
    assert out["source_frame"]["number"] == 3
