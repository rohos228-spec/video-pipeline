"""Проверка ElevenLabs API через прокси из .env. Запуск: CHECK-PROXY.cmd"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_env(name: str) -> str | None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return None
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line)
        if not m:
            continue
        value = m.group(1).strip().strip('"').strip("'")
        if value:
            return value
    return None


def mask_proxy(url: str) -> str:
    if "@" not in url:
        return url
    creds, host = url.rsplit("@", 1)
    scheme = creds.split("://", 1)[0] + "://"
    return f"{scheme}***@{host}"


def main() -> int:
    print("=== ElevenLabs: проверка прокси ===")
    print(f"Папка: {ROOT}")
    print()

    key = read_env("ELEVENLABS_API_KEY")
    proxy = read_env("ELEVENLABS_PROXY_URL") or read_env("TELEGRAM_PROXY_URL")
    upload_proxy = read_env("ELEVENLABS_UPLOAD_PROXY_URL")

    if not key:
        print("ОШИБКА: в .env нет ELEVENLABS_API_KEY")
        return 1

    print(f"Ключ: {key[:8]}…")
    if proxy:
        print(f"API proxy (SOCKS/HTTP): {mask_proxy(proxy)}")
    else:
        print("API proxy: не задан (ELEVENLABS_PROXY_URL пустой)")
        print("Добавь в .env, например:")
        print("ELEVENLABS_PROXY_URL=http://user:pass@host:64240")
        return 1
    if upload_proxy:
        print(f"Upload proxy (HTTP): {mask_proxy(upload_proxy)}")

    print()
    print("Подключение к api.elevenlabs.io…")

    sys.path.insert(0, str(ROOT))
    out_path = ROOT / "data" / "proxy-check-result.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def log(line: str = "") -> None:
        print(line)
        lines.append(line)

    try:
        from app.services.elevenlabs_api import connect_by_ip

        result = asyncio.run(connect_by_ip())
    except Exception as exc:
        log()
        log("FAIL — не удалось подключиться")
        log(str(exc))
        log(traceback.format_exc())
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return 1

    mode = result.get("connection_mode", "?")
    voices = result.get("voice_count", "?")
    note = result.get("note") or ""
    working_proxy = result.get("proxy") or proxy

    log()
    log("OK — прокси работает")
    log(f"  режим: {mode}")
    log(f"  proxy: {mask_proxy(str(working_proxy)) if working_proxy else 'direct'}")
    log(f"  голосов: {voices}")
    if note:
        log(f"  note: {note}")
    log()
    log(json.dumps(
        {k: result[k] for k in ("voice_count", "connection_mode", "key_hint", "proxy") if k in result},
        ensure_ascii=False,
        indent=2,
    ))
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
