"""B10: автоматические снимки баланса провайдеров для ``spend_reconcile``.

Сеть здесь НЕ ходит ни при импорте, ни при регистрации: адаптер это функция, которая обращается к
провайдеру только когда её вызывают (до и после платного шага). Сбой запроса баланса никогда не роняет и
не блокирует шаг: он превращается в «нет снимка» (``spend_reconcile.take_snapshot`` ловит любое исключение).

Адаптеры:
* ``kie``: ``kie_http.get_credits()`` (кредиты) * ``kie_catalog.CREDIT_USD`` (1 кредит = $0,005, замер);
* ``outsee``: ``outsee_http.fetch_balance()``. Единица баланса в ответе не подтверждена живьём, поэтому
  в USD он принимается только при явной пометке (``balance_usd`` / ``usd`` или ``currency = USD``); иначе
  честный отказ «единица баланса не подтверждена» (снимка нет);
* ``elevenlabs``: ``GET /v1/user/subscription``, остаток = ``character_limit - character_count`` символов.
  Пересчёт в USD по ``ELEVENLABS_USD_PER_CHAR`` берётся только если цена откалибрована (статус «замер»,
  не менее 3 проб). Сейчас это «оценка», поэтому адаптер возвращает символы в тексте причины и снимка USD нет;
* ``vibecode``: баланса по API нет (``gpt_api._credits_hint`` лишь подсказка в тексте ошибки), адаптер не
  регистрируется: только журнал приложения.

Автосверка на границах платных шагов: ``open_auto_window`` / ``close_auto_window`` (вызываются из
``orchestrator.pipeline.advance_project``). Включение: ``SPEND_RECONCILE_AUTO`` (пусто = авто: только при
наличии ключа провайдера и не под pytest; 1/0 принудительно).
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

SNAPSHOT_TIMEOUT_S = 20.0          # на один снимок
FINISH_TIMEOUT_S = 40.0            # на снимок после шага вместе с записью результата
ELEVEN_SUBSCRIPTION_URL = "https://api.elevenlabs.io/v1/user/subscription"
RELIABLE_MIN_PROBES = 3


class BalanceUnavailable(Exception):
    """Баланс получен или не получен, но в USD его выразить нельзя (причина в тексте)."""


# --------------------------------------------------------------------------- адаптеры


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
    """Цена символа, если она откалибрована замерами; иначе None (пересчёт ненадёжен)."""
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
# Провайдеры без баланса по API: адаптер не регистрируется, контроль только по журналу приложения.
JOURNAL_ONLY = ("vibecode",)


def register_default_adapters() -> list[str]:
    """Только регистрация колбэков, без сети. Повторный вызов безопасен."""
    from app.services import spend_reconcile
    for name, fn in ADAPTERS.items():
        spend_reconcile.register_balance_adapter(name, fn)
    return sorted(ADAPTERS)


# --------------------------------------------------------------------------- автосверка


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
    except Exception:  # noqa: BLE001
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
    pre_mark: int | None = None   # W70/K11: max id of the ledger right after the "before" snapshot
    result: dict[str, Any] = field(default_factory=dict)


_active: dict[str, list[AutoWindow]] = {}


async def _snap(provider: str):
    from app.services import spend_reconcile as sr
    try:
        return await asyncio.wait_for(sr.take_snapshot(provider), SNAPSHOT_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - в т.ч. таймаут
        return sr.Snapshot(sr._norm(provider), None, sr.datetime.utcnow(), "error",
                           f"{type(exc).__name__}: {exc}"[:200])


async def open_auto_window(status_value: str, project_id: int) -> AutoWindow | None:
    """Снимок до платного шага. Никогда не бросает исключение; None = сверка не включена или сорвалась."""
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
        if others:  # шаги одного провайдера идут параллельно: баланс общий, окна не разделить
            win.overlapped = True
            for o in others:
                o.overlapped = True
        others.append(win)
        return win
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto reconcile: снимок до шага не сделан ({}): шаг продолжается", exc)
        return None


async def close_auto_window(win: AutoWindow | None) -> dict[str, Any] | None:
    """Снимок после шага, сверка и запись. Никогда не бросает исключение и не отменяет шаг."""
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
    except Exception as exc:  # noqa: BLE001
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
    # rows written between the window mark and the "before" snapshot, or after the point just before the "after"
    # snapshot, may or may not be inside the balance delta: they only make a gap inconclusive (K11)
    ambiguous = [(win.start.ledger_max_id, win.pre_mark if win.pre_mark is not None else win.start.ledger_max_id),
                 (end_mark, None)]
    async with spend_guard._sessions()() as session:
        sums = await sr.window_sums(session, win.start, provider=win.provider, project_id=win.project_id,
                                    ambiguous=ambiguous)
        result = sr.compare(provider=win.provider, before=win.before, after=after, **sums)
        if result["status"] == sr.STATUS_NO_SNAPSHOT:
            # нет снимка: только запись в журнал, без инцидента и без блокировки
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
