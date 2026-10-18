"""Ревью #352 п.15: стоп разбора стиля не снимает стоп всего проекта."""

from app.services.style_agent import job as style_job


def test_stop_style_job_does_not_touch_project_stop(monkeypatch) -> None:
    called = {}

    def _boom(*_a, **_k):
        called["stop"] = True
        raise AssertionError("request_stop")

    class _Task:
        def done(self) -> bool:
            return False

        def cancel(self) -> bool:
            self.was_cancelled = True
            return True

    monkeypatch.setattr("app.services.step_cancel.request_stop", _boom)
    monkeypatch.setattr("app.services.step_cancel.clear_stop", _boom)
    task = _Task()
    style_job._tasks[4242] = task
    try:
        assert style_job.stop_job(4242) is True
        assert style_job.style_extract_cancel_requested(4242) is True
        assert "stop" not in called
        assert task.was_cancelled is True
    finally:
        style_job._tasks.pop(4242, None)
        style_job._style_cancel.discard(4242)
