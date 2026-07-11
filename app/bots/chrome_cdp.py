"""Chrome CDP: нормализация URL, блокировка, авто-перезапуск при зависании Playwright.

Симптом: /json/version отвечает, ws connected, но connect_over_cdp висит 60+ с —
Chrome запущен без изолированного профиля или «зомби»-сессия CDP. Лечение:
перезапуск через scripts/VpBrowserProfile.ps1 (профиль %USERPROFILE%\\.vp_browser_data).
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
import time
from pathlib import Path

import aiohttp
from loguru import logger

from app.project_root import find_project_root
from app.settings import settings

# Только одно Playwright-подключение к Chrome за раз (второе часто вешает CDP).
_CDP_LOCK = asyncio.Lock()
_last_recovery_mono: float = 0.0
_RECOVERY_COOLDOWN_SEC = 90.0
_linux_chrome_proc: asyncio.subprocess.Process | None = None


class ChromeCdpUnavailableError(RuntimeError):
    """Chrome не слушает CDP — пайплайн не может открыть ChatGPT/Outsee."""


def is_cdp_connection_error(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "cannot connect to host",
        "clientconnectorerror",
        "connection refused",
        "connect call failed",
        "connectionreseterror",
        "cdp get",
        "29229",
        "chrome cdp недоступен",
    )
    return any(m in msg for m in markers)


def normalize_cdp_http_url(url: str) -> str:
    """localhost → 127.0.0.1 (меньше сюрпризов на Windows)."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return "http://127.0.0.1:29229"
    u = re.sub(r"^http://localhost(?=[:/]|$)", "http://127.0.0.1", u, flags=re.I)
    u = re.sub(r"^ws://localhost(?=[:/]|$)", "ws://127.0.0.1", u, flags=re.I)
    if not u.startswith(("http://", "https://", "ws://", "wss://")):
        u = f"http://{u}"
    return u


def cdp_port_from_url(url: str) -> int:
    m = re.search(r":(\d+)(?:/|$)", normalize_cdp_http_url(url))
    return int(m.group(1)) if m else 29229


async def fetch_cdp_version(cdp_url: str) -> dict:
    base = normalize_cdp_http_url(cdp_url)
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        async with sess.get(f"{base}/json/version") as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"CDP GET {base}/json/version → HTTP {resp.status}"
                )
            data = await resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("CDP /json/version: invalid JSON")
    return data


async def log_cdp_health(cdp_url: str) -> str:
    data = await fetch_cdp_version(cdp_url)
    browser = data.get("Browser") or data.get("browser") or "?"
    ws = data.get("webSocketDebuggerUrl") or "?"
    logger.info(
        "cdp health: {} | ws={} | {}",
        browser,
        ws,
        normalize_cdp_http_url(cdp_url),
    )
    return str(browser)


async def _linux_start_chrome_cdp(port: int) -> bool:
    """Linux/macOS: поднять Chrome/Chromium с CDP, если бинарник есть в PATH."""
    global _linux_chrome_proc

    if await _probe_cdp(port):
        return True

    if _linux_chrome_proc is not None and _linux_chrome_proc.returncode is None:
        for _ in range(15):
            if await _probe_cdp(port):
                return True
            await asyncio.sleep(1)

    exe: str | None = None
    for name in (
        "google-chrome-stable",
        "google-chrome",
        "chromium-browser",
        "chromium",
        "chrome",
    ):
        found = shutil.which(name)
        if found:
            exe = found
            break
    if not exe:
        logger.warning("chrome_cdp: нет google-chrome/chromium в PATH")
        return False

    user_data = Path.home() / ".vp_browser_data"
    user_data.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "about:blank",
    ]
    logger.warning("chrome_cdp: запуск {} :{} ...", exe, port)
    try:
        _linux_chrome_proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("chrome_cdp: не удалось запустить Chrome: {}", exc)
        return False

    for _ in range(45):
        if await _probe_cdp(port):
            logger.info("chrome_cdp: CDP :{} отвечает (linux)", port)
            return True
        if _linux_chrome_proc.returncode is not None:
            logger.error(
                "chrome_cdp: Chrome завершился rc={}",
                _linux_chrome_proc.returncode,
            )
            return False
        await asyncio.sleep(1)
    return False


async def _probe_cdp(port: int) -> bool:
    try:
        await fetch_cdp_version(f"http://127.0.0.1:{port}")
        return True
    except Exception:  # noqa: BLE001
        return False


async def ensure_cdp_ready(*, force_recover: bool = False) -> None:
    """Проверить CDP; при отказе — до 3 перезапусков Chrome."""
    url = normalize_cdp_http_url(settings.browser_cdp_url)
    port = cdp_port_from_url(url)

    try:
        await fetch_cdp_version(url)
        return
    except Exception as exc:  # noqa: BLE001
        if not is_cdp_connection_error(exc):
            raise
        first_err = exc

    max_attempts = 3 if force_recover else 2
    last_err: BaseException | None = first_err

    for attempt in range(1, max_attempts + 1):
        logger.warning(
            "cdp: нет ответа :{} — перезапуск Chrome ({}/{}): {}",
            port,
            attempt,
            max_attempts,
            last_err,
        )
        recovered = await restart_chrome_cdp(
            reason=f"ensure_cdp_ready attempt {attempt}",
            force=True,
        )
        if recovered:
            try:
                await fetch_cdp_version(url)
                logger.info("cdp: Chrome готов на {} (попытка {})", url, attempt)
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if not is_cdp_connection_error(exc):
                    raise
        await asyncio.sleep(2)

    raise ChromeCdpUnavailableError(
        f"Chrome CDP недоступен на 127.0.0.1:{port} после {max_attempts} перезапусков. "
        "Запустите Start-Chrome.cmd, войдите в ChatGPT."
    ) from last_err


def playwright_cdp_hang(exc: BaseException) -> bool:
    """Playwright подключился по ws, но не завершил handshake (типичный зомби CDP)."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    if "ws connected" in msg:
        return True
    return "connect_over_cdp" in msg and "timeout" in msg and "exceeded" in msg


async def restart_chrome_cdp(*, reason: str = "", force: bool = True) -> bool:
    """Полный перезапуск Chrome (kill + start) и ожидание CDP."""
    global _linux_chrome_proc, _last_recovery_mono

    if reason:
        logger.info("chrome_cdp: restart_chrome_cdp — {}", reason[:200])

    port = cdp_port_from_url(settings.browser_cdp_url)

    if sys.platform != "win32":
        if _linux_chrome_proc is not None and _linux_chrome_proc.returncode is None:
            try:
                _linux_chrome_proc.terminate()
                await asyncio.wait_for(_linux_chrome_proc.wait(), timeout=8)
            except Exception:  # noqa: BLE001
                try:
                    _linux_chrome_proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            _linux_chrome_proc = None
            await asyncio.sleep(1)
        ok = await _linux_start_chrome_cdp(port)
        if ok:
            _last_recovery_mono = time.monotonic()
        return ok

    return await recover_chrome_cdp(force=force)


async def recover_chrome_cdp(*, force: bool = False) -> bool:
    """Убить chrome.exe и поднять CDP с профилем pipeline (Windows)."""
    global _last_recovery_mono

    if not settings.browser_cdp_auto_recover and not force:
        return False

    now = time.monotonic()
    if not force and (now - _last_recovery_mono) < _RECOVERY_COOLDOWN_SEC:
        logger.warning(
            "chrome_cdp: авто-перезапуск пропущен (кулдаун {:.0f} с). "
            "Закройте Chrome и запустите Start-Chrome.cmd",
            _RECOVERY_COOLDOWN_SEC - (now - _last_recovery_mono),
        )
        return False

    if sys.platform != "win32":
        return await _linux_start_chrome_cdp(cdp_port_from_url(settings.browser_cdp_url))

    root = find_project_root()
    ps1 = root / "scripts" / "VpBrowserProfile.ps1"
    if not ps1.is_file():
        logger.error("chrome_cdp: нет скрипта {}", ps1)
        return False

    port = cdp_port_from_url(settings.browser_cdp_url)
    logger.warning(
        "chrome_cdp: перезапуск Chrome :{} (профиль pipeline, CDP завис)...",
        port,
    )

    script = (
        f"& '{ps1}'; "
        "Stop-VpChromeProcesses | Out-Null; "
        "Start-Sleep -Seconds 2; "
        "$d = Get-VpBrowserUserDataDir; "
        "Clear-VpChromeProfileLocks -UserDataDir $d; "
        f"Start-VpChromeCdp -Port {port} -SkipCloseCheck -ForceNew"
    )
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
        cwd=str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except TimeoutError:
        proc.kill()
        logger.error("chrome_cdp: перезапуск Chrome — таймаут 120 с")
        return False

    if proc.returncode != 0:
        err = (stderr or b"").decode(errors="replace").strip()
        logger.error("chrome_cdp: перезапуск failed rc={} {}", proc.returncode, err[:600])
        return False

    # Дождаться живого CDP
    base = f"http://127.0.0.1:{port}"
    for _ in range(45):
        try:
            await fetch_cdp_version(base)
            _last_recovery_mono = time.monotonic()
            logger.info("chrome_cdp: Chrome CDP снова отвечает на {}", base)
            return True
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1)

    logger.error("chrome_cdp: Chrome запущен, но CDP :{} не отвечает", port)
    return False
