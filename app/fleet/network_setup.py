"""Автонастройка сети fleet при старте Studio (без ручных скриптов)."""

from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path

from loguru import logger

from app.project_root import find_project_root


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"')
    return out


def _write_env_keys(path: Path, updates: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    for key, value in updates.items():
        pattern = re.compile(rf"^\s*{re.escape(key)}=.*$", re.MULTILINE)
        line = f"{key}={value}"
        if pattern.search(text):
            text = pattern.sub(line, text, count=1)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
    path.write_text(text, encoding="utf-8")


def is_valid_tailscale_ipv4(ip: str) -> bool:
    """Tailscale CGNAT: 100.64.0.0 – 100.127.255.255 (не 100.0.0.1 и пр.)."""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b, c, d = (int(p) for p in parts)
    except ValueError:
        return False
    if a != 100 or not (0 <= c <= 255 and 0 <= d <= 255):
        return False
    return 64 <= b <= 127


def detect_tailscale_ipv4() -> str | None:
    if platform.system() != "Windows":
        return None
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Tailscale" / "tailscale.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Tailscale" / "tailscale.exe",
    ]
    exe = next((p for p in candidates if p.is_file()), None)
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [str(exe), "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if proc.returncode != 0:
            return None
        for raw in proc.stdout.splitlines():
            ip = raw.strip()
            if is_valid_tailscale_ipv4(ip):
                return ip
    except Exception as exc:  # noqa: BLE001
        logger.debug("tailscale ip detect failed: {}", exc)
    return None


def _needs_external_bind(fleet_enabled: bool, fleet_role: str, hub_is_worker: bool) -> bool:
    if not fleet_enabled:
        return False
    role = (fleet_role or "hub").strip().lower()
    if role == "agent":
        return True
    if role == "hub" and hub_is_worker:
        return True
    return False


def ensure_windows_firewall(port: int = 8765) -> None:
    if platform.system() != "Windows":
        return
    rule_name = f"Video Pipeline Studio {port}"
    try:
        check = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Get-NetFirewallRule -DisplayName '{rule_name}' -ErrorAction SilentlyContinue",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if check.stdout.strip():
            return
        script = find_project_root() / "scripts" / "allow-studio-firewall.ps1"
        if not script.is_file():
            return
        logger.info("fleet: creating firewall rule for port {} …", port)
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("fleet: firewall auto-setup skipped: {}", exc)


def prepare_fleet_network() -> str:
    """Вызывается до uvicorn: 0.0.0.0 для agent, Tailscale URL в .env, firewall."""
    root = find_project_root()
    env_path = root / ".env"
    env_map = _read_env_file(env_path)

    fleet_enabled = env_map.get("FLEET_ENABLED", "true").lower() in ("1", "true", "yes")
    fleet_role = env_map.get("FLEET_ROLE", "hub")
    hub_is_worker = env_map.get("FLEET_HUB_IS_WORKER", "false").lower() in ("1", "true", "yes")
    web_host = (env_map.get("WEB_HOST") or os.environ.get("WEB_HOST") or "127.0.0.1").strip()
    web_port = (env_map.get("WEB_PORT") or os.environ.get("WEB_PORT") or "8765").strip()
    updates: dict[str, str] = {}

    if _needs_external_bind(fleet_enabled, fleet_role, hub_is_worker):
        if web_host in ("127.0.0.1", "localhost", "::1"):
            web_host = "0.0.0.0"
            updates["WEB_HOST"] = web_host
            os.environ["WEB_HOST"] = web_host
            logger.info("fleet: WEB_HOST auto → 0.0.0.0 (доступ hub по Tailscale)")
        ensure_windows_firewall(int(web_port or 8765))

    ts_ip = detect_tailscale_ipv4()
    if ts_ip and fleet_enabled:
        public = (env_map.get("FLEET_PUBLIC_URL") or "").strip().rstrip("/")
        expected = f"http://{ts_ip}:{web_port or 8765}"
        if not public or "127.0.0.1" in public or "localhost" in public:
            updates["FLEET_PUBLIC_URL"] = expected
            os.environ["FLEET_PUBLIC_URL"] = expected
            logger.info("fleet: FLEET_PUBLIC_URL auto → {}", expected)
        else:
            pub_host = public.split("://", 1)[-1].split(":", 1)[0]
            if not is_valid_tailscale_ipv4(pub_host):
                updates["FLEET_PUBLIC_URL"] = expected
                os.environ["FLEET_PUBLIC_URL"] = expected
                logger.warning(
                    "fleet: FLEET_PUBLIC_URL {} invalid → {}",
                    public,
                    expected,
                )
            elif ts_ip not in public:
                logger.warning(
                    "fleet: Tailscale IP {} ≠ FLEET_PUBLIC_URL {} — hub может не достучаться",
                    ts_ip,
                    public,
                )

    if updates and env_path.is_file():
        try:
            _write_env_keys(env_path, updates)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fleet: .env auto-patch failed: {}", exc)

    return web_host
