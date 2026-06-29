"""ElevenLabs REST API — TTS без браузера (нет CDP/гео-банов UI)."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from contextlib import asynccontextmanager
from urllib.parse import quote

import aiohttp
from loguru import logger

from app.models import Project
from app.services.elevenlabs_voices import resolve_elevenlabs_voice_id
from app.services.frame_audio import (
    concat_mp3_files,
    delete_frame_audio_files,
    resolve_full_voiceover_text,
    _run_ffmpeg,
)
from app.services.media_probe import probe_duration
from app.settings import settings

_API_BASE = "https://api.elevenlabs.io/v1"
_LAB_DIR_NAME = "elevenlabs_lab"


def lab_dir() -> Path:
    p = settings.data_dir / _LAB_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


class ElevenLabsApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def api_key_configured() -> bool:
    key = (settings.elevenlabs_api_key or "").strip()
    return len(key) > 10


def build_proxy_url(
    *,
    proxy_url: str | None = None,
    proxy_ip: str | None = None,
    proxy_port: int | None = None,
    proxy_user: str | None = None,
    proxy_password: str | None = None,
    proxy_scheme: str | None = "http",
) -> str | None:
    raw = (proxy_url or settings.elevenlabs_proxy_url or "").strip()
    if not raw:
        raw = (settings.telegram_proxy_url or "").strip()
    if raw:
        return _normalize_proxy_url(raw)
    ip = (proxy_ip or "").strip()
    if not ip:
        return None
    port = proxy_port or (8000 if (proxy_scheme or "socks5").startswith("socks") else 8080)
    scheme = (proxy_scheme or "socks5").strip().lower().removesuffix("://")
    if scheme not in ("http", "https", "socks4", "socks5", "socks5h"):
        scheme = "socks5h"
    elif scheme == "socks5":
        scheme = "socks5h"
    user = (proxy_user or "").strip()
    pwd = (proxy_password or "").strip()
    if user and pwd:
        return f"{scheme}://{quote(user, safe='')}:{quote(pwd, safe='')}@{ip}:{port}"
    return f"{scheme}://{ip}:{port}"


def build_upload_proxy_url(
    *,
    proxy_url: str | None = None,
    proxy_ip: str | None = None,
    proxy_port: int | None = None,
    proxy_user: str | None = None,
    proxy_password: str | None = None,
    proxy_scheme: str | None = "http",
) -> str | None:
    """HTTP proxy для multipart upload; иначе рабочий proxy из connect/cache."""
    upload = (settings.elevenlabs_upload_proxy_url or "").strip()
    if upload:
        return _normalize_proxy_url(upload)
    cached = effective_proxy_url(proxy_url)
    if cached:
        return cached
    return build_proxy_url(
        proxy_url=proxy_url,
        proxy_ip=proxy_ip,
        proxy_port=proxy_port,
        proxy_user=proxy_user,
        proxy_password=proxy_password,
        proxy_scheme=proxy_scheme,
    )


def _is_socks_proxy(proxy: str | None) -> bool:
    if not proxy:
        return False
    return proxy.lower().startswith(("socks4://", "socks5://", "socks5h://"))


def _proxy_connector(proxy: str):
    from aiohttp_socks import ProxyConnector

    return ProxyConnector.from_url(
        proxy,
        rdns=True,
        enable_cleanup_closed=True,
    )


def _json_timeout() -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=60, connect=15, sock_connect=15, sock_read=45)


def _upload_timeout() -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=180, connect=45, sock_connect=45, sock_read=180)


def _curl_proxy_cmd_args(proxy: str | None) -> list[str]:
    """Windows curl: лучше --proxy host + --proxy-user, не creds в URL."""
    if not proxy:
        return []
    p = _normalize_proxy_url(proxy)
    parsed = urlparse(p)
    if not parsed.hostname:
        return ["--proxy", p]
    scheme = parsed.scheme or "http"
    default_port = 8080 if scheme in ("http", "https") else 1080
    port = parsed.port or default_port
    hostport = f"{scheme}://{parsed.hostname}:{port}"
    args = ["--proxy", hostport]
    if parsed.username is not None:
        user = unquote(parsed.username)
        pwd = unquote(parsed.password or "")
        args.extend(["--proxy-user", f"{user}:{pwd}"])
    return args


def _curl_common_args(*, probe: bool = False) -> list[str]:
    if probe:
        args = ["-sS", "--fail", "--max-time", "16", "--connect-timeout", "8"]
    else:
        args = ["-sS", "--fail", "--max-time", "30", "--connect-timeout", "12"]
    if sys.platform == "win32":
        args.append("--ssl-no-revoke")
    return args


def _proxy_host_port(proxy: str) -> tuple[str, int] | None:
    parsed = urlparse(_normalize_proxy_url(proxy))
    if not parsed.hostname:
        return None
    scheme = (parsed.scheme or "http").lower()
    port = parsed.port or (8080 if scheme in ("http", "https") else 1080)
    return parsed.hostname, port


def _tcp_probe(proxy: str, *, timeout: float = 5.0) -> None:
    hp = _proxy_host_port(proxy)
    if not hp:
        return
    host, port = hp
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return
    except OSError as exc:
        raise ElevenLabsApiError(
            f"Proxy {host}:{port} недоступен (TCP) — проверьте IP/порт в кабинете: {exc}"
        ) from exc


def _is_transient_network_error(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    msg = str(exc).lower()
    needles = (
        "winerror 121",
        "semaphore",
        "connection reset",
        "broken pipe",
        "timed out",
        "timeout",
        "server disconnected",
        "cannot connect",
    )
    return any(n in msg for n in needles)


def _prefer_socks5h(proxy: str | None) -> str | None:
    return _normalize_proxy_url(proxy) if proxy else None


def _normalize_proxy_url(proxy: str) -> str:
    p = proxy.strip()
    if p.lower().startswith("socks5://"):
        return "socks5h://" + p[9:]
    return p


_working_proxy_cache: str | None = None


def remember_working_proxy(url: str | None) -> None:
    global _working_proxy_cache
    if url:
        _working_proxy_cache = _normalize_proxy_url(url)


def effective_proxy_url(proxy_url: str | None = None) -> str | None:
    if _working_proxy_cache:
        return _working_proxy_cache
    return build_proxy_url(proxy_url=proxy_url) if proxy_url else build_proxy_url()


def _proxy_label(url: str) -> str:
    hp = _proxy_host_port(url)
    scheme = (urlparse(_normalize_proxy_url(url)).scheme or "?").upper()
    if hp:
        return f"{scheme} {hp[0]}:{hp[1]}"
    return url.split("@")[-1]


def proxy_profiles() -> list[dict[str, str]]:
    """Два явных профиля из .env — без автоперебора."""
    out: list[dict[str, str]] = []
    primary = (settings.elevenlabs_proxy_url or "").strip()
    alt = (settings.elevenlabs_proxy_alt_url or "").strip()
    if primary:
        out.append({"id": "primary", "label": _proxy_label(primary), "url": primary})
    if alt and alt != primary:
        out.append({"id": "alt", "label": _proxy_label(alt), "url": alt})
    return out


def _proxy_scheme_variants(proxy: str) -> list[str]:
    """Только схема из URL — не смешиваем HTTP и SOCKS на одном порту."""
    p = _normalize_proxy_url(proxy.strip())
    parsed = urlparse(p)
    if not parsed.hostname:
        return [p]
    scheme = (parsed.scheme or "http").lower()
    port = parsed.port or (8080 if scheme in ("http", "https") else 1080)
    user = parsed.username
    pwd = parsed.password or ""
    auth = ""
    if user is not None:
        auth = f"{quote(unquote(user), safe='')}:{quote(unquote(pwd or ''), safe='')}@"
    hostpart = f"{parsed.hostname}:{port}"
    if scheme.startswith("socks"):
        order = ("socks5h",)
    elif scheme in ("http", "https"):
        order = ("http",)
    else:
        order = (scheme,)
    out: list[str] = []
    seen: set[str] = set()
    for sch in order:
        url = f"{sch}://{auth}{hostpart}"
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _is_http_proxy(proxy: str | None) -> bool:
    if not proxy:
        return False
    return proxy.lower().startswith(("http://", "https://"))


def _find_curl() -> str | None:
    return shutil.which("curl") or shutil.which("curl.exe")


def _parse_clone_json(body: str) -> dict:
    if not body.strip():
        raise ElevenLabsApiError("clone: пустой ответ ElevenLabs")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ElevenLabsApiError(f"clone: не-JSON ответ: {body[:200]}") from exc


def _clone_upload_via_curl(
    *,
    url: str,
    api_key: str,
    name: str,
    description: str,
    sample_path: Path,
    proxy: str | None,
) -> dict:
    curl = _find_curl()
    if not curl:
        raise ElevenLabsApiError("curl не найден в PATH — установите curl или используйте HTTP proxy")

    cmd = [curl, *_curl_proxy_cmd_args(proxy), *_curl_common_args()]
    cmd.extend([
        "-H",
        f"xi-api-key: {api_key}",
        "-F",
        f"name={name}",
        "-F",
        f"files=@{sample_path.resolve().as_posix()};type=audio/mpeg",
        url,
    ])
    if description.strip():
        cmd.extend(["-F", f"description={description.strip()}"])

    logger.debug("clone curl cmd: {}", " ".join(cmd[:8] + ["..."]))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=130,
        )
    except subprocess.TimeoutExpired as exc:
        raise ElevenLabsApiError(
            "curl: таймаут 120s — SOCKS proxy не принимает upload файла"
        ) from exc
    body = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        snippet = (body or err)[:500]
        raise ElevenLabsApiError(f"curl upload через proxy failed: {snippet or proc.returncode}")
    return _parse_clone_json(body)


def _clone_upload_via_requests(
    *,
    url: str,
    api_key: str,
    name: str,
    description: str,
    sample_path: Path,
    proxy: str | None,
) -> dict:
    try:
        import requests
    except ImportError as exc:
        raise ElevenLabsApiError(
            "requests[socks] не установлен — в .venv: pip install \"requests[socks]\""
        ) from exc

    proxy_h = _normalize_proxy_url(proxy) if proxy else None
    proxies = {"http": proxy_h, "https": proxy_h} if proxy_h else None
    data: dict[str, str] = {"name": name}
    if description.strip():
        data["description"] = description.strip()

    try:
        with sample_path.open("rb") as fh:
            files = {"files": (sample_path.name, fh, "audio/mpeg")}
            resp = requests.post(
                url,
                headers={"xi-api-key": api_key},
                data=data,
                files=files,
                proxies=proxies,
                timeout=(45, 120),
            )
    except requests.RequestException as exc:
        raise ElevenLabsApiError(f"requests upload через proxy: {exc}") from exc

    if resp.status_code >= 400:
        raise ElevenLabsApiError(
            _parse_api_error_body(resp.text, resp.status_code),
            status=resp.status_code,
        )
    return _parse_clone_json(resp.text)


async def _clone_upload_via_aiohttp(
    *,
    url: str,
    headers: dict[str, str],
    safe_name: str,
    description: str,
    sample_bytes: bytes,
    safe_filename: str,
    content_type: str,
    proxy: str | None,
) -> dict:
    def _build_form() -> aiohttp.FormData:
        form = aiohttp.FormData()
        form.add_field("name", safe_name)
        if description.strip():
            form.add_field("description", description.strip())
        form.add_field(
            "files",
            sample_bytes,
            filename=safe_filename,
            content_type=content_type,
        )
        return form

    last_net: BaseException | None = None
    for attempt in range(3):
        try:
            async with _aiohttp_session(proxy) as session:
                async with session.post(
                    url,
                    headers=headers,
                    data=_build_form(),
                    timeout=_upload_timeout(),
                    **_request_proxy_kw(proxy),
                ) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        raise ElevenLabsApiError(
                            _parse_api_error_body(body, resp.status),
                            status=resp.status,
                        )
                    return _parse_clone_json(body)
        except ElevenLabsApiError:
            raise
        except aiohttp.ClientError as exc:
            last_net = exc
            if attempt < 2 and _is_transient_network_error(exc):
                logger.warning("clone aiohttp retry {}/3 after {}", attempt + 1, exc)
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            break

    err = last_net or RuntimeError("clone aiohttp upload failed")
    raise ElevenLabsApiError(f"aiohttp upload failed: {err}") from err


@asynccontextmanager
async def _aiohttp_session(proxy: str | None):
    if _is_socks_proxy(proxy):
        try:
            connector = _proxy_connector(proxy)
        except ImportError as exc:
            raise ElevenLabsApiError(
                "SOCKS proxy требует aiohttp-socks: pip install aiohttp-socks"
            ) from exc
        session = aiohttp.ClientSession(connector=connector, trust_env=False)
    else:
        session = aiohttp.ClientSession(trust_env=False)
    try:
        yield session
    finally:
        await session.close()


def _request_proxy_kw(proxy: str | None) -> dict:
    """SOCKS через connector; HTTP — через параметр proxy=."""
    if _is_socks_proxy(proxy):
        return {}
    return {"proxy": proxy}


def resolve_api_key(api_key: str | None = None) -> tuple[str, str]:
    """Вернёт (key, source) где source = ui | env."""
    req = (api_key or "").strip()
    env = (settings.elevenlabs_api_key or "").strip()
    if req:
        if len(req) < 10:
            raise ElevenLabsApiError("API key в форме слишком короткий — проверьте sk_…")
        return req, "ui"
    if env:
        if len(env) < 10:
            raise ElevenLabsApiError("ELEVENLABS_API_KEY в .env слишком короткий")
        return env, "env"
    raise ElevenLabsApiError(
        "ELEVENLABS_API_KEY не задан — вставьте sk_… в поле «API key» или в .env и перезапустите backend"
    )


def key_hint(key: str) -> str:
    k = (key or "").strip()
    return f"{k[:8]}…" if len(k) > 10 else "—"


def _resolve_key(api_key: str | None) -> str:
    key, _ = resolve_api_key(api_key)
    return key


def _headers(api_key: str | None = None) -> dict[str, str]:
    return {"xi-api-key": _resolve_key(api_key), "Accept": "application/json"}


def _parse_json_response(body: str, status: int) -> dict:
    if status >= 400:
        detail = body[:500]
        try:
            parsed = json.loads(body)
            detail = parsed.get("detail", parsed.get("message", detail))
        except json.JSONDecodeError:
            pass
        if status == 401:
            msg = f"Авторизация: неверный или просроченный API key (401). {detail}"
        elif status == 403:
            msg = f"Доступ запрещён (403) — возможно нужен proxy. {detail}"
        else:
            msg = f"ElevenLabs API {status}: {detail}"
        raise ElevenLabsApiError(msg, status=status)
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ElevenLabsApiError(f"ElevenLabs: не-JSON ответ: {body[:200]}") from exc


async def _request_json_aiohttp(
    method: str,
    url: str,
    *,
    api_key: str | None,
    proxy: str | None,
    **kwargs,
) -> dict:
    async with _aiohttp_session(proxy) as session:
        async with session.request(
            method,
            url,
            headers=_headers(api_key),
            timeout=_json_timeout(),
            **_request_proxy_kw(proxy),
            **kwargs,
        ) as resp:
            body = await resp.text()
            return _parse_json_response(body, resp.status)


def _request_json_via_curl(
    method: str,
    url: str,
    *,
    api_key: str | None,
    proxy: str | None,
    probe: bool = False,
    **kwargs,
) -> dict:
    curl = _find_curl()
    if not curl:
        raise ElevenLabsApiError("curl не найден в PATH")
    cmd = [
        curl,
        *_curl_proxy_cmd_args(proxy),
        *_curl_common_args(probe=probe),
        "-X",
        method.upper(),
        "-H",
        f"xi-api-key: {_resolve_key(api_key)}",
        "-H",
        "Accept: application/json",
        url,
    ]
    run_timeout = 20 if probe else 130
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=run_timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ElevenLabsApiError("curl: таймаут запроса к ElevenLabs API") from exc
    body = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        snippet = (body or err)[:500]
        raise ElevenLabsApiError(f"curl API через proxy failed: {snippet or proc.returncode}")
    return _parse_json_response(body, 200)


def _request_json_via_requests(
    method: str,
    url: str,
    *,
    api_key: str | None,
    proxy: str | None,
    probe: bool = False,
    **kwargs,
) -> dict:
    try:
        import requests
    except ImportError as exc:
        raise ElevenLabsApiError(
            "requests[socks] не установлен — в .venv: pip install \"requests[socks]\""
        ) from exc

    proxy_h = _normalize_proxy_url(proxy) if proxy else None
    proxies = {"http": proxy_h, "https": proxy_h} if proxy_h else None
    timeout = (8, 14) if probe else (45, 120)
    try:
        resp = requests.request(
            method.upper(),
            url,
            headers=_headers(api_key),
            proxies=proxies,
            timeout=timeout,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise ElevenLabsApiError(f"requests API через proxy: {exc}") from exc
    return _parse_json_response(resp.text, resp.status_code)


async def _request_json(
    method: str,
    path: str,
    *,
    api_key: str | None = None,
    proxy_url: str | None = None,
    **kwargs,
) -> dict:
    url = f"{_API_BASE}{path}"
    proxy = effective_proxy_url(proxy_url)
    errors: list[str] = []

    if proxy and _is_http_proxy(proxy):
        try:
            data = await asyncio.to_thread(
                _request_json_via_requests,
                method,
                url,
                api_key=api_key,
                proxy=proxy,
                probe=False,
                **kwargs,
            )
            remember_working_proxy(proxy)
            return data
        except ElevenLabsApiError as exc:
            if exc.status in (401, 403, 422):
                raise
            errors.append(f"requests: {exc}")

    try:
        return await _request_json_aiohttp(method, url, api_key=api_key, proxy=proxy, **kwargs)
    except aiohttp.ClientError as exc:
        errors.append(f"aiohttp: {exc}")
    except ElevenLabsApiError as exc:
        if exc.status in (401, 403, 422):
            raise
        errors.append(f"aiohttp: {exc}")

    if not proxy:
        tail = "; ".join(errors) or "network error"
        raise ElevenLabsApiError(f"Сеть/proxy: {tail}")

    for name, fn in (("curl", _request_json_via_curl), ("requests", _request_json_via_requests)):
        if name == "curl" and not _find_curl():
            continue
        try:
            data = await asyncio.to_thread(
                fn,
                method,
                url,
                api_key=api_key,
                proxy=proxy,
                **kwargs,
            )
            logger.info("elevenlabs {} ok via {} ({})", path, name, proxy.split("@")[-1] if "@" in proxy else proxy)
            remember_working_proxy(proxy)
            return data
        except ElevenLabsApiError as exc:
            if exc.status in (401, 403, 422):
                raise
            errors.append(f"{name}: {exc}")
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            errors.append(f"{name}: {exc}")

    tail = "; ".join(errors) or "network error"
    raise ElevenLabsApiError(f"Сеть/proxy: {tail}")


def _missing_user_read(exc: ElevenLabsApiError) -> bool:
    msg = str(exc).lower()
    return exc.status == 401 and ("user_read" in msg or "missing_permissions" in msg)


def _subscription_connect_fields(sub: dict | None) -> dict:
    s = sub or {}
    tier = str(s.get("tier") or "").strip() or None
    status = str(s.get("status") or "").strip() or None
    ivc = s.get("can_use_instant_voice_cloning")
    pvc = s.get("can_use_professional_voice_cloning")
    return {
        "subscription_tier": tier,
        "subscription_status": status,
        "can_use_instant_voice_cloning": ivc if isinstance(ivc, bool) else None,
        "can_use_professional_voice_cloning": pvc if isinstance(pvc, bool) else None,
        "voice_slots_used": s.get("voice_slots_used"),
        "voice_limit": s.get("voice_limit"),
    }


async def fetch_account_diag(*, api_key: str | None = None, proxy_url: str | None = None) -> dict:
    """Что ElevenLabs видит по этому API key (тариф, IVC, совпадение ключа)."""
    key, key_source = resolve_api_key(api_key)
    proxy = effective_proxy_url(proxy_url)
    out: dict = {
        "key_source": key_source,
        "key_hint": key_hint(key),
        "proxy": proxy.split("@")[-1] if proxy and "@" in proxy else proxy,
        "user_read_ok": False,
        "verdict": None,
        "website_ivc_test": "https://elevenlabs.io/app/voice-library",
        "website_subscription": "https://elevenlabs.io/app/subscription",
        "website_api_keys": "https://elevenlabs.io/app/settings/api-keys",
    }
    try:
        user = await asyncio.to_thread(
            _request_json_via_requests,
            "GET",
            f"{_API_BASE}/user",
            api_key=key,
            proxy=proxy,
            probe=True,
        )
        out["user_read_ok"] = True
        sub = user.get("subscription") if isinstance(user.get("subscription"), dict) else {}
        out.update(_subscription_connect_fields(sub))
        out["user_id"] = user.get("user_id")
        out["seat_type"] = user.get("seat_type")
        out["api_key_preview"] = user.get("xi_api_key_preview")
        out["character_count"] = (sub or {}).get("character_count")
        out["character_limit"] = (sub or {}).get("character_limit")

        tier = (sub or {}).get("tier") or "?"
        st = (sub or {}).get("status") or "?"
        ivc = (sub or {}).get("can_use_instant_voice_cloning")
        if ivc is True:
            out["verdict"] = (
                f"API key привязан к тарифу «{tier}» (status={st}) — Instant Voice Clone разрешён. "
                "Если клон в Lab падает — проверьте permissions ключа: create_instant_voice_clone."
            )
        else:
            out["verdict"] = (
                f"API key НЕ на Creator/Starter аккаунте: тариф «{tier}», status={st}, IVC={ivc}. "
                "На сайте может быть другой логин — создайте новый API key на том же аккаунте, "
                "где видите Creator, и обновите .env (или очистите поле API key в Lab)."
            )
    except ElevenLabsApiError as exc:
        if _missing_user_read(exc):
            out["note"] = (
                "Ключ без user_read — тариф не виден. Включите user_read в API key "
                "или откройте check-env после обновления ключа."
            )
            out["verdict"] = str(exc)
        else:
            out["verdict"] = str(exc)
            out["error"] = str(exc)
    return out


async def _assert_ivc_allowed(*, api_key: str, proxy: str | None) -> None:
    """Раннее предупреждение: /user показывает can_use_instant_voice_cloning."""
    try:
        user = await asyncio.to_thread(
            _request_json_via_requests,
            "GET",
            f"{_API_BASE}/user",
            api_key=api_key,
            proxy=proxy,
            probe=True,
        )
    except ElevenLabsApiError as exc:
        if _missing_user_read(exc):
            return
        return

    sub = user.get("subscription") if isinstance(user.get("subscription"), dict) else {}
    if sub.get("can_use_instant_voice_cloning") is True:
        return

    tier = sub.get("tier") or "?"
    st = sub.get("status") or "?"
    raise ElevenLabsApiError(
        f"По этому API key Instant Voice Cloning недоступен: тариф «{tier}», status={st}. "
        "Нужен Starter ($6/мес) или выше на том же аккаунте, что и ключ. "
        "Если на сайте уже Starter — создайте новый API key там же "
        "(Profile → API Keys → включите create_instant_voice_clone + voices_write)."
    )


async def _probe_voices_via_proxy(
    *,
    key: str,
    proxy: str,
    voices_url: str,
) -> dict:
    """curl + requests; для HTTP сначала requests (стабильнее на Windows)."""
    errors: list[str] = []
    runners: list[tuple[str, object]] = []
    if _is_http_proxy(proxy):
        runners.append(("requests", _request_json_via_requests))
        if _find_curl():
            runners.append(("curl", _request_json_via_curl))
    else:
        if _find_curl():
            runners.append(("curl", _request_json_via_curl))
        runners.append(("requests", _request_json_via_requests))

    for name, fn in runners:
        try:
            data = await asyncio.to_thread(
                fn,
                "GET",
                voices_url,
                api_key=key,
                proxy=proxy,
                probe=True,
            )
            remember_working_proxy(proxy)
            logger.info("elevenlabs connect ok via {} {}", name, proxy.split("@")[-1])
            return data
        except ElevenLabsApiError as exc:
            if exc.status in (401, 403):
                raise
            errors.append(f"{name}: {exc}")
    raise ElevenLabsApiError("; ".join(errors) or "proxy probe failed")


async def _connect_attempt(*, key: str, proxy_url: str | None) -> dict:
    """Быстрая проверка: curl/requests, без aiohttp и без direct-fallback."""
    voices_payload: dict | None = None
    used_proxy = proxy_url
    voices_url = f"{_API_BASE}/voices"

    if proxy_url:
        errors: list[str] = []
        for px in _proxy_scheme_variants(proxy_url):
            try:
                await asyncio.to_thread(_tcp_probe, px, timeout=5.0)
            except ElevenLabsApiError as exc:
                errors.append(str(exc))
                continue
            try:
                voices_payload = await _probe_voices_via_proxy(
                    key=key,
                    proxy=px,
                    voices_url=voices_url,
                )
                used_proxy = px
                break
            except ElevenLabsApiError as exc:
                if exc.status in (401, 403):
                    raise
                errors.append(str(exc))

        if voices_payload is None:
            host = _proxy_host_port(proxy_url)
            host_hint = f"{host[0]}:{host[1]}" if host else proxy_url.split("@")[-1]
            tail = "; ".join(errors[-2:]) if errors else "нет ответа"
            low = tail.lower()
            if "invalid_api_key" in low or "401" in low or "авторизация" in low:
                raise ElevenLabsApiError(
                    f"Неверный API key (401). {tail} "
                    "Создайте ключ на elevenlabs.io → Profile → API Keys (sk_…).",
                    status=401,
                )
            raise ElevenLabsApiError(
                f"Proxy {host_hint} недоступен — {tail}. "
                "Обновите ELEVENLABS_PROXY_URL в .env (URL из кабинета провайдера).",
            )
    else:
        voices_payload = await _request_json("GET", "/voices", api_key=key, proxy_url=None)
        used_proxy = None

    voice_count = len(voices_payload.get("voices") or [])

    sub: dict | None = None
    user_read_ok = True
    note: str | None = None
    try:
        user = await asyncio.to_thread(
            _request_json_via_requests,
            "GET",
            f"{_API_BASE}/user",
            api_key=key,
            proxy=used_proxy,
            probe=True,
        )
        sub = user.get("subscription") if isinstance(user.get("subscription"), dict) else {}
        logger.info(
            "elevenlabs /user tier={} status={} ivc={} key={}",
            (sub or {}).get("tier"),
            (sub or {}).get("status"),
            (sub or {}).get("can_use_instant_voice_cloning"),
            key_hint(key),
        )
    except ElevenLabsApiError as user_exc:
        if _missing_user_read(user_exc):
            user_read_ok = False
            note = "Ключ без user_read — IVC/тариф не видим; для клона включите user_read"
        elif user_exc.status in (401, 403):
            user_read_ok = False
            note = "Профиль (/user) недоступен — проверка по списку голосов прошла"
        else:
            user_read_ok = False
            note = "Профиль (/user) недоступен — для Lab достаточно списка голосов"

    return {
        "voice_count": voice_count,
        "subscription": sub or None,
        "character_count": (sub or {}).get("character_count"),
        "character_limit": (sub or {}).get("character_limit"),
        "user_read_ok": user_read_ok,
        "note": note,
        "proxy": used_proxy,
        **_subscription_connect_fields(sub),
    }


async def connect_by_ip(
    *,
    api_key: str | None = None,
    proxy_url: str | None = None,
    proxy_ip: str | None = None,
    proxy_port: int | None = None,
    proxy_user: str | None = None,
    proxy_password: str | None = None,
    proxy_scheme: str | None = "http",
) -> dict:
    """Проверка ключа через ElevenLabs API (SOCKS5/HTTP proxy)."""
    key, key_source = resolve_api_key(api_key)
    ui_proxy = build_proxy_url(
        proxy_url=proxy_url,
        proxy_ip=proxy_ip,
        proxy_port=proxy_port,
        proxy_user=proxy_user,
        proxy_password=proxy_password,
        proxy_scheme=proxy_scheme,
    )
    env_proxy = (settings.elevenlabs_proxy_url or "").strip() or None
    env_alt = (settings.elevenlabs_proxy_alt_url or "").strip() or None

    plans: list[tuple[str, str | None]] = []
    seen: set[str | None] = set()
    for mode, px in (
        ("ui_proxy", ui_proxy),
        ("env_proxy", env_proxy),
        ("env_alt", env_alt if env_alt and env_alt != env_proxy else None),
    ):
        if not px or px in seen:
            continue
        seen.add(px)
        plans.append((mode, px))
    if not plans:
        plans.append(("direct", None))

    last: ElevenLabsApiError | None = None
    for mode, px in plans:
        if mode == "ui_proxy" and not px:
            continue
        if mode == "env_proxy" and not px:
            continue
        try:
            probe = await _connect_attempt(key=key, proxy_url=px)
            working = probe.get("proxy") or px
            return {
                "ok": True,
                "proxy": working,
                "connection_mode": mode,
                "key_source": key_source,
                "key_hint": key_hint(key),
                **{k: v for k, v in probe.items() if k != "proxy"},
            }
        except ElevenLabsApiError as exc:
            last = exc
            if exc.status in (401, 403):
                hint = (
                    "Создайте API key на elevenlabs.io с правами Text to Speech, Voices "
                    "и create_instant_voice_clone (user_read желателен для проверки тарифа)"
                )
                raise ElevenLabsApiError(
                    f"{exc} · ключ: {key_source} ({key_hint(key)}). {hint}",
                    status=exc.status,
                ) from exc
            logger.warning("elevenlabs connect {} via {} failed: {}", mode, px or "direct", exc)

    hint = (
        "ElevenLabs недоступен напрямую из РФ — добавьте proxy в ELEVENLABS_PROXY_URL (.env)"
        if not ui_proxy and not env_proxy and not env_alt
        else "Proxy не отвечает — проверьте URL в .env (кабинет провайдера). "
        "Очистите Proxy URL в Lab, если дублируете .env"
    )
    tail = f" ({last})" if last else ""
    raise ElevenLabsApiError(f"connect failed{tail}. {hint}") from last


async def check_connection() -> dict:
    return await _request_json("GET", "/user")


async def list_voices(*, api_key: str | None = None, proxy_url: str | None = None) -> list[dict]:
    data = await _request_json("GET", "/voices", api_key=api_key, proxy_url=proxy_url)
    return list(data.get("voices") or [])


async def list_shared_voices(
    *,
    api_key: str | None = None,
    proxy_url: str | None = None,
    search: str | None = None,
    max_pages: int = 5,
    page_size: int = 100,
    gender: str | None = None,
    age: str | None = None,
    accent: str | None = None,
    language: str | None = None,
    locale: str | None = None,
    sort: str | None = None,
    category: str | None = None,
) -> tuple[list[dict], int | None]:
    """Публичная библиотека ElevenLabs — GET /v1/shared-voices (до 100 на страницу)."""
    from urllib.parse import urlencode

    page_size = min(max(int(page_size), 1), 100)
    max_pages = min(max(int(max_pages), 1), 20)
    out: list[dict] = []
    seen: set[str] = set()
    total_count: int | None = None

    def _opt(key: str, val: str | None) -> None:
        v = (val or "").strip()
        if v:
            params[key] = v

    for page in range(max_pages):
        params: dict[str, str | int] = {"page_size": page_size, "page": page}
        _opt("search", search)
        _opt("gender", gender)
        _opt("age", age)
        _opt("accent", accent)
        _opt("language", language)
        _opt("locale", locale)
        _opt("sort", sort)
        _opt("category", category)
        path = f"/shared-voices?{urlencode(params)}"
        data = await _request_json("GET", path, api_key=api_key, proxy_url=proxy_url)
        if isinstance(data.get("total_count"), int):
            total_count = int(data["total_count"])
        batch = data.get("voices") or []
        for v in batch:
            if not isinstance(v, dict):
                continue
            vid = str(v.get("voice_id") or "")
            if vid and vid not in seen:
                seen.add(vid)
                out.append(v)
        if not data.get("has_more"):
            break
    return out, total_count


def _library_filters_active(
    *,
    gender: str | None = None,
    age: str | None = None,
    accent: str | None = None,
    language: str | None = None,
    locale: str | None = None,
    sort: str | None = None,
    category: str | None = None,
) -> bool:
    return any(
        (x or "").strip()
        for x in (gender, age, accent, language, locale, sort, category)
    )


async def list_remote_voices(
    *,
    api_key: str | None = None,
    proxy_url: str | None = None,
    scope: str = "all",
    search: str | None = None,
    max_pages: int = 5,
    gender: str | None = None,
    age: str | None = None,
    accent: str | None = None,
    language: str | None = None,
    locale: str | None = None,
    sort: str | None = None,
    category: str | None = None,
) -> tuple[list[dict], dict]:
    """scope: account | library | all."""
    scope_n = (scope or "all").strip().lower()
    filters_on = _library_filters_active(
        gender=gender,
        age=age,
        accent=accent,
        language=language,
        locale=locale,
        sort=sort,
        category=category,
    )
    if filters_on and scope_n == "all":
        scope_n = "library"

    shared_kw = dict(
        api_key=api_key,
        proxy_url=proxy_url,
        search=search,
        max_pages=max_pages,
        gender=gender,
        age=age,
        accent=accent,
        language=language,
        locale=locale,
        sort=sort or ("trending" if filters_on else None),
        category=category,
    )
    meta: dict = {"scope": scope_n, "max_pages": max_pages, "filters_active": filters_on}

    if scope_n == "account":
        voices = await list_voices(api_key=api_key, proxy_url=proxy_url)
        meta["account_count"] = len(voices)
        return voices, meta

    if scope_n == "library":
        voices, total = await list_shared_voices(**shared_kw)
        meta["library_count"] = len(voices)
        meta["api_total_count"] = total
        return voices, meta

    account = await list_voices(api_key=api_key, proxy_url=proxy_url)
    shared, total = await list_shared_voices(**shared_kw)
    by_id: dict[str, dict] = {}
    for v in account:
        vid = str(v.get("voice_id") or "")
        if vid:
            by_id[vid] = v
    for v in shared:
        vid = str(v.get("voice_id") or "")
        if vid and vid not in by_id:
            by_id[vid] = v
    merged = list(by_id.values())
    meta["account_count"] = len(account)
    meta["library_count"] = len(shared)
    meta["total_count"] = len(merged)
    meta["api_total_count"] = total
    return merged, meta


def slim_remote_voice(v: dict) -> dict | None:
    vid = v.get("voice_id")
    if not vid:
        return None
    labels = v.get("labels") if isinstance(v.get("labels"), dict) else None
    gender = v.get("gender") or (labels or {}).get("gender")
    age = v.get("age") or (labels or {}).get("age")
    accent = v.get("accent") or (labels or {}).get("accent")
    language = v.get("language") or (labels or {}).get("language")
    use_case = v.get("use_case") or (labels or {}).get("use case")
    return {
        "voice_id": vid,
        "name": v.get("name"),
        "category": v.get("category"),
        "preview_url": v.get("preview_url"),
        "description": (v.get("description") or "")[:240] or None,
        "labels": labels,
        "gender": gender,
        "age": age,
        "accent": accent,
        "language": language,
        "use_case": use_case,
        "descriptive": v.get("descriptive"),
    }


def _guess_audio_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }.get(ext, "application/octet-stream")


def _parse_api_error_body(body: str, status: int) -> str:
    detail = body[:500]
    parsed: dict | None = None
    try:
        raw = json.loads(body)
        if isinstance(raw, dict):
            parsed = raw
            inner = raw.get("detail")
            if isinstance(inner, dict):
                parsed = inner
            elif isinstance(inner, str):
                detail = inner
    except json.JSONDecodeError:
        pass

    if isinstance(parsed, dict):
        code = str(parsed.get("code") or parsed.get("status") or "")
        msg = str(parsed.get("message") or parsed.get("detail") or detail)
        low = f"{code} {msg}".lower()
        if any(
            k in low
            for k in ("paid_plan", "instant_voice", "payment_required", "can_not_use_instant")
        ):
            return (
                "ElevenLabs отклонил Instant Voice Clone (IVC). "
                "Нужен Starter ($6/мес) или выше на том же аккаунте, что и API key, "
                "и в ключе permissions: voices_write + create_instant_voice_clone. "
                f"{msg}"
            )
        detail = msg

    if status == 401:
        return f"Авторизация: неверный API key или нет прав на клон (401). {detail}"
    if status == 403:
        return f"Доступ запрещён (403) — проверьте тариф/права voice cloning. {detail}"
    if status == 422:
        return f"Некорректный образец для клона (422). {detail}"
    if status == 400 and "instant voice cloning" in detail.lower():
        return detail
    return f"ElevenLabs clone {status}: {detail}"


def _clone_upload_fatal(exc: ElevenLabsApiError) -> bool:
    """Ошибки API/тарифа — не маскируем под «proxy не принял upload»."""
    if exc.status in (401, 403, 422):
        return True
    msg = str(exc).lower()
    return any(
        k in msg
        for k in (
            "paid_plan",
            "instant voice cloning",
            "payment_required",
            "can_not_use_instant",
            "тариф elevenlabs",
        )
    )


async def prepare_clone_sample(sample_bytes: bytes, filename: str) -> tuple[bytes, str, float]:
    """Приводит любой upload/WAV к mp3 и проверяет длительность."""
    if len(sample_bytes) < 100:
        raise ElevenLabsApiError("Образец пустой или повреждён")

    work = lab_dir() / f"clone_prep_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or "sample.bin").suffix or ".wav"
    src = work / f"input{suffix}"
    out = work / "sample.mp3"
    src.write_bytes(sample_bytes)
    try:
        await _run_ffmpeg([
            "ffmpeg", "-y", "-i", str(src),
            "-ac", "1", "-ar", "16000",
            "-b:a", "32k",
            "-t", "15",
            "-c:a", "libmp3lame",
            str(out),
        ])
    except RuntimeError as exc:
        raise ElevenLabsApiError(f"ffmpeg не смог прочитать образец: {exc}") from exc

    if not out.is_file() or out.stat().st_size < 200:
        raise ElevenLabsApiError("После конвертации образец пустой — выделите более длинный фрагмент")

    try:
        dur = await probe_duration(out)
    except RuntimeError as exc:
        raise ElevenLabsApiError(f"Не удалось определить длительность образца: {exc}") from exc
    if dur < 1.0:
        raise ElevenLabsApiError(
            f"Образец слишком короткий для клона: {dur:.2f}s (нужно ≥ 1s, лучше 10–30s речи)"
        )
    try:
        return out.read_bytes(), "sample.mp3", dur
    finally:
        src.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


async def clone_voice_from_sample(
    *,
    name: str,
    sample_bytes: bytes,
    sample_filename: str,
    api_key: str | None = None,
    proxy_url: str | None = None,
    description: str = "",
) -> dict:
    """Instant Voice Clone → voice_id."""
    proxy = build_upload_proxy_url(proxy_url=proxy_url)
    safe_name = (name or "Lab Voice").strip() or "Lab Voice"
    safe_filename = sample_filename or "sample.mp3"
    content_type = _guess_audio_content_type(safe_filename)
    url = f"{_API_BASE}/voices/add"
    api_key_resolved, key_source = resolve_api_key(api_key)
    headers = {"xi-api-key": api_key_resolved}
    logger.info(
        "clone start name={} key={} ({}) proxy={}",
        safe_name,
        key_hint(api_key_resolved),
        key_source,
        (proxy or "direct").split("@")[-1],
    )

    await _assert_ivc_allowed(api_key=api_key_resolved, proxy=proxy)

    upload_path = lab_dir() / f"clone_upload_{uuid.uuid4().hex[:8]}.mp3"
    upload_path.write_bytes(sample_bytes)
    data: dict | None = None
    upload_errors: list[str] = []

    try:
        if proxy:
            uploaders: list[tuple[str, object]] = []
            if _is_http_proxy(proxy):
                # HTTP: requests стабильнее для multipart; aiohttp часто таймаутит
                uploaders.append(("requests", _clone_upload_via_requests))
                if _find_curl():
                    uploaders.append(("curl", _clone_upload_via_curl))
            else:
                if _find_curl():
                    uploaders.append(("curl", _clone_upload_via_curl))
                uploaders.append(("requests", _clone_upload_via_requests))

            for uploader_name, uploader in uploaders:
                if data is not None:
                    break
                try:
                    data = await asyncio.to_thread(
                        uploader,
                        url=url,
                        api_key=api_key_resolved,
                        name=safe_name,
                        description=description,
                        sample_path=upload_path,
                        proxy=proxy,
                    )
                    logger.info(
                        "clone upload ok via {} ({} bytes)",
                        uploader_name,
                        upload_path.stat().st_size,
                    )
                except ElevenLabsApiError as exc:
                    if _clone_upload_fatal(exc):
                        raise
                    upload_errors.append(f"{uploader_name}: {exc}")
                except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
                    upload_errors.append(f"{uploader_name}: {exc}")
                except Exception as exc:
                    upload_errors.append(f"{uploader_name}: {exc}")
                    logger.exception("clone {} unexpected error", uploader_name)
        else:
            try:
                data = await _clone_upload_via_aiohttp(
                    url=url,
                    headers=headers,
                    safe_name=safe_name,
                    description=description,
                    sample_bytes=sample_bytes,
                    safe_filename=safe_filename,
                    content_type=content_type,
                    proxy=None,
                )
            except ElevenLabsApiError as exc:
                upload_errors.append(f"aiohttp: {exc}")
    finally:
        upload_path.unlink(missing_ok=True)

    if data is None:
        tail = " | ".join(upload_errors[-3:]) if upload_errors else "upload failed"
        if any("instant voice cloning" in e.lower() or "paid_plan" in e.lower() for e in upload_errors):
            raise ElevenLabsApiError(tail)
        kind = "HTTP" if _is_http_proxy(proxy) else "SOCKS"
        hint = (
            f"{kind} proxy не доставил файл на ElevenLabs (таймаут). "
            "Проверьте proxy и длину образца 10–20s."
        )
        raise ElevenLabsApiError(f"{hint} ({tail})")

    voice_id = data.get("voice_id") or data.get("id")
    if not voice_id:
        raise ElevenLabsApiError(f"clone: нет voice_id в ответе: {data}")
    return {"voice_id": str(voice_id), "name": safe_name}


def replace_word_in_text(text: str, old_word: str, new_word: str) -> tuple[str, int]:
    if not old_word:
        raise ValueError("old_word пуст")
    pattern = re.compile(re.escape(old_word), re.IGNORECASE)
    new_text, n = pattern.subn(new_word, text, count=1)
    return new_text, n


async def splice_audio_patch(
    *,
    source_path: Path,
    patch_path: Path,
    out_path: Path,
    start_s: float,
    end_s: float,
) -> Path:
    if start_s < 0 or end_s <= start_s:
        raise ValueError("некорректный интервал start_s/end_s")
    total = await probe_duration(source_path)
    if end_s > total + 0.05:
        raise ValueError(f"end_s {end_s:.2f}s > длительность {total:.2f}s")

    work = out_path.parent / f"_patch_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    head = work / "head.mp3"
    tail = work / "tail.mp3"

    if start_s > 0.01:
        await _run_ffmpeg([
            "ffmpeg", "-y", "-i", str(source_path), "-t", f"{start_s:.3f}",
            "-c:a", "libmp3lame", "-q:a", "2", str(head),
        ])
    else:
        head.write_bytes(b"")

    tail_dur = max(0.0, total - end_s)
    if tail_dur > 0.01:
        await _run_ffmpeg([
            "ffmpeg", "-y", "-ss", f"{end_s:.3f}", "-i", str(source_path),
            "-t", f"{tail_dur:.3f}", "-c:a", "libmp3lame", "-q:a", "2", str(tail),
        ])
    else:
        tail.write_bytes(b"")

    parts: list[Path] = []
    if head.is_file() and head.stat().st_size > 100:
        parts.append(head)
    parts.append(patch_path)
    if tail.is_file() and tail.stat().st_size > 100:
        parts.append(tail)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    await concat_mp3_files(parts, out_path)
    return out_path


async def _resolve_voice_for_redub(
    *,
    sample_bytes: bytes,
    sample_filename: str,
    voice_name: str,
    api_key: str | None,
    proxy_url: str | None,
    voice_id: str | None,
) -> tuple[str, dict]:
    if not voice_id:
        cloned = await clone_voice_from_sample(
            name=voice_name,
            sample_bytes=sample_bytes,
            sample_filename=sample_filename,
            api_key=api_key,
            proxy_url=proxy_url,
        )
        return cloned["voice_id"], cloned
    return voice_id, {"voice_id": voice_id, "name": voice_name, "skipped_clone": True}


async def preview_redub_word(
    *,
    sample_bytes: bytes,
    sample_filename: str,
    voice_name: str,
    fragment_text: str,
    old_word: str,
    new_word: str,
    api_key: str | None = None,
    proxy_url: str | None = None,
    voice_id: str | None = None,
) -> dict:
    """Клон (если нужен) + TTS патча. Без склейки — для предпросмотра."""
    run_id = uuid.uuid4().hex[:8]
    voice_id, cloned = await _resolve_voice_for_redub(
        sample_bytes=sample_bytes,
        sample_filename=sample_filename,
        voice_name=voice_name,
        api_key=api_key,
        proxy_url=proxy_url,
        voice_id=voice_id,
    )

    new_text, replacements = replace_word_in_text(fragment_text, old_word, new_word)
    if replacements == 0:
        raise ValueError(f"слово «{old_word}» не найдено во fragment_text")

    patch_path = lab_dir() / f"patch_{run_id}.mp3"
    await text_to_speech_file(
        text=new_text,
        out_path=patch_path,
        voice_id=voice_id,
        api_key=api_key,
        proxy_url=proxy_url,
    )
    patch_dur = await probe_duration(patch_path)

    return {
        "ok": True,
        "run_id": run_id,
        "voice_id": voice_id,
        "clone": cloned,
        "old_word": old_word,
        "new_word": new_word,
        "fragment_text": fragment_text,
        "spoken_text": new_text,
        "patch_mp3": str(patch_path.resolve()),
        "patch_filename": patch_path.name,
        "patch_duration_s": patch_dur,
    }


async def apply_redub_splice(
    *,
    source_path: Path,
    patch_path: Path,
    start_s: float,
    end_s: float,
    run_id: str | None = None,
) -> dict:
    """Вставляет одобренный патч в исходную дорожку."""
    if not patch_path.is_file():
        raise ValueError(f"patch не найден: {patch_path.name}")
    rid = run_id or uuid.uuid4().hex[:8]
    out_path = lab_dir() / f"redub_{rid}.mp3"
    await splice_audio_patch(
        source_path=source_path,
        patch_path=patch_path,
        out_path=out_path,
        start_s=start_s,
        end_s=end_s,
    )
    final_dur = await probe_duration(out_path)
    patch_dur = await probe_duration(patch_path)
    return {
        "ok": True,
        "run_id": rid,
        "interval_s": [start_s, end_s],
        "patch_mp3": str(patch_path.resolve()),
        "patch_filename": patch_path.name,
        "output_mp3": str(out_path.resolve()),
        "output_filename": out_path.name,
        "patch_duration_s": patch_dur,
        "output_duration_s": final_dur,
    }


async def clone_and_redub_word(
    *,
    sample_bytes: bytes,
    sample_filename: str,
    voice_name: str,
    source_path: Path,
    start_s: float,
    end_s: float,
    fragment_text: str,
    old_word: str,
    new_word: str,
    api_key: str | None = None,
    proxy_url: str | None = None,
    voice_id: str | None = None,
) -> dict:
    """Клон голоса (если нет voice_id) → TTS фрагмента с заменой слова → вставка в mp3."""
    preview = await preview_redub_word(
        sample_bytes=sample_bytes,
        sample_filename=sample_filename,
        voice_name=voice_name,
        fragment_text=fragment_text,
        old_word=old_word,
        new_word=new_word,
        api_key=api_key,
        proxy_url=proxy_url,
        voice_id=voice_id,
    )
    patch_path = Path(preview["patch_mp3"])
    merged = await apply_redub_splice(
        source_path=source_path,
        patch_path=patch_path,
        start_s=start_s,
        end_s=end_s,
        run_id=preview["run_id"],
    )
    return {**preview, **merged}


async def text_to_speech_file(
    *,
    text: str,
    out_path: Path,
    voice_id: str,
    model_id: str | None = None,
    api_key: str | None = None,
    proxy_url: str | None = None,
) -> Path:
    model = (model_id or settings.elevenlabs_api_model or "eleven_multilingual_v2").strip()
    url = f"{_API_BASE}/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": model,
    }
    proxy = effective_proxy_url(proxy_url)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if proxy and _is_http_proxy(proxy):
        await asyncio.to_thread(
            _tts_via_requests,
            url=url,
            payload=payload,
            api_key=api_key,
            proxy=proxy,
            out_path=out_path,
        )
        return out_path

    async with _aiohttp_session(proxy) as session:
        async with session.post(
            url,
            headers={**_headers(api_key), "Content-Type": "application/json", "Accept": "audio/mpeg"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=600),
            **_request_proxy_kw(proxy),
        ) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:500]
                raise ElevenLabsApiError(
                    f"TTS failed {resp.status}: {body}",
                    status=resp.status,
                )
            data = await resp.read()
            if len(data) < 1000:
                raise ElevenLabsApiError("TTS: слишком короткий ответ (проверьте ключ/voice_id)")
            out_path.write_bytes(data)
    return out_path


def _tts_via_requests(
    *,
    url: str,
    payload: dict,
    api_key: str | None,
    proxy: str | None,
    out_path: Path,
) -> None:
    try:
        import requests
    except ImportError as exc:
        raise ElevenLabsApiError(
            "requests[socks] не установлен — в .venv: pip install \"requests[socks]\""
        ) from exc

    proxy_h = _normalize_proxy_url(proxy) if proxy else None
    proxies = {"http": proxy_h, "https": proxy_h} if proxy_h else None
    try:
        resp = requests.post(
            url,
            headers={**_headers(api_key), "Content-Type": "application/json", "Accept": "audio/mpeg"},
            json=payload,
            proxies=proxies,
            timeout=(10, 180),
        )
    except requests.RequestException as exc:
        raise ElevenLabsApiError(f"TTS requests через proxy: {exc}") from exc
    if resp.status_code >= 400:
        raise ElevenLabsApiError(
            f"TTS failed {resp.status_code}: {resp.text[:500]}",
            status=resp.status_code,
        )
    if len(resp.content) < 1000:
        raise ElevenLabsApiError("TTS: слишком короткий ответ (проверьте ключ/voice_id)")
    out_path.write_bytes(resp.content)


async def synthesize_full_voice_api(
    *,
    project: Project,
    audio_dir: Path,
) -> Path:
    """Один voice_full.mp3 через API."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    delete_frame_audio_files(audio_dir)

    full_text = resolve_full_voiceover_text(project)
    if len(full_text) < 50:
        raise RuntimeError(
            "нет voiceover.txt / script_text — сначала шаг «Закадровый текст»"
        )

    voice_id = resolve_elevenlabs_voice_id(project)
    full_path = audio_dir / f"voice_full_{uuid.uuid4().hex[:8]}.mp3"
    logger.info(
        "[#{}] elevenlabs_api: full voice ({} симв.) voice_id={} → {}",
        project.id,
        len(full_text),
        voice_id,
        full_path.name,
    )
    await text_to_speech_file(
        text=full_text,
        out_path=full_path,
        voice_id=voice_id,
    )
    return full_path
