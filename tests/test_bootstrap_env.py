"""Early bootstrap env for NVIDIA ASR on Windows."""

import os
import tempfile
from pathlib import Path

import pytest

import app.bootstrap_env as bootstrap


def test_bootstrap_uses_pid_temp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("ASR_BACKEND", "nvidia")
    bootstrap.apply_nvidia_env(force=True)
    temp = os.environ["TEMP"]
    assert temp.endswith(f"pid-{os.getpid()}")
    assert tempfile.gettempdir() == temp
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"


def test_apply_if_nvidia_skips_without_explicit_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ASR_BACKEND", raising=False)
    called = {"n": 0}

    def _mark(**_k):
        called["n"] += 1

    monkeypatch.setattr(bootstrap, "apply_nvidia_env", _mark)
    bootstrap.apply_if_nvidia_asr(force=True)
    assert called["n"] == 0


def test_apply_if_nvidia_runs_when_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASR_BACKEND", "nvidia")
    called = {"n": 0}

    def _mark(**_k):
        called["n"] += 1

    monkeypatch.setattr(bootstrap, "apply_nvidia_env", _mark)
    bootstrap.apply_if_nvidia_asr(force=True)
    assert called["n"] == 1
