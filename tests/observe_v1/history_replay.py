import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

SOURCE = Path("/observe-source")
ROOT = Path('/results')
if (ROOT/'history-root.txt').exists():
    ROOT=Path((ROOT/'history-root.txt').read_text().strip()).resolve()
    assert ROOT.is_relative_to(Path('/results')), 'Synthetic history must remain under results'
os.environ.update({"DATA_DIR":str(ROOT/'free-run-data'), "SQLITE_PATH":str(ROOT/'free-run-data/state.db'),
    "GPT_API_KEY":"synthetic-history-only", "GPT_BASE_URL":"http://127.0.0.1:18766",
    "TELEGRAM_ENABLED":"false", "FLEET_ENABLED":"false", "TELEGRAM_BOT_TOKEN":""})
sys.path.insert(0,str(SOURCE))

async def run():
    import uvicorn
    from app.services.observation import get_observer
    from app.web.api import create_app
    observer=get_observer()
    await observer.reload()
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
    (ROOT / "history-replay-modules.json").write_text(json.dumps(loaded,indent=2))
    server=uvicorn.Server(uvicorn.Config(create_app(),host="127.0.0.1",port=18765,lifespan="off",log_level="warning"))
    async def finish():
        while not (ROOT/'browser-finish-requested').exists():
            await asyncio.sleep(.25)
        server.should_exit=True
    stopper=asyncio.create_task(finish())
    try:
        await server.serve()
    finally:
        stopper.cancel()
        loaded=[]
        for name,module in sys.modules.copy().items():
            filename=getattr(module,'__file__',None)
            if name.startswith('app.') and filename:
                path=Path(filename).resolve()
                assert path.is_relative_to(SOURCE),name
                relative=path.relative_to(SOURCE).as_posix()
                sha=hashlib.sha256(path.read_bytes()).hexdigest()
                assert manifest[relative]==sha,name
                loaded.append({'module':name,'path':relative,'sha256':sha})
        (ROOT/'history-replay-final-modules.json').write_text(json.dumps(loaded,indent=2))
        sys.stdout.flush();sys.stderr.flush()
        os._exit(0)

asyncio.run(run())
