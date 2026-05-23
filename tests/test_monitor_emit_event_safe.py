"""Tests verifying that emit_event() never propagates I/O errors, and that
_screenshot_if_available() never propagates CancelledError.

Critical correctness requirement: both emit_event() and
_screenshot_if_available() are called (a) before the wrapped pipeline method
runs and (b) inside finally blocks after it runs.  If either raised, the
pipeline method would either never execute or have its successful result
discarded.  We verify the safe-by-default behaviour: errors are swallowed
and logged as warnings.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.monitor import log_sink


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_log_sink() -> None:
    """Reset module-level state so tests don't bleed into each other."""
    with log_sink._lock:
        if log_sink._events_file is not None:
            try:
                log_sink._events_file.close()
            except Exception:
                pass
        log_sink._events_file = None
    log_sink._monitor_dir = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmitEventNeverRaises:
    """emit_event() must be a no-throw function."""

    def setup_method(self):
        _reset_log_sink()

    def teardown_method(self):
        _reset_log_sink()

    def test_emit_event_no_file_open_is_noop(self):
        """With no file open, emit_event silently does nothing."""
        log_sink.emit_event("test_event", project_id=1, step="planning")

    def test_emit_event_writes_to_open_file(self, tmp_path: Path):
        """With a file open, emit_event writes a JSON line."""
        log_sink.init(tmp_path)
        log_sink.emit_event("pipeline_step", project_id=42, step="scripting")

        events_dir = tmp_path / "events"
        jsonl_files = list(events_dir.glob("events_*.jsonl"))
        assert jsonl_files, "events JSONL file should be created"

        content = jsonl_files[0].read_text(encoding="utf-8")
        assert "pipeline_step" in content
        assert '"project_id": 42' in content

    def test_emit_event_swallows_oserror_on_write(self):
        """An OSError during file write must NOT propagate to the caller.

        Trigger: _events_file.write() raises OSError (e.g. disk full).
        Expected: emit_event() returns normally; pipeline code is unaffected.
        """
        broken_file = MagicMock(spec=io.TextIOBase)
        broken_file.write.side_effect = OSError("No space left on device")

        with log_sink._lock:
            log_sink._events_file = broken_file

        try:
            # Must NOT raise — this is the critical invariant.
            log_sink.emit_event("chatgpt_ask_fresh_start", project_id=1)
        finally:
            with log_sink._lock:
                log_sink._events_file = None

    def test_emit_event_swallows_oserror_on_flush(self):
        """An OSError during flush must NOT propagate to the caller."""
        broken_file = MagicMock(spec=io.TextIOBase)
        broken_file.flush.side_effect = OSError("I/O error")

        with log_sink._lock:
            log_sink._events_file = broken_file

        try:
            log_sink.emit_event("outsee_generate_image_start", project_id=2)
        finally:
            with log_sink._lock:
                log_sink._events_file = None

    def test_emit_event_write_failure_does_not_prevent_pipeline_method(self):
        """Simulate the full failure scenario: emit_event raises inside a
        pipeline wrapper → the wrapped method must still be called.

        This test recreates the concrete crash scenario from the bug:
        1. emit_event("*_start") is called before the original method.
        2. The file write fails (OSError).
        3. Before the fix: OSError propagated → original method never called.
        4. After the fix: emit_event swallows the error → original runs fine.
        """
        called = []

        async def fake_pipeline_method():
            called.append(True)
            return "result"

        broken_file = MagicMock(spec=io.TextIOBase)
        broken_file.write.side_effect = OSError("No space left on device")

        with log_sink._lock:
            log_sink._events_file = broken_file

        try:
            # Simulate what _wrap_async's wrapper does:
            #   1. emit start event (file broken)
            #   2. run original method
            log_sink.emit_event("some_step_start")         # must not raise
            result = asyncio.get_event_loop().run_until_complete(fake_pipeline_method())
        finally:
            with log_sink._lock:
                log_sink._events_file = None

        assert called, "Pipeline method must be called even when emit_event write fails"
        assert result == "result"

    def test_emit_event_finally_failure_does_not_discard_result(self):
        """Simulate the full finally-block failure scenario:

        1. Original pipeline method succeeds, stores result.
        2. emit_event("*_end") in the finally block encounters an OSError.
        3. Before the fix: OSError propagated from finally → successful result lost.
        4. After the fix: emit_event swallows the error → result is returned.
        """
        broken_file = MagicMock(spec=io.TextIOBase)
        broken_file.write.side_effect = OSError("No space left on device")

        async def simulate_wrapper():
            """Mirrors the structure of _wrap_async's inner wrapper."""
            result = None
            error_info = None
            with log_sink._lock:
                log_sink._events_file = broken_file
            try:
                result = "pipeline-output"
                return result
            except Exception as exc:
                error_info = str(exc)
                raise
            finally:
                # This is the call that used to crash before the fix.
                log_sink.emit_event(
                    "some_step_end",
                    detail={"duration_s": 1.0, **({"error": error_info} if error_info else {})},
                )
                with log_sink._lock:
                    log_sink._events_file = None

        returned = asyncio.get_event_loop().run_until_complete(simulate_wrapper())
        assert returned == "pipeline-output", (
            "Successful pipeline result must not be discarded when emit_event "
            "write fails in the finally block"
        )


class TestScreenshotCancelledError:
    """`_screenshot_if_available` must swallow CancelledError.

    CancelledError is a BaseException (not Exception) in Python 3.8+.  The
    old `except Exception` guard missed it, so a CancelledError raised inside
    `take_screenshot_now()` would escape into the `finally` block of
    `_wrap_async`, replacing a successfully computed return value with an
    exception.  Concrete impact: an outsee image that was already saved to
    disk would be lost because the `Artifact` record was never committed.
    """

    def setup_method(self):
        _reset_log_sink()

    def teardown_method(self):
        _reset_log_sink()

    def test_screenshot_if_available_swallows_cancelled_error(self):
        """_screenshot_if_available must not propagate CancelledError."""
        from app.monitor import action_tracker

        watcher = MagicMock()
        watcher.take_screenshot_now = AsyncMock(
            side_effect=asyncio.CancelledError("task cancelled during screenshot")
        )

        # Temporarily inject the mock watcher.
        original_watcher = action_tracker._watcher
        action_tracker._watcher = watcher
        try:
            result = asyncio.get_event_loop().run_until_complete(
                action_tracker._screenshot_if_available("test_label")
            )
        finally:
            action_tracker._watcher = original_watcher

        assert result == [], (
            "_screenshot_if_available must return [] when take_screenshot_now "
            "raises CancelledError"
        )

    def test_cancelled_error_in_finally_does_not_discard_pipeline_result(self):
        """A CancelledError from the post-operation screenshot must not
        replace the successful return value of the wrapped pipeline method.

        This is the concrete bug scenario:
        1. outsee.generate_image() completes — file saved to disk.
        2. In the finally block, _screenshot_if_available raises CancelledError.
        3. Before fix: CancelledError escapes, result is lost — pipeline must
           re-generate the image (wasted API credits, potential duplicate file).
        4. After fix: CancelledError is swallowed, result is returned normally.
        """
        from app.monitor import action_tracker

        watcher = MagicMock()
        watcher.take_screenshot_now = AsyncMock(
            side_effect=asyncio.CancelledError("task cancelled")
        )

        original_watcher = action_tracker._watcher
        action_tracker._watcher = watcher
        try:
            async def simulate_wrap_async_finally():
                """Mirrors _wrap_async's finally block with screenshot_after=True."""
                try:
                    return "pipeline-output"
                finally:
                    # This is exactly what _wrap_async's finally block does.
                    await action_tracker._screenshot_if_available("some_step_after")

            returned = asyncio.get_event_loop().run_until_complete(
                simulate_wrap_async_finally()
            )
        finally:
            action_tracker._watcher = original_watcher

        assert returned == "pipeline-output", (
            "Successful pipeline result must not be discarded when "
            "_screenshot_if_available raises CancelledError in the finally block"
        )
