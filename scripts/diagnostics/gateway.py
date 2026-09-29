"""Single-destination Unix-socket gateway for the isolated online diagnostic.

The application container has --network none. This separate gateway container
receives HTTP over a shared Unix socket and only forwards the Kie text route.
It never receives filesystem credentials or a Docker socket. The bearer token
arrives only as the already-authorized request header and is never written.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import socketserver
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from collections import Counter
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

import httpx

SOCKET = Path("/gateway/llm.sock")
LEDGER = Path("/results/gateway-ledger.sqlite")
KIE = "https://api.kie.ai"
MAX_REQUESTS = 8
MAX_BYTES = 30000
MAX_OUTPUT = 15000
# Conservative reserve if the provider ignores max_output_tokens: 128k model
# output at the published 1680 credits/MTok, plus 30k input bytes at the sum
# of published input, cache-write, and cached-input rates (658 credits/MTok).
MAX_POST_CREDITS_IF_CAP_IGNORED = 235
MAX_ACCUMULATED_CHARS = 2_000_000
MAX_SSE_EVENTS = 100_000
MAX_UPSTREAM_WALL_S = 900
FIXTURE = os.environ.get("DIAGNOSTIC_GATEWAY_FIXTURE") == "1"
FIXTURE_COUNTS = Counter()
EXTERNAL_CALLS_ALLOWED = False  # Explicit online binding only; never inferred from synthetic mode.
AUDIT_FAILURE = Path('/results/gateway-audit-failure.marker')
BODY_ROOT = Path('/results/gateway-bodies')
AUDIT_DEGRADED = False
_SCHEMA_LOCK = threading.Lock()
_WRITE_LOCK = threading.RLock()
_SCHEMA_PATH = None
DRAIN_REQUEST = Path('/results/gateway-drain-requested.marker')


def safe_event(value):
    """Remove credential-bearing fields and URLs before writing provider data."""
    if isinstance(value, dict):
        return {str(k): ('[redacted]' if re.fullmatch(r'authorization|cookie|set-cookie|api.?key|secret|access_token|refresh_token|password', str(k), re.I)
                         else safe_event(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_event(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'Bearer\s+[^\s"\\]+', 'Bearer [redacted]', value, flags=re.I)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}\b', '[redacted-key]', value)
        return re.sub(r'https?://[^\s"<>]+', '[url-redacted]', value)
    return value


def durable_body(ordinal: int, kind: str, value) -> dict:
    raw = json.dumps(safe_event(value), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    sha = hashlib.sha256(raw).hexdigest()
    directory = BODY_ROOT / str(ordinal)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f'{kind}-{sha}.json'
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise OSError('existing_body_hash_mismatch')
    if not path.exists():
        fd, temporary = tempfile.mkstemp(prefix='.body-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(raw); file.flush(); os.fsync(file.fileno())
            os.replace(temporary, path)
            fd = os.open(directory, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {'path': str(path.relative_to(BODY_ROOT)), 'sha256': sha, 'bytes': len(raw)}


def degrade(reason: str, exc: BaseException | None = None, *, operation: str = '',
            ordinal: int | None = None, response_id: str | None = None,
            connection_id: int | None = None, db: sqlite3.Connection | None = None) -> None:
    global AUDIT_DEGRADED
    AUDIT_DEGRADED = True
    entry = {'at': time.time(), 'operation': operation or reason, 'reason': reason,
             'exception_type': type(exc).__name__ if exc else None,
             'exception_text': safe_event(str(exc)) if exc else None,
             'sqlite_errorcode': getattr(exc, 'sqlite_errorcode', None),
             'sqlite_errorname': getattr(exc, 'sqlite_errorname', None),
             'traceback': safe_event(''.join(traceback.format_exception(exc))) if exc else None,
             'request_ordinal': ordinal, 'response_id': response_id,
             'pid': os.getpid(), 'thread_id': threading.get_ident(),
             'connection_id': connection_id or (id(db) if db else None),
             'transaction_open': db.in_transaction if db else None}
    line = json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + '\n'
    # The fallback must not depend on the failing SQLite database.
    try:
        AUDIT_FAILURE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(AUDIT_FAILURE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as file:
            file.write(line); file.flush(); os.fsync(file.fileno())
    except Exception as marker_exc:
        try:
            print('gateway_emergency_marker_unavailable:' + type(marker_exc).__name__, file=sys.stderr, flush=True)
        except OSError:
            pass
    try:
        print('gateway_audit_degraded:' + line, file=sys.stderr, flush=True)
    except OSError:
        pass


def completion_credits(event: dict) -> float | None:
    """Kie places credits on the SSE event; accept the nested form too."""
    response = event.get("response")
    nested = response.get("credits_consumed") if isinstance(response, dict) else None
    for value in (event.get("credits_consumed"), nested):
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            return float(value)
    return None


def connect() -> sqlite3.Connection:
    global _SCHEMA_PATH
    db = sqlite3.connect(LEDGER, timeout=30, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA synchronous=FULL")
        with _SCHEMA_LOCK:
            schema_key = (str(LEDGER), os.stat(LEDGER).st_ino)
            if _SCHEMA_PATH != schema_key:
                db.execute("PRAGMA journal_mode=WAL")
                _initialize_schema(db)
                _SCHEMA_PATH = schema_key
    except BaseException:
        db.close()
        raise
    return db


def _initialize_schema(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS requests (ordinal INTEGER PRIMARY KEY, "
               "at REAL NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL, "
               "state TEXT NOT NULL, http_status INTEGER, credits REAL)")
    additions = {
        'body_sha256': 'TEXT', 'upstream_first_at': 'REAL', 'upstream_completed_at': 'REAL',
        'downstream_last_write_at': 'REAL', 'downstream_finished_at': 'REAL',
        'upstream_complete': 'INTEGER NOT NULL DEFAULT 0',
        'downstream_complete': 'INTEGER NOT NULL DEFAULT 0',
        'response_id': 'TEXT', 'usage_json': 'TEXT', 'terminal_body_path': 'TEXT',
        'accumulated_body_path': 'TEXT', 'event_count': 'INTEGER NOT NULL DEFAULT 0',
        'storage_ms': 'REAL NOT NULL DEFAULT 0', 'socket_write_ms': 'REAL NOT NULL DEFAULT 0',
        'delivery_error': 'TEXT', 'application_received_at': 'REAL',
        'application_accepted_at': 'REAL',
    }
    existing = {row[1] for row in db.execute('PRAGMA table_info(requests)')}
    for name, sql_type in additions.items():
        if name not in existing:
            db.execute(f'ALTER TABLE requests ADD COLUMN {name} {sql_type}')
    db.execute("CREATE TABLE IF NOT EXISTS event_batches (request_ordinal INTEGER NOT NULL, "
               "batch_index INTEGER NOT NULL, first_event INTEGER NOT NULL, last_event INTEGER NOT NULL, "
               "body_path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, "
               "saved_at REAL NOT NULL, PRIMARY KEY(request_ordinal,batch_index))")
    db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id=1), "
               "blocked_reason TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS ack_events (response_id TEXT NOT NULL, stage TEXT NOT NULL, "
               "first_at REAL NOT NULL, last_at REAL NOT NULL, attempts INTEGER NOT NULL, "
               "accepted INTEGER NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(response_id,stage))")
    db.execute("INSERT OR IGNORE INTO state(id,blocked_reason) VALUES(1,NULL)")


@contextmanager
def connection():
    db = connect()
    try:
        with _WRITE_LOCK:
            yield db
    finally:
        db.close()


def recover_pending() -> None:
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM requests WHERE state='reserved'").fetchone()[0]:
            db.execute("UPDATE state SET blocked_reason=COALESCE(blocked_reason,'uncertain_after_restart') WHERE id=1")
            db.execute("UPDATE requests SET state='uncertain',delivery_error='possible_unflushed_events_after_restart' WHERE state='reserved'")
        db.execute("COMMIT")
    finally:
        db.close()


def reserve(method: str, path: str, body_sha256: str | None = None) -> int:
    if AUDIT_DEGRADED or AUDIT_FAILURE.exists() or DRAIN_REQUEST.exists():
        raise RuntimeError('gateway_audit_storage_degraded')
    with _WRITE_LOCK:
        return _reserve_locked(method, path, body_sha256)


def _reserve_locked(method: str, path: str, body_sha256: str | None) -> int:
    if AUDIT_DEGRADED or AUDIT_FAILURE.exists() or DRAIN_REQUEST.exists():
        raise RuntimeError('gateway_audit_storage_degraded')
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        reason = db.execute("SELECT blocked_reason FROM state WHERE id=1").fetchone()[0]
        if reason:
            raise RuntimeError("gateway_blocked:" + reason)
        n = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        if n >= MAX_REQUESTS:
            raise RuntimeError("gateway_request_limit")
        credits = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN state='reserved' THEN ? ELSE COALESCE(credits,0) END),0) FROM requests",
            (MAX_POST_CREDITS_IF_CAP_IGNORED,)
        ).fetchone()[0]
        if credits is not None and credits >= 400:
            raise RuntimeError("gateway_credit_limit")
        if method == "POST" and credits is not None and credits + MAX_POST_CREDITS_IF_CAP_IGNORED > 400:
            raise RuntimeError("gateway_insufficient_headroom")
        ordinal = n + 1
        db.execute("INSERT INTO requests(ordinal,at,method,path,state,body_sha256) VALUES(?,?,?,?,?,?)",
                   (ordinal, time.time(), method, path, "reserved", body_sha256))
        db.execute("COMMIT")
        return ordinal
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def finish(ordinal: int, *, status: int | None, credits: float | None, complete: bool,
           downstream_complete: bool = False, delivery_error: str | None = None,
           terminal_info: dict | None = None) -> None:
    with _WRITE_LOCK:
        _finish_locked(ordinal, status=status, credits=credits, complete=complete,
                       downstream_complete=downstream_complete, delivery_error=delivery_error,
                       terminal_info=terminal_info)


def _finish_locked(ordinal, *, status, credits, complete, downstream_complete,
                   delivery_error, terminal_info):
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        state = 'complete' if complete and downstream_complete else ('delivery_failed' if complete else 'uncertain')
        db.execute("UPDATE requests SET state=?,http_status=?,credits=?,downstream_complete=?,"
                   "downstream_finished_at=?,delivery_error=?,upstream_complete=?,"
                   "upstream_completed_at=?,response_id=?,usage_json=?,terminal_body_path=?,"
                   "accumulated_body_path=? WHERE ordinal=? AND state='reserved'",
                   (state, status, credits, int(downstream_complete),
                    time.time() if downstream_complete else None, delivery_error,
                    int(bool(terminal_info)), terminal_info.get('at') if terminal_info else None,
                    terminal_info.get('response_id') if terminal_info else None,
                    terminal_info.get('usage_json') if terminal_info else None,
                    terminal_info.get('terminal_path') if terminal_info else None,
                    terminal_info.get('accumulated_path') if terminal_info else None, ordinal))
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise RuntimeError("gateway_request_state_missing")
        if state != 'complete':
            db.execute("UPDATE state SET blocked_reason=COALESCE(blocked_reason,?) WHERE id=1",
                       ('delivery_failed' if complete else 'uncertain_response',))
        if credits is not None and credits >= 0:
            total = db.execute("SELECT COALESCE(SUM(credits),0) FROM requests").fetchone()[0]
            if total >= 400:
                db.execute("UPDATE state SET blocked_reason=COALESCE(blocked_reason,'credit_limit_reached') WHERE id=1")
        db.execute("COMMIT")
    except BaseException:
        exc = sys.exc_info()[1]
        degrade('gateway_ledger_failure:' + type(exc).__name__, exc,
                operation='settlement_transaction', ordinal=ordinal, db=db)
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def save_batch(ordinal: int, batch_index: int, first_event: int, events: list[dict],
               accumulated_text: str, *, terminal: bool = False,
               terminal_at: float | None = None) -> tuple[float, dict | None]:
    """Persist a bounded batch before forwarding its final upstream chunk."""
    started = time.perf_counter()
    last_event = first_event + len(events) - 1
    ref = durable_body(ordinal, f'events-{batch_index:04d}',
                       {'events': events, 'accumulated_text': accumulated_text,
                        'first_event': first_event, 'last_event': last_event})
    terminal_ref = durable_body(ordinal, 'terminal', events[-1]) if terminal else None
    accumulated_ref = durable_body(ordinal, 'accumulated',
                                   {'text': accumulated_text, 'complete': terminal}) if terminal else None
    last = events[-1] if terminal else {}
    response = last.get('response') if isinstance(last.get('response'), dict) else {}
    response_id = response.get('id') if response else None
    usage = response.get('usage') if isinstance(response.get('usage'), dict) else None
    with connection() as db:
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO event_batches(request_ordinal,batch_index,first_event,last_event,body_path,sha256,bytes,saved_at) VALUES(?,?,?,?,?,?,?,?)',
                       (ordinal, batch_index, first_event, last_event, ref['path'], ref['sha256'], ref['bytes'], time.time()))
            db.execute('UPDATE requests SET event_count=? WHERE ordinal=?', (last_event, ordinal))
            db.execute('COMMIT')
        except BaseException as exc:
            degrade('mandatory_storage_failure:' + type(exc).__name__, exc,
                    operation='save_batch_transaction', ordinal=ordinal,
                    response_id=response_id, db=db)
            raise
    info = ({'at': terminal_at, 'response_id': response_id,
             'usage_json': json.dumps(safe_event(usage)) if usage is not None else None,
             'terminal_path': terminal_ref['path'], 'accumulated_path': accumulated_ref['path']}
            if terminal else None)
    return (time.perf_counter() - started) * 1000.0, info


def acknowledge(response_id: str, stage: str) -> bool:
    # A terminal can reach the app before the gateway's final ledger commit.
    # Wait without holding a transaction; never acknowledge a file alone.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not (AUDIT_DEGRADED or AUDIT_FAILURE.exists()):
        with connection() as lookup:
            row = lookup.execute('SELECT state FROM requests WHERE response_id=?', (response_id,)).fetchone()
            pending = lookup.execute("SELECT COUNT(*) FROM requests WHERE state='reserved'").fetchone()[0]
        if (row and row[0] != 'reserved') or not pending:
            break
        time.sleep(0.02)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        valid = stage in {'received', 'accepted'} and bool(re.fullmatch(r'resp_[A-Za-z0-9_-]{1,160}', response_id))
        rows = db.execute('SELECT ordinal,upstream_complete,application_received_at,state,downstream_complete FROM requests '
                          'WHERE response_id=?', (response_id,)).fetchall() if valid else []
        reason = ('invalid_binding' if not valid else 'foreign_or_ambiguous_response' if len(rows) != 1
                  else 'settlement_incomplete' if rows[0][3] != 'complete' or not rows[0][4]
                  else 'upstream_incomplete' if not rows[0][1]
                  else 'received_required' if stage == 'accepted' and not rows[0][2] else 'recorded')
        ok = reason == 'recorded'
        now = time.time()
        if ok:
            field = 'application_received_at' if stage == 'received' else 'application_accepted_at'
            db.execute(f'UPDATE requests SET {field}=COALESCE({field},?) WHERE ordinal=?', (now, rows[0][0]))
        db.execute('INSERT INTO ack_events(response_id,stage,first_at,last_at,attempts,accepted,reason) '
                   'VALUES(?,?,?,?,1,?,?) ON CONFLICT(response_id,stage) DO UPDATE SET '
                   'last_at=excluded.last_at,attempts=ack_events.attempts+1,accepted=excluded.accepted,reason=excluded.reason',
                   (response_id, stage, now, now, int(ok), reason))
        db.execute('COMMIT')
        return ok


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def reject(self, code: int, label: str) -> None:
        body = label.encode("ascii")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not re.fullmatch(r"/(?:codex/)?v1/responses/[A-Za-z0-9_-]{1,160}", self.path):
            self.reject(403, "route_blocked")
            return
        self.forward(b"")

    def do_POST(self):
        if self.path == '/local/gateway-stop':
            if not DRAIN_REQUEST.exists():
                self.reject(409, 'drain_not_requested'); return
            try:
                with connection() as db:
                    unresolved = db.execute("SELECT COUNT(*) FROM requests WHERE state!='complete' "
                        "OR response_id IS NULL OR application_received_at IS NULL "
                        "OR application_accepted_at IS NULL").fetchone()[0]
            except sqlite3.Error as exc:
                degrade('gateway_drain_storage_failure', exc, operation='drain_pending')
                self.reject(503, 'drain_state_unknown'); return
            if unresolved:
                self.reject(409, 'drain_unresolved'); return
            with self.server.active_lock:
                busy = self.server.active_handlers != 1
            if busy:
                self.reject(409, 'drain_handlers_active'); return
            self.reject(200, 'gateway_stopping')
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path == '/local/decision-ack':
            payload = {}
            try:
                size = int(self.headers.get('Content-Length', '-1'))
                if not 1 <= size <= 512:
                    self.reject(413, 'ack_size_blocked'); return
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    payload = {}
                ok = acknowledge(str(payload.get('response_id') or ''), str(payload.get('stage') or ''))
            except (ValueError, TypeError, sqlite3.Error) as exc:
                if isinstance(exc, sqlite3.Error):
                    degrade('gateway_ack_storage_failure', exc, operation='acknowledge',
                            response_id=str(payload.get('response_id') or '') if isinstance(payload, dict) else None)
                ok = False
            self.reject(200 if ok else 409, 'ack_recorded' if ok else 'ack_rejected')
            return
        if self.path != "/codex/v1/responses":
            self.reject(403, "route_blocked")
            return
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            size = -1
        if size < 1 or size > MAX_BYTES or self.headers.get("Transfer-Encoding"):
            self.reject(413, "request_size_blocked")
            return
        body = self.rfile.read(size)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            self.reject(400, "invalid_json")
            return
        if (not isinstance(payload, dict) or payload.get("model") != "gpt-5-6-sol"
                or payload.get("max_output_tokens") != MAX_OUTPUT or payload.get("tools")):
            self.reject(403, "model_or_token_cap_blocked")
            return
        self.forward(body)

    def forward(self, body: bytes) -> None:
        upstream = urlsplit(KIE)
        upstream_host = upstream.hostname
        if not FIXTURE and upstream_host not in {'127.0.0.1', 'localhost', '::1'}:
            if (upstream.scheme != 'https' or upstream_host != 'api.kie.ai'
                    or upstream.port not in (None, 443) or upstream.username or upstream.password
                    or upstream.path not in ('', '/') or upstream.query or upstream.fragment
                    or self.command != 'POST' or self.path != '/codex/v1/responses'):
                self.reject(403, 'external_route_blocked')
                return
        if not FIXTURE and upstream_host not in {'127.0.0.1', 'localhost', '::1'} and not EXTERNAL_CALLS_ALLOWED:
            self.reject(403, 'external_gateway_disabled')
            return
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or len(auth) < 15:
            self.reject(401, "no_auth")
            return
        try:
            ordinal = reserve(self.command, self.path, hashlib.sha256(body).hexdigest() if body else None)
        except RuntimeError:
            self.reject(429, "diagnostic_budget_blocked")
            return
        if FIXTURE:
            if self.command != "POST":
                self.reject(404, "fixture_no_recovery")
                finish(ordinal, status=404, credits=0, complete=True, downstream_complete=True)
                return
            from group_runner import synthetic_answer
            node = self.headers.get("x-diagnostic-node", "")
            FIXTURE_COUNTS[node] += 1
            answer = synthetic_answer(node, FIXTURE_COUNTS[node])
            response = {"id": f"resp_gateway_fixture_{ordinal}", "status": "completed",
                        "output": [{"content": [{"type": "output_text", "text": answer}]}],
                        "credits_consumed": 0,
                        "usage": {"input_tokens": 0, "output_tokens": 0, "fixture": True}}
            events = [{"type": "response.created", "response": {"id": response["id"]}},
                      {"type": "response.output_text.delta", "delta": answer},
                      {"type": "response.output_text.done", "text": answer},
                      {"type": "response.completed", "response": response}]
            data = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            finish(ordinal, status=200, credits=0, complete=True, downstream_complete=True)
            return
        status = None
        credits = None
        complete = False
        downstream_complete = False
        downstream_open = True
        delivery_error = None
        parser = b""
        events = []
        event_count = 0
        batch_index = 0
        accumulated_text = ''
        storage_ms = 0.0
        socket_write_ms = 0.0
        last_write_at = None
        first_upstream = False
        terminal_received_at = None
        terminal_info = None
        upstream_deadline = time.monotonic() + MAX_UPSTREAM_WALL_S

        def persist_batch(terminal=False):
            nonlocal events, batch_index, storage_ms, terminal_info
            if not events:
                return
            batch_index += 1
            elapsed, info = save_batch(ordinal, batch_index, event_count - len(events) + 1,
                                       events, accumulated_text, terminal=terminal,
                                       terminal_at=terminal_received_at)
            storage_ms += elapsed
            if info:
                terminal_info = info
            events = []

        try:
            headers = {"Authorization": auth}
            headers["Accept-Encoding"] = "identity"
            if body:
                headers["Content-Type"] = "application/json"
            with httpx.Client(trust_env=False, follow_redirects=False, verify=True, timeout=900.0) as client:
                with client.stream(self.command, KIE + self.path, headers=headers, content=body) as upstream:
                    status = upstream.status_code
                    self.connection.settimeout(15.0)
                    self.send_response(status)
                    self.send_header("Content-Type", upstream.headers.get("content-type", "application/octet-stream"))
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for chunk in upstream.iter_raw():
                        if time.monotonic() > upstream_deadline:
                            raise TimeoutError('gateway_upstream_wall_deadline')
                        if not chunk:
                            continue
                        if not first_upstream:
                            first_upstream = True
                            with connection() as db:
                                db.execute('UPDATE requests SET upstream_first_at=? WHERE ordinal=?', (time.time(), ordinal))
                        parser += chunk
                        if len(parser) > 1_000_000:
                            raise ValueError('oversized_unterminated_sse_event')
                        while b"\n" in parser:
                            line, parser = parser.split(b"\n", 1)
                            if line.startswith(b"data:"):
                                try:
                                    event = json.loads(line[5:].strip())
                                except (ValueError, UnicodeError):
                                    continue
                                if isinstance(event, dict):
                                    event_count += 1
                                    if event_count > MAX_SSE_EVENTS:
                                        raise ValueError('gateway_sse_event_limit')
                                    events.append(safe_event(event))
                                    if event.get('type') == 'response.output_text.delta':
                                        accumulated_text += str(event.get('delta') or '')
                                        if len(accumulated_text) > MAX_ACCUMULATED_CHARS:
                                            raise ValueError('gateway_accumulated_text_limit')
                                    terminal = event.get('type') == 'response.completed'
                                    if terminal:
                                        terminal_received_at = time.time()
                                        complete = True
                                        credits = completion_credits(event)
                                    if terminal or len(events) >= 16:
                                        try: persist_batch(terminal=terminal)
                                        except Exception as exc:
                                            delivery_error = 'mandatory_storage_failure:' + type(exc).__name__
                                            degrade(delivery_error, exc, operation='save_batch', ordinal=ordinal,
                                                    response_id=terminal_info.get('response_id') if terminal_info else None)
                                            events = []
                        if downstream_open:
                            started = time.perf_counter()
                            try:
                                self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
                                self.wfile.flush()
                                last_write_at = time.time()
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                downstream_open = False
                                delivery_error = delivery_error or 'downstream_disconnected'
                            socket_write_ms += (time.perf_counter() - started) * 1000.0
                    if events:
                        try: persist_batch()
                        except Exception as exc:
                            delivery_error = 'mandatory_storage_failure:' + type(exc).__name__
                            degrade(delivery_error, exc, operation='save_batch_tail', ordinal=ordinal,
                                    response_id=terminal_info.get('response_id') if terminal_info else None)
                    if downstream_open:
                        started = time.perf_counter()
                        try:
                            self.wfile.write(b"0\r\n\r\n")
                            self.wfile.flush()
                            downstream_complete = True
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            delivery_error = delivery_error or 'downstream_disconnected'
                        socket_write_ms += (time.perf_counter() - started) * 1000.0
                    if status in {400, 401, 403, 404, 422, 429} or self.command == "GET":
                        complete = True  # A definite pre-processing rejection or completed GET.
                    elif credits is None:
                        complete = False  # Without settled credits, stop all later sends.
        except BaseException as exc:
            delivery_error = delivery_error or ('upstream_or_gateway_error:' + type(exc).__name__)
            degrade(delivery_error, exc, operation='forward', ordinal=ordinal,
                    response_id=terminal_info.get('response_id') if terminal_info else None)
        finally:
            try:
                with connection() as db:
                    db.execute('UPDATE requests SET storage_ms=?,socket_write_ms=?,downstream_last_write_at=? WHERE ordinal=?',
                               (storage_ms, socket_write_ms, last_write_at, ordinal))
                finish(ordinal, status=status, credits=credits, complete=complete,
                       downstream_complete=downstream_complete and delivery_error is None,
                       delivery_error=delivery_error, terminal_info=terminal_info)
            except Exception as exc:
                degrade('gateway_ledger_failure:' + type(exc).__name__, exc,
                        operation='settlement', ordinal=ordinal,
                        response_id=terminal_info.get('response_id') if terminal_info else None)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.active_lock = threading.Lock()
        self.active_handlers = 0
        super().__init__(*args, **kwargs)

    def process_request_thread(self, request, client_address):
        with self.active_lock:
            self.active_handlers += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.active_lock:
                self.active_handlers -= 1


if __name__ == "__main__":
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET.exists():
        SOCKET.unlink()
    recover_pending()
    with Server(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o666)
        print("gateway_ready", flush=True)
        server.serve_forever()
