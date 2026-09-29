"""Local stdlib line coverage, without installing/uploading tracing services."""
import json
import sys
import trace
import threading
from pathlib import Path


def begin(source):
    files = {str(source / relative): set() for relative in (
        "app/services/observation.py", "app/services/gpt_operator_client.py",
        "app/services/node_write_contract.py", "app/services/apply_ops_batches.py", "app/web/routers/observations.py", "app/services/diagnosis.py",
        "app/services/actions_v1.py", "app/services/actions_recovery.py",
        "app/services/profiles_state.py", "app/services/profiles_v1.py", "app/services/decision_state.py", "app/services/decision_transport.py", "app/services/decision_v1.py", "app/services/decision_config.py")}
    pilot = source / 'scripts/readonly_qc_pilot.py'
    if pilot.exists():
        files[str(pilot)] = set()
        files[str(source / 'pilot_gateway.py')] = set()

    def collect(frame, event, _arg):
        filename = frame.f_code.co_filename
        if filename not in files:
            return None
        if event == "line":
            files[filename].add(frame.f_lineno)
        return collect

    sys.settrace(collect)
    old_thread_trace = threading.gettrace()
    threading.settrace(collect)

    def finish(results):
        sys.settrace(None)
        threading.settrace(old_thread_trace)
        data = {}
        for filename, hits in files.items():
            executable = set(trace._find_executable_linenos(filename))
            data[Path(filename).relative_to(source).as_posix()] = {
                "executed": sorted(hits & executable), "executable": sorted(executable),
            }
        (results / "line-coverage.json").write_text(json.dumps(data, indent=2))
    return finish
