from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

SNAPSHOT_TIMEOUT_S = 20.0
FINISH_TIMEOUT_S = 40.0
ELEVEN_SUBSCRIPTION_URL = "https://api.elevenlabs.io/v1/user/subscription"
RELIABLE_MIN_PROBES = 3


class BalanceUnavailable(Exception):
    pass




async def kie_balance_usd() -> float:
    from app.bots import kie_http
    from app.services import kie_catalog
    credits = await kie_http.get_credits()
    if credits is None:
        raise BalanceUnavailable("Kie не вернул кредиты (нет ключа или ошибка запроса)")
    return float(credits) * float(kie_catalog.CREDIT_USD)


def _outsee_usd(payload: dict[str, Any]) -> float:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for key in ("balance_usd", "usd", "usd_balance"):
        if data.get(key) is not None:
            return float(data[key])
    cur = str(data.get("currency") or "").strip().upper()
    for key in ("balance", "amount", "credits"):
        if data.get(key) is not None:
            if cur == "USD":
                return float(data[key])
            raise BalanceUnavailable(
                f"единица баланса Outsee не подтверждена (поле {key}, валюта '{cur or 'не указана'}'): "
                "в USD не пересчитывается")
    raise BalanceUnavailable(f"в ответе баланса Outsee нет числа (поля: {sorted(map(str, data))[:8]})")


async def outsee_balance_usd() -> float:
    from app.bots import outsee_http
    return _outsee_usd(await outsee_http.fetch_balance())


def eleven_usd_per_char() -> float | None:
    from app.services import media_spend, price_calibration
    cal = price_calibration.by_name().get("ELEVENLABS_USD_PER_CHAR")
    if cal is None or cal.status != "measured" or cal.probes < RELIABLE_MIN_PROBES:
        return None
    return float(media_spend.ELEVENLABS_USD_PER_CHAR)


async def eleven_remaining_chars() -> int:
    import httpx
    from app.services import elevenlabs_api
    key = elevenlabs_api._resolve_api_key()
    proxy = elevenlabs_api._resolve_proxy_url()
    kwargs: dict[str, Any] = {"timeout": 20.0}
    if proxy:
        kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**kwargs) as client:
        r = await client.get(ELEVEN_SUBSCRIPTION_URL, headers={"xi-api-key": key})
    if r.status_code >= 400:
        raise BalanceUnavailable(f"ElevenLabs subscription: HTTP {r.status_code}")
    body = r.json()
    return int(body["character_limit"]) - int(body["character_count"])


async def elevenlabs_balance_usd() -> float:
    chars = await eleven_remaining_chars()
    rate = eleven_usd_per_char()
    if rate is None:
        raise BalanceUnavailable(
            f"нет USD-снимка: остаток {chars} символов, цена символа не откалибрована "
            "(ELEVENLABS_USD_PER_CHAR - оценка), в USD не пересчитывается")
    return chars * rate


ADAPTERS = {"kie": kie_balance_usd, "outsee": outsee_balance_usd, "elevenlabs": elevenlabs_balance_usd}
JOURNAL_ONLY = ("vibecode",)


def register_default_adapters() -> list[str]:
    from app.services import spend_reconcile
    for name, fn in ADAPTERS.items():
        spend_reconcile.register_balance_adapter(name, fn)
    return sorted(ADAPTERS)




_STATUS_PROVIDER = {
    "generating_hero": "image", "generating_items": "image", "generating_images": "image",
    "generating_videos": "video", "generating_music": "kie",
    "generating_sfx": "elevenlabs", "generating_audio": "elevenlabs",
}


def provider_for_status(status_value: str) -> str | None:
    kind = _STATUS_PROVIDER.get(status_value)
    if kind is None:
        return None
    from app.settings import settings
    if kind == "image":
        raw = str(getattr(settings, "image_provider", "") or "").strip().lower()
    elif kind == "video":
        raw = str(getattr(settings, "video_provider", "") or "").strip().lower()
    else:
        raw = kind
    raw = "kie" if raw in {"kie", "kie.ai"} else raw
    return raw if raw in ADAPTERS else None


def _key_configured(provider: str) -> bool:
    try:
        if provider == "kie":
            from app.bots.kie_http import kie_configured
            return bool(kie_configured())
        if provider == "outsee":
            from app.bots.outsee_http import outsee_api_configured
            return bool(outsee_api_configured())
        if provider == "elevenlabs":
            from app.services.elevenlabs_api import elevenlabs_api_configured
            return bool(elevenlabs_api_configured())
    except Exception:
        return False
    return False


def auto_enabled(provider: str) -> bool:
    from app.settings import settings
    flag = getattr(settings, "spend_reconcile_auto", None)
    if flag is not None:
        return bool(flag)
    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return _key_configured(provider)


@dataclass
class AutoWindow:
    provider: str
    project_id: int
    start: Any
    before: Any
    overlapped: bool = False
    pre_mark: int | None = None
    result: dict[str, Any] = field(default_factory=dict)


_active: dict[str, list[AutoWindow]] = {}


async def _snap(provider: str):
    from app.services import spend_reconcile as sr
    try:
        return await asyncio.wait_for(sr.take_snapshot(provider), SNAPSHOT_TIMEOUT_S)
    except Exception as exc:
        return sr.Snapshot(sr._norm(provider), None, sr.datetime.utcnow(), "error",
                           f"{type(exc).__name__}: {exc}"[:200])


async def open_auto_window(status_value: str, project_id: int) -> AutoWindow | None:
    try:
        provider = provider_for_status(status_value)
        if provider is None or not auto_enabled(provider):
            return None
        from app.services import spend_guard, spend_reconcile as sr
        register_default_adapters()
        async with spend_guard._sessions()() as session:
            start = await asyncio.wait_for(sr.mark(session), SNAPSHOT_TIMEOUT_S)
        before = await _snap(provider)
        async with spend_guard._sessions()() as session:
            pre_mark = await sr._ledger_max_id(session)
        win = AutoWindow(provider, int(project_id), start, before, pre_mark=pre_mark)
        others = _active.setdefault(provider, [])
        if others:
            win.overlapped = True
            for o in others:
                o.overlapped = True
        others.append(win)
        return win
    except Exception as exc:
        logger.warning("auto reconcile: снимок до шага не сделан ({}): шаг продолжается", exc)
        return None


async def close_auto_window(win: AutoWindow | None) -> dict[str, Any] | None:
    if win is None:
        return None
    exc_now = sys.exc_info()[1]
    try:
        lst = _active.get(win.provider, [])
        if win in lst:
            lst.remove(win)
        if isinstance(exc_now, asyncio.CancelledError):
            return None
        return await asyncio.wait_for(_finish(win), FINISH_TIMEOUT_S)
    except Exception as exc:
        logger.warning("auto reconcile {}: сверка после шага не записана ({})", win.provider, exc)
        return None


async def _finish(win: AutoWindow) -> dict[str, Any] | None:
    from app.services import spend_guard, spend_reconcile as sr
    if win.overlapped:
        logger.info("auto reconcile {}: окна шагов пересеклись, сверка пропущена (баланс общий)", win.provider)
        return None
    async with spend_guard._sessions()() as session:
        end_mark = await sr._ledger_max_id(session)
    after = await _snap(win.provider)
    ambiguous = [(win.start.ledger_max_id, win.pre_mark if win.pre_mark is not None else win.start.ledger_max_id),
                 (end_mark, None)]
    async with spend_guard._sessions()() as session:
        sums = await sr.window_sums(session, win.start, provider=win.provider, project_id=win.project_id,
                                    ambiguous=ambiguous)
        result = sr.compare(provider=win.provider, before=win.before, after=after, **sums)
        if result["status"] == sr.STATUS_NO_SNAPSHOT:
            await spend_guard.record_event(session, project_id=win.project_id, provider=win.provider,
                                           source=sr.SOURCE_GAP, note=sr._note(result))
            logger.warning("auto reconcile {}: {}", win.provider, sr.message_ru(result))
        else:
            key = f"{win.start.ledger_max_id}-{win.start.tracker_max_id}"
            result["incident_id"] = await sr.record_result(session, result, project_id=win.project_id,
                                                           window_key=key)
            logger.info("auto reconcile: {}", sr.message_ru(result))
        await session.commit()
    win.result = result
    return result
