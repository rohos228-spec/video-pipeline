"""REST: поиск по индексу знаний (prompts/docs/key modules)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.services.knowledge_index import build_index, load_index, search_knowledge

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/search")
def knowledge_search(
    q: str = Query("", description="Поисковый запрос"),
    limit: int = Query(12, ge=1, le=50),
) -> dict[str, Any]:
    hits = search_knowledge(q, limit=limit, rebuild_if_missing=True)
    return {"q": q, "count": len(hits), "hits": hits}


@router.post("/rebuild")
def knowledge_rebuild() -> dict[str, Any]:
    payload = build_index(write=True)
    return {"ok": True, "count": payload.get("count", 0)}


@router.get("/status")
def knowledge_status() -> dict[str, Any]:
    data = load_index(rebuild_if_missing=False)
    return {
        "count": data.get("count", 0),
        "version": data.get("version"),
        "has_index": bool(data.get("entries")),
    }


@router.get("/help", response_class=HTMLResponse)
def knowledge_help() -> str:
    """Мини-справка в браузере (без отдельной Studio-страницы)."""
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/>
<title>Studio — справка знаний</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;
  background:#0f1115;color:#e8eaed}
 input{width:70%;padding:.5rem;border-radius:8px;border:1px solid #333;background:#1a1d24;color:#eee}
 button{padding:.5rem 1rem;border-radius:8px;border:0;background:#3b82f6;color:#fff;cursor:pointer}
 pre{background:#1a1d24;padding:1rem;border-radius:8px;overflow:auto;font-size:12px}
 a{color:#93c5fd}
</style></head><body>
<h1>Справка video-pipeline</h1>
<p>Карта: <code>docs/AGENT_MAP.md</code> · Библия: <code>docs/OPERATOR_BIBLE.md</code></p>
<p>
 <input id="q" placeholder="anim_pr, gpt_workspace, mass…"/>
 <button type="button" onclick="go()">Искать</button>
</p>
<pre id="out">Введите запрос</pre>
<script>
async function go(){
  const q=document.getElementById('q').value;
  const r=await fetch('/api/knowledge/search?q='+encodeURIComponent(q));
  const j=await r.json();
  document.getElementById('out').textContent=JSON.stringify(j,null,2);
}
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter')go();});
</script>
</body></html>"""
