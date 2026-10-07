"""TEST-ONLY launcher: real app in-process + fake paid/destructive actions (offline).

Developer tool for checking the complete paid/destructive flow in the real UI
(preview -> operator confirmation -> approve -> run -> live progress -> stop ->
history/budget) WITHOUT any real spend. It refuses to do anything unless
``ACTION_LAYER_TEST_ACTIONS=1`` is set, so the ``test.*`` actions can never
appear in a normal run.

    set ACTION_LAYER_TEST_ACTIONS=1
    .venv/Scripts/python.exe scripts/e2e_action_layer_fake_paid.py [PORT] [--data-dir DIR]

* PORT default 8796.
* The data dir is an isolated temp copy of the live state.db (never the live
  one). ``--data-dir`` reuses an existing isolated dir (restart scenarios).
* No provider keys, Supabase points to a dead port, the fake actions make no
  network call at all (a fake job is a record in process memory).
* Fake job events are appended to ``<data dir>/fake_jobs.jsonl`` so a test can
  prove that the "background job" really started / stopped / finished.

Registered actions: test.fake_paid_job (paid+destructive, background, 40-60 cr,
FAKE_JOB_SECONDS default 40), test.fake_paid_instant (paid, 30-50 cr, spends 42),
test.fake_destructive (destructive, no money).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ENV_FLAG = "ACTION_LAYER_TEST_ACTIONS"
ROOT = Path(__file__).resolve().parents[1]
# The seed (a data folder whose state.db is COPIED, never opened for writing) comes from --seed-dir or the
# E2E_SEED_DATA_DIR variable. There is no hard-coded default path: A19.
SEED_ENV = "E2E_SEED_DATA_DIR"
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import e2e_guard as guard  # noqa: E402  (A19: refuses to touch live data)

# Test seams (tests replace these); production code path uses the real clock.
_now = time.monotonic
JOBS: dict[str, dict] = {}
FAKE_ACTION_IDS = ("test.fake_paid_job", "test.fake_paid_instant", "test.fake_destructive")


class FakeActionsDisabled(RuntimeError):
    pass


def _enabled() -> bool:
    return os.environ.get(ENV_FLAG, "") == "1"


def _log_event(event: str, job_id: str, **extra) -> None:
    data_dir = os.environ.get("DATA_DIR")
    if not data_dir:
        return
    try:
        with open(Path(data_dir) / "fake_jobs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), "event": event, "job": job_id, **extra},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def _schema(with_seconds: bool = False) -> dict:
    props: dict = {"label": {"type": "string", "maxLength": 80}}
    if with_seconds:
        props["seconds"] = {"type": "number", "minimum": 0.5, "maximum": 600}
    return {"type": "object", "properties": props, "additionalProperties": False}


def _job_seconds(params: dict) -> float:
    if params.get("seconds") is not None:
        return float(params["seconds"])
    return float(os.environ.get("FAKE_JOB_SECONDS", "40"))


def build_fake_specs():
    from app.services.action_layer.spec import ActionSpec, CostEstimate, JobState

    async def job_start(ctx, params):
        job_id = f"{ctx.plan_id}:{ctx.step_index}"
        JOBS[job_id] = {"t0": _now(), "seconds": _job_seconds(params), "stopped": False,
                        "stopped_at": None, "finished": False}
        _log_event("started", job_id, seconds=JOBS[job_id]["seconds"])
        return {"job_id": job_id, "fake": True}

    async def job_poll(ctx, params, started):
        job = JOBS.get(started["job_id"])
        if job is None:  # lost (process restarted): never pretend success
            return JobState(done=True, ok=False, error="fake job not found (memory lost)")
        frac = min(1.0, (_now() - job["t0"]) / job["seconds"])
        if job["stopped"]:
            return JobState(done=True, ok=False, progress=frac, error="fake job stopped",
                            note="остановлено")
        note = f"тестовая задача: {int(frac * 100)}% (никаких реальных вызовов)"
        if frac >= 1.0:
            job["finished"] = True
            _log_event("finished", started["job_id"])
            return JobState(done=True, ok=True, progress=1.0, note=note,
                            result={"fake_result": "ok"}, credits_spent=47.5)
        return JobState(done=False, progress=frac, note=note)

    async def job_stop(ctx, params):
        job_id = f"{ctx.plan_id}:{ctx.step_index}"
        job = JOBS.get(job_id)
        if job is not None and not job["stopped"]:
            job["stopped"] = True
            job["stopped_at"] = _now()
            _log_event("stopped", job_id, progress=min(1.0, (_now() - job["t0"]) / job["seconds"]))

    async def instant(ctx, params):
        _log_event("instant_paid", f"{ctx.plan_id}:{ctx.step_index}")
        return {"fake": True, "credits_spent": 42.0}

    async def destructive(ctx, params):
        _log_event("destructive_done", f"{ctx.plan_id}:{ctx.step_index}")
        return {"fake": True, "wiped": params.get("label", "")}

    return [
        ActionSpec(
            id="test.fake_paid_job", title_ru="ТЕСТ: платная фоновая задача (фейк)",
            description_ru="Тестовое платное и стирающее фоновое действие: живёт в памяти, "
                          "~40 с, без сети, ничего реально не тратит.",
            params_schema=_schema(True), executor=job_start, risk="destructive",
            spends_money=True, destructive=True, idempotent=False, poll=job_poll,
            stop=job_stop, poll_interval_s=1.0, scope="test", tags=("test",),
            estimator=lambda p: CostEstimate(40.0, 60.0, "тестовая оценка 40-60 кр."),
            destructive_effects=lambda p: "ТЕСТ: фейковые данные будут стёрты (реально ничего не удаляется)."),
        ActionSpec(
            id="test.fake_paid_instant", title_ru="ТЕСТ: платное мгновенное (фейк)",
            description_ru="Тестовое платное действие: мгновенно, факт 42 кр., без сети.",
            params_schema=_schema(), executor=instant, risk="paid", spends_money=True,
            idempotent=False, scope="test", tags=("test",),
            estimator=lambda p: CostEstimate(30.0, 50.0, "тестовая оценка 30-50 кр.")),
        ActionSpec(
            id="test.fake_destructive", title_ru="ТЕСТ: стирающее без денег (фейк)",
            description_ru="Тестовое необратимое действие без трат: мгновенно, без сети.",
            params_schema=_schema(), executor=destructive, risk="destructive",
            destructive=True, idempotent=False, scope="test", tags=("test",),
            destructive_effects=lambda p: "ТЕСТ: фейковая запись будет стёрта (реально ничего не удаляется)."),
    ]


def register_fake_actions(registry=None) -> list[str]:
    """Registers test.* actions; refuses unless ACTION_LAYER_TEST_ACTIONS=1."""
    if not _enabled():
        raise FakeActionsDisabled(f"set {ENV_FLAG}=1 to register test.* actions")
    from app.services.action_layer.spec import registry as default_registry
    reg = registry or default_registry
    ids = []
    for spec in build_fake_specs():
        if reg.find(spec.id) is None:
            reg.register(spec)
        ids.append(spec.id)
    return ids


def _prepare_data_dir(arg: str | None, seed: str | None = None) -> Path:
    """Scratch data dir for the launcher. Never the live one: an existing --data-dir that is (or lies inside)
    the live data folder is refused; the seed state.db is copied through the sqlite backup API (consistent)."""
    if arg:
        d = guard.assert_scratch_data_dir(Path(arg))
        if (d / "state.db").exists():
            return d
    else:
        d = Path(tempfile.mkdtemp(prefix="al_fake_paid_"))
    d.mkdir(parents=True, exist_ok=True)
    seed_dir = seed or os.environ.get(SEED_ENV)
    src = Path(seed_dir) / "state.db" if seed_dir else None
    if src is not None and src.exists():
        import sqlite3
        with sqlite3.connect(str(src), timeout=30) as source, sqlite3.connect(str(d / "state.db")) as target:
            source.backup(target)
    return d


def main(argv: list[str]) -> int:
    if not _enabled():
        print(f"REFUSED: set {ENV_FLAG}=1 to run this test-only launcher.", file=sys.stderr)
        return 2
    port, data_arg, seed_arg = "8796", None, None
    it = iter(argv)
    for a in it:
        if a == "--data-dir":
            data_arg = next(it, None)
        elif a == "--seed-dir":
            seed_arg = next(it, None)
        elif a.isdigit():
            port = a
    d = _prepare_data_dir(data_arg, seed_arg)
    os.environ.update(
        TELEGRAM_ENABLED="false", DATA_DIR=str(d), SQLITE_PATH=str(d / "state.db"),
        SUPABASE_URL="http://127.0.0.1:9", SUPABASE_KEY="unused", WEB_PORT=port,
        WEB_HOST="127.0.0.1", WORKER_ENABLED="false", GPT_API_KEY="", KIE_API_KEY="",
        VIBECODE_API_KEY="", ELEVENLABS_API_KEY="", OUTSEE_API_KEY="", GPT_RELAY_TOKEN="",
        FLEET_AGENT_TOKEN="", TELEGRAM_BOT_TOKEN="", YANDEX_STORAGE_ACCESS_KEY="",
        YANDEX_STORAGE_SECRET_KEY="")
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import app.services.api_tracker_hook as hook
    hook._TRACKER_DB = d / "tracker.db"
    ids = register_fake_actions()
    print(f"[fake-paid] registered {ids}; data_dir={d}; port={port}; launcher={Path(__file__).resolve()}",
          flush=True)
    import runpy
    runpy.run_module("app.main", run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
