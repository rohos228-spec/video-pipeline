import importlib.util
from pathlib import Path

_PATH = Path(__file__).parent.parent / "scripts" / "e2e_stop_resume_excel_gpt.py"
_spec = importlib.util.spec_from_file_location("e2e_stop_resume_excel_gpt", _PATH)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def _s(t, injected=0, refused=0):
    return {"t": t, "counters": {"injected_worker": injected, "real_decision": 0,
                                 "real_worker_recovery": 0, "refused": refused}}


def test_total_requests_sums_all_kinds():
    assert script.total_requests({"injected_worker": 2, "refused": 3, "unknown": 99}) == 5


def test_frozen_ignores_settle_window_and_detects_growth():
    samples = [_s(0, 1), _s(5, 2), _s(11, 2), _s(20, 2)]
    assert script.counters_frozen_after(samples, t_mark=2, settle_s=8) is True
    assert script.counters_frozen_after(samples + [_s(30, 3)], t_mark=2, settle_s=8) is False


def test_frozen_is_false_without_late_samples():
    assert script.counters_frozen_after([_s(0, 1), _s(3, 1)], t_mark=2, settle_s=8) is False


def test_counter_delta_from_mark():
    samples = [_s(0, 1), _s(5, 2), _s(10, 4)]
    assert script.counter_delta(samples, 5) == 3
    assert script.counter_delta(samples, 0) == 4
    assert script.counter_delta(samples, 99) == 0


def test_incident_pairs_and_event_kinds():
    diag = {"incidents": [{"type": "user_stop", "status": "open"}, {"type": "runtime_wait", "status": "open"}]}
    assert script.incident_pairs(diag) == [("runtime_wait", "open"), ("user_stop", "open")]
    assert script.incident_pairs({}) == []
    ev = {"events": [{"kind": "a"}, {"kind": "a"}, {"kind": "b"}]}
    assert script.event_kinds(ev) == {"a": 2, "b": 1}


def test_decision_steps():
    assert script.decision_steps({"records": [{"status": "done", "model_steps": "2"}]}) == [("done", 2)]


def _snap(node="pending", project="script_ready", stop=True, total=3):
    return {"node_status": node, "project_status": project, "user_stop": stop, "counters_total": total}


def test_classify_continue():
    before = _snap()
    assert script.classify_continue(before, _snap(node="running", stop=False)) == "worker_restarted"
    assert script.classify_continue(before, _snap(stop=False, total=4)) == "worker_restarted"
    assert script.classify_continue(before, _snap(stop=False)) == "stop_cleared_only"
    assert script.classify_continue(before, _snap(project="enriching_1", stop=False)) == "state_changed_no_worker_run"
    assert script.classify_continue(before, _snap()) == "no_effect"


class _R:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        if self._b is None:
            raise ValueError("no json")
        return self._b


def test_probe_json_keeps_detail_or_keys_only():
    assert script.probe_json(_R(409, {"detail": "user_stop is active"})) == {"status": 409, "detail": "user_stop is active"}
    assert script.probe_json(_R(200, {"project": {"x": 1}, "b": 1, "a": 2})) == {"status": 200, "keys": ["a", "b"]}
    assert script.probe_json(_R(500, None)) == {"status": 500, "keys": []} or script.probe_json(_R(500, None)) == {"status": 500}
