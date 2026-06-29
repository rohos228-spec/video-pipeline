"""Диагностика: какой тариф ElevenLabs видит по ELEVENLABS_API_KEY из .env."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "diag-account.txt"
sys.path.insert(0, str(ROOT))


async def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    from app.services.elevenlabs_api import fetch_account_diag

    diag = await fetch_account_diag()
    lines = [
        "=== ElevenLabs account diag (.env key) ===",
        json.dumps(diag, ensure_ascii=False, indent=2),
        "",
        "VERDICT:",
        str(diag.get("verdict") or diag.get("error") or "?"),
        "",
        "Проверка IVC на сайте:",
        str(diag.get("website_ivc_test")),
        "Подписка:",
        str(diag.get("website_subscription")),
        "API keys:",
        str(diag.get("website_api_keys")),
    ]
    text = "\n".join(lines)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    return 0 if diag.get("can_use_instant_voice_cloning") else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
