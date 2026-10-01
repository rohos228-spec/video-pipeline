"""Reconcile the project's LLM cost estimate with the real Kie credit balance.

Reads the account balance (GET /api/v1/chat/credit, free) before and after N minimal real
`gpt-5-6-sol` calls through the normal gpt_api path, then compares the balance delta
(credits x CREDIT_USD) with what api_tracker_hook._calc_cost estimates from the same usage.

PAID: sends up to 3 probe requests (tiny, long unique input ~15k tokens, ~500 output tokens). Refuses to run without --live.
Credentials: only GPT_API_KEY / GPT_BASE_URL / GPT_CHAT_PATH / GPT_API_MODE / GPT_PROXY_URL /
KIE_API_KEY / KIE_API_BASE_URL are read from --env-file into this process' environment;
nothing is printed or written except balances, token counts and costs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_ENV = ("GPT_API_KEY", "GPT_BASE_URL", "GPT_CHAT_PATH", "GPT_API_MODE", "GPT_PROXY_URL",
        "GPT_RELAY_TOKEN", "KIE_API_KEY", "KIE_API_BASE_URL")
PROMPT = "Reply with exactly the single word: OK"


def load_env(path: str) -> dict:
    out = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() in _ENV:
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def compare(credits_spent: float, credit_usd: float, estimates_usd: float) -> dict:
    billed = credits_spent * credit_usd
    return {"billed_usd": round(billed, 6), "estimated_usd": round(estimates_usd, 6),
            "ratio_billed_over_estimated": round(billed / estimates_usd, 3) if estimates_usd else None}


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", action="store_true")
    p.add_argument("--env-file", required=True)
    p.add_argument("--calls", type=int, default=3)
    p.add_argument("--long-words", type=int, default=2500)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--idle-reads", type=int, default=0, help="balance reads 10 s apart before any paid call, to expose other consumers")
    p.add_argument("--out", default="")
    a = p.parse_args()
    if not a.live or not 1 <= a.calls <= 5:
        print("needs --live and 1..5 calls")
        return 2
    os.environ.update(load_env(a.env_file))
    os.environ.update(SUPABASE_URL="http://127.0.0.1:9", SUPABASE_KEY="unused", TELEGRAM_ENABLED="false")
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    import app.services.api_tracker_hook as tracker
    from app.bots.kie_http import get_credits
    from app.services import gpt_api
    from app.services.kie_catalog import CREDIT_USD
    from app.services.observation import _usage_tokens

    before = await get_credits()
    print("balance before (credits):", before)
    if before is None:
        print("cannot read balance; aborting before any paid call")
        return 3
    import random
    import string
    rnd = random.Random(7)
    # Probe different cost drivers; each call is followed by its own balance read.
    long_text = " ".join("".join(rnd.choice(string.ascii_lowercase) for _ in range(rnd.randint(3, 9)))
                         for _ in range(a.long_words))
    probes = [("tiny_cached", PROMPT),
              ("long_unique_input", "Count the words below. Reply only with a number.\n" + long_text),
              ("long_output", "Write about 350 words about a paper boat crossing a puddle.")][:a.calls]
    idle = [before]
    for _ in range(a.idle_reads):
        await asyncio.sleep(10)
        idle.append(await get_credits())
    print("idle balance reads (no calls from this script):", idle)
    before = idle[-1] if idle[-1] is not None else before
    rows = []
    last = before
    all_probes = []
    for rep in range(a.repeat):
        rr = random.Random(100 + rep)
        text = " ".join("".join(rr.choice(string.ascii_lowercase) for _ in range(rr.randint(3, 9)))
                        for _ in range(a.long_words))
        header = "Count the words below. Reply only with a number.\n"
        all_probes += [(n, (header + text) if n == "long_unique_input" else t) for n, t in probes]
    for name, prompt_text in all_probes:
        r = await gpt_api.chat(prompt=prompt_text, model="gpt-5-6-sol", max_retries=0, timeout=90,
                               auto_pack=False, volume_complete=False)
        prompt, completion, cached = _usage_tokens(r.usage)
        est = tracker._calc_cost("gpt-5-6-sol", prompt, completion, "text", 1.0, cached_tok=cached)
        await asyncio.sleep(6)
        now = await get_credits()
        delta = None if now is None else round(last - now, 4)
        if now is not None:
            last = now
        rows.append({"probe": name, "prompt_tokens": prompt, "completion_tokens": completion,
                     "cached_tokens": cached,
                     "reasoning_tokens": ((r.usage or {}).get("output_tokens_details") or {}).get("reasoning_tokens"),
                     "estimated_usd": est, "credits_spent": delta,
                     "billed_usd": None if delta is None else round(delta * CREDIT_USD, 6)})
        print(f"{name}: prompt={prompt} completion={completion} cached={cached} reasoning={rows[-1]['reasoning_tokens']} est=${est} credits={delta}")
    after = last
    print("balance after (credits):", after)
    spent = before - after
    total_est = sum(r["estimated_usd"] for r in rows)
    result = {"idle_reads": idle, "credits_before": before, "credits_after": after, "credits_spent": round(spent, 6),
              "credit_usd": CREDIT_USD, "calls": rows, **compare(spent, CREDIT_USD, total_est)}
    print(json.dumps(result, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
