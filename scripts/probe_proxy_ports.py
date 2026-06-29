"""Быстрая диагностика proxy — пишет data/proxy-probe.txt. Запуск: PROBE-PROXY.cmd"""
from __future__ import annotations

import asyncio
import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "proxy-probe.txt"


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
        if value and not value.startswith("#"):
            return value
    return None


def tcp(host: str, port: int, timeout: float = 6.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "OK"
    except OSError as exc:
        return f"FAIL: {exc}"


async def try_connect(proxy_url: str) -> str:
    sys.path.insert(0, str(ROOT))
    from app.services.elevenlabs_api import connect_by_ip

    try:
        r = await asyncio.wait_for(
            connect_by_ip(proxy_url=proxy_url),
            timeout=30.0,
        )
        return f"OK voices={r.get('voice_count')} proxy={r.get('proxy', '')[-30:]}"
    except Exception as exc:
        return f"FAIL: {exc}"


async def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    host = "154.196.58.31"
    user, pwd = "fAwkTDGh3", "FuSQ1ch7y"
    key = read_env("ELEVENLABS_API_KEY") or ""
    current = read_env("ELEVENLABS_PROXY_URL") or ""

    lines.append("=== Proxy probe ===")
    lines.append(f"KEY: {key[:8]}…" if len(key) > 8 else "KEY: missing")
    lines.append(f".env: {current}")
    lines.append("")
    lines.append("TCP (справочно):")
    for port in (64240, 64241):
        lines.append(f"  {host}:{port} -> {tcp(host, port)}")

    candidates: list[str] = []
    for url in (current, read_env("ELEVENLABS_PROXY_ALT_URL")):
        if url and url not in candidates:
            candidates.append(url)
    lines.append("")
    lines.append("ElevenLabs /voices (только URL из .env):")
    for url in candidates:
        scheme = url.split("://", 1)[0]
        lines.append(f"  [{scheme}] …@{url.rsplit('@', 1)[-1]}")
        lines.append(f"    -> {await try_connect(url)}")

    text = "\n".join(lines)
    OUT.write_text(text + "\n", encoding="utf-8")
    print(text)
    ok = any("OK voices=" in ln for ln in lines)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
