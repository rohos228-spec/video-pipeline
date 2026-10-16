
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KNOWN_PLATFORMS = ("tiktok", "yt_shorts", "ig_reels", "vk_clips", "likee")

ENV_JSON = "PUBLISH_ACCOUNTS_JSON"
ENV_FILE_NAME = "publish_accounts.json"


REFUSALS: dict[str, str] = {
    "profile_missing": "Для площадки не задан профиль MoreLogin. Укажите его в data/publish_accounts.json.",
    "profile_unavailable": "Профиль MoreLogin недоступен: запустите MoreLogin и проверьте номер профиля.",
    "page_unavailable": "Страница площадки не открылась. Проверьте интернет и профиль.",
    "not_logged_in": "В профиле нет входа в аккаунт площадки. Войдите вручную и повторите.",
    "captcha": "Площадка показывает капчу. Пройдите её вручную, автоповтора не будет.",
    "two_factor": "Площадка просит код подтверждения (2FA). Введите его вручную, автоповтора не будет.",
    "rate_limited": "Площадка ограничила частоту действий. Подождите и проверьте аккаунт вручную.",
    "rejected": "Площадка отклонила публикацию. Проверьте аккаунт вручную.",
    "upload_failed": "Ролик не загрузился на площадку.",
    "processing_timeout": "Площадка не закончила обработку ролика за отведённое время, публикация не нажималась.",
    "selector_not_found": "Не найден нужный элемент страницы (поле файла, подписи или кнопка). Интерфейс площадки мог измениться.",
    "platform_limit": "Подпись или публикация не проходит по лимитам площадки.",
    "daily_limit": "Достигнут лимит публикаций в сутки для площадки. Публикация перенесена.",
    "outside_window": "Сейчас вне разрешённого окна публикации. Публикация перенесена.",
    "caption_empty": "Подпись пуста: публикация не начата.",
    "caption_field_missing": "Поле подписи не найдено, публикация не нажималась.",
    "caption_fill_failed": "Не удалось заполнить подпись, публикация не нажималась.",
    "caption_not_filled": "Подпись не появилась в поле после заполнения, публикация не нажималась.",
    "accounts_config_invalid": "Конфиг аккаунтов публикации содержит ошибку.",
    "click_not_allowed": "Журнал публикаций не разрешил нажатие.",
    "no_success_marker": "Кнопка нажата, признак успеха не найден или не настроен. Проверьте площадку вручную.",
    "confirmed_without_url": "Признак успеха есть, но ссылка на пост не получена. Проверьте площадку вручную.",
    "exception_before_click": "Сбой до нажатия кнопки публикации.",
    "exception_after_click": "Сбой после нажатия кнопки публикации: проверьте площадку вручную.",
}

HUMAN_STOP_CODES = frozenset({"not_logged_in", "captcha", "two_factor"})
NO_AUTO_RETRY_CODES = HUMAN_STOP_CODES | {"rate_limited", "rejected", "profile_missing", "accounts_config_invalid"}


def refusal_text(code: str | None) -> str:
    return REFUSALS.get(code or "", f"Отказ: {code}" if code else "")



_SECRET_KEYS = re.compile(
    r"(pass(word|wd)?|pwd|token|secret|cookie|api[_-]?key|authorization|session|credential|otp|2fa_?code)", re.I)


class AccountsConfigError(ValueError):
    pass


def mask_profile(profile_id: str | None) -> str:
    pid = str(profile_id or "").strip()
    return f"…{pid[-4:]}" if pid else "не задан"



_LIST_FIELDS = (
    "hashtags", "success_selectors", "success_url_markers", "processing_busy_selectors",
    "processing_done_selectors", "captcha_selectors", "two_factor_selectors", "login_selectors",
    "login_url_markers", "logged_in_selectors", "verify_ok_selectors", "verify_gone_selectors",
    "title_selectors",
)
_STR_FIELDS = ("account_label", "region", "upload_url", "post_url_regex", "post_link_selector", "caption_suffix",
               "feed_url", "feed_item_selector", "on_overflow", "hashtag_separator")
_NUM_FIELDS = ("max_per_day", "caption_limit", "title_limit", "processing_timeout_s", "confirm_timeout_s")
_ALLOWED = (set(_LIST_FIELDS) | set(_STR_FIELDS) | set(_NUM_FIELDS)
            | {"profile_id", "error_selectors", "window_hours", "enabled"})
_DEFAULT_KEYS = {"pause_s", "jitter_s"}


@dataclass
class PlatformAccount:
    platform: str
    profile_id: str = field(default="", repr=False)
    account_label: str = ""
    region: str = ""
    enabled: bool = True
    upload_url: str = ""
    max_per_day: int | None = None
    window_hours: tuple[int, int] | None = None
    caption_limit: int | None = None
    title_limit: int | None = None
    on_overflow: str = "reject"
    hashtags: list[str] = field(default_factory=list)
    hashtag_separator: str = "\n\n"
    caption_suffix: str = ""
    title_selectors: list[str] = field(default_factory=list)
    processing_timeout_s: float | None = None
    processing_busy_selectors: list[str] = field(default_factory=list)
    processing_done_selectors: list[str] = field(default_factory=list)
    captcha_selectors: list[str] = field(default_factory=list)
    two_factor_selectors: list[str] = field(default_factory=list)
    login_selectors: list[str] = field(default_factory=list)
    login_url_markers: list[str] = field(default_factory=list)
    logged_in_selectors: list[str] = field(default_factory=list)
    error_selectors: dict[str, list[str]] = field(default_factory=dict)
    success_selectors: list[str] = field(default_factory=list)
    success_url_markers: list[str] = field(default_factory=list)
    post_url_regex: str = ""
    post_link_selector: str = ""
    confirm_timeout_s: float | None = None
    verify_ok_selectors: list[str] = field(default_factory=list)
    verify_gone_selectors: list[str] = field(default_factory=list)
    feed_url: str = ""
    feed_item_selector: str = ""

    def label(self) -> str:
        return self.account_label or f"{self.platform} (профиль {mask_profile(self.profile_id)})"


@dataclass
class AccountsConfig:
    platforms: dict[str, PlatformAccount] = field(default_factory=dict)
    pause_s: float = 3.0
    jitter_s: float = 0.0
    source: str = "none"

    @property
    def configured(self) -> bool:
        return any(a.profile_id for a in self.platforms.values())

    def for_platform(self, platform: str) -> PlatformAccount:
        return self.platforms.get(platform) or PlatformAccount(platform=platform)


def _check_secret_keys(obj: Any, path: str) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _SECRET_KEYS.search(str(k)):
                raise AccountsConfigError(
                    f"В конфиге аккаунтов найден ключ «{path}{k}», похожий на секрет. "
                    "Пароли, токены и куки здесь не хранятся: войдите в аккаунт вручную в профиле MoreLogin.")
            _check_secret_keys(v, f"{path}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _check_secret_keys(v, f"{path}{i}.")


def _str_list(v: Any, name: str) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise AccountsConfigError(f"Поле «{name}» должно быть списком строк.")
    return [x.strip() for x in v if x.strip()]


def _num(v: Any, name: str, *, integer: bool, minimum: float) -> Any:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise AccountsConfigError(f"Поле «{name}» должно быть числом.")
    if v < minimum:
        raise AccountsConfigError(f"Поле «{name}» не может быть меньше {minimum:g}.")
    return int(v) if integer else float(v)


def _parse_platform(platform: str, raw: Any) -> PlatformAccount:
    if platform not in KNOWN_PLATFORMS:
        raise AccountsConfigError(f"Неизвестная площадка «{platform}». Допустимо: {', '.join(KNOWN_PLATFORMS)}.")
    if not isinstance(raw, dict):
        raise AccountsConfigError(f"Настройки площадки «{platform}» должны быть объектом.")
    unknown = sorted(set(raw) - _ALLOWED)
    if unknown:
        raise AccountsConfigError(f"Площадка «{platform}»: неизвестные поля {', '.join(unknown)}.")
    acct = PlatformAccount(platform=platform)
    pid = raw.get("profile_id", "")
    if pid is not None and not isinstance(pid, (str, int)):
        raise AccountsConfigError(f"Площадка «{platform}»: profile_id должен быть строкой.")
    acct.profile_id = str(pid or "").strip()
    for name in _STR_FIELDS:
        if name in raw and raw[name] is not None:
            if not isinstance(raw[name], str):
                raise AccountsConfigError(f"Площадка «{platform}»: поле «{name}» должно быть строкой.")
            setattr(acct, name, raw[name] if name in ("caption_suffix", "hashtag_separator") else raw[name].strip())
    for name in _LIST_FIELDS:
        if name in raw:
            setattr(acct, name, _str_list(raw[name], f"{platform}.{name}"))
    acct.max_per_day = _num(raw.get("max_per_day"), f"{platform}.max_per_day", integer=True, minimum=1)
    acct.caption_limit = _num(raw.get("caption_limit"), f"{platform}.caption_limit", integer=True, minimum=1)
    acct.title_limit = _num(raw.get("title_limit"), f"{platform}.title_limit", integer=True, minimum=1)
    acct.processing_timeout_s = _num(raw.get("processing_timeout_s"), f"{platform}.processing_timeout_s",
                                     integer=False, minimum=0)
    acct.confirm_timeout_s = _num(raw.get("confirm_timeout_s"), f"{platform}.confirm_timeout_s",
                                  integer=False, minimum=0)
    if acct.on_overflow not in ("reject", "truncate"):
        raise AccountsConfigError(f"Площадка «{platform}»: on_overflow должен быть reject или truncate.")
    if "enabled" in raw:
        acct.enabled = bool(raw["enabled"])
    wh = raw.get("window_hours")
    if wh is not None:
        if (not isinstance(wh, list) or len(wh) != 2 or not all(isinstance(x, int) and not isinstance(x, bool)
                                                                 and 0 <= x <= 24 for x in wh) or wh[0] == wh[1]):
            raise AccountsConfigError(f"Площадка «{platform}»: window_hours - два часа от 0 до 24, например [10, 22].")
        acct.window_hours = (wh[0], wh[1])
    es = raw.get("error_selectors") or {}
    if not isinstance(es, dict):
        raise AccountsConfigError(f"Площадка «{platform}»: error_selectors должен быть объектом код -> список.")
    for code, sels in es.items():
        if code not in REFUSALS:
            raise AccountsConfigError(f"Площадка «{platform}»: error_selectors, неизвестный код «{code}».")
        acct.error_selectors[code] = _str_list(sels, f"{platform}.error_selectors.{code}")
    if acct.post_url_regex:
        try:
            re.compile(acct.post_url_regex)
        except re.error as e:
            raise AccountsConfigError(f"Площадка «{platform}»: post_url_regex не компилируется ({e}).") from e
    return acct


def parse_config(data: Any, *, source: str = "dict") -> AccountsConfig:
    if not isinstance(data, dict):
        raise AccountsConfigError("Конфиг аккаунтов должен быть JSON-объектом.")
    _check_secret_keys(data, "")
    platforms_raw = data["platforms"] if "platforms" in data else {k: v for k, v in data.items()
                                                                    if k not in ("defaults", "version")}
    if not isinstance(platforms_raw, dict):
        raise AccountsConfigError("Поле «platforms» должно быть объектом.")
    cfg = AccountsConfig(source=source)
    d = data.get("defaults") or {}
    if not isinstance(d, dict) or set(d) - _DEFAULT_KEYS:
        raise AccountsConfigError("Поле «defaults» допускает только pause_s и jitter_s.")
    if "pause_s" in d:
        cfg.pause_s = _num(d["pause_s"], "defaults.pause_s", integer=False, minimum=0)
    if "jitter_s" in d:
        cfg.jitter_s = _num(d["jitter_s"], "defaults.jitter_s", integer=False, minimum=0)
    for name, raw in platforms_raw.items():
        cfg.platforms[name] = _parse_platform(name, raw)
    return cfg


def default_config_path(data_dir: str | Path | None = None) -> Path:
    base = Path(data_dir) if data_dir else Path(os.environ.get("DATA_DIR") or "data")
    return base / ENV_FILE_NAME


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    bp, op = base.get("platforms"), over.get("platforms")
    out.update({k: v for k, v in over.items() if k not in ("platforms", "defaults")})
    if "defaults" in over or "defaults" in base:
        out["defaults"] = {**(base.get("defaults") or {}), **(over.get("defaults") or {})}
    if bp is not None or op is not None:
        merged = {k: dict(v) if isinstance(v, dict) else v for k, v in (bp or {}).items()}
        for k, v in (op or {}).items():
            merged[k] = {**merged[k], **v} if isinstance(merged.get(k), dict) and isinstance(v, dict) else v
        out["platforms"] = merged
    return out


def _normalize(data: Any) -> Any:
    if isinstance(data, dict) and "platforms" not in data:
        rest = {k: v for k, v in data.items() if k not in ("defaults", "version")}
        out = {"platforms": rest}
        if "defaults" in data:
            out["defaults"] = data["defaults"]
        return out
    return data


def load_accounts(*, env: dict[str, str] | None = None, path: str | Path | None = None,
                  data_dir: str | Path | None = None) -> AccountsConfig:
    env = os.environ if env is None else env
    p = Path(path) if path else default_config_path(data_dir or env.get("DATA_DIR"))
    merged: dict = {}
    sources: list[str] = []
    if p.is_file():
        try:
            merged = _normalize(json.loads(p.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError) as e:
            raise AccountsConfigError(f"Файл {p.name} не читается как JSON ({type(e).__name__}).") from e
        if not isinstance(merged, dict):
            raise AccountsConfigError(f"Файл {p.name}: ожидался JSON-объект.")
        sources.append(p.name)
    raw_env = (env.get(ENV_JSON) or "").strip()
    if raw_env:
        try:
            over = _normalize(json.loads(raw_env))
        except ValueError as e:
            raise AccountsConfigError(f"Переменная {ENV_JSON} не читается как JSON.") from e
        if not isinstance(over, dict):
            raise AccountsConfigError(f"Переменная {ENV_JSON}: ожидался JSON-объект.")
        _check_secret_keys(over, "")
        merged = _merge(merged, over)
        sources.append(ENV_JSON)
    if not merged:
        return AccountsConfig(source="none")
    return parse_config(merged, source="+".join(sources))




def select_profile(platform: str, cfg: AccountsConfig, *, legacy_profile_id: str | None = None) -> str | None:
    if cfg.configured:
        acct = cfg.platforms.get(platform)
        return (acct.profile_id or None) if acct else None
    pid = (legacy_profile_id if legacy_profile_id is not None else os.environ.get("MORELOGIN_PROFILE_ID") or "").strip()
    return pid or None




@dataclass
class CaptionResult:
    text: str = ""
    truncated: bool = False
    error_code: str | None = None
    error: str = ""


def _norm_tag(t: str) -> str:
    t = t.strip().lstrip("#").replace(" ", "")
    return f"#{t}" if t else ""


def compose_caption(caption: str, acct: PlatformAccount) -> CaptionResult:
    body = (caption or "").strip()
    if not body:
        return CaptionResult(error_code="caption_empty", error=refusal_text("caption_empty"))
    present = {w.lower() for w in re.findall(r"#\w+", body)}
    tags, seen = [], set()
    for t in map(_norm_tag, acct.hashtags):
        if t and t.lower() not in present and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    tail_parts = []
    if acct.caption_suffix.strip():
        tail_parts.append(acct.caption_suffix.strip())
    if tags:
        tail_parts.append(" ".join(tags))
    tail = acct.hashtag_separator.join(tail_parts) if tail_parts else ""
    sep = acct.hashtag_separator if tail else ""
    full = body + sep + tail
    lim = acct.caption_limit
    if lim is None or len(full) <= lim:
        return CaptionResult(text=full)
    if acct.on_overflow != "truncate":
        return CaptionResult(error_code="platform_limit",
                             error=f"Подпись {len(full)} символов, лимит площадки {lim}. {refusal_text('platform_limit')}")
    room = lim - len(sep) - len(tail) - 1
    if room < 1:
        return CaptionResult(error_code="platform_limit",
                             error=f"Хэштеги и суффикс ({len(tail)} символов) не помещаются в лимит {lim}.")
    cut = body[:room].rstrip()
    sp = cut.rfind(" ")
    if sp > room * 0.6:
        cut = cut[:sp].rstrip()
    return CaptionResult(text=cut + "…" + sep + tail, truncated=True)


def make_title(caption: str, acct: PlatformAccount) -> str:
    first = (caption or "").strip().splitlines()[0] if (caption or "").strip() else ""
    lim = acct.title_limit
    if lim and len(first) > lim:
        first = first[: lim - 1].rstrip() + "…"
    return first



DAY_S = 86400.0


@dataclass
class LimitDecision:
    allowed: bool
    code: str | None = None
    used: int = 0
    limit: int | None = None
    retry_after: float | None = None
    text: str = ""


def daily_limit_decision(click_times: list[float] | None, max_per_day: int | None,
                         now: float | None = None) -> LimitDecision:
    if not max_per_day or click_times is None:
        return LimitDecision(True, limit=max_per_day)
    now = time.time() if now is None else now
    recent = sorted(t for t in click_times if t is not None and now - DAY_S < t <= now + 1)
    if len(recent) < max_per_day:
        return LimitDecision(True, used=len(recent), limit=max_per_day)
    retry_after = recent[len(recent) - max_per_day] + DAY_S
    return LimitDecision(False, "daily_limit", len(recent), max_per_day, retry_after,
                         f"Сегодня уже {len(recent)} из {max_per_day} публикаций. {refusal_text('daily_limit')}")


def window_decision(window: tuple[int, int] | None, now: float | None = None) -> LimitDecision:
    if not window:
        return LimitDecision(True)
    now = time.time() if now is None else now
    lt = time.localtime(now)
    h = lt.tm_hour + lt.tm_min / 60
    s, e = window
    inside = (s <= h < e) if s < e else (h >= s or h < e)
    if inside:
        return LimitDecision(True)
    start_today = now - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec) + s * 3600
    retry = start_today if start_today > now else start_today + DAY_S
    return LimitDecision(False, "outside_window", retry_after=retry,
                         text=f"Окно публикации {s}:00-{e}:00. {refusal_text('outside_window')}")


async def recent_click_times(session, platform: str, *, now: float | None = None) -> list[float]:
    from sqlalchemy import text as _t

    from app.services import publications_store as pstore
    await pstore.ensure_schema(session)
    now = time.time() if now is None else now
    rows = (await session.execute(
        _t("SELECT clicked_at FROM publications WHERE platform=:p AND clicked_at IS NOT NULL AND clicked_at>=:s"),
        {"p": platform, "s": now - DAY_S})).all()
    await session.commit()
    return [float(r[0]) for r in rows]



_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_part(s: str | int | None, default: str = "unassigned") -> str:
    out = _SAFE.sub("_", str(s if s is not None else "")).strip("._")
    return out[:80] or default


def screenshot_relpath(project_label: str | int | None, platform: str) -> str:
    return f"publications/{safe_part(project_label)}/{safe_part(platform, 'platform')}.png"


def data_root(data_dir: str | Path | None = None) -> Path:
    return Path(data_dir) if data_dir else Path(os.environ.get("DATA_DIR") or "data")
