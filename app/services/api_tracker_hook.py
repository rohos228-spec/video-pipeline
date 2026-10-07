"""Хук для автоматической записи активности API-ключей в API Tracker.

Работает через SQLite WAL в фоновом потоке:
- 0 мс задержки для основного пайплайна
- Сбой записи не влияет на вызов, но НЕ молчит: лог, счётчик, spill-файл, пробел в журнале трат
- Автоматический расчёт стоимости (оценка; факт только если провайдер прислал сумму: metadata.cost_kind)
- Облако по умолчанию выключено (API_TRACKER_CLOUD_ENABLED), имена ОС и устройства в облако хэшем
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.tracker_schema import ensure_tracker_schema

# Путь к локальной базе API Tracker (API_TRACKER_DB переопределяет: песочницы и тесты не пишут в боевую базу)
_TRACKER_DB = (Path(os.environ["API_TRACKER_DB"]) if os.environ.get("API_TRACKER_DB", "").strip()
               else Path(__file__).resolve().parent.parent.parent / "api-tracker" / "tracker.db")

# Быстрый расчет тарифов
_RATES: dict[str, tuple[float, float]] = {
    # model: (input_usd_per_m, output_usd_per_m)
    "gemini-3.7-flash": (0.147622, 0.738108),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.295243, 1.476216),
    "gemini-3-flash": (0.098414, 0.590486),
    "gemini-3.1-pro": (0.393658, 2.361946),
    "gpt-5.6-sol": (1.20, 4.80),
    "gpt-5.6-terra": (1.50, 6.00),
    "gpt-5.6-luna": (2.00, 8.00),
    "deepseek-v4-flash": (0.291896, 0.875687),
    "deepseek-v4-pro": (0.55, 2.19),
}


# Calibrated against the real Kie credit balance (GET /api/v1/chat/credit, 1 credit = $0.005,
# see scripts/calibrate_kie_cost.py and docs/KIE_COST_CALIBRATION.md, measured 2026-10-01 on 3 probe
# shapes, reasoning tokens included in completion): cached input is effectively free, uncached
# input ~$1.6/M, output ~$8.4/M. Max absolute error on the probes: ~$0.0003 per call.
# (usd per M tokens: uncached input, output, cached input)
_MEASURED_RATES: dict[str, tuple[float, float, float]] = {
    "gpt-5.6-sol": (1.6, 8.4, 0.0),
}


# Where Vibecode text prices come from, and how far they can be trusted.
# NOT calibrated: unlike _MEASURED_RATES (Kie), nobody compared these figures with the
# vibecode.moe balance. The snapshot pricing is the raw per-model list price; the app's own
# catalog/UI shows it multiplied by vibecode_catalog.PRICE_MARKUP (x3). Which of the two
# vibecode.moe really charges the key is UNKNOWN, so the raw snapshot price is the base
# (cheaper, i.e. possibly an UNDER-estimate by up to the markup) and every row says so.
# The snapshot file has no date, so its freshness is unknown too.
VIBECODE_PRICE_SOURCE = "vibecode_models_snapshot.json raw list price per 1M tokens; not calibrated"
VIBECODE_PRICING_BASIS = "vibecode_snapshot_list_price"


def _is_vibecode(provider: str | None) -> bool:
    return str(provider or "").strip().lower() == "vibecode"


def _vibecode_pricing(model: str | None) -> dict[str, Any] | None:
    """Raw snapshot pricing of a Vibecode model id (exact id match, dots as in the catalog)."""
    try:
        from app.services.vibecode_catalog import load_snapshot
        wanted = str(model or "").strip().lower()
        for entry in load_snapshot():
            if str(entry.get("id") or "").lower() == wanted and isinstance(entry.get("pricing"), dict):
                return entry["pricing"]
    except Exception:
        pass
    return None


def vibecode_cost(model: str | None, p_tok: int, c_tok: int, cached_tok: int = 0) -> float | None:
    """Estimated USD from the snapshot list price; None when the model has no known price."""
    pricing = _vibecode_pricing(model)
    if not pricing or pricing.get("input_usd_per_m") is None or pricing.get("output_usd_per_m") is None:
        return None
    in_r = float(pricing["input_usd_per_m"])
    out_r = float(pricing["output_usd_per_m"])
    cache_r = float(pricing.get("cache_read_usd_per_m") if pricing.get("cache_read_usd_per_m") is not None else in_r)
    cached = min(max(cached_tok, 0), p_tok)
    return round(((p_tok - cached) / 1e6) * in_r + (cached / 1e6) * cache_r + (c_tok / 1e6) * out_r, 6)


def vibecode_cost_metadata(model: str | None, *, usage_known: bool) -> dict[str, Any]:
    """Row metadata for a Vibecode call: states the basis and that it is not calibrated."""
    meta: dict[str, Any] = {"provider": "vibecode", "pricing_basis": VIBECODE_PRICING_BASIS,
        "calibrated": False, "price_source": VIBECODE_PRICE_SOURCE}
    try:
        from app.services.vibecode_catalog import PRICE_MARKUP
        meta["ui_markup"] = PRICE_MARKUP
    except Exception:
        pass
    if _vibecode_pricing(model) is None:
        meta.update(cost_basis="unknown", price_known=False)
    if not usage_known:
        # cost_usd is stored as 0.0 (NOT NULL-ish column); this flag says it is not a real zero.
        meta.update(cost_basis="unknown", usage_known=False)
    return meta


_MEDIA_RATES: dict[str, float] = {
    "flux-2-pro": 0.05,
    "seedream-5-pro": 0.045,
    "z-image": 0.02,
    "qwen3-image": 0.035,
    "gpt-image-2": 0.04,
    "kling": 0.08,
    "seedance": 0.06,
    "veo": 0.15,
    "sora": 0.15,
    "wan": 0.05,
    "hailuo": 0.09,
    "suno": 0.05,
    "elevenlabs": 0.01,
    "sound-generation": 0.01,
}


def _calc_cost(
    model: str,
    p_tok: int,
    c_tok: int,
    call_type: str,
    duration: float,
    media_count: int = 1,
    cost_usd: float | None = None,
    cached_tok: int = 0,
    provider: str | None = None,
) -> float:
    if cost_usd is not None and cost_usd >= 0:
        return round(cost_usd, 6)
    if _is_vibecode(provider):
        # Never fall through to Kie's calibrated/generic tables: they are another channel's prices.
        est = vibecode_cost(model, p_tok, c_tok, cached_tok)
        return est if est is not None else 0.0
    m = (model or "").lower().replace("_", "-").replace(" ", "-").replace(".", "-")
    for k, (in_r, out_r, cached_r) in _MEASURED_RATES.items():
        if k.replace(".", "-") in m:
            cached = min(max(cached_tok, 0), p_tok)
            return round(((p_tok - cached) / 1e6) * in_r + (cached / 1e6) * cached_r + (c_tok / 1e6) * out_r, 6)
    for k, (in_r, out_r) in _RATES.items():
        # Kie names models with dashes ("gpt-5-6-sol"), the table with dots
        # ("gpt-5.6-sol"): compare in one form or the dashed name silently
        # falls through to the generic default tier below.
        if k.replace(".", "-") in m:
            return round((p_tok / 1e6) * in_r + (c_tok / 1e6) * out_r, 6)
    for k, rate in _MEDIA_RATES.items():
        if k in m:
            return round(rate * max(media_count, 1), 6)
    if call_type == "video":
        return round(max(duration, 5.0) * 0.02, 6)
    if call_type == "image":
        return round(0.04 * max(media_count, 1), 6)
    if call_type == "audio":
        return 0.03
    return round((p_tok / 1e6) * 0.50 + (c_tok / 1e6) * 2.00, 6)


def _get_identity() -> tuple[str, str]:
    """Определить имя пользователя и ПК из сохранённого профиля или системы."""
    import getpass
    import os
    import socket
    try:
        app_data = os.environ.get("APPDATA")
        candidates = []
        if app_data:
            candidates.append(Path(app_data) / "API-Tracker" / "user_profile.json")
        candidates.append(_TRACKER_DB.parent / "user_profile.json")
        for p in candidates:
            if p.is_file():
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("user_name"):
                    return str(d["user_name"]).strip(), str(d.get("device_name", "")).strip()
    except Exception:
        pass
    u = os.environ.get("USERNAME") or os.environ.get("USER") or getpass.getuser() or "Пользователь"
    h = socket.gethostname() or "Desktop"
    return u, h


def _redact_value(value: Any) -> Any:
    """Маскирует секреты тем же `redact`, что и журнал наблюдателя (fail-closed для текста)."""
    try:
        from app.services.observation import redact
        return redact(value)
    except Exception:
        return "[redacted]" if isinstance(value, str) and value else value


def _cloud_enabled() -> bool:
    """Облако включено только явным API_TRACKER_CLOUD_ENABLED=true (по умолчанию false, A17).

    Ошибка чтения настроек означает «выключено»: без явного согласия соединений не делаем."""
    try:
        from app.settings import settings
        return bool(settings.api_tracker_cloud_enabled)
    except Exception:
        logger.opt(exception=True).error("api_tracker: не удалось прочитать настройку облака, облако считаю выключенным")
        return False


def _cloud_credentials() -> tuple[str, str]:
    """URL и ключ облака из настроек (SUPABASE_URL, SUPABASE_KEY|SUPABASE_ANON_KEY). В исходниках их нет."""
    from app.settings import settings
    return (str(settings.api_tracker_supabase_url or "").strip(),
            str(settings.api_tracker_supabase_key or "").strip())


def _cloud_salt() -> str:
    """Локальная соль хэша имени: случайная на установку, лежит рядом с базой трекера и никуда не уходит."""
    try:
        path = _TRACKER_DB.parent / ".cloud_identity_salt"
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(16)
        path.write_text(value, encoding="utf-8")
        return value
    except Exception:
        logger.opt(exception=True).error("api_tracker: нет локальной соли для хэша имени, имя в облако не отправляю")
        return ""


def _hash_identity(value: str, prefix: str) -> str:
    text = str(value or "").strip()
    salt = _cloud_salt() if text else ""
    if not text or not salt:
        return ""  # без соли хэш подбирается по словарю: лучше пустая строка
    return prefix + hmac.new(salt.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def _cloud_identity(user_name: str, device_name: str) -> tuple[str, str]:
    """Имя пользователя ОС и имя устройства для ОБЛАЧНОЙ строки: необратимый хэш с локальной солью.

    Как есть имена уходят только при явном API_TRACKER_CLOUD_SEND_IDENTITY=true. Локальная строка
    в tracker.db хранит имена как раньше."""
    try:
        from app.settings import settings
        if settings.api_tracker_cloud_send_identity:
            return user_name, device_name
    except Exception:
        logger.opt(exception=True).error("api_tracker: не удалось прочитать настройку имени, отправляю только хэш")
    return _hash_identity(user_name, "u-"), _hash_identity(device_name, "d-")


_cloud_warned: set[str] = set()


def _push_to_supabase(payload: dict[str, Any]) -> bool:
    """Отправка записи в облако. Без явного включения не делает ни одного соединения."""
    if not _cloud_enabled():
        return False
    import httpx
    url, key = _cloud_credentials()
    if not url or not key:
        if "no_credentials" not in _cloud_warned:
            _cloud_warned.add("no_credentials")
            logger.warning("api_tracker: облако включено, но не заданы SUPABASE_URL и SUPABASE_KEY; строки не уходят")
        return False
    endpoint = f"{url.rstrip('/')}/rest/v1/api_calls"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.post(endpoint, headers=headers, json=payload)
        if r.status_code not in (200, 201):
            logger.warning("api_tracker: облако ответило {}, строка останется synced=0", r.status_code)
            return False
        return True
    except Exception as exc:
        logger.warning("api_tracker: облачная отправка не удалась ({}), строка останется synced=0", type(exc).__name__)
        return False


# -- запись строки: очередь не теряется, сбой оставляет след ---------------------------------

_inflight = 0
_inflight_cv = threading.Condition()
_write_failures = 0
_spill_lock = threading.Lock()


def write_failure_count() -> int:
    """Сколько строк трекера не удалось записать с начала процесса (каждая оставила след и копию в spill)."""
    return _write_failures


def pending_writes() -> int:
    return _inflight


def flush(timeout: float = 15.0) -> bool:
    """Дождаться уже запущенных записей (остановка, тесты). True, если очередь пуста."""
    import time
    deadline = time.monotonic() + max(0.0, timeout)
    with _inflight_cv:
        while _inflight > 0:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _inflight_cv.wait(left)
    return True


def _spill_path() -> Path:
    return _TRACKER_DB.parent / "tracker_spill.jsonl"


def _spill_row(inputs: dict[str, Any]) -> bool:
    """Копия невставленной строки: из неё replay_spill вернёт её в базу, когда база снова доступна."""
    try:
        with _spill_lock:
            path = _spill_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(inputs, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:
        logger.opt(exception=True).error("api_tracker: spill-файл недоступен, строка осталась только в логе: {}", inputs)
        return False


def _build_rows(inputs: dict[str, Any]) -> tuple[float, dict[str, Any], dict[str, Any]]:
    """(стоимость, облачная строка, локальная строка) из входных данных вызова."""
    cost = _calc_cost(
        inputs["model"],
        inputs["prompt_tokens"],
        inputs["completion_tokens"],
        inputs["call_type"],
        inputs["duration_sec"],
        media_count=inputs["media_count"] or 1,
        cost_usd=inputs["cost_usd"],
        cached_tok=inputs["cached_tokens"],
        provider=inputs["provider"],
    )
    meta_dict = dict(inputs.get("metadata") or {})
    # Оценка и факт раздельно: факт только если провайдер сам прислал сумму.
    reported = inputs["cost_usd"] is not None and inputs["cost_usd"] >= 0
    meta_dict.setdefault("cost_kind", "actual" if reported else "estimated")
    clean_prov = inputs["provider"] or "unknown"
    p_lower = clean_prov.lower()
    if p_lower == "kie":
        clean_prov = "Kie.ai"
    elif p_lower == "vibecode":
        clean_prov = "vibecode"
    elif p_lower == "outsee":
        clean_prov = "Outsee"
    elif p_lower == "google":
        clean_prov = "Google"
    elif p_lower == "openai":
        clean_prov = "OpenAI"
    elif "eleven" in p_lower:
        clean_prov = "ElevenLabs"
    total_tok = inputs["prompt_tokens"] + inputs["completion_tokens"]
    common = {
        "timestamp": inputs["timestamp"],
        "provider": clean_prov,
        "key_alias": "",
        "model": inputs["model"],
        "call_type": inputs["call_type"],
        "prompt_tokens": inputs["prompt_tokens"],
        "completion_tokens": inputs["completion_tokens"],
        "cached_tokens": inputs["cached_tokens"],
        "total_tokens": total_tok,
        "media_count": inputs["media_count"],
        "duration_sec": round(inputs["duration_sec"], 3),
        "cost_usd": cost,
        "status_code": inputs["status_code"],
        "error_message": inputs["error_message"],
        "project_source": inputs["project_source"],
        "metadata_json": meta_dict,
    }
    return cost, common, meta_dict


_INSERT_SQL = """
    INSERT INTO api_calls (
        timestamp, user_name, device_name, provider, key_alias, model, call_type,
        prompt_tokens, completion_tokens, cached_tokens, total_tokens,
        media_count, duration_sec, cost_usd, status_code,
        error_message, project_source, metadata_json, synced
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _insert_local(common: dict[str, Any], user_name: str, device_name: str, synced: bool) -> None:
    from contextlib import closing
    _TRACKER_DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(_TRACKER_DB), timeout=5.0)) as conn:
        with conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            ensure_tracker_schema(conn, _TRACKER_DB)  # versioned migrations (tracker_schema.py)
            conn.execute(_INSERT_SQL, (
                common["timestamp"], user_name, device_name, common["provider"], "", common["model"],
                common["call_type"], common["prompt_tokens"], common["completion_tokens"],
                common["cached_tokens"], common["total_tokens"], common["media_count"],
                common["duration_sec"], common["cost_usd"], common["status_code"],
                common["error_message"], common["project_source"],
                json.dumps(common["metadata_json"], ensure_ascii=False), 1 if synced else 0,
            ))


def replay_spill() -> int:
    """Вернуть в базу строки из spill-файла. Возвращает число вставленных; не вставленные остаются в файле."""
    path = _spill_path()
    if not path.is_file():
        return 0
    done = 0
    with _spill_lock:
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except Exception:
            logger.opt(exception=True).error("api_tracker: spill-файл не читается")
            return 0
        left: list[str] = []
        u_name, d_name = _get_identity()
        for ln in lines:
            try:
                inputs = json.loads(ln)
                _, common, _ = _build_rows(inputs)
                _insert_local(common, u_name, d_name, synced=not _cloud_enabled())
                done += 1
            except Exception:
                logger.opt(exception=True).warning("api_tracker: строка spill не вставлена, остаётся в файле")
                left.append(ln)
        try:
            if left:
                path.write_text("\n".join(left) + "\n", encoding="utf-8")
            else:
                path.unlink()
        except Exception:
            logger.opt(exception=True).error("api_tracker: spill-файл не обновлён, повторная вставка возможна")
    return done


def _on_write_failed(inputs: dict[str, Any], exc: BaseException) -> None:
    """Сбой записи строки трекера: лог с трассировкой, счётчик, копия в spill и строка-пробел в журнале трат."""
    global _write_failures
    _write_failures += 1
    logger.opt(exception=exc).error(
        "api_tracker: строка вызова НЕ записана ({} {}): {}", inputs.get("provider"), inputs.get("model"),
        type(exc).__name__)
    spilled = _spill_row(inputs)
    est = 0.0
    try:
        est = float(_calc_cost(inputs["model"], inputs["prompt_tokens"], inputs["completion_tokens"],
                               inputs["call_type"], inputs["duration_sec"], media_count=inputs["media_count"] or 1,
                               cost_usd=inputs["cost_usd"], cached_tok=inputs["cached_tokens"],
                               provider=inputs["provider"]))
    except Exception:
        pass
    try:
        from app.services import media_spend
        media_spend.note_gap("tracker", "api_calls_row", usd=est, error=exc,
                             detail=f"{inputs.get('provider')}/{inputs.get('model')} spill={'yes' if spilled else 'no'}")
    except Exception:
        logger.opt(exception=True).error("api_tracker: пробел не записан в журнал трат")


def _with_correlation(metadata: dict[str, Any] | None, project_source: str) -> tuple[dict[str, Any] | None, str]:
    """B02: put project/run/request/plan ids of the CALLER into the row (the write runs in another thread, where
    contextvars are not inherited, so they are read here). Our own ids are added after redaction: they are
    not secrets. An id the caller already set is kept. tracker.db has no columns for them yet: they live in
    ``metadata_json`` (``json_extract``) until the integrator adds the columns (docs in the W2-3 report)."""
    try:
        from app.services import request_context
        corr = request_context.correlation()
        meta = dict(metadata or {})
        run_id, request_id = corr["run_id"], corr["request_id"]
        if run_id:
            meta.setdefault("run_id", run_id)
            if run_id.startswith("plan:"):
                meta.setdefault("plan_id", run_id[len("plan:"):])
        if request_id:
            meta.setdefault("request_id", request_id)
        if corr["project_id"] is not None:
            meta.setdefault("project_id", corr["project_id"])
            if not project_source:
                project_source = str(corr["project_id"])
        return (meta or metadata), project_source
    except Exception:  # noqa: BLE001 - telemetry never breaks the call
        logger.opt(exception=True).warning("api_tracker: correlation ids unavailable, the row is written without them")
        return metadata, project_source


def record_api_call(
    *,
    provider: str,
    model: str,
    call_type: str = "text",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    media_count: int = 0,
    duration_sec: float = 0.0,
    cost_usd: float | None = None,
    status_code: int = 200,
    error_message: str = "",
    project_source: str = "",
    metadata: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> None:
    """Запись вызова API в базу трекера (и в облако, только если оно включено явно).

    Запись идёт в фоновом потоке, который держит процесс до конца записи (не daemon): остановка не теряет
    очередь. Сбой записи не глотается: лог, счётчик, копия строки в spill-файле и пробел в журнале трат."""
    global _inflight
    error_message = str(_redact_value(str(error_message or "")))
    project_source = str(_redact_value(str(project_source or "")))
    metadata = _redact_value(metadata) if metadata else metadata
    metadata, project_source = _with_correlation(metadata, project_source)
    inputs: dict[str, Any] = {
        "provider": provider, "model": model, "call_type": call_type, "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens, "cached_tokens": cached_tokens, "media_count": media_count,
        "duration_sec": duration_sec, "cost_usd": cost_usd, "status_code": status_code,
        "error_message": error_message, "project_source": project_source, "metadata": metadata or {},
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }

    def _write():
        try:
            _, common, _ = _build_rows(inputs)
            u_name, d_name = _get_identity()
            is_synced = True  # True: нечего досылать
            if _cloud_enabled():
                cu, cd = _cloud_identity(u_name, d_name)
                is_synced = _push_to_supabase({**common, "user_name": cu, "device_name": cd})
            _insert_local(common, u_name, d_name, is_synced)
        except Exception as exc:
            _on_write_failed(inputs, exc)
            return
        try:
            replay_spill()
        except Exception:
            logger.opt(exception=True).warning("api_tracker: повторная вставка spill не удалась")

    def _run():
        global _inflight
        try:
            _write()
        finally:
            with _inflight_cv:
                _inflight -= 1
                _inflight_cv.notify_all()

    with _inflight_cv:
        _inflight += 1
    try:
        threading.Thread(target=_run, daemon=False).start()
    except Exception as exc:
        with _inflight_cv:
            _inflight -= 1
            _inflight_cv.notify_all()
        _on_write_failed(inputs, exc)


class ApiEmergencyBlockedError(RuntimeError):
    """Вызов API отклонён: активирован аварийный рубильник."""


_KILLSWITCH_CACHE: dict[str, Any] = {
    "is_blocked": False,
    "updated_by": "",
    "updated_at": "",
    "reason": "",
    "expires_at": 0.0,
}
_KILLSWITCH_LOCK = threading.Lock()


def _fetch_cloud_killswitch() -> tuple[bool, dict[str, Any]]:
    """Получить статус рубильника из облачной базы данных."""
    if not _cloud_enabled():
        return False, {}  # облако выключено: соединений нет, рубильник читается из локальной базы
    url, key = _cloud_credentials()
    if not (url and key):
        return False, {}
    try:
        import httpx
        endpoint = f"{url.rstrip('/')}/rest/v1/api_calls?call_type=eq.system&provider=eq.SYSTEM&order=timestamp.desc&limit=1"
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(endpoint, headers=headers)
            if resp.status_code == 200:
                rows = resp.json()
                if rows and isinstance(rows, list):
                    last = rows[0]
                    return True, {
                        "is_blocked": (last.get("model") == "KILLSWITCH_ACTIVATED"),
                        "updated_by": str(last.get("user_name") or ""),
                        "updated_at": str(last.get("timestamp") or ""),
                        "reason": str(last.get("error_message") or ""),
                    }
    except Exception as exc:
        logger.warning("api_tracker: облачный рубильник недоступен ({}), беру локальный", type(exc).__name__)
    return False, {}


def is_killswitch_active(force_refresh: bool = False) -> tuple[bool, dict[str, Any]]:
    """Проверить статус аварийного рубильника (с кэшированием на 5 секунд)."""
    import time
    global _KILLSWITCH_CACHE
    now = time.time()
    if not force_refresh and now < _KILLSWITCH_CACHE.get("expires_at", 0):
        return bool(_KILLSWITCH_CACHE.get("is_blocked")), dict(_KILLSWITCH_CACHE)

    st = {"is_blocked": False, "updated_by": "", "updated_at": "", "reason": ""}
    got_cloud, cloud_st = _fetch_cloud_killswitch()
    if got_cloud:
        st = cloud_st

    # 2. Локальный SQLite fallback
    if not got_cloud and _TRACKER_DB.is_file():
        try:
            with sqlite3.connect(str(_TRACKER_DB), timeout=2.0) as conn:
                row = conn.execute(
                    "SELECT model, user_name, timestamp, error_message FROM api_calls WHERE call_type = 'system' AND provider = 'SYSTEM' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row:
                    st = {
                        "is_blocked": (row[0] == "KILLSWITCH_ACTIVATED"),
                        "updated_by": str(row[1] or ""),
                        "updated_at": str(row[2] or ""),
                        "reason": str(row[3] or ""),
                    }
        except Exception:
            logger.opt(exception=True).warning("api_tracker: локальный рубильник не прочитан")

    with _KILLSWITCH_LOCK:
        _KILLSWITCH_CACHE = {
            **st,
            "expires_at": now + 5.0,
        }
    return bool(st["is_blocked"]), st


def check_api_allowed(provider: str = "", model: str = "") -> None:
    """Проверяет, разрешены ли сейчас вызовы API.
    
    Если включён аварийный рубильник, фиксирует отклоненный вызов (статус 403)
    и выбрасывает ApiEmergencyBlockedError.
    """
    blocked, info = is_killswitch_active()
    if blocked:
        author = info.get("updated_by") or "администратором"
        err_text = f"Вызовы API временно заблокированы ({author})."
        try:
            record_api_call(
                provider=provider or "system",
                model=model or "unknown",
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                duration_sec=0.0,
                status_code=403,
                error_message=f"Отклонено аварийным рубильником ({author})",
                project_source="killswitch_guard",
            )
        except Exception:
            logger.opt(exception=True).error("api_tracker: отказ рубильника не записан в трекер")
        raise ApiEmergencyBlockedError(err_text)


def toggle_killswitch(
    action: str,  # "block" | "unblock"
    user_name: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Переключить аварийный рубильник (заблокировать или возобновить)."""
    import time
    global _KILLSWITCH_CACHE

    is_block = (action.lower() == "block")
    model_action = "KILLSWITCH_ACTIVATED" if is_block else "KILLSWITCH_DEACTIVATED"
    u_name = user_name.strip() if user_name else _get_identity()[0]
    d_name = _get_identity()[1]
    ts = datetime.now(timezone.utc).isoformat()
    desc = "Аварийный рубильник: API заморожены" if is_block else "Аварийный рубильник: работа API возобновлена"
    if reason:
        desc += f" ({reason})"

    payload = {
        "timestamp": ts,
        "user_name": u_name,
        "device_name": d_name,
        "provider": "SYSTEM",
        "key_alias": "",
        "model": model_action,
        "call_type": "system",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "media_count": 0,
        "duration_sec": 0.0,
        "cost_usd": 0.0,
        "status_code": 200,
        "error_message": desc,
        "project_source": "killswitch",
        "metadata_json": {"action": model_action, "reason": reason},
    }

    # 1. Запись в облако (только если оно включено явно; имена уходят хэшем)
    is_synced = True  # True: нечего досылать
    if _cloud_enabled():
        cu, cd = _cloud_identity(u_name, d_name)
        is_synced = _push_to_supabase({**payload, "user_name": cu, "device_name": cd})

    # 2. Запись в локальный SQLite
    try:
        if _TRACKER_DB.is_file():
            with sqlite3.connect(str(_TRACKER_DB), timeout=4.0) as conn:
                conn.execute(
                    """
                    INSERT INTO api_calls (
                        timestamp, user_name, device_name, provider, key_alias, model, call_type,
                        prompt_tokens, completion_tokens, cached_tokens, total_tokens,
                        media_count, duration_sec, cost_usd, status_code,
                        error_message, project_source, metadata_json, synced
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts, u_name, d_name, "SYSTEM", "", model_action, "system",
                        0, 0, 0, 0, 0, 0.0, 0.0, 200, desc, "killswitch", "{}", 1 if is_synced else 0,
                    ),
                )
        else:
            logger.warning("api_tracker: базы трекера нет, переключение рубильника записано только в кэш процесса")
    except Exception as exc:
        logger.opt(exception=exc).error("api_tracker: переключение рубильника не записано в локальную базу")
        try:
            from app.services import media_spend
            media_spend.note_gap("tracker", "killswitch_row", error=exc, detail=model_action)
        except Exception:
            logger.opt(exception=True).error("api_tracker: пробел рубильника не записан в журнал трат")

    # 3. Мгновенно обновляем кэш
    with _KILLSWITCH_LOCK:
        _KILLSWITCH_CACHE = {
            "is_blocked": is_block,
            "updated_by": u_name,
            "updated_at": ts,
            "reason": desc,
            "expires_at": time.time() + 5.0,
        }

    return {
        "is_blocked": is_block,
        "updated_by": u_name,
        "updated_at": ts,
        "reason": desc,
        "synced": is_synced,
    }

