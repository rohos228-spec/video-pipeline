"""nvidia_asr mono conversion for NeMo."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import nvidia_asr


def test_ensure_mono_skips_mono_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"fake")
    with patch.object(nvidia_asr, "_probe_audio_channels", return_value=1):
        assert nvidia_asr._ensure_mono_for_nemo(audio) == audio


def test_ensure_mono_converts_stereo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"fake")
    mono = tmp_path / "data" / ".cache" / "mono" / "voice_mono16k.wav"
    with (
        patch.object(nvidia_asr, "_probe_audio_channels", return_value=2),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = type("R", (), {"returncode": 0, "stderr": ""})()
        mono.parent.mkdir(parents=True, exist_ok=True)
        mono.write_bytes(b"x" * 2000)

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"x" * 2000)
            return type("R", (), {"returncode": 0, "stderr": ""})()

        mock_run.side_effect = fake_run
        out = nvidia_asr._ensure_mono_for_nemo(audio)
    assert out.name == "voice_mono16k.wav"
    assert "mono" in str(out)
