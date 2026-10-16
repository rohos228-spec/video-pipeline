"""Plan step F06 (phase D row D06): `media.resume_poll` RESUMES a Kie task that is already known, it does not pay again.

Sibling of scripts/e2e_music_kie.py (same modes, work dir / settings patching / Gate / result.json discipline, none of its
files edited: its pure helpers are imported through `_load_sibling`, not copied) and of scripts/e2e_media_kill_after_submit.py.
That music stand proves one PAID generation; this one proves the opposite half of the money story: a generation that was
already created and paid for earlier is picked up by its task id and nothing is sent that could cost money.

Scenario (both modes): a Kie Suno task (model suno-music, V5_5, project node `music`) was created and finished earlier; its
real task id is passed as --task-id. The stand seeds a throwaway project in a scratch work dir OUTSIDE the repository with a
scratch state.db whose provider_task_ledger holds exactly what media_resume.submit leaves behind when the process dies after
the submit (intent -> submitted -> bound -> intent_done=bound; no result, no file, no `downloaded` row). Then it drives the
REAL production recovery path through the action layer's own entry point: the registered action `media.resume_poll`
(its preconditions, then its executor) calls the app's own endpoint /projects/{id}/media-recovery/music/resume-poll over the
in-process ASGI transport (the same call the executor makes in production, no server socket), which runs
media_admission.run_and_verify -> media_recovery.resume_poll -> media_resume._reattach_one inside media_spend.resume_only():
ONE GET record-info for the task, then a download of the audio the poll answer named. `media.get_recovery_assessment` is
read before and after.

PASS needs every one of: ZERO writes (POST or any other) on the wire; at least one poll GET for exactly that task id; an audio
file in the node folder (non-empty, ffprobe duration > 5 s when ffprobe exists); the ledger shows the SAME task moving from
`submitted` to `downloaded` and no new task id, intent or submit row; no new spend row with money; the balance read before
and after (GET /api/v1/chat/credit) unchanged (delta 0 credits); the kill switch refuses the action before a single request
leaves; and create_task inside the resume-only scope is refused by the production code before the wire. If Kie no longer
returns the task, the task is not finished, it failed, or the clip URL has expired, the verdict is NOT PASSED with that
reason; the stand NEVER retries with a POST (the Gate would refuse one in any case).

Modes (exactly one of --dry / --live is required):
  --dry   FREE, offline, no key: a fake Kie server on 127.0.0.1 (balance, record-info, mp3 download; it also counts any POST
          that would reach it) answers the real production HTTP client. --dry-scenario also exercises the failure branches
          (task_missing, url_expired, still_pending, task_failed) and the NEGATIVE CONTROL `resume_posts_again`: a seam
          switches the resume-only scope off inside the resume and makes the production code try a createTask. The Gate
          must refuse it before it leaves the machine and the verdict must be NOT PASSED; if it is not, the check is blind.
  --live  FREE for Kie by design (no createTask exists on this path), still capped: needs --env-file and --task-id; ONLY the
          names KIE_API_KEY and KIE_API_BASE_URL are read from it, nothing is printed. The Gate allows balance GETs (max 3),
          poll GETs of the given task id only, downloads only from https URLs the poll answer named (max 3), and NOTHING
          else. --max-usd (default 0.05, hard ceiling 0.25) is the application's spend cap and the ceiling for anything
          the stand could record. NOT RUN in live mode when written.

Exit codes: 0 passed, 1 not passed, 2 bad arguments, 3 refused before anything was sent.

Usage (repository root, project venv):
    python scripts/e2e_media_resume_poll.py --dry --work-dir <empty dir outside the repository>
    python scripts/e2e_media_resume_poll.py --dry --dry-scenario resume_posts_again --work-dir <dir>
    python scripts/e2e_media_resume_poll.py --live --work-dir <dir> --env-file <env file> --task-id <32 hex chars>
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import uuid as _uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import e2e_guard as guard


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name + "_for_resume_stand", _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


music = _load_sibling("e2e_music_kie")

BALANCE_PATH = music.BALANCE_PATH
POLL_PATH = music.POLL_PATH
ENV_NAMES = music.ENV_NAMES
SECRET_NAMES = music.SECRET_NAMES
FAKE_KEY = music.FAKE_KEY
DEFAULT_BASE_URL = music.DEFAULT_BASE_URL
AUDIO_EXTS = music.AUDIO_EXTS
MIN_DURATION_S = music.MIN_DURATION_S
MAX_BALANCE_READS = music.MAX_BALANCE_READS
MAX_DOWNLOADS = music.MAX_DOWNLOADS
MAX_POLLS = 10
INTERNAL_HOST = "action-layer.invalid"
ACTION_ID = "media.resume_poll"
ASSESS_ID = "media.get_recovery_assessment"
NODE = "music"
KEY = "music"
MODEL = "suno-music"
API = "suno"
DEFAULT_MAX_USD = 0.05
HARD_CEILING_USD = 0.25
DRY_TASK_ID = "0123456789abcdef0123456789abcdef"
TASK_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
DRY_SCENARIOS = ("ok", "resume_posts_again", "task_missing", "url_expired", "still_pending", "task_failed")
RESUME_EVENTS = ("downloaded", "failed", "poll")
SETTLE_WAIT_S = 5.0
EXIT_PASS, EXIT_FAIL, EXIT_ARGS, EXIT_REFUSED = 0, 1, 2, 3



def load_env_whitelist(path: str, names: tuple[str, ...] = ENV_NAMES) -> dict[str, str]:
    return music.load_env_whitelist(path, names)


def base_url_problem(url: str) -> str | None:
    return music.base_url_problem(url)


def is_loopback(host: str | None) -> bool:
    return music.is_loopback(host)


def task_id_problem(task_id: str | None) -> str | None:
    if not task_id or not TASK_ID_RE.match(task_id):
        return "--task-id must be exactly 32 hex characters (a Kie task id)"
    return None


def authorisation_problem(max_usd: float) -> str | None:
    """No paid request exists on this path; --max-usd is the application's spend cap, not a price to be paid."""
    if max_usd <= 0:
        return "--max-usd must be > 0"
    if max_usd > HARD_CEILING_USD + 1e-9:
        return f"--max-usd {max_usd} is above the hard ceiling ${HARD_CEILING_USD}"
    return None


def app_cap_usd(max_usd: float) -> float:
    return round(min(float(max_usd), HARD_CEILING_USD), 2)


def check(name: str, ok: bool, detail: Any = None) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def verdict(checks: list[dict]) -> tuple[bool, list[str]]:
    failed = [c["name"] for c in checks if not c["ok"]]
    return not failed, failed


def final_verdict(mode: str, ok: bool) -> str:
    if not ok:
        return "NOT PASSED"
    if mode == "live":
        return ("PASS (all checks, live: a known Kie Suno task was polled and its audio downloaded with zero POST and zero "
                "balance delta; media.resume_poll proven to resume instead of paying again for this task)")
    return "DRY OK (fake Kie server on loopback: machinery proven offline, NOT a live proof)"


@dataclass
class Judgement:
    action: str
    kind: str
    reason: str = ""


@dataclass
class Gate:
    """Every outgoing request is judged here (pure bookkeeping, no I/O): the send hook obeys it.

    Allowed, and nothing else: GET <base>/api/v1/chat/credit (<= MAX_BALANCE_READS), GET <base>/api/v1/generate/record-info
    ?taskId=<the given task> (<= MAX_POLLS), GET of an audio URL that the poll answer itself named (https; max
    MAX_DOWNLOADS; same host as a learned URL) and the in-process ASGI calls of the action layer to
    http://action-layer.invalid/api/projects/<seeded project>/... (they never leave the process). A POST, PUT, PATCH or
    DELETE to anything else is refused AND counted in `write_attempts`: Kie must never see a write on this path."""
    mode: str
    host: str = "api.kie.ai"
    scheme: str = "https"
    port: int | None = None
    task_id: str = ""
    project_id: int | None = None
    wire: list = field(default_factory=list)
    internal: list = field(default_factory=list)
    refused: list = field(default_factory=list)
    write_attempts: list = field(default_factory=list)
    downloads: dict = field(default_factory=dict)

    def count(self, kind: str) -> int:
        return sum(1 for w in self.wire if w["kind"] == kind)

    def wire_of(self, kind: str) -> list:
        return [w for w in self.wire if w["kind"] == kind]

    def _refuse(self, kind: str, why: str, reason: str, method: str, label: str) -> Judgement:
        row = {"kind": kind, "why": why, "method": method, "what": label[:120], "reason": reason[:160]}
        self.refused.append(row)
        if method != "GET":
            self.write_attempts.append(row)
        return Judgement("refuse", kind, reason)

    def learn_downloads(self, urls: list[str]) -> None:
        for url in urls:
            parts = urlsplit(str(url))
            if parts.hostname and Path(parts.path.lower()).suffix in AUDIO_EXTS:
                self.downloads[str(url)] = parts.hostname.lower()

    def _origin_ok(self, scheme: str, host: str, port: int | None) -> bool:
        if host != self.host or scheme != self.scheme:
            return False
        return self.port is None or port == self.port

    def judge(self, method: str, scheme: str, host: str | None, path: str, *, port: int | None = None,
              params: dict | None = None, url: str = "") -> Judgement:
        method, host_l = method.upper(), (host or "").strip("[]").lower()
        path, params = path or "", params or {}
        label = f"{method} {scheme}://{host_l}{path}"
        if host_l == INTERNAL_HOST:
            base = f"/api/projects/{self.project_id}" if self.project_id is not None else None
            if scheme == "http" and base and (path == base or path.startswith(base + "/")):
                self.internal.append({"method": method, "path": path})
                return Judgement("pass", "internal")
            return self._refuse("other", "foreign", f"refused: {label} is not an allowed in-process call", method, label)
        if method != "GET":
            return self._refuse("write", "no_writes", f"refused: {label}: no write of any kind may leave on this path",
                                method, label)
        if url and (url in self.downloads or (host_l in self.downloads.values() and Path(path.lower()).suffix in AUDIO_EXTS)):
            if scheme != self.scheme or (self.mode == "live" and is_loopback(host_l)) or (
                    self.mode == "dry" and (not is_loopback(host_l) or (self.port is not None and port != self.port))):
                return self._refuse("download", "foreign", f"refused: download {label} is not on the allow list", method, label)
            if self.count("download") >= MAX_DOWNLOADS:
                return self._refuse("download", "download_cap", f"download cap reached ({MAX_DOWNLOADS})", method, label)
            self.wire.append({"kind": "download", "host": host_l})
            return Judgement("pass", "download")
        if not self._origin_ok(scheme, host_l, port):
            return self._refuse("other", "foreign", f"refused: {label} is not on the allow list", method, label)
        if path == BALANCE_PATH:
            if self.count("balance") >= MAX_BALANCE_READS:
                return self._refuse("balance", "balance_cap", f"balance read cap reached ({MAX_BALANCE_READS})", method, label)
            self.wire.append({"kind": "balance", "host": host_l})
            return Judgement("pass", "balance")
        if path == POLL_PATH:
            if not self.task_id or str(params.get("taskId") or "") != self.task_id:
                return self._refuse("poll", "foreign_task", "poll of a task this stand was not given", method, label)
            if self.count("poll") >= MAX_POLLS:
                return self._refuse("poll", "poll_cap", f"poll cap reached ({MAX_POLLS})", method, label)
            self.wire.append({"kind": "poll", "host": host_l, "task_id": str(params.get("taskId"))})
            return Judgement("pass", "poll")
        return self._refuse("other", "foreign", f"refused: {label} is not on the allow list", method, label)


def task_rows(rows: list[dict], task_id: str) -> list[dict]:
    return [r for r in rows if r.get("task_id") == task_id]


def newest_status(rows: list[dict], task_id: str) -> str | None:
    mine = task_rows(rows, task_id)
    return mine[-1].get("status") if mine else None


def new_rows(before: list[dict], after: list[dict]) -> list[dict]:
    last = max((int(r.get("id") or 0) for r in before), default=0)
    return [r for r in after if int(r.get("id") or 0) > last]


def seeded_ledger_ok(rows: list[dict], task_id: str) -> bool:
    """The seed looks like a killed run: a resolved intent, a bound task, no result row, no downloaded row."""
    intents = [r for r in rows if str(r.get("task_id", "")).startswith("intent:")]
    events = [r.get("event") for r in task_rows(rows, task_id)]
    return (len(intents) == 2 and [r.get("event") for r in intents] == ["intent", "intent_done"]
            and intents[-1].get("status") == "bound" and "bound" in events
            and newest_status(rows, task_id) == "submitted" and "downloaded" not in events)


def not_passed_reason(snap: dict) -> str | None:
    """The honest cause in one line, from the facts (never from a guess); None when nothing explains the failure."""
    g = snap.get("gate") or {}
    if g.get("write_attempts"):
        return f"a write (POST) was attempted on the wire {len(g['write_attempts'])} time(s); the Gate refused it before it left the machine"
    act = snap.get("action") or {}
    if act.get("error") and not act.get("result"):
        return f"the action did not run: {act['error']}"
    res = act.get("result") or {}
    resumed = (res.get("resumed") or [{}])[0] if res.get("resumed") else {}
    outcome, error = resumed.get("outcome"), resumed.get("error")
    answers = (snap.get("poll_answers") or [])
    last_answer = answers[-1] if answers else {}
    if outcome == "pending":
        return f"the provider task is not finished (status {last_answer.get('data_status') or 'pending'}); nothing was resubmitted"
    if outcome == "failed":
        return "the provider reports the task as failed; resubmitting is a human decision, nothing was resubmitted"
    if outcome == "no_result_urls":
        return "the poll answer named no audio url (not ready or expired); nothing was resubmitted"
    if outcome == "invalid_file":
        return "the downloaded file is too small to be a result; nothing was resubmitted"
    if outcome is None and error:
        if last_answer and last_answer.get("code") not in (None, 200):
            return (f"Kie no longer returns the task: record-info answered code {last_answer.get('code')} "
                    f"({last_answer.get('msg')}); nothing was resubmitted")
        dl = (snap.get("gate") or {}).get("download_status")
        if dl and dl >= 400:
            return f"the clip url is not downloadable any more (HTTP {dl}, expired?); nothing was resubmitted"
        return f"poll or download raised {error}; nothing was resubmitted"
    if outcome is None and res:
        return f"the task was not resumed ({', '.join(res.get('problems') or []) or 'no outcome'}); nothing was resubmitted"
    return None


def _spend_money(rows: list[dict]) -> float:
    return round(sum(float(r.get("usd") or 0.0) for r in rows), 6)


def evaluate(snap: dict, *, mode: str, max_usd: float = DEFAULT_MAX_USD) -> list[dict]:
    """Design assertions over a collected run snapshot (dry and live share them)."""
    task = snap.get("task_id") or ""
    g = snap.get("gate") or {}
    fake = snap.get("fake")
    act = snap.get("action") or {}
    res = act.get("result") or {}
    pre = act.get("preconditions") or {}
    before, after = snap.get("ledger_before") or [], snap.get("ledger_after") or []
    added = new_rows(before, after)
    spend_added = new_rows(snap.get("spend_before") or [], snap.get("spend_after") or [])
    files = snap.get("music_files") or []
    f0 = files[0] if files else {}
    cb, ca = snap.get("credits_before"), snap.get("credits_after")
    delta = None if cb is None or ca is None else round(float(cb) - float(ca), 6)
    ks = snap.get("kill_switch") or {}
    probe = snap.get("probe") or {}
    ab, aa = snap.get("assessment_before") or {}, snap.get("assessment_after") or {}
    resumed = (res.get("resumed") or [{}])[0] if res.get("resumed") else {}
    measurable = bool(snap.get("ffprobe"))
    duration = f0.get("duration_s")
    out = [
        check("seed_looks_like_a_killed_run_before_the_resume", seeded_ledger_ok(before, task) and not files_before(snap),
              {"events": [(r.get("event"), r.get("status")) for r in before], "files_before": files_before(snap)}),
        check("assessment_before_names_the_known_task_as_resumable",
              ab.get("mode") == "resume_poll" and ab.get("task_id") == task and ab.get("provider") == "kie", ab),
        check("kill_switch_refuses_the_action_before_any_request",
              ks.get("refused_status") == 409 and ks.get("refused_code") == "kill_switch_active"
              and ks.get("wire_requests_during") == 0 and ks.get("released") is True, ks),
        check("action_validated_and_every_precondition_passed",
              not act.get("validate_problems") and set(pre) == {"project_exists", "project_idle", "resume_available"}
              and all(v is None for v in pre.values()), {"validate": act.get("validate_problems"), "preconditions": pre}),
        check("zero_writes_on_the_wire", not g.get("write_attempts") and (fake is None or fake.get("posts") == 0),
              {"write_attempts": g.get("write_attempts"), "fake_posts": (fake or {}).get("posts")}),
        check("poll_get_for_exactly_the_given_task",
              g.get("poll", 0) >= 1 and set(g.get("poll_task_ids") or []) == {task}
              and (fake is None or (fake.get("poll_task_ids") and set(fake["poll_task_ids"]) == {task})),
              {"polls": g.get("poll"), "task_ids": g.get("poll_task_ids"), "fake_saw": (fake or {}).get("poll_task_ids")}),
        check("action_resumed_and_production_verified_it",
              res.get("operation") == "resume_poll" and res.get("verified") is True and not res.get("problems")
              and resumed.get("outcome") == "downloaded" and resumed.get("task_id") == task,
              {"operation": res.get("operation"), "verified": res.get("verified"), "problems": res.get("problems"),
               "resumed": res.get("resumed"), "error": act.get("error")}),
        check("production_reports_zero_new_paid_requests", res.get("new_paid_requests") == 0 and "resumed" in res,
              {"new_paid_requests": res.get("new_paid_requests")}),
        check("audio_downloaded_into_the_node_folder_and_not_empty",
              len(files) == 1 and f0.get("bytes", 0) > 1000 and g.get("download", 0) >= 1, {"files": files, "downloads": g.get("download")}),
        check("audio_duration_over_5_seconds",
              (duration is not None and duration > MIN_DURATION_S) or (duration is None and not measurable and bool(files)),
              {"duration_s": duration, "measurable": measurable, "min": MIN_DURATION_S}),
        check("ledger_shows_the_same_task_moving_from_submitted_to_downloaded",
              newest_status(before, task) == "submitted" and newest_status(after, task) == "downloaded",
              {"before": newest_status(before, task), "after": newest_status(after, task)}),
        check("ledger_has_no_new_task_id_intent_or_submit_row",
              bool(added) and all(r.get("task_id") == task and r.get("event") in RESUME_EVENTS for r in added)
              and {r.get("task_id") for r in after} == {r.get("task_id") for r in before},
              [(r.get("task_id"), r.get("event"), r.get("status")) for r in added]),
        check("no_new_spend_row_with_money_and_no_create_row",
              _spend_money(spend_added) == 0.0 and not any(str(r.get("note", "")).startswith("create") for r in spend_added)
              and _spend_money(spend_added) <= max_usd + 1e-9,
              [(r.get("kind"), r.get("usd"), r.get("note")) for r in spend_added]),
        check("balance_read_before_and_after", cb is not None and ca is not None, {"before": cb, "after": ca}),
        check("balance_delta_is_zero_credits", delta is not None and abs(delta) < 1e-9, {"credits_delta": delta}),
        check("assessment_after_says_downloaded_nothing_left_to_resume",
              aa.get("mode") == "nothing_to_do" and aa.get("code") == "downloaded", aa),
        check("production_resume_only_scope_refuses_create_task_before_the_wire",
              bool(probe.get("raised")) and probe.get("scope_raised") == "ResumeOnlyBlocked"
              and probe.get("wire_writes_during") == 0, probe),
        check("nothing_foreign_was_attempted", not g.get("refused"), (g.get("refused") or [])[:5]),
    ]
    if mode == "dry":
        out.append(check("provider_independently_saw_no_write_and_no_foreign_key",
                         (fake or {}).get("posts") == 0 and (fake or {}).get("auth_failures") == 0, fake))
    return out


def files_before(snap: dict) -> list:
    return snap.get("music_files_before") or []


def advisories(snap: dict) -> list[str]:
    out = []
    ps = snap.get("project_after") or {}
    if ps and not ps.get("artifacts"):
        out.append("the resume downloads the file but writes no Artifact(kind=music) row and leaves the project status as it "
                   f"was ({ps.get('status')}): the node has to be continued by a human (or the worker) to take the file")
    out.append("the disk guard and the spend caps sit in the paid seam (create_task); a poll-only recovery never reaches "
               "them, and a createTask inside the resume-only scope is refused by media_spend before the seam: the disk guard "
               "itself is not exercised on this path")
    out.append("`media.resume_poll` never creates the original ledger rows: they come from the earlier run; a task that is "
               "older than MEDIA_RESUME_WINDOW_S (3600 s) is not found by the recovery at all (nothing_to_do)")
    return out


def reality_report(mode: str, counters: dict) -> dict:
    live = mode == "live"
    return {
        "real": [x for x in (
            "production recovery in-process through the action layer entry point: action media.resume_poll (preconditions + "
            "executor) -> the app's endpoint over the ASGI transport -> media_admission.run_and_verify -> "
            "media_recovery.resume_poll -> media_resume._reattach_one -> kie_http.task_status / download",
            "media_spend.resume_only (refuses a new paid request before the wire), kill_switch, the provider_task_ledger and "
            "spend_ledger of a scratch state.db",
            f"{counters.get('wire_poll', 0)} real Kie record-info poll(s), {counters.get('wire_balance', 0)} balance read(s), "
            f"{counters.get('wire_download', 0)} download(s)" if live else None) if x],
        "not_real": [x for x in (
            "seeded project and ledger rows: written by this script with the production ledger functions, as a killed run "
            "leaves them; the original submit never happened in this scratch database",
            "the original spend_ledger row of the paid submit is not seeded (only the task ledger), so the 'before' money "
            "total is zero",
            None if live else "the provider: a local fake Kie server on loopback (nothing left the machine, nothing was paid)",
            "the worker, the node step and the observation / Decision layers are not started: the action is driven directly",
            "musical quality: never judged") if x],
    }


def reality_not_proven(mode: str) -> list[str]:
    out = ["the exact shape of Kie's record-info answer for an OLD finished task, and how long Kie keeps the clip URL "
           "alive (the stand assumes data.status SUCCESS and data.response.sunoData[].audioUrl, as the production client does)",
           "a task id Kie no longer knows: the stand reports the provider's answer, it cannot choose what Kie says",
           "the true charge: only the balance delta, which can lag or include other consumers of the same account",
           "Suno may return two clips; only the first (longest) is downloaded"]
    if mode != "live":
        out.insert(0, "anything about the real Kie: this is a dry run on a fake server, NOT a live proof")
    return out



class FakeKie:
    """A fake Kie server on 127.0.0.1 (dry only): balance, record-info for ONE known task, the mp3 file.

    It also counts any POST that reaches it (the Gate must make that number zero). The key the production client sends is
    checked: any other Authorization is a 401."""

    def __init__(self, scenario: str = "ok", task_id: str = DRY_TASK_ID, seconds: float = 8.0, credits: float = 1000.0) -> None:
        self.scenario = scenario
        self.task_id = task_id
        self.audio = music.make_audio(seconds)
        self.seconds = seconds
        self.credits = credits
        self.posts: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.poll_task_ids: list[str] = []
        self.auth_failures = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1]) if self._server else 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def snapshot(self) -> dict:
        return {"posts": len(self.posts), "poll_task_ids": list(self.poll_task_ids), "auth_failures": self.auth_failures,
                "requests": len(self.requests)}

    def start(self) -> None:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a, **_k) -> None:
                return

            def _send(self, status: int, payload: Any = None, raw: bytes | None = None, ctype: str = "application/json"):
                data = raw if raw is not None else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorised(self) -> bool:
                if self.headers.get("Authorization") != f"Bearer {FAKE_KEY}":
                    fake.auth_failures += 1
                    self._send(200, {"code": 401, "msg": "fake kie: wrong or missing key"})
                    return False
                return True

            def do_GET(self) -> None:
                parts = urlsplit(self.path)
                fake.requests.append(("GET", parts.path))
                if parts.path == BALANCE_PATH:
                    if not self._authorised():
                        return
                    return self._send(200, {"code": 200, "msg": "success", "data": fake.credits})
                if parts.path == POLL_PATH:
                    if not self._authorised():
                        return
                    task = (parse_qs(parts.query).get("taskId") or [""])[0]
                    fake.poll_task_ids.append(task)
                    if task != fake.task_id or fake.scenario == "task_missing":
                        return self._send(200, {"code": 404, "msg": "fake kie: task not found"})
                    base = {"taskId": task, "type": "GENERATE", "errorCode": None, "errorMessage": None}
                    if fake.scenario == "task_failed":
                        data = {**base, "status": "GENERATE_AUDIO_FAILED", "errorMessage": "fake kie: generation failed"}
                    elif fake.scenario == "still_pending":
                        data = {**base, "status": "PENDING", "response": None}
                    else:
                        data = {**base, "status": "SUCCESS", "response": {"taskId": task, "sunoData": [{
                            "id": "fake-clip-1", "audioUrl": f"{fake.base_url}/files/dry-music.mp3",
                            "duration": fake.seconds, "title": "dry"}]}}
                    return self._send(200, {"code": 200, "msg": "success", "data": data})
                if parts.path == "/files/dry-music.mp3":
                    if fake.scenario == "url_expired":
                        return self._send(404, {"code": 404, "msg": "fake kie: file expired"})
                    return self._send(200, raw=fake.audio, ctype="audio/mpeg")
                return self._send(404, {"code": 404, "msg": "fake kie: no such route"})

            def do_POST(self) -> None:
                parts = urlsplit(self.path)
                fake.requests.append(("POST", parts.path))
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                except Exception:
                    body = {}
                fake.posts.append({"path": parts.path, "body": body})
                fake.credits -= music.CREDITS_PER_GENERATION_HINT
                return self._send(200, {"code": 200, "msg": "success", "data": {"taskId": "fake-new-task-should-not-exist"}})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None



def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true", help="FREE: fake Kie server on loopback, nothing leaves the machine")
    p.add_argument("--live", action="store_true", help="capped: polls ONE known real Kie task, no createTask exists on this path")
    p.add_argument("--env-file", help="env file; only " + ", ".join(ENV_NAMES) + " are read from it")
    p.add_argument("--work-dir", help="scratch dir for data/, result.json (recreated)")
    p.add_argument("--task-id", help="the already finished Kie task id (32 hex characters); required with --live")
    p.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                   help=f"application spend cap and ceiling for anything recorded (hard ceiling ${HARD_CEILING_USD})")
    p.add_argument("--dry-scenario", choices=DRY_SCENARIOS, default="ok",
                   help="dry only: a failure branch, or the negative control resume_posts_again")
    args = p.parse_args(argv)
    if bool(args.dry) == bool(args.live):
        p.error("choose exactly one of --dry and --live")
    if not args.work_dir:
        p.error("--work-dir is required")
    if args.live and not args.env_file:
        p.error("--live needs --env-file")
    if args.live and args.dry_scenario != "ok":
        p.error("--dry-scenario is for --dry only")
    if args.live and not args.task_id:
        p.error("--live needs --task-id")
    if args.task_id is not None and task_id_problem(args.task_id):
        p.error(task_id_problem(args.task_id))
    problem = authorisation_problem(args.max_usd)
    if problem:
        p.error(problem)
    return args



def _rows(state_db: Path, sql: str) -> list[dict]:
    """Plain read connection (a database in WAL mode needs it); a missing table reads as no rows."""
    if not Path(state_db).exists():
        return []
    try:
        db = sqlite3.connect(state_db)
        try:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql).fetchall()]
        finally:
            db.close()
    except sqlite3.Error:
        return []


def ledger_rows(state_db: Path) -> list[dict]:
    return _rows(state_db, "SELECT id,project_id,key,task_id,provider,api,model,event,status,note "
                           "FROM provider_task_ledger ORDER BY id")


def spend_rows(state_db: Path) -> list[dict]:
    return _rows(state_db, "SELECT id,provider,kind,usd,source,note,project_id,ref FROM spend_ledger ORDER BY id")


def music_files(data_dir: Path) -> list[dict]:
    folder = Path(data_dir) / "music"
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("music_*")):
        if p.is_file() and not p.name.endswith(".part"):
            out.append({"name": p.name, "bytes": p.stat().st_size, "duration_s": music.probe_duration(p)})
    return out



async def _seed(task_id: str) -> tuple[int, Path]:
    """A throwaway project and the ledger rows of a run that died after the submit (the production ledger functions)."""
    from app.db import SessionLocal, session_scope
    from app.models import Project, ProjectStatus
    from app.services import media_resume, spend_guard
    slug = f"music-resume-{_uuid.uuid4().hex[:6]}"
    async with session_scope() as session:
        project = Project(slug=slug, topic="music resume_poll e2e", status=ProjectStatus.failed, auto_mode=False,
                          general_plan="", script_text="", meta={})
        session.add(project)
        await session.flush()
        pid, data_dir = int(project.id), project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    ph = media_resume.prompt_hash(MODEL, {"customMode": False, "instrumental": True, "model": music.MUSIC_MODEL,
                                          "prompt": music.MUSIC_PROMPT})
    intent, run = _uuid.uuid4().hex[:16], f"native:{pid}:music"
    common = {"project_id": pid, "key": KEY, "model": MODEL, "api": API}
    async with SessionLocal() as s:
        await spend_guard.append_intent(s, **common, intent_id=intent, ph=ph)
        await spend_guard.append_task_event(s, project_id=pid, key=KEY, task_id=task_id, provider="kie", api=API, model=MODEL,
                                            event="submitted", status="submitted", run_id=run)
        await spend_guard.append_task_event(s, project_id=pid, key=KEY, task_id=task_id, provider="kie", api=API, model=MODEL,
                                            event="bound", status="submitted", note=f"ph={ph} intent={intent}", run_id=run)
        await spend_guard.resolve_intent(s, **common, intent_id=intent, status="bound")
        await s.commit()
    return pid, data_dir


async def _project_after(pid: int) -> dict:
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import Artifact, ArtifactKind, Project
    async with SessionLocal() as session:
        project = await session.get(Project, pid)
        arts = list((await session.scalars(select(Artifact).where(
            Artifact.project_id == pid, Artifact.kind == ArtifactKind.music))).all())
        return {"status": project.status.value, "artifacts": len(arts)}


def _assessment_of(data: dict) -> dict:
    keys = data.get("keys") or []
    row = next((k for k in keys if k.get("key") == KEY), {})
    verdict_ = data.get("verdict") or {}
    return {"mode": verdict_.get("mode"), "code": verdict_.get("code"), "task_id": row.get("task_id"),
            "provider": row.get("provider"), "admission": (data.get("supervisor_admission") or {}).get("operation")}


async def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    mode = "live" if args.live else "dry"
    live_env: dict[str, str] = {}
    base_url = DEFAULT_BASE_URL
    if mode == "live":
        if not Path(args.env_file).is_file():
            print("env file not found; nothing sent")
            return EXIT_REFUSED
        live_env = load_env_whitelist(args.env_file, ENV_NAMES)
        if not live_env.get("KIE_API_KEY"):
            print("env file has no KIE_API_KEY; nothing sent")
            return EXIT_REFUSED
        base_url = (live_env.get("KIE_API_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
        problem = base_url_problem(base_url)
        if problem:
            print(problem + "; nothing sent")
            return EXIT_REFUSED
    task_id = args.task_id or DRY_TASK_ID
    secrets = sorted((v for k, v in live_env.items() if k in SECRET_NAMES and len(v) >= 6), key=len, reverse=True)

    def scrub(text: str) -> str:
        for s in secrets:
            text = text.replace(s, "***")
        return text

    work = Path(args.work_dir).resolve()
    data = guard.reset_scratch_data(work)
    (work / "result.json").unlink(missing_ok=True)
    os.chdir(ROOT)
    cap = app_cap_usd(args.max_usd)
    fake: FakeKie | None = None
    if mode == "dry":
        fake = FakeKie(args.dry_scenario, task_id)
        fake.start()
        base_url = fake.base_url
    key = live_env["KIE_API_KEY"] if mode == "live" else FAKE_KEY
    os.environ.update(music.env_overrides(data, cap=cap, dry=(mode == "dry")))
    os.environ["KIE_API_KEY"] = key
    os.environ["KIE_API_BASE_URL"] = base_url

    import httpx
    from app.bots import kie_http, kie_kling
    from app.settings import settings
    settings.kie_api_key = key
    settings.kie_api_base_url = base_url
    settings.gpt_api_key = ""
    settings.gpt_relay_token = ""
    settings.gpt_base_url = ""
    settings.media_spend_recording = True
    settings.spend_daily_cap_usd = settings.spend_project_cap_usd = settings.spend_run_cap_usd = cap
    for guarded in (settings.data_dir, settings.sqlite_path):
        if not guard._inside(Path(guarded).resolve(), work):
            if fake:
                fake.stop()
            raise guard.refuse(f"настройки указывают на {guarded}, это вне рабочей папки {work}; ничего не отправлено.")

    from app import main as app_main
    from app.services import media_spend
    await app_main._init_db()

    parts = urlsplit(base_url)
    gate = Gate(mode=mode, host=(parts.hostname or "").lower(), scheme=parts.scheme,
                port=parts.port if mode == "dry" else None, task_id=task_id)
    original_send = httpx.AsyncClient.send
    original_keys = kie_kling._iter_kie_keys
    original_interval = kie_http._POLL_INTERVAL_S
    poll_answers: list[dict] = []
    download_status: list[int] = []

    async def send(client, request, **kwargs):
        judged = gate.judge(request.method, request.url.scheme, request.url.host, request.url.path,
                            port=request.url.port, params=dict(request.url.params), url=str(request.url))
        if judged.action == "refuse":
            raise RuntimeError("stand: " + judged.reason)
        response = await original_send(client, request, **kwargs)
        if judged.kind == "download":
            download_status.append(response.status_code)
        elif judged.kind == "poll":
            try:
                body = response.json()
                inner = body.get("data") if isinstance(body.get("data"), dict) else {}
                poll_answers.append({"http": response.status_code, "code": body.get("code"),
                                     "msg": str(body.get("msg"))[:100], "data_status": inner.get("status")})
                gate.learn_downloads(kie_http._result_media_urls(inner))
            except Exception:
                poll_answers.append({"http": response.status_code, "code": None, "msg": "unreadable answer"})
        return response

    httpx.AsyncClient.send = send
    kie_kling._iter_kie_keys = lambda: [("KIE_API_KEY", key)]
    kie_kling.reset_working_kie_key()
    if mode == "dry":
        kie_http._POLL_INTERVAL_S = 0.05
    media_spend.reset_gap_state()

    state_db = data / "state.db"
    snap: dict[str, Any] = {"mode": mode, "task_id": task_id, "scenario": args.dry_scenario if mode == "dry" else None,
                            "ffprobe": shutil.which("ffprobe") is not None}
    result: dict[str, Any] = {"mode": mode, "dry_scenario": snap["scenario"], "task_id": task_id,
                              "args": {k: v for k, v in vars(args).items() if k != "env_file"}, "app_caps_usd": cap}
    print(f"preflight: mode={mode} scenario={snap['scenario']} task={task_id} app_caps=${cap} "
          f"base={gate.scheme}://{gate.host} (no createTask exists on this path)", flush=True)
    seam = {"create_task_called": False, "error": None}
    original_reattach = None
    try:
        original_reattach = await _flow(snap, gate, state_db, data, args, mode, task_id, seam, kie_http, media_spend)
    except Exception as exc:
        result["error"] = scrub(f"{type(exc).__name__}: {exc}")
        import traceback
        result["traceback"] = scrub(traceback.format_exc())[-2500:]
        print("STOP:", result["error"], flush=True)
    finally:
        if original_reattach is not None:
            from app.services import media_resume
            media_resume._reattach_one = original_reattach
        httpx.AsyncClient.send = original_send
        kie_kling._iter_kie_keys = original_keys
        kie_http._POLL_INTERVAL_S = original_interval
        if fake:
            snap["fake"] = fake.snapshot()
            fake.stop()
    snap["poll_answers"] = poll_answers
    snap["gate"] = {"balance": gate.count("balance"), "poll": gate.count("poll"), "download": gate.count("download"),
                    "internal": len(gate.internal), "refused": gate.refused, "write_attempts": gate.write_attempts,
                    "poll_task_ids": [w["task_id"] for w in gate.wire_of("poll")],
                    "download_status": download_status[-1] if download_status else None}
    snap["seam"] = seam
    return _finish(args, mode, result, snap, gate, state_db, scrub, work)


async def _flow(snap, gate: Gate, state_db: Path, data: Path, args, mode: str, task_id: str, seam: dict, kie_http, media_spend):
    from app.db import SessionLocal
    from app.services import kill_switch, media_resume
    from app.services.action_layer.context import ActionCallError, ActionContext
    from app.services.action_layer.spec import ensure_builtin_actions, registry
    from app.services.kie_catalog import CREDIT_USD
    from app.web.api import create_app

    credits_before = await kie_http.get_credits()
    snap["credits_before"] = credits_before
    pid, project_dir = await _seed(task_id)
    gate.project_id = pid
    await media_spend.drain()
    snap["project_id"] = pid
    snap["ledger_before"], snap["spend_before"] = ledger_rows(state_db), spend_rows(state_db)
    snap["music_files_before"] = music_files(project_dir)

    ensure_builtin_actions()
    resume_spec, assess_spec = registry.get(ACTION_ID), registry.get(ASSESS_ID)
    params = {"project_id": pid, "node_type": NODE}
    app = create_app()
    ctx = ActionContext(app=app, operator="e2e_media_resume_poll")
    snap["assessment_before"] = _assessment_of(await assess_spec.executor(ctx.readonly_copy(), params))

    wire_before = len(gate.wire)
    ks: dict[str, Any] = {"refused_status": None, "refused_code": None}
    await kill_switch.set_state(SessionLocal, active=True, actor="e2e_media_resume_poll", rationale="stand: kill switch check")
    try:
        await resume_spec.executor(ctx, params)
        ks["refused_status"] = "not_refused"
    except ActionCallError as exc:
        ks["refused_status"] = exc.status
        ks["refused_code"] = (exc.detail or {}).get("code") if isinstance(exc.detail, dict) else str(exc.detail)[:80]
    finally:
        await kill_switch.set_state(SessionLocal, active=False, actor="e2e_media_resume_poll", rationale="stand: switch released")
    ks["wire_requests_during"] = len(gate.wire) - wire_before
    ks["released"] = not await _kill_active()
    snap["kill_switch"] = ks

    if args.dry_scenario == "resume_posts_again" and mode == "dry":
        original = _install_post_seam(gate, kie_http, seam)
    else:
        original = media_resume._reattach_one

    act: dict[str, Any] = {"validate_problems": registry.validate_params(ACTION_ID, params), "preconditions": {}}
    for pre in resume_spec.preconditions:
        act["preconditions"][pre.id] = await pre.check(ctx.readonly_copy(), params)
    snap["action"] = act
    if all(v is None for v in act["preconditions"].values()) and not act["validate_problems"]:
        try:
            act["result"] = await resume_spec.executor(ctx, params)
        except ActionCallError as exc:
            act["error"] = f"HTTP {exc.status}: {str(exc.detail)[:200]}"
        except Exception as exc:
            act["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    else:
        act["error"] = "preconditions refused: " + "; ".join(f"{k}: {v}" for k, v in act["preconditions"].items() if v)
    await media_spend.drain()

    snap["music_files"] = music_files(project_dir)
    snap["ledger_after"], snap["spend_after"] = ledger_rows(state_db), spend_rows(state_db)
    snap["assessment_after"] = _assessment_of(await assess_spec.executor(ctx.readonly_copy(), params))
    snap["project_after"] = await _project_after(pid)

    writes_before = len(gate.write_attempts) + sum(1 for w in gate.wire if w["kind"] == "write")
    probe: dict[str, Any] = {"raised": None}
    try:
        with media_spend.resume_only():
            await kie_http.create_task(API, "/api/v1/generate", {"customMode": False, "instrumental": True,
                                                                  "model": music.MUSIC_MODEL, "prompt": music.MUSIC_PROMPT})
    except Exception as exc:
        probe["raised"] = type(exc).__name__
    try:
        with media_spend.resume_only():
            media_spend.assert_submit_allowed("kie", "createTask")
    except Exception as exc:
        probe["scope_raised"] = type(exc).__name__
    probe["wire_writes_during"] = len(gate.write_attempts) - writes_before
    snap["probe"] = probe

    await asyncio.sleep(0.2 if mode == "dry" else SETTLE_WAIT_S)
    credits_after = await kie_http.get_credits()
    snap["credits_after"] = credits_after
    snap["credits_delta"] = None if credits_after is None or credits_before is None else round(credits_before - credits_after, 6)
    snap["billed_usd_by_balance"] = None if snap["credits_delta"] is None else round(snap["credits_delta"] * CREDIT_USD, 6)
    return original


async def _kill_active() -> bool:
    from app.db import SessionLocal
    from app.services import kill_switch
    async with SessionLocal() as session:
        return await kill_switch.is_active(session)


def _install_post_seam(gate: Gate, kie_http, seam: dict):
    """NEGATIVE CONTROL: a regression that switches the resume-only scope off and makes the resume try a createTask.

    The real create_task is called (so the kill switch / disk / cap seam and the real HTTP client are on the path); if one
    of those refuses before the wire, a raw POST goes out the same way. Either way the Gate must see it and refuse it."""
    from app.services import media_resume, media_spend
    original = media_resume._reattach_one

    async def regressed(project, t, key, stats):
        token = media_spend._resume_only.set(False)
        body = {"customMode": False, "instrumental": True, "model": music.MUSIC_MODEL, "prompt": music.MUSIC_PROMPT}
        try:
            seam["create_task_called"] = True
            try:
                await kie_http.create_task(API, "/api/v1/generate", body)
            except Exception as exc:
                seam["error"] = type(exc).__name__
            if not gate.write_attempts:
                import httpx
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(f"{gate.scheme}://{gate.host}" + (f":{gate.port}" if gate.port else "") +
                                          "/api/v1/generate", json=body)
                except Exception as exc:
                    seam["error"] = type(exc).__name__
        finally:
            media_spend._resume_only.reset(token)
        return await original(project, t, key, stats)

    media_resume._reattach_one = regressed
    return original



def _finish(args, mode, result, snap, gate: Gate, state_db, scrub, work) -> int:
    counters = {"wire_balance": gate.count("balance"), "wire_poll": gate.count("poll"), "wire_download": gate.count("download"),
                "write_attempts": len(gate.write_attempts), "refused": len(gate.refused), "internal_calls": len(gate.internal),
                "wire_posts": sum(1 for w in gate.wire if w["kind"] == "write")}
    if result.get("error") or "action" not in snap:
        checks = [check("flow_completed_without_error", False, scrub(str(result.get("error") or "the flow stopped early")))]
        checks += evaluate(snap, mode=mode, max_usd=args.max_usd) if "action" in snap else []
    else:
        checks = evaluate(snap, mode=mode, max_usd=args.max_usd)
    ok, failed = verdict(checks)
    reason = None if ok else (not_passed_reason(snap) or result.get("error") or (", ".join(failed) if failed else None))
    reality = reality_report(mode, counters)
    notes = advisories(snap)
    summary = {"mode": mode, "scenario": args.dry_scenario if mode == "dry" else None, "verdict": final_verdict(mode, ok),
               "reason": scrub(str(reason)) if reason else None, "failed_checks": failed,
               "task_id": snap.get("task_id"), "writes_attempted": counters["write_attempts"],
               "balance": {"before_credits": snap.get("credits_before"), "after_credits": snap.get("credits_after"),
                           "credits_delta": snap.get("credits_delta"), "billed_usd_by_balance": snap.get("billed_usd_by_balance")},
               "caps": {"max_usd": args.max_usd, "app_caps_usd": result.get("app_caps_usd")},
               "advisories": notes, "not_proven": reality_not_proven(mode)}
    out = {**result, "counters": counters, "checks": checks, "ok": ok, "failed": failed, "reality": reality,
           "summary": summary, "snapshot": snap, "gate_wire": gate.wire, "gate_refused": gate.refused}
    (work / "result.json").write_text(scrub(json.dumps(out, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
    print("\n=== RESULT ===")
    print(f"node: {NODE} | mode: {mode} | scenario: {summary['scenario']} | writes attempted: {counters['write_attempts']} "
          f"| balance reads: {counters['wire_balance']} | polls: {counters['wire_poll']} | downloads: {counters['wire_download']}")
    for c in checks:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"])
        if not c["ok"]:
            print("     detail:", scrub(json.dumps(c["detail"], ensure_ascii=False, default=str))[:500])
    print("balance:", summary["balance"])
    print("real:", reality["real"])
    print("not real:", reality["not_real"])
    print("not proven:", summary["not_proven"])
    for note in notes:
        print("advisory:", scrub(note))
    if summary["reason"] and not ok:
        print("reason:", summary["reason"])
    print("verdict:", summary["verdict"])
    return EXIT_PASS if ok else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
