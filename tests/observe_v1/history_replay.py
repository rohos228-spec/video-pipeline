"""Serve original observation API and UI against saved synthetic history only.

No pipeline worker, model transport, startup guards or application maintenance
are launched. This viewer never claims to be a second pipeline run.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

SOURCE = Path("/observe-source")
os.environ.update({"DATA_DIR":"/results/free-run-data", "SQLITE_PATH":"/results/free-run-data/state.db",
    "GPT_API_KEY":"synthetic-history-only", "GPT_BASE_URL":"http://127.0.0.1:18766",
    "TELEGRAM_ENABLED":"false", "FLEET_ENABLED":"false", "TELEGRAM_BOT_TOKEN":""})
sys.path.insert(0,str(SOURCE))

async def run():
    import uvicorn
    from app.services.observation import get_observer
    from app.web.api import create_app
    observer=get_observer()
    await observer.reload()
    consumer=asyncio.create_task(observer.loop())
    manifest=json.loads((SOURCE / "source-manifest.json").read_text())
    loaded=[]
    for name,module in sys.modules.copy().items():
        filename=getattr(module,"__file__",None)
        if name.startswith("app.") and filename:
            path=Path(filename).resolve()
            assert path.is_relative_to(SOURCE), name
            relative=path.relative_to(SOURCE).as_posix()
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            assert manifest[relative]==sha, name
            loaded.append({"module":name,"path":relative,"sha256":sha})
    (Path("/results") / "history-replay-modules.json").write_text(json.dumps(loaded,indent=2))
    try:
        await uvicorn.Server(uvicorn.Config(create_app(),host="127.0.0.1",port=18765,lifespan="off",log_level="warning")).serve()
    finally:
        consumer.cancel()

asyncio.run(run())
