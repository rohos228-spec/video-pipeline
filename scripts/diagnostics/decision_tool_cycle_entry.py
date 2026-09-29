"""Run the original observer/DecisionSupervisor against an isolated synthetic DB.

Only the HTTP transport and credential header are bound to the existing Unix
socket gateway. No application worker or business executor is started.
"""
import asyncio
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx
from loguru import logger

ROOT = Path('/results')
SOURCE = Path('/observe-source')
DATA = ROOT / 'free-run-data'
SERVICE = {'observation_executions', 'observation_outbox', 'observation_events',
           'observation_incidents', 'observation_diagnosis', 'action_execution_controls',
           'action_v1_records', 'decision_execution_controls', 'decision_runtime_bindings',
           'decision_records', 'decision_turns'}

def save(name, value):
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

def snapshot():
    result = {}
    for path in DATA.rglob('*.db'):
        with sqlite3.connect(path) as db:
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                if table in SERVICE:
                    continue
                rows = db.execute('SELECT * FROM "' + table + '"').fetchall()
                result[str(path.relative_to(DATA)) + ':' + table] = sorted([list(row) for row in rows], key=str)
    return result

def prohibit_business_writes():
    for path in DATA.rglob('*.db'):
        with sqlite3.connect(path) as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                if table in SERVICE:
                    continue
                for op in ('INSERT', 'UPDATE', 'DELETE'):
                    db.execute(f'''CREATE TRIGGER "readonly_{table}_{op}" BEFORE {op} ON "{table}"
                        BEGIN SELECT RAISE(ABORT,'diagnostic executor forbids business/profile/recovery write'); END''')
            if 'action_v1_records' in tables:
                db.execute('''CREATE TRIGGER readonly_action_audit BEFORE INSERT ON action_v1_records
                    WHEN NEW.action_kind NOT IN ('decision_mode','human_review')
                    BEGIN SELECT RAISE(ABORT,'diagnostic executor forbids modifying action'); END''')

def verify_isolation():
    active = sorted(name for _, name in socket.if_nameindex()
        if int((Path('/sys/class/net') / name / 'flags').read_text().strip(), 16) & 1)
    routes = Path('/proc/net/route').read_text().splitlines()[1:]
    if active != ['lo'] or any(line.strip() for line in routes):
        raise RuntimeError('application network isolation failed')
    test = socket.socket()
    test.settimeout(2)
    try:
        blocked = test.connect_ex(('192.0.2.1', 9))
    finally:
        test.close()
    child = subprocess.run([sys.executable, '-c',
        "import socket; s=socket.socket(); s.settimeout(2); print(s.connect_ex(('192.0.2.1',9))); s.close()"],
        capture_output=True, text=True, timeout=5, check=True)
    if blocked == 0 or int(child.stdout.strip()) == 0:
        raise RuntimeError('foreign address reachable')
    save('isolation.json', {'active_interfaces': active, 'external_routes': 0,
        'foreign_connect_errno': blocked, 'child_foreign_connect_errno': int(child.stdout.strip()),
        'foreign_bytes_sent': 0})

async def run(key):
    if not Path('/.dockerenv').exists():
        raise RuntimeError('container required')
    ROOT.mkdir(exist_ok=True)
    verify_isolation()
    provenance = json.loads(Path('/observe-checkpoint-provenance.json').read_text())
    if not provenance['synthetic_only'] or provenance['status'] != 'exhausted before supervisor action':
        raise RuntimeError('unexpected seed')
    shutil.copytree('/observe-denial-seed', DATA)
    save('checkpoint-provenance.json', provenance)
    cfg = {'version': 1, 'mode': 'production', 'online_enabled': True,
           'endpoint': 'http://127.0.0.1/codex/v1/responses',
           'decision': {'catalog_id': 'gpt-kie', 'model_id': 'gpt-5-6-sol',
                        'reasoning_effort': 'low', 'max_output_tokens': 1024, 'timeout_s': 90},
           'profiles': {}, 'allowed_actions': ['read_events', 'read_contract', 'wait', 'human_review'],
           'model_transport': 'real', 'external_calls_enabled': True,
           'retrieval_allowed': False, 'continuation_allowed': False, 'gateway_ack': True}
    save('runtime-config.json', cfg)
    os.environ.update({'DATA_DIR': str(DATA), 'SQLITE_PATH': str(DATA / 'state.db'),
        'GPT_API_KEY': 'synthetic-readonly-transport-key', 'GPT_BASE_URL': 'http://127.0.0.1',
        'GPT_API_MODE': 'responses', 'GPT_CHAT_PATH': '/codex/v1/responses',
        'GPT_MAX_RETRIES': '0', 'ORCHESTRATOR_RUNTIME_CONFIG': str(ROOT / 'runtime-config.json'),
        'TELEGRAM_ENABLED': 'false', 'TELEGRAM_BOT_TOKEN': '', 'FLEET_ENABLED': 'false'})
    sys.path.insert(0, str(SOURCE))
    from sqlalchemy import select
    from app.db import engine, SessionLocal
    from app.services import observation, gpt_api
    from app.services.actions_v1 import ActionExecutionControl
    from app.services.decision_v1 import get_decider
    from app.services.decision_state import DecisionRecord, DecisionTurn
    from app.services.actions_recovery import RecoveryAction
    async with engine.begin() as connection:
        await observation.migrate_observation_schema(connection)
    observer = observation.get_observer()
    await observer.reload()
    execution = provenance['execution_id']
    async with SessionLocal() as s:
        control = await s.get(ActionExecutionControl, execution)
        if control.requests_used != 18 or not control.enabled or control.held:
            raise RuntimeError('seed permission/counters changed')
        if (await s.scalars(select(RecoveryAction))).all():
            raise RuntimeError('seed has recovery actions')
        used_before = control.requests_used
        control.request_limit = used_before + 3
        await s.commit()
    await observer.reload()
    decider = get_decider(observer)
    await decider.configure(project_id=2, execution_id=execution, enabled=True,
        initiator='authorized-readonly-online-pilot',
        rationale='At most three Decision turns with only read tools, wait, human review')
    prohibit_business_writes()
    before = snapshot()
    save('business-before.json', before)
    def socket_client(**kwargs):
        kwargs['transport'] = httpx.AsyncHTTPTransport(uds='/gateway/llm.sock', retries=0)
        kwargs['trust_env'] = False
        return httpx.AsyncClient(**kwargs)
    task = None
    try:
        with patch.object(gpt_api, '_async_client', socket_client), patch.object(
                gpt_api, '_headers', return_value={'Authorization': 'Bearer ' + key}):
            task = asyncio.create_task(observer.loop())
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                async with SessionLocal() as s:
                    records = (await s.scalars(select(DecisionRecord))).all()
                    if records and records[0].status in {'human_review', 'done'}:
                        break
                await asyncio.sleep(.2)
            else:
                raise RuntimeError('pilot deadline reached; no further POST authorized')
            await asyncio.sleep(1)
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
    after = snapshot()
    save('business-after.json', after)
    async with SessionLocal() as s:
        records = (await s.scalars(select(DecisionRecord))).all()
        turns = (await s.scalars(select(DecisionTurn).order_by(DecisionTurn.id))).all()
        control = await s.get(ActionExecutionControl, execution)
        recoveries = (await s.scalars(select(RecoveryAction))).all()
        result = {'execution_id': execution, 'decision_id': records[0].decision_id if records else None,
            'status': records[0].status if records else 'not_started',
            'model_steps': records[0].model_steps if records else 0,
            'requests_before': used_before, 'requests_after': control.requests_used,
            'request_limit': control.request_limit, 'business_unchanged': before == after,
            'recovery_count': len(recoveries), 'action_id': records[0].action_id if records else None,
            'proposals': [t.payload for t in turns if t.role == 'assistant'],
            'tool_results': [t.payload for t in turns if t.role == 'tool'],
            'http_events': [t.payload for t in turns if t.role == 'http_event']}
    save('result.json', result)
    if not result['business_unchanged'] or result['recovery_count'] or result['action_id']:
        raise RuntimeError('read-only invariant violated')
    print(json.dumps({'status': result['status'], 'model_steps': result['model_steps'],
                      'execution_id': execution, 'business_unchanged': True}), flush=True)

if __name__ == '__main__':
    key = sys.stdin.read().strip()
    if not key or '\n' in key or key.startswith('synthetic'):
        raise SystemExit('No authorized bearer')
    ROOT.mkdir(exist_ok=True)
    logger.remove()
    def safe_sink(message):
        text = str(message).replace(key, '[redacted]')
        with (ROOT / 'adapter-console.log').open('a', encoding='utf-8') as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
    logger.add(safe_sink)
    try:
        asyncio.run(run(key))
    except BaseException as exc:
        save('failure.json', {'type': type(exc).__name__, 'reason': str(exc).replace(key, '[redacted]')})
        raise
