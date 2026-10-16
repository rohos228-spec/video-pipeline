"""Публикация готового mp4 на 5 платформ через один MoreLogin-профиль.

Общая стратегия: открываем страницу загрузки каждой платформы, загружаем файл
через `input[type=file]`, вбиваем подпись (caption), жмём Publish. Селекторы
отличаются, но структура одинаковая — поэтому есть базовый класс Publisher.

⚠️ Селекторы каждой платформы — кандидаты, **не верифицированы** на живом UI.
Для калибровки есть режим разведки: `python -m app.bots.publishers recon tiktok`.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from loguru import logger
from playwright.async_api import Browser, Page

from app.bots.morelogin import MoreLoginError, morelogin_browser
from app.services import publish_accounts as pa
from app.services.publish_accounts import (
    HUMAN_STOP_CODES,
    AccountsConfig,
    AccountsConfigError,
    PlatformAccount,
    refusal_text,
)


@dataclass
class PublishResult:
    platform: str
    ok: bool
    url: str | None = None
    error: str | None = None
    status: str = ""
    clicked: bool = False
    post_id: str | None = None
    error_code: str | None = None
    screenshot: str | None = None
    account_label: str | None = None
    needs_human: bool = False
    retry_after: float | None = None
    caption_truncated: bool = False


@dataclass
class PreflightResult:
    platform: str
    ok: bool
    code: str | None = None
    message: str = ""
    page_url: str = ""
    account_label: str = ""

    @property
    def needs_human(self) -> bool:
        return self.code in HUMAN_STOP_CODES


class PublishGate:

    async def claim(self, platform: str) -> bool:
        return True

    async def before_click(self, platform: str) -> bool:
        return True

    async def record(self, result: PublishResult) -> None:
        return None

    async def recent_clicks(self, platform: str) -> list[float] | None:
        return None


GENERIC_CAPTCHA = [
    "iframe[src*='captcha' i]", "iframe[src*='recaptcha' i]", "iframe[title*='captcha' i]",
    "[id*='captcha' i]",
    "text=/verify you are human|i'm not a robot|подтвердите, что вы не робот|я не робот/i",
]
GENERIC_TWO_FACTOR = [
    "input[autocomplete='one-time-code']",
    "text=/two-factor|2-step verification|verification code|код подтверждения|двухфакторн|двухэтапн/i",
]
GENERIC_LOGIN_SELECTORS = ["input[type='password']"]
GENERIC_LOGIN_URL_MARKERS = ["/login", "/signin", "/sign_in", "/accounts/login", "accounts.google.com"]
GENERIC_ERRORS: dict[str, list[str]] = {
    "upload_failed": ["text=/upload failed|failed to upload|не удалось загрузить|ошибка загрузки/i"],
    "platform_limit": ["text=/daily limit|upload limit|лимит публикаций|превышен лимит/i"],
    "rate_limited": ["text=/too many (requests|attempts)|слишком много (запросов|попыток)|try again later/i"],
    "rejected": ["text=/violates our|нарушает правила|не прошло проверку|publication rejected/i"],
}
PRE_CLICK_ERROR_CODES = ("upload_failed", "platform_limit", "rate_limited", "rejected")


def _clean_url(url: str | None) -> str:
    try:
        u = urlparse(url or "")
        return urlunparse((u.scheme, u.netloc, u.path, "", "", ""))
    except Exception:
        return ""


def _is_http(url: str | None) -> bool:
    return (url or "").lower().startswith(("http://", "https://"))


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


class Publisher:
    platform: str = "base"
    upload_url: str = ""
    extra_hosts: list[str] = []
    file_input_selectors: list[str] = ["input[type='file']"]
    caption_selectors: list[str] = ["textarea", "div[contenteditable='true']"]
    publish_button_selectors: list[str] = ["button:has-text('Post')", "button:has-text('Publish')"]
    success_selectors: list[str] = []
    success_url_markers: list[str] = []
    login_url_markers: list[str] = []
    confirm_timeout_s: float = 8.0
    processing_timeout_s: float = 300.0
    poll_s: float = 0.5
    settle_s: float = 1.5
    find_timeout_ms: int = 30_000
    login_wait_s: float = 4.0

    account: PlatformAccount | None = None
    project_label: str | int | None = None
    data_dir: str | Path | None = None

    def __init__(self, browser: Browser) -> None:
        self.browser = browser


    def _acct(self) -> PlatformAccount:
        return self.account or PlatformAccount(platform=self.platform)

    def _list(self, name: str, default: list[str]) -> list[str]:
        v = getattr(self._acct(), name, None) if self.account is not None else None
        return list(v) if v else list(default)

    def _upload_url(self) -> str:
        return (self.account.upload_url if self.account is not None and self.account.upload_url
                else self.upload_url)

    def _confirm_timeout(self) -> float:
        if self.account is not None and self.account.confirm_timeout_s is not None:
            return self.account.confirm_timeout_s
        return self.confirm_timeout_s

    def _processing_timeout(self) -> float:
        if self.account is not None and self.account.processing_timeout_s is not None:
            return self.account.processing_timeout_s
        return self.processing_timeout_s

    def _error_selectors(self, code: str) -> list[str]:
        cfg = self.account.error_selectors.get(code) if self.account is not None else None
        return list(cfg) if cfg else list(GENERIC_ERRORS.get(code, []))

    def _hosts(self) -> list[str]:
        host = urlparse(self._upload_url()).netloc
        return [h for h in [host, *self.extra_hosts] if h]


    async def _open(self) -> Page:
        ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        url = self._upload_url()
        hosts = self._hosts()
        for p in ctx.pages:
            try:
                if p.url and hosts and any(host in p.url for host in hosts):
                    await p.goto(url, wait_until="domcontentloaded")
                    return p
            except Exception:
                continue
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        return page

    async def _first_visible(self, page: Page, selectors: list[str], *, timeout_ms: int = 20_000) -> str | None:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            for sel in selectors:
                try:
                    if await page.locator(sel).count() > 0:
                        return sel
                except Exception:
                    continue
            await asyncio.sleep(0.3)
        return None

    async def _visible(self, page: Page, sel: str) -> bool:
        try:
            return bool(await page.locator(sel).first.is_visible())
        except Exception:
            return False

    async def _any_visible(self, page: Page, selectors: list[str]) -> str | None:
        for sel in selectors:
            if await self._visible(page, sel):
                return sel
        return None


    async def check_page(self, page: Page) -> PreflightResult:
        label = self._acct().label()
        cur = page.url or ""
        res = lambda ok, code=None, msg="": PreflightResult(
            platform=self.platform, ok=ok, code=code, message=msg or (refusal_text(code) if code else ""),
            page_url=_clean_url(cur), account_label=label)
        if not cur or cur.startswith("about:"):
            return res(False, "page_unavailable")
        if self.settle_s > 0:
            await asyncio.sleep(self.settle_s)
            cur = page.url or cur
        if await self._any_visible(page, self._list("captcha_selectors", GENERIC_CAPTCHA)):
            return res(False, "captcha")
        if await self._any_visible(page, self._list("two_factor_selectors", GENERIC_TWO_FACTOR)):
            return res(False, "two_factor")
        markers = self._list("login_url_markers", self.login_url_markers + GENERIC_LOGIN_URL_MARKERS)
        low = cur.lower()
        if any(m.lower() in low for m in markers):
            return res(False, "not_logged_in")
        if await self._any_visible(page, self._list("login_selectors", GENERIC_LOGIN_SELECTORS)):
            return res(False, "not_logged_in")
        logged = self._list("logged_in_selectors", [])
        if logged:
            deadline = asyncio.get_event_loop().time() + self.login_wait_s
            while True:
                if await self._any_visible(page, logged):
                    break
                if asyncio.get_event_loop().time() >= deadline:
                    return res(False, "not_logged_in", "Признак входа в аккаунт на странице не найден. "
                               + refusal_text("not_logged_in"))
                await asyncio.sleep(self.poll_s)
        hosts = self._hosts()
        if hosts and not any(h in (urlparse(cur).netloc or "") for h in hosts):
            return res(False, "page_unavailable", "Открылась страница другого сайта вместо площадки. "
                       + refusal_text("page_unavailable"))
        return res(True)

    async def _detect_error(self, page: Page, codes: tuple[str, ...] = PRE_CLICK_ERROR_CODES) -> str | None:
        for code in codes:
            if await self._any_visible(page, self._error_selectors(code)):
                return code
        return None


    @staticmethod
    async def _read_text(page: Page, sel: str) -> str:
        return str(await page.locator(sel).first.evaluate(
            "el => ('value' in el && typeof el.value === 'string') ? el.value : (el.innerText || el.textContent || '')"))

    async def _fill_verified(self, page: Page, sel: str, text: str) -> bool:
        want = _nospace(text)
        loc = page.locator(sel).first
        await loc.click()
        await loc.fill(text)
        if want and want in _nospace(await self._read_text(page, sel)):
            return True
        await loc.click()
        with contextlib.suppress(Exception):
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
        await page.keyboard.insert_text(text)
        return bool(want) and want in _nospace(await self._read_text(page, sel))

    async def _wait_ready(self, page: Page) -> tuple[str | None, str | None]:
        busy = self._list("processing_busy_selectors", [])
        done = self._list("processing_done_selectors", [])
        buttons = self._list("publish_button_selectors", self.publish_button_selectors)
        deadline = asyncio.get_event_loop().time() + self._processing_timeout()
        saw_button = False
        while True:
            err = await self._detect_error(page)
            if err:
                return None, err
            cap = await self._any_visible(page, self._list("captcha_selectors", GENERIC_CAPTCHA))
            if cap:
                return None, "captcha"
            if not (busy and await self._any_visible(page, busy)) and (not done or await self._any_visible(page, done)):
                for sel in buttons:
                    if await self._visible(page, sel):
                        saw_button = True
                        try:
                            if await page.locator(sel).first.is_enabled():
                                return sel, None
                        except Exception:
                            pass
            if asyncio.get_event_loop().time() >= deadline:
                return None, ("processing_timeout" if (saw_button or busy or done) else "selector_not_found")
            await asyncio.sleep(self.poll_s)


    def _result(self, code: str, *, status: str = "failed", clicked: bool = False, detail: str = "",
                url: str | None = None) -> PublishResult:
        text = refusal_text(code)
        err = f"{self.platform}: {text}" + (f" ({detail})" if detail else "")
        return PublishResult(
            platform=self.platform, ok=False, url=url, error=err, status=status, clicked=clicked, error_code=code,
            account_label=self._acct().label() if self.account is not None else None,
            needs_human=code in HUMAN_STOP_CODES or code in ("rate_limited", "rejected"))

    async def _shot(self, page: Page) -> str | None:
        rel = pa.screenshot_relpath(self.project_label, self.platform)
        try:
            path = pa.data_root(self.data_dir) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(path))
            return rel
        except Exception:
            logger.warning("{}: скриншот после клика не сохранён", self.platform)
            return None

    async def publish(self, video_path: Path, caption: str, *, before_click=None) -> PublishResult:
        clicked = False
        acct = self.account
        try:
            if not (caption or "").strip():
                return PublishResult(platform=self.platform, ok=False, status="failed",
                                     error="подпись пуста: публикация не начата", error_code="caption_empty")
            truncated = False
            if acct is not None:
                comp = pa.compose_caption(caption, acct)
                if comp.error_code:
                    return self._result(comp.error_code, detail=comp.error)
                caption, truncated = comp.text, comp.truncated
            try:
                page = await self._open()
            except Exception as e:
                if acct is None:
                    raise
                return self._result("page_unavailable", detail=type(e).__name__)

            if acct is not None:
                pre = await self.check_page(page)
                if not pre.ok:
                    r = self._result(pre.code or "page_unavailable")
                    r.error = f"{self.platform}: {pre.message}"
                    return r

            file_sel = await self._first_visible(page, self.file_input_selectors, timeout_ms=self.find_timeout_ms)
            if not file_sel:
                if acct is None:
                    raise RuntimeError(f"{self.platform}: input[type=file] не найден")
                return self._result("selector_not_found", detail="поле выбора файла")
            await page.locator(file_sel).first.set_input_files(str(video_path))

            cap_sel = await self._first_visible(page, self.caption_selectors, timeout_ms=self.find_timeout_ms)
            if not cap_sel:
                return PublishResult(platform=self.platform, ok=False, status="failed",
                                     error=f"{self.platform}: поле подписи не найдено, клик не делался",
                                     error_code="caption_field_missing")
            try:
                if acct is None:
                    await page.locator(cap_sel).first.click()
                    await page.locator(cap_sel).first.fill(caption)
                elif not await self._fill_verified(page, cap_sel, caption):
                    return self._result("caption_not_filled")
                tsel = self._list("title_selectors", []) if acct is not None else []
                if tsel:
                    title_sel = await self._first_visible(page, tsel, timeout_ms=10_000)
                    title = pa.make_title(caption, acct)
                    if not title_sel or not await self._fill_verified(page, title_sel, title):
                        return self._result("caption_not_filled", detail="заголовок")
            except Exception as e:
                logger.warning("{}: не удалось заполнить caption", self.platform)
                return PublishResult(platform=self.platform, ok=False, status="failed",
                                     error=f"{self.platform}: подпись не заполнена ({e}), клик не делался",
                                     error_code="caption_fill_failed")

            if acct is None:
                pub_sel = await self._first_visible(page, self.publish_button_selectors, timeout_ms=60_000)
                if not pub_sel:
                    raise RuntimeError(f"{self.platform}: кнопка Publish не найдена")
            else:
                pub_sel, bad = await self._wait_ready(page)
                if bad:
                    return self._result(bad)
            if before_click is not None:
                clicked = True
                if not await before_click():
                    return PublishResult(platform=self.platform, ok=False, status="failed",
                                         error=f"{self.platform}: клик не разрешён журналом публикаций",
                                         error_code="click_not_allowed", clicked=False)
            clicked = True
            await page.locator(pub_sel).first.click()

            if acct is not None:
                res = await self._outcome(page)
                res.screenshot = await self._shot(page)
                res.caption_truncated = truncated
                return res
            return await self._legacy_outcome(page)
        except Exception as e:
            logger.exception("{}: publish failed", self.platform)
            return PublishResult(platform=self.platform, ok=False, error=str(e), status="failed",
                                 clicked=clicked, error_code="exception_after_click" if clicked else "exception_before_click",
                                 account_label=acct.label() if acct is not None else None)

    async def _legacy_outcome(self, page: Page) -> PublishResult:
        if await self._confirm(page):
            if not page.url:
                return PublishResult(platform=self.platform, ok=False, status="unconfirmed", clicked=True,
                                     error="unconfirmed: признак успеха есть, но URL поста пуст",
                                     error_code="confirmed_without_url")
            return PublishResult(platform=self.platform, ok=True, url=page.url, status="confirmed",
                                 clicked=True)
        logger.warning("{}: публикация не подтверждена признаком успеха", self.platform)
        return PublishResult(
            platform=self.platform, ok=False, url=page.url, status="unconfirmed", clicked=True,
            error="unconfirmed: кнопка нажата, признак успешной публикации не найден",
            error_code="no_success_marker",
        )

    async def _confirm(self, page: Page) -> bool:
        if not self.success_selectors and not self.success_url_markers:
            await asyncio.sleep(self.confirm_timeout_s)
            return False
        deadline = asyncio.get_event_loop().time() + self.confirm_timeout_s
        while True:
            try:
                if any(m in (page.url or "") for m in self.success_url_markers):
                    return True
                for sel in self.success_selectors:
                    if await page.locator(sel).count() > 0:
                        return True
            except Exception:
                pass
            if asyncio.get_event_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.5)


    async def _marker_hit(self, page: Page, sels: list[str], markers: list[str]) -> bool:
        low = (page.url or "")
        if any(m and m in low for m in markers):
            return True
        for sel in sels:
            try:
                if await page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def extract_post(self, page: Page) -> tuple[str | None, str | None]:
        acct = self._acct()
        link: str | None = None
        if acct.post_link_selector:
            try:
                link = await page.locator(acct.post_link_selector).first.get_attribute("href", timeout=1500)
            except Exception:
                link = None
        cur = page.url or ""
        cands = [x for x in (link, cur) if x]
        rx = re.compile(acct.post_url_regex) if acct.post_url_regex else None
        if rx is not None and not any(rx.search(x) for x in cands if _is_http(x)):
            with contextlib.suppress(Exception):
                cands += [str(h) for h in await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")]
        for x in cands:
            if not _is_http(x):
                continue
            if rx is not None:
                m = rx.search(x)
                if m:
                    pid = m.groupdict().get("id") or (m.group(1) if m.groups() else None)
                    return x, pid
            elif x == link or _clean_url(x) != _clean_url(self._upload_url()):
                return x, None
        return None, None

    async def _outcome(self, page: Page) -> PublishResult:
        acct = self._acct()
        label = acct.label()
        sels = self._list("success_selectors", self.success_selectors)
        marks = self._list("success_url_markers", self.success_url_markers)
        base = dict(platform=self.platform, clicked=True, account_label=label)
        if not sels and not marks:
            await asyncio.sleep(self._confirm_timeout())
            code = await self._detect_error(page, ("rejected", "rate_limited", "upload_failed", "platform_limit"))
            return self._after_error(code, base, page) or PublishResult(
                ok=False, url=None, status="unconfirmed", error_code="no_success_marker",
                error=f"{self.platform}: {refusal_text('no_success_marker')}", **base)
        deadline = asyncio.get_event_loop().time() + self._confirm_timeout()
        hit = False
        while True:
            if await self._marker_hit(page, sels, marks):
                hit = True
                url, pid = await self.extract_post(page)
                if url or pid:
                    return PublishResult(ok=True, url=url, post_id=pid, status="confirmed", **base)
            else:
                code = await self._detect_error(page, ("rejected", "rate_limited", "upload_failed", "platform_limit"))
                if code or await self._any_visible(page, self._list("captcha_selectors", GENERIC_CAPTCHA)):
                    return self._after_error(code or "captcha", base, page)
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(self.poll_s)
        if hit:
            return PublishResult(ok=False, url=None, status="unconfirmed", error_code="confirmed_without_url",
                                 error=f"{self.platform}: {refusal_text('confirmed_without_url')}", **base)
        return PublishResult(ok=False, url=None, status="unconfirmed", error_code="no_success_marker",
                             error=f"{self.platform}: {refusal_text('no_success_marker')}", **base)

    def _after_error(self, code: str | None, base: dict, page: Page) -> PublishResult | None:
        if not code:
            return None
        return PublishResult(ok=False, url=None, status="rejected" if code == "rejected" else "unconfirmed",
                             error_code=code, error=f"{self.platform}: {refusal_text(code)}",
                             needs_human=True, **base)

    async def verify_post(self, post_url: str) -> str:
        acct = self._acct()
        if not _is_http(post_url) or not (acct.verify_ok_selectors or acct.verify_gone_selectors):
            return "unknown"
        ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(post_url, wait_until="domcontentloaded")
            deadline = asyncio.get_event_loop().time() + self._confirm_timeout()
            while True:
                if await self._any_visible(page, acct.verify_gone_selectors):
                    return "gone"
                if await self._any_visible(page, acct.verify_ok_selectors):
                    return "ok"
                if asyncio.get_event_loop().time() >= deadline:
                    return "unknown"
                await asyncio.sleep(self.poll_s)
        except Exception:
            return "unknown"
        finally:
            with contextlib.suppress(Exception):
                await page.close()

    async def feed_has_post(self, snippet: str) -> bool | None:
        acct = self._acct()
        snippet = _nospace(snippet)[:60]
        if not (acct.feed_url and acct.feed_item_selector and snippet):
            return None
        ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(acct.feed_url, wait_until="domcontentloaded")
            await asyncio.sleep(self.settle_s)
            texts = await page.eval_on_selector_all(
                acct.feed_item_selector, "els => els.map(e => e.innerText || e.textContent || '')")
            return any(snippet in _nospace(str(t)) for t in texts)
        except Exception:
            return None
        finally:
            with contextlib.suppress(Exception):
                await page.close()

    async def preflight(self, *, open_page=None) -> PreflightResult:
        try:
            page = await (open_page() if open_page else self._open())
        except Exception as e:
            return PreflightResult(self.platform, False, "page_unavailable",
                                   f"{refusal_text('page_unavailable')} ({type(e).__name__})",
                                   account_label=self._acct().label())
        return await self.check_page(page)


class TikTokPublisher(Publisher):
    platform = "tiktok"
    upload_url = "https://www.tiktok.com/tiktokstudio/upload"
    login_url_markers = ["/login"]
    caption_selectors = [
        "div[contenteditable='true'][data-placeholder*='каптион' i]",
        "div[contenteditable='true']",
        "textarea",
    ]
    publish_button_selectors = [
        "button:has-text('Post')",
        "button:has-text('Опубликовать')",
    ]


class YouTubeShortsPublisher(Publisher):
    platform = "yt_shorts"
    upload_url = "https://www.youtube.com/upload"
    extra_hosts = ["studio.youtube.com", "www.youtube.com"]
    login_url_markers = ["accounts.google.com", "/signin"]
    caption_selectors = [
        "ytcp-social-suggestions-textbox div[contenteditable='true']",
        "div[contenteditable='true']",
    ]
    publish_button_selectors = [
        "ytcp-button#done-button button",
        "button:has-text('Опубликовать')",
        "button:has-text('Publish')",
    ]


class InstagramReelsPublisher(Publisher):
    platform = "ig_reels"
    upload_url = "https://www.instagram.com/"
    file_input_selectors = ["input[type='file'][accept*='video']"]
    caption_selectors = ["textarea[aria-label*='caption' i]", "textarea"]
    publish_button_selectors = ["button:has-text('Share')", "div[role='button']:has-text('Share')"]


class VKPublisher(Publisher):
    platform = "vk_clips"
    upload_url = "https://vk.com/clips"
    caption_selectors = ["textarea[name='caption']", "textarea"]
    publish_button_selectors = [
        "button:has-text('Опубликовать')",
        "button:has-text('Публиковать')",
        "button:has-text('Post')",
    ]


class LikeePublisher(Publisher):
    platform = "likee"
    upload_url = "https://likee.video/upload"
    caption_selectors = ["textarea", "div[contenteditable='true']"]
    publish_button_selectors = ["button:has-text('Publish')", "button:has-text('Опубликовать')"]


ALL_PUBLISHERS: list[type[Publisher]] = [
    TikTokPublisher,
    YouTubeShortsPublisher,
    InstagramReelsPublisher,
    VKPublisher,
    LikeePublisher,
]


def _fail_result(platform: str, code: str, acct: PlatformAccount | None, *, detail: str = "",
                 status: str = "failed", retry_after: float | None = None) -> PublishResult:
    text = refusal_text(code)
    return PublishResult(
        platform=platform, ok=False, status=status, clicked=False, error_code=code, retry_after=retry_after,
        error=f"{platform}: {text}" + (f" ({detail})" if detail else ""),
        account_label=acct.label() if acct is not None else None,
        needs_human=code in HUMAN_STOP_CODES or code in ("profile_missing", "accounts_config_invalid"))


async def publish_everywhere(
    video_path: Path,
    caption: str,
    *,
    skip_platforms: set[str] | None = None,
    gate: PublishGate | None = None,
    accounts: AccountsConfig | None = None,
    project_label: str | int | None = None,
    now: float | None = None,
) -> list[PublishResult]:
    """Проходим по платформам; профиль MoreLogin берётся по площадке из конфига аккаунтов.

    skip_platforms — не публиковать (уже есть итог). gate — журнал публикаций: claim до работы,
    before_click до клика, record сразу после каждой платформы (падение на следующей не теряет итог).
    Конфига аккаунтов нет — старый режим: один профиль MORELOGIN_PROFILE_ID на все платформы.
    Лимит в сутки и окно публикации проверяются ДО claim: перенос (status=skipped, retry_after) ничего не жжёт.
    """
    skip = skip_platforms or set()
    results: list[PublishResult] = []
    try:
        cfg = accounts if accounts is not None else pa.load_accounts()
    except AccountsConfigError as e:
        logger.error("publish: конфиг аккаунтов неверен ({})", e)
        for cls in ALL_PUBLISHERS:
            if cls.platform in skip:
                continue
            res = _fail_result(cls.platform, "accounts_config_invalid", None, detail=str(e))
            if gate is not None:
                await gate.record(res)
            results.append(res)
        return results

    async with contextlib.AsyncExitStack() as stack:
        browsers: dict[str | None, object] = {}
        broken: dict[str | None, MoreLoginError] = {}

        async def _browser_for(pid: str | None):
            if pid in browsers:
                return browsers[pid]
            if pid in broken:
                raise broken[pid]
            try:
                b = await stack.enter_async_context(morelogin_browser(pid) if pid else morelogin_browser())
            except MoreLoginError as e:
                broken[pid] = e
                raise
            browsers[pid] = b
            return b

        async def _stop(res: PublishResult) -> None:
            if gate is not None:
                await gate.record(res)
            results.append(res)

        for cls in ALL_PUBLISHERS:
            platform = cls.platform
            if platform in skip:
                logger.info("skipping {} — уже опубликовано", platform)
                continue
            acct = cfg.for_platform(platform)
            if not acct.enabled:
                logger.info("skipping {} — площадка выключена в конфиге аккаунтов", platform)
                continue
            win = pa.window_decision(acct.window_hours, now)
            if not win.allowed:
                await _stop(_fail_result(platform, "outside_window", acct, status="skipped",
                                         retry_after=win.retry_after))
                continue
            if gate is not None and acct.max_per_day:
                lim = pa.daily_limit_decision(await gate.recent_clicks(platform), acct.max_per_day, now)
                if not lim.allowed:
                    logger.info("skipping {} — {}", platform, lim.text)
                    await _stop(_fail_result(platform, "daily_limit", acct, status="skipped",
                                             detail=f"{lim.used} из {lim.limit}", retry_after=lim.retry_after))
                    continue

            pid = pa.select_profile(platform, cfg) if cfg.configured else None
            if cfg.configured and not pid:
                await _stop(_fail_result(platform, "profile_missing", acct))
                continue
            try:
                browser = await _browser_for(pid)
            except MoreLoginError as e:
                await _stop(_fail_result(platform, e.code if e.code in pa.REFUSALS else "profile_unavailable",
                                         acct, detail=str(e)[:200]))
                continue

            pub = cls(browser)
            pub.account = acct
            pub.project_label = project_label
            if gate is not None and not await gate.claim(platform):
                logger.info("skipping {} — платформа занята или ждёт человека (журнал публикаций)", platform)
                continue
            logger.info("publishing → {} ({})", platform, acct.label())

            async def _bc(_p=platform):
                return await gate.before_click(_p)

            res = await pub.publish(video_path, caption, before_click=_bc if gate is not None else None)
            if gate is not None:
                await gate.record(res)
            results.append(res)
            await asyncio.sleep(cfg.pause_s + (random.uniform(0, cfg.jitter_s) if cfg.jitter_s else 0.0))
    return results


async def preflight_everywhere(*, platforms: list[str] | None = None,
                               accounts: AccountsConfig | None = None) -> list[PreflightResult]:
    try:
        cfg = accounts if accounts is not None else pa.load_accounts()
    except AccountsConfigError as e:
        return [PreflightResult(c.platform, False, "accounts_config_invalid",
                                f"{refusal_text('accounts_config_invalid')} {e}")
                for c in ALL_PUBLISHERS if platforms is None or c.platform in platforms]
    out: list[PreflightResult] = []
    async with contextlib.AsyncExitStack() as stack:
        browsers: dict[str | None, object] = {}
        broken: dict[str | None, MoreLoginError] = {}
        for cls in ALL_PUBLISHERS:
            if platforms is not None and cls.platform not in platforms:
                continue
            acct = cfg.for_platform(cls.platform)
            pid = pa.select_profile(cls.platform, cfg) if cfg.configured else None
            if cfg.configured and not pid:
                out.append(PreflightResult(cls.platform, False, "profile_missing", refusal_text("profile_missing"),
                                           account_label=acct.label()))
                continue
            try:
                if pid in broken:
                    raise broken[pid]
                if pid not in browsers:
                    browsers[pid] = await stack.enter_async_context(
                        morelogin_browser(pid) if pid else morelogin_browser())
            except MoreLoginError as e:
                broken[pid] = e
                code = e.code if e.code in pa.REFUSALS else "profile_unavailable"
                out.append(PreflightResult(cls.platform, False, code, refusal_text(code), account_label=acct.label()))
                continue
            pub = cls(browsers[pid])
            pub.account = acct
            out.append(await pub.preflight())
    return out


async def _recon(platform: str) -> None:
    from urllib.parse import urlparse
    cls = next((c for c in ALL_PUBLISHERS if c.platform == platform), None)
    if cls is None:
        print("unknown platform. choose one of:", ", ".join(c.platform for c in ALL_PUBLISHERS))
        sys.exit(1)
    async with morelogin_browser() as browser:
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto(cls.upload_url, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        info = await page.evaluate(
            """() => {
                const q = sel => Array.from(document.querySelectorAll(sel)).slice(0, 12)
                    .map(el => ({
                        tag: el.tagName, id: el.id,
                        cls: el.className && el.className.toString().slice(0, 120),
                        ariaLabel: el.getAttribute('aria-label'),
                        placeholder: el.getAttribute('placeholder'),
                        text: (el.innerText || '').slice(0, 80),
                    }));
                return {
                    fileInputs: q("input[type='file']"),
                    textareas: q('textarea'),
                    contenteditables: q("[contenteditable='true']"),
                    buttons: q('button'),
                };
            }"""
        )
        for k, v in info.items():
            logger.info("--- {} ({}) ---", k, len(v))
            for i, x in enumerate(v):
                logger.info("  [{}] {}", i, x)
        _host = urlparse(page.url).netloc
        logger.info("done recon for {}", _host)


def _cli() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "preflight":
        only = sys.argv[2:] or None
        for r in asyncio.run(preflight_everywhere(platforms=only)):
            print(f"{r.platform}: {'OK' if r.ok else r.code} - {r.account_label} {r.message}")
        return
    if len(sys.argv) < 3 or sys.argv[1] != "recon":
        print("usage: python -m app.bots.publishers recon <tiktok|yt_shorts|ig_reels|vk_clips|likee>\n"
              "       python -m app.bots.publishers preflight [platform ...]")
        sys.exit(1)
    asyncio.run(_recon(sys.argv[2]))


if __name__ == "__main__":
    _cli()
