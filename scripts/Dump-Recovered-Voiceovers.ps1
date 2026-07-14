# Выгрузить ВСЕ найденные длинные voiceover-тексты (родительские проекты) в консоль.
# Запуск:
#   powershell -ExecutionPolicy Bypass -File scripts\Dump-Recovered-Voiceovers.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\Dump-Recovered-Voiceovers.ps1 > recovered.txt

param(
    [string]$DataDir = "",
    [int]$MinChars = 80
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $DataDir) { $DataDir = Join-Path $RepoRoot "data" }
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$dumpPy = @'
import asyncio, json, sqlite3, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import session_scope
from app.models import Project
from app.services.mass_factory import mass_parent_id
from app.services.voiceover_recovery import discover_original_candidates
from sqlalchemy import select

MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 80

async def main():
    out = []
    async with session_scope() as session:
        rows = (await session.execute(select(Project).order_by(Project.id))).scalars().all()
        parents = [p for p in rows if mass_parent_id(p) is None]
        for p in parents:
            item = {
                "project_id": p.id,
                "slug": p.slug,
                "topic": p.topic,
                "db_script_len": len((p.script_text or "").strip()),
                "candidates": [],
            }
            vo = p.data_dir / "voiceover.txt"
            if vo.is_file():
                t = vo.read_text(encoding="utf-8", errors="replace").strip()
                if len(t) >= MIN:
                    item["candidates"].append({"source": "voiceover.txt (current)", "chars": len(t), "text": t})
            cands = await discover_original_candidates(session, p)
            for c in cands:
                if len(c.text) >= MIN:
                    item["candidates"].append({"source": c.source, "chars": len(c.text), "text": c.text})
            # dedupe texts
            seen = set()
            uniq = []
            for c in item["candidates"]:
                if c["text"] in seen:
                    continue
                seen.add(c["text"])
                uniq.append(c)
            item["candidates"] = uniq
            out.append(item)
    print(json.dumps(out, ensure_ascii=False, indent=2))

asyncio.run(main())
'@

$tmp = Join-Path $env:TEMP "vp_dump_voiceover.py"
Set-Content -Path $tmp -Value $dumpPy -Encoding UTF8
Push-Location $RepoRoot
& $Py $tmp $MinChars
Pop-Location
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
