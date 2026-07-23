"""nvidia_asr environment + lock helpers."""

import os
import tempfile
import time
from pathlib import Path

import pytest

from app.services import nvidia_asr
from app.services.nvidia_asr import (
    _is_file_lock_error,
    _nemo_filename,
    _nemo_file_ready,
    _stable_nemo_path,
)
from app.services.nvidia_asr_env import (
    clear_stale_nvidia_load_lock,
    configure_nvidia_asr_environment,
)


def test_configure_forces_temp_out_of_appdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
    monkeypatch.setenv("TEMP", r"C:\Users\X\AppData\Local\Temp")
    configure_nvidia_asr_environment(force=True)
    expected = str((tmp_path / "data" / ".cache" / "temp").resolve())
    assert os.environ["TEMP"] == expected
    assert os.environ["TMP"] == expected
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"
    assert tempfile.gettempdir() == expected


def test_clear_stale_lock_by_age(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "data" / ".cache"
    lock = cache / "locks" / "parakeet.load.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    assert clear_stale_nvidia_load_lock(cache) is True
    assert not lock.is_file()


def test_clear_stale_lock_keeps_fresh(tmp_path: Path) -> None:
    cache = tmp_path / "data" / ".cache"
    lock = cache / "locks" / "parakeet.load.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    assert clear_stale_nvidia_load_lock(cache) is False
    assert lock.is_file()


def test_is_file_lock_error_russian_message() -> None:
    exc = PermissionError(
        "[WinError 32] Процесс не может получить доступ к файлу, "
        "так как этот файл занят другим процессом"
    )
    assert _is_file_lock_error(exc)


def test_nemo_filename_for_parakeet_v3() -> None:
    assert _nemo_filename("nvidia/parakeet-tdt-0.6b-v3") == "parakeet-tdt-0.6b-v3.nemo"


def test_nemo_file_ready_requires_size(tmp_path: Path) -> None:
    tiny = tmp_path / "tiny.nemo"
    tiny.write_bytes(b"x" * 1024)
    assert _nemo_file_ready(tiny) is False
    big = tmp_path / "big.nemo"
    big.write_bytes(b"x" * (nvidia_asr._MIN_NEMO_BYTES + 1))
    assert _nemo_file_ready(big) is True


def test_download_model_uses_local_nemo_without_hf_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "data" / ".cache"
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
    model = "nvidia/parakeet-tdt-0.6b-v3"
    stable = _stable_nemo_path(model, cache)
    stable.parent.mkdir(parents=True)
    stable.write_bytes(b"x" * (nvidia_asr._MIN_NEMO_BYTES + 1))

    def fake_restore(_model_name: str, nemo_path: Path):
        assert nemo_path == stable
        return object()

    def fail_download(*_args, **_kwargs):
        raise AssertionError("hf download must not run when local .nemo exists")

    monkeypatch.setattr(nvidia_asr, "_restore_nemo_model", fake_restore)
    monkeypatch.setattr(nvidia_asr, "_http_download_nemo", fail_download)
    nvidia_asr._download_model(model)
