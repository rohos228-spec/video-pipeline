"""nvidia_asr environment + lock helpers."""

import os
import tempfile
import time
from pathlib import Path

import pytest

from app.services.nvidia_asr import _is_file_lock_error
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
