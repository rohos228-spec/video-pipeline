"""Throw-away real server for the command-line card AND the media recovery panel smoke (seeded provider task ledger): empty data dir, seeded project 1, fake planner
model, fake paid/destructive test.* actions (no network, no real spend), kill switch usable from the UI."""
import asyncio, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(r"C:\video-pipeline\video-pipeline-observe-v1")
PORT = int(os.environ.get("E2E_PORT", "8802"))
SEED_MEDIA = os.environ.get("E2E_SEED_MEDIA", "1") == "1"  # seed an open Kie task, a failed one and two unknown-outcome keys
work = Path(tempfile.mkdtemp(prefix="ui_media_"))
(work / "data").mkdir()
os.environ.update(
    DATA_DIR=str(work / "data"), SQLITE_PATH=str(work / "data" / "state.db"), TELEGRAM_ENABLED="false",
    SUPABASE_URL="http://127.0.0.1:9", SUPABASE_KEY="unused", VIBECODE_API_KEY="vk-fake-not-used",
    VIBECODE_BASE_URL="https://vibecode.moe/v1", WEB_PORT=str(PORT), WEB_HOST="127.0.0.1",
    ACTION_LAYER_PLANNER_CALLS_PER_HOUR="10000", ACTION_LAYER_SESSION_CAP_CREDITS="60",
    ACTION_LAYER_TEST_ACTIONS="1", WORKER_ENABLED="false", GPT_API_KEY="", KIE_API_KEY="fake-not-used" if SEED_MEDIA else "")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
print("data_dir", work, flush=True)

import uvicorn

from app.main import _init_db
from app.services.action_layer import planner
from app.services.action_layer.spec import ensure_builtin_actions
from app.services.gpt_api import GptChatResult
from app.web.api import create_app


def fake(aid, **p):
    return {"action_id": aid, "params": p, "depends_on": []}


async def fake_model(**kw):
    payload = json.loads(kw["body"]["messages"][1]["content"])
    text, pid = payload["user_request"], payload["project_context"]["project_id"]
    low = text.lower()
    st = lambda aid, **p: {"action_id": aid, "params": {"project_id": pid, **p}, "depends_on": []}
    steps, clar, expl = [], None, "Делаю."
    if "тест" in low and "стира" in low:
        steps, expl = [fake("test.fake_destructive", label="демо")], "Тестовое стирающее действие (фейк)."
    elif "тест" in low and "платн" in low and "дважды" in low:
        steps = [fake("test.fake_paid_instant", label="1"), {**fake("test.fake_paid_instant", label="2"), "depends_on": [0]}]
        expl = "Два платных теста подряд (фейк)."
    elif "тест" in low and "платн" in low:
        steps, expl = [fake("test.fake_paid_instant", label="демо")], "Платный тест (фейк)."
    elif "шот" in low and "поменя" in low:
        steps, expl = [st("montage.swap_shots", frame_number=4, kind="both")], "Меняю шоты кадра 4."
    elif "доск" in low:
        steps, expl = [st("montage.get_board")], "Читаю состояние доски."
    elif "озвуч" in low:
        steps = [st("montage.upload_voice")]
    else:
        clar = "Что именно сделать? Такого действия в каталоге нет."
    body = {"steps": steps, "needs_clarification": clar, "explanation_ru": expl}
    return GptChatResult(text=json.dumps(body, ensure_ascii=False), model="m", finish_reason="stop",
                         usage={"prompt_tokens": 100, "completion_tokens": 20},
                         raw={"choices": [{"finish_reason": "stop"}]})


async def seed_media(pid):
    """Ledger rows as the real submit path writes them (append-only table: old rows are INSERTED with their time)."""
    from datetime import datetime, timedelta
    from app.db import SessionLocal
    from app.services import spend_guard
    async with SessionLocal() as s:
        await spend_guard.ensure_task_schema(s)
        def row(key, task_id, provider, api, event, status, ago_s=0):
            s.add(spend_guard.ProviderTaskLedger(
                project_id=pid, key=key, task_id=task_id, provider=provider, api=api, model="m-1", event=event,
                status=status, note="ph=0123456789abcdef", created_at=datetime.utcnow() - timedelta(seconds=ago_s)))
        row("clip_001", "kie_task_0001abcd", "kie", "jobs", "submit", "pending", 600)          # open task -> resume_poll
        row("clip_002", "kie_task_0002efgh", "kie", "jobs", "submit", "failed", 900)           # failed -> retry_same (human)
        row("clip_005", spend_guard.intent_task_id("aaaa1111"), "kie", "jobs", "intent", "submit_unknown", 2520)
        row("frame_012", spend_guard.intent_task_id("bbbb2222"), "outsee", "outsee", "intent", "submit_unknown", 3000)
        row("predmet3", "kie_task_0003ijkl", "kie", "jobs", "submit", "pending", 300)         # only an open task -> resumable
        await s.commit()
    print("seeded media ledger", flush=True)


async def main():
    await _init_db()
    ensure_builtin_actions()
    import importlib.util
    spec = importlib.util.spec_from_file_location("fp", ROOT / "scripts" / "e2e_action_layer_fake_paid.py")
    fp = importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)
    print("registered", fp.register_fake_actions(), flush=True)
    import eval_seed
    info = await eval_seed.seed_realistic(1, prompts_dir=work / "prompts")
    for clip in (info.data_dir / "videos").glob("clip_004*.mp4"):   # the swap check wants clips >= 1 KB
        clip.write_bytes(clip.read_bytes() + bytes(2048))
    if SEED_MEDIA:
        await seed_media(1)
    planner.set_transport(fake_model)
    cfg = uvicorn.Config(create_app(), host="127.0.0.1", port=PORT, log_level="warning")
    await uvicorn.Server(cfg).serve()

asyncio.run(main())
