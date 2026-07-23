"""nvidia_asr environment + lock helpers."""

import os
import tempfile
from pathlib import Path

import pytest

from app.services.nvidia_asr import _is_file_lock_error
from app.services.nvidia_asr_env import configure_nvidia_asr_environment


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


def test_is_file_lock_error_russian_message() -> None:
    exc = PermissionError(
        "[WinError 32] Процесс не может получить доступ к файлу, "
        "так как этот файл занят другим процессом"
    )
    assert _is_file_lock_error(exc)
