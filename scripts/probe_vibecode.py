"""Live probe of the Vibecode text route: at most 6 tiny requests, hard cost ceiling.

Learns what the offline stubs only guessed: stream/non-stream response shape, finish_reason,
usage fields, which token-limit parameter is accepted, and whether response_format is honoured.
PAID (a few cents at most). Refuses to run without --live.
Only VIBECODE_API_KEY / VIBECODE_BASE_URL / GPT_RELAY_TOKEN / GPT_BASE_URL / GPT_PROXY_URL are read from
--env-file into this process; nothing secret is printed or written.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

_ENV = ("VIBECODE_API_KEY", "VIBECODE_BASE_URL", "GPT_RELAY_TOKEN", "GPT_BASE_URL", "GPT_PROXY_URL")
MAX_REQUESTS = 6
MAX_USD = 0.05
MODEL = "gpt-5.6-sol"


def load_env(path: str) -> dict:
    out = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in _ENV:
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def shape(value, depth=0):
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) if depth < 2 else type(v).__name__ for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def summarize(label, status, payload, text, finish, usage):
    return {
        "probe": label,
        "http": status,
        "finish_reason": finish,
        "text": (text or "")[:100],
        "usage": usage,
        "top_keys": sorted(payload) if isinstance(payload, dict) else None,
        "shape": shape(payload) if isinstance(payload, dict) else None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if not args.live:
        print("needs --live")
        return 2
    os.environ.update(load_env(args.env_file))
    work = Path(tempfile.mkdtemp(prefix="probe-vibecode-"))
    os.environ.update(SUPABASE_URL="http://127.0.0.1:9", SUPABASE_KEY="unused", TELEGRAM_ENABLED="false",
                      DATA_DIR=str(work / "data"), SQLITE_PATH=str(work / "data" / "state.db"))
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    import app.services.api_tracker_hook as tracker
    from app.services import gpt_api
    from app.services.llm_override import NodeLlmOverride, use_override

    tracker._TRACKER_DB = work / "tracker.db"
    if not (os.environ.get("VIBECODE_API_KEY") or "").strip():
        print("VIBECODE_API_KEY missing in env file; nothing sent")
        return 3

    override = NodeLlmOverride(model_id=MODEL, channel="stable", kind="text", provider="vibecode",
                               label="probe", reasoning_effort=None)
    results = []
    sent = 0
    spent = 0.0

    def account(usage):
        nonlocal spent
        prompt = int((usage or {}).get("prompt_tokens") or (usage or {}).get("input_tokens") or 0)
        completion = int((usage or {}).get("completion_tokens") or (usage or {}).get("output_tokens") or 0)
        spent += (tracker.vibecode_cost(MODEL, prompt, completion) or 0.0) * 3

    with use_override(override):
        print("route host:", gpt_api._chat_url(MODEL).split("/")[2])

        async def post(label, body):
            nonlocal sent
            if sent >= MAX_REQUESTS or spent >= MAX_USD:
                print("ceiling reached; stopping before", label)
                return None
            sent += 1
            url = gpt_api._chat_url(MODEL)
            async with gpt_api._async_client(timeout=gpt_api._http_timeout(90)) as client:
                resp = await client.post(url, headers=gpt_api._headers(), json=body)
            try:
                payload = resp.json()
            except ValueError:
                payload = None
            text = finish = None
            usage = {}
            if resp.status_code < 400 and isinstance(payload, dict):
                try:
                    text, finish = gpt_api._parse_choice(payload)
                except Exception as exc:
                    text, finish = "", str(getattr(exc, "context", {}).get("finish_reason") or "")
                usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                account(usage)
            else:
                text = str(resp.text)[:200]
            row = summarize(label, resp.status_code, payload if resp.status_code < 400 else None, text, finish, usage)
            results.append(row)
            print(json.dumps(row, ensure_ascii=False))
            return resp.status_code

        base = [{"role": "user", "content": "Reply with exactly the single word: OK"}]
        first = await post("nonstream_max_completion_tokens", {"model": MODEL, "messages": base, "stream": False,
                                                              "max_completion_tokens": 64})
        if first in (401, 402, 403):
            print("auth/credit failure; stopping")
        else:
            await post("nonstream_max_tokens", {"model": MODEL, "messages": base, "stream": False, "max_tokens": 64})
            await post("nonstream_truncation", {"model": MODEL, "stream": False, "max_completion_tokens": 4,
                                                "messages": [{"role": "user", "content": "Count from 1 to 30 separated by commas."}]})
            await post("nonstream_response_format_json", {"model": MODEL, "stream": False, "max_completion_tokens": 64,
                                                          "response_format": {"type": "json_object"},
                                                          "messages": [{"role": "user", "content": 'Return the JSON object {"ok": true} and nothing else.'}]})
            sent += 1
            try:
                chat = await gpt_api.chat(prompt="Reply with exactly the single word: OK", model=MODEL, max_retries=0,
                                          timeout=90, auto_pack=False, volume_complete=False)
                account(chat.usage)
                row = {"probe": "production_stream_path", "text": (chat.text or "")[:100],
                       "finish_reason": chat.finish_reason, "usage": chat.usage,
                       "raw_keys": sorted(chat.raw) if isinstance(chat.raw, dict) else None,
                       "raw_finish_reason": (chat.raw or {}).get("finish_reason") if isinstance(chat.raw, dict) else None}
            except Exception as exc:
                row = {"probe": "production_stream_path", "error": type(exc).__name__, "message": str(exc)[:200]}
            results.append(row)
            print(json.dumps(row, ensure_ascii=False, default=str))

    summary = {"requests_sent": sent, "estimated_cost_usd_catalog_x3": round(spent, 6), "ceiling_usd": MAX_USD}
    print(json.dumps(summary))
    if args.out:
        Path(args.out).write_text(json.dumps({"results": results, "summary": summary}, indent=1, ensure_ascii=False, default=str),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
