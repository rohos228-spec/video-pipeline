"""nvidia_asr WinError 32 helpers."""

from app.services.nvidia_asr import _is_file_lock_error


def test_is_file_lock_error_winerror_32() -> None:
    exc = OSError(32, "Процесс не может получить доступ к файлу")
    exc.winerror = 32  # type: ignore[attr-defined]
    assert _is_file_lock_error(exc)


def test_is_file_lock_error_permission() -> None:
    assert _is_file_lock_error(PermissionError("locked"))
