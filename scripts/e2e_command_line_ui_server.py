import asyncio, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("E2E_PORT", "8802"))
work = Path(tempfile.mkdtemp(prefix="ui_cmdline_"))
(work / "data").mkdir()
os.environ.update(
    DATA_DIR=str(work / "data"), SQLITE_PATH=str(work / "data" / "state.db"), TELEGRAM_ENABLED="false",
    SUPABASE_URL="http://127.0.0.1:9", SUPABASE_KEY="unused", VIBECODE_API_KEY="vk-fake-not-used",
    VIBECODE_BASE_URL="https://vibecode.moe/v1", WEB_PORT=str(PORT), WEB_HOST="127.0.0.1",
    ACTION_LAYER_PLANNER_CALLS_PER_HOUR="10000", ACTION_LAYER_SESSION_CAP_CREDITS="60",
    ACTION_LAYER_TEST_ACTIONS="1", WORKER_ENABLED="false", GPT_API_KEY="", KIE_API_KEY="")
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


async def main():
    await _init_db()
    ensure_builtin_actions()
    import importlib.util
    spec = importlib.util.spec_from_file_location("fp", ROOT / "scripts" / "e2e_action_layer_fake_paid.py")
    fp = importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)
    print("registered", fp.register_fake_actions(), flush=True)
    import eval_seed
    info = await eval_seed.seed_realistic(1, prompts_dir=work / "prompts")
    for clip in (info.data_dir / "videos").glob("clip_004*.mp4"):
        clip.write_bytes(clip.read_bytes() + bytes(2048))
    planner.set_transport(fake_model)
    cfg = uvicorn.Config(create_app(), host="127.0.0.1", port=PORT, log_level="warning")
    await uvicorn.Server(cfg).serve()

asyncio.run(main())
