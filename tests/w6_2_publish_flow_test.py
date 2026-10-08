"""W6-2: сценарий публикации на ФЕЙКОВОЙ странице (локальный Chromium через Playwright, страницы подменяются
маршрутом https://fake.test/**, сеть не используется) и publish_everywhere на заменителях (класс доказательств U).

Это синтетический HTML, не интерфейс реальных площадок: селекторы и признаки успеха площадок не проверены.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright

from app.bots import morelogin
from app.bots import publishers as pubmod
from app.bots.publishers import Publisher, PublishGate, PublishResult
from app.services import publish_accounts as pa
from tests._stand_workdir import make_workdir, remove_workdir

SITE = "https://fake.test"

# Общий каркас: файл -> «обработка» 300 мс -> кнопка активна; клик -> /post/777 и тост.
_BASE = """<!doctype html><meta charset=utf-8><title>fake</title><body>
<input type=file id=f>
<div id=cap contenteditable=true style="min-height:30px;border:1px solid #000">&nbsp;</div>
<div class=progress id=prog style="display:none">Загрузка...</div>
<button id=pub disabled>Опубликовать</button>
%(extra)s
<script>
window.__clicks = 0; window.__uploaded = 0;
const f = document.getElementById('f'), prog = document.getElementById('prog'), pub = document.getElementById('pub');
f.addEventListener('change', () => {
  window.__uploaded = 1; prog.style.display = 'block';
  %(on_file)s
});
pub.addEventListener('click', () => { window.__clicks++; %(on_click)s });
%(script)s
</script></body>"""

_OK_ON_FILE = "setTimeout(() => { prog.style.display = 'none'; pub.disabled = false; }, %d);"
_OK_ON_CLICK = ("history.pushState({}, '', '/post/777');"
                "const t = document.createElement('div'); t.className = 'toast'; t.textContent = 'Опубликовано';"
                "document.body.appendChild(t);")


def page_html(*, extra="", on_file=None, on_click=_OK_ON_CLICK, script="", delay=300, no_file_input=False):
    html = _BASE % {"extra": extra, "on_file": on_file if on_file is not None else _OK_ON_FILE % delay,
                    "on_click": on_click, "script": script}
    return html.replace("<input type=file id=f>", "") if no_file_input else html


class FakePub(Publisher):
    platform = "tiktok"
    upload_url = f"{SITE}/upload"
    file_input_selectors = ["#f"]
    caption_selectors = ["#cap"]
    publish_button_selectors = ["#pub"]
    settle_s = 0.0
    poll_s = 0.05
    find_timeout_ms = 400
    confirm_timeout_s = 0.6
    processing_timeout_s = 3.0
    html = ""
    opened = 0
    last_page = None

    async def _open(self):
        type(self).opened += 1
        ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        html = type(self).html

        async def handler(route):
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

        await ctx.route(f"{SITE}/**", handler)
        page = await ctx.new_page()
        await page.goto(self._upload_url(), wait_until="domcontentloaded")
        type(self).last_page = page
        return page


@pytest.fixture
def workdir():
    d = make_workdir("w62_flow_")
    (d / "v.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42synthetic")
    yield d
    remove_workdir(d)


@pytest.fixture
async def browser():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            await b.close()


def make(browser, workdir, html, **acct_kw) -> FakePub:
    class P(FakePub):
        pass

    P.html, P.opened, P.last_page = html, 0, None
    pub = P(browser)
    pub.account = pa.PlatformAccount(platform="tiktok", account_label="тест-аккаунт", **acct_kw)
    pub.project_label = "p77"
    pub.data_dir = workdir
    return pub


async def clicks(pub) -> int:
    return await type(pub).last_page.evaluate("window.__clicks")


class Counter:
    def __init__(self, allow=True):
        self.n, self.allow = 0, allow

    async def __call__(self):
        self.n += 1
        return self.allow


MARKERS = dict(success_selectors=[".toast"], post_url_regex=r"https://fake\.test/post/(?P<id>\d+)")


# ── критерии задачи ──────────────────────────────────────────────────────────────


async def test_success_by_configured_markers_is_confirmed_with_post_url_and_screenshot(browser, workdir):
    pub = make(browser, workdir, page_html(), **MARKERS)
    bc = Counter()
    res = await pub.publish(workdir / "v.mp4", "Привет, мир", before_click=bc)
    assert res.status == "confirmed" and res.ok and res.clicked
    assert res.url == f"{SITE}/post/777" and res.post_id == "777"
    assert res.account_label == "тест-аккаунт" and bc.n == 1 and await clicks(pub) == 1
    assert res.screenshot == "publications/p77/tiktok.png"
    shot = workdir / res.screenshot
    assert shot.is_file() and shot.stat().st_size > 100


async def test_without_markers_unconfirmed_even_though_clicked(browser, workdir):
    pub = make(browser, workdir, page_html())  # признаки успеха не настроены
    res = await pub.publish(workdir / "v.mp4", "Привет")
    assert res.status == "unconfirmed" and not res.ok and res.clicked
    assert res.error_code == "no_success_marker" and res.url is None
    assert res.screenshot and (workdir / res.screenshot).is_file()


async def test_caption_not_filled_refuses_before_click(browser, workdir):
    # страница стирает введённый текст: поле «заполнено» только формально
    html = page_html(script="document.getElementById('cap').addEventListener('input', e => { e.target.textContent = ''; });")
    pub = make(browser, workdir, html, **MARKERS)
    bc = Counter()
    res = await pub.publish(workdir / "v.mp4", "Текст подписи", before_click=bc)
    assert res.status == "failed" and res.error_code == "caption_not_filled" and not res.clicked
    assert bc.n == 0 and await clicks(pub) == 0
    assert "подпись" in res.error.lower()


async def test_captcha_refuses_with_code_before_upload(browser, workdir):
    html = page_html(extra='<div id="captcha-box" style="width:200px;height:50px">Подтвердите, что вы не робот</div>')
    pub = make(browser, workdir, html, **MARKERS)
    bc = Counter()
    res = await pub.publish(workdir / "v.mp4", "x", before_click=bc)
    assert res.error_code == "captcha" and res.status == "failed" and not res.clicked and res.needs_human
    assert "капч" in res.error.lower() and bc.n == 0
    assert await type(pub).last_page.evaluate("window.__uploaded") == 0  # файл даже не выбирался


async def test_configured_captcha_selector(browser, workdir):
    html = page_html(extra='<div class="x-challenge">проверка</div>')
    pub = make(browser, workdir, html, captcha_selectors=[".x-challenge"])
    assert (await pub.publish(workdir / "v.mp4", "x")).error_code == "captcha"


async def test_two_factor_and_not_logged_in(browser, workdir):
    p2 = make(browser, workdir, page_html(extra='<input autocomplete="one-time-code">'))
    r2 = await p2.publish(workdir / "v.mp4", "x")
    assert r2.error_code == "two_factor" and r2.needs_human and not r2.clicked
    p3 = make(browser, workdir, page_html(extra='<input type="password">'))
    r3 = await p3.publish(workdir / "v.mp4", "x")
    assert r3.error_code == "not_logged_in" and r3.needs_human and "вход" in r3.error.lower()


async def test_logged_in_selector_absent_means_not_logged_in(browser, workdir):
    pub = make(browser, workdir, page_html(), logged_in_selectors=["#avatar"])
    pub.login_wait_s = 0.3
    pre = await pub.preflight()
    assert not pre.ok and pre.code == "not_logged_in"
    pub2 = make(browser, workdir, page_html(extra='<div id="avatar">я</div>'), logged_in_selectors=["#avatar"])
    pre2 = await pub2.preflight()
    assert pre2.ok and pre2.account_label == "тест-аккаунт" and pre2.page_url == f"{SITE}/upload"


async def test_preflight_does_not_upload_or_click(browser, workdir):
    pub = make(browser, workdir, page_html())
    pre = await pub.preflight()
    assert pre.ok
    page = type(pub).last_page
    assert await page.evaluate("window.__uploaded") == 0 and await page.evaluate("window.__clicks") == 0


async def test_waits_for_processing_then_enables_button(browser, workdir):
    pub = make(browser, workdir, page_html(delay=900), **MARKERS)
    t0 = time.monotonic()
    res = await pub.publish(workdir / "v.mp4", "x")
    assert res.status == "confirmed" and time.monotonic() - t0 >= 0.8  # клик не раньше конца «обработки»


async def test_processing_timeout_never_clicks(browser, workdir):
    pub = make(browser, workdir, page_html(on_file="", extra=""), processing_timeout_s=0.6)
    bc = Counter()
    res = await pub.publish(workdir / "v.mp4", "x", before_click=bc)
    assert res.error_code == "processing_timeout" and not res.clicked and bc.n == 0 and await clicks(pub) == 0


async def test_configured_busy_selector_blocks_until_gone(browser, workdir):
    # кнопка уже активна, но индикатор занятости виден -> ждём (таймаут), не кликаем
    html = page_html(on_file="pub.disabled = false;", extra="")
    pub = make(browser, workdir, html, processing_timeout_s=0.6, processing_busy_selectors=[".progress"])
    res = await pub.publish(workdir / "v.mp4", "x")
    assert res.error_code == "processing_timeout" and not res.clicked


async def test_upload_failed_marker_refuses_before_click(browser, workdir):
    html = page_html(on_file="document.body.insertAdjacentHTML('beforeend', '<p>Не удалось загрузить видео</p>');")
    pub = make(browser, workdir, html)
    res = await pub.publish(workdir / "v.mp4", "x")
    assert res.error_code == "upload_failed" and not res.clicked and await clicks(pub) == 0


async def test_missing_file_input_and_missing_button_are_selector_not_found(browser, workdir):
    r1 = await make(browser, workdir, page_html(no_file_input=True)).publish(workdir / "v.mp4", "x")
    assert r1.error_code == "selector_not_found" and not r1.clicked
    html = page_html().replace('<button id=pub disabled>Опубликовать</button>', "")
    r2 = await make(browser, workdir, html, processing_timeout_s=0.4).publish(workdir / "v.mp4", "x")
    assert r2.error_code == "selector_not_found" and not r2.clicked


async def test_marker_without_post_url_is_unconfirmed(browser, workdir):
    # тост есть, адрес страницы не изменился и ссылки на пост нет -> не confirmed
    html = page_html(on_click="document.body.insertAdjacentHTML('beforeend', '<div class=toast>ok</div>');")
    pub = make(browser, workdir, html, success_selectors=[".toast"])
    res = await pub.publish(workdir / "v.mp4", "x")
    assert res.status == "unconfirmed" and res.error_code == "confirmed_without_url" and res.clicked and not res.ok


async def test_post_link_selector_gives_url_and_url_marker_without_regex(browser, workdir):
    html = page_html(on_click="document.body.insertAdjacentHTML('beforeend', "
                              "'<div class=toast>ok</div><a id=lnk href=\"https://fake.test/p/55\">пост</a>');")
    pub = make(browser, workdir, html, success_selectors=[".toast"], post_link_selector="#lnk",
               post_url_regex=r"/p/(\d+)$")
    res = await pub.publish(workdir / "v.mp4", "x")
    assert res.status == "confirmed" and res.url == f"{SITE}/p/55" and res.post_id == "55"
    pub2 = make(browser, workdir, page_html(), success_url_markers=["/post/"])
    res2 = await pub2.publish(workdir / "v.mp4", "x")
    assert res2.status == "confirmed" and res2.url == f"{SITE}/post/777"


async def test_rejected_after_click(browser, workdir):
    html = page_html(on_click="document.body.insertAdjacentHTML('beforeend', '<p>Видео нарушает правила</p>');")
    pub = make(browser, workdir, html, **MARKERS)
    res = await pub.publish(workdir / "v.mp4", "x")
    assert res.status == "rejected" and res.error_code == "rejected" and res.clicked and res.needs_human


async def test_caption_over_limit_refused_before_opening_page(browser, workdir):
    pub = make(browser, workdir, page_html(), caption_limit=5)
    res = await pub.publish(workdir / "v.mp4", "слишком длинная подпись")
    assert res.error_code == "platform_limit" and not res.clicked and type(pub).opened == 0


async def test_hashtags_from_config_are_typed(browser, workdir):
    pub = make(browser, workdir, page_html(), hashtags=["#ai", "видео"], **MARKERS)
    res = await pub.publish(workdir / "v.mp4", "Текст")
    assert res.ok
    # подпись на странице после клика: основной текст + хэштеги
    text = await type(pub).last_page.evaluate("document.getElementById('cap').innerText")
    assert "Текст" in text and "#ai" in text and "#видео" in text


async def test_title_field_filled_when_configured(browser, workdir):
    html = page_html(extra='<input id=ttl>')
    pub = make(browser, workdir, html, title_selectors=["#ttl"], title_limit=12, **MARKERS)
    res = await pub.publish(workdir / "v.mp4", "Длинный заголовок ролика\nописание")
    assert res.ok
    assert await type(pub).last_page.evaluate("document.getElementById('ttl').value") == "Длинный заг…"


async def test_screenshot_name_has_no_secret_parts(browser, workdir):
    pub = make(browser, workdir, page_html(), **MARKERS)
    pub.project_label = "../token=abc123/секрет"
    res = await pub.publish(workdir / "v.mp4", "x")
    assert res.screenshot.startswith("publications/") and ".." not in res.screenshot and "=" not in res.screenshot
    assert (workdir / res.screenshot).is_file()


async def test_verify_post_and_feed_check(browser, workdir):
    ok_page = page_html(extra='<div class="video-ok">пост</div>')
    pub = make(browser, workdir, ok_page, verify_ok_selectors=[".video-ok"], verify_gone_selectors=[".gone"],
               feed_url=f"{SITE}/feed", feed_item_selector=".item")
    await pub._open()  # создаёт контекст с маршрутом фейкового сайта
    assert await pub.verify_post(f"{SITE}/post/1") == "ok"
    gone = make(browser, workdir, page_html(extra='<div class="gone">снято модерацией</div>'),
                verify_ok_selectors=[".video-ok"], verify_gone_selectors=[".gone"])
    await gone._open()
    assert await gone.verify_post(f"{SITE}/post/1") == "gone"
    unknown = make(browser, workdir, page_html())  # признаки не настроены
    await unknown._open()
    assert await unknown.verify_post(f"{SITE}/post/1") == "unknown"
    feed = make(browser, workdir, page_html(extra='<div class="item">Привет, мир #ai</div>'),
                feed_url=f"{SITE}/feed", feed_item_selector=".item")
    await feed._open()
    assert await feed.feed_has_post("Привет, мир") is True
    assert await feed.feed_has_post("Совсем другой текст") is False
    assert await unknown.feed_has_post("Привет") is None  # сверка не настроена


async def test_empty_account_still_unconfirmed_and_caption_checked(browser, workdir):
    """publish_everywhere всегда задаёт аккаунт, даже пустой: поведение по умолчанию безопасное."""
    pub = make(browser, workdir, page_html())
    pub.account = pa.PlatformAccount(platform="tiktok")
    res = await pub.publish(workdir / "v.mp4", "x")
    assert res.status == "unconfirmed" and res.clicked


# ── publish_everywhere: профиль по площадке, лимиты, окно (заменители, без браузера) ──


PLATFORMS = ["tiktok", "yt_shorts", "ig_reels", "vk_clips", "likee"]


class SpyGate(PublishGate):
    def __init__(self, clicks_by_platform=None):
        self.claimed, self.records, self.clicks_by_platform = [], [], clicks_by_platform or {}

    async def claim(self, platform):
        self.claimed.append(platform)
        return True

    async def before_click(self, platform):
        return True

    async def record(self, result):
        self.records.append(result)

    async def recent_clicks(self, platform):
        return self.clicks_by_platform.get(platform)


def _fake_world(monkeypatch):
    opened, published = [], []

    def make_cls(platform):
        class F(Publisher):
            pass

        F.platform = platform

        async def publish(self, video_path, caption, *, before_click=None):
            published.append((platform, self.browser, self.account.label(), self.project_label))
            if before_click is not None:
                await before_click()
            return PublishResult(platform, True, url=f"https://{platform}/p", status="confirmed", clicked=True)

        F.publish = publish
        return F

    @asynccontextmanager
    async def fake_browser(pid=None, **k):
        if pid == "broken-0000":
            raise morelogin.MoreLoginError("профиль не поднялся", "profile_unavailable")
        opened.append(pid)
        yield f"browser[{pid}]"

    async def nosleep(*_):
        return None

    monkeypatch.setattr(pubmod, "ALL_PUBLISHERS", [make_cls(p) for p in PLATFORMS])
    monkeypatch.setattr(pubmod, "morelogin_browser", fake_browser)
    monkeypatch.setattr(pubmod.asyncio, "sleep", nosleep)
    return opened, published


async def test_each_platform_gets_its_own_profile(monkeypatch, workdir):
    opened, published = _fake_world(monkeypatch)
    cfg = pa.parse_config({"platforms": {
        "tiktok": {"profile_id": "prof-A-0001", "account_label": "A"},
        "yt_shorts": {"profile_id": "prof-A-0001"},
        "ig_reels": {"profile_id": "prof-B-0002"},
        "vk_clips": {"profile_id": "prof-B-0002"},
        "likee": {"profile_id": "prof-C-0003"}}})
    res = await pubmod.publish_everywhere(workdir / "v.mp4", "x", accounts=cfg, project_label=5)
    assert [r.status for r in res] == ["confirmed"] * 5
    assert opened == ["prof-A-0001", "prof-B-0002", "prof-C-0003"]  # профиль на площадку, не больше одного раза
    by = {p[0]: p[1] for p in published}
    assert by["tiktok"] == by["yt_shorts"] == "browser[prof-A-0001]" and by["ig_reels"] == "browser[prof-B-0002]"
    assert all(p[3] == 5 for p in published)


async def test_missing_and_unavailable_profile_codes(monkeypatch, workdir):
    opened, published = _fake_world(monkeypatch)
    cfg = pa.parse_config({"platforms": {"tiktok": {"profile_id": "prof-A-0001"},
                                         "yt_shorts": {"profile_id": "broken-0000"},
                                         "ig_reels": {"profile_id": "broken-0000"}}})
    gate = SpyGate()
    res = await pubmod.publish_everywhere(workdir / "v.mp4", "x", accounts=cfg, gate=gate)
    codes = {r.platform: r.error_code for r in res}
    assert codes["yt_shorts"] == "profile_unavailable" and codes["ig_reels"] == "profile_unavailable"
    assert codes["vk_clips"] == "profile_missing" and codes["likee"] == "profile_missing"
    assert [p[0] for p in published] == ["tiktok"]  # чужой профиль никому не подставлен
    assert "yt_shorts" not in gate.claimed and "vk_clips" not in gate.claimed  # отказ до claim
    assert all(not r.clicked for r in res if r.error_code)
    assert next(r for r in res if r.platform == "vk_clips").needs_human


async def test_daily_limit_skips_before_claim_with_retry_after(monkeypatch, workdir):
    _, published = _fake_world(monkeypatch)
    now = 2_000_000.0
    cfg = pa.parse_config({"platforms": {p: {"profile_id": "prof-A-0001", "max_per_day": 1} for p in PLATFORMS}})
    gate = SpyGate({"tiktok": [now - 600], "yt_shorts": [now - pa.DAY_S - 1], "ig_reels": []})
    res = await pubmod.publish_everywhere(workdir / "v.mp4", "x", accounts=cfg, gate=gate, now=now)
    by = {r.platform: r for r in res}
    assert by["tiktok"].error_code == "daily_limit" and by["tiktok"].status == "skipped"
    assert by["tiktok"].retry_after == pytest.approx(now - 600 + pa.DAY_S) and not by["tiktok"].clicked
    assert "tiktok" not in gate.claimed and "tiktok" not in [p[0] for p in published]
    assert by["yt_shorts"].status == "confirmed" and by["ig_reels"].status == "confirmed"
    assert by["vk_clips"].status == "confirmed"  # журнал не дал данных -> лимит не проверяется
    assert any(r.platform == "tiktok" for r in gate.records)  # перенос записан в журнал, не потерян


async def test_publish_window_postpones(monkeypatch, workdir):
    _, published = _fake_world(monkeypatch)
    noon = time.mktime((2026, 10, 8, 12, 0, 0, 0, 0, -1))
    cfg = pa.parse_config({"platforms": {"tiktok": {"profile_id": "prof-A-0001", "window_hours": [13, 22]},
                                         "yt_shorts": {"profile_id": "prof-A-0001", "window_hours": [10, 22]}}})
    res = await pubmod.publish_everywhere(workdir / "v.mp4", "x", accounts=cfg, now=noon)
    by = {r.platform: r for r in res}
    assert by["tiktok"].error_code == "outside_window" and by["tiktok"].retry_after == pytest.approx(noon + 3600)
    assert by["yt_shorts"].status == "confirmed"


async def test_invalid_config_blocks_everything_without_leaking(monkeypatch, workdir):
    _, published = _fake_world(monkeypatch)
    monkeypatch.setenv(pa.ENV_JSON, '{"platforms": {"tiktok": {"profile_id": "p", "password": "TopSecret!"}}}')
    monkeypatch.setenv("DATA_DIR", str(workdir))
    res = await pubmod.publish_everywhere(workdir / "v.mp4", "x")
    assert {r.error_code for r in res} == {"accounts_config_invalid"} and published == []
    assert all("TopSecret" not in (r.error or "") for r in res)


async def test_legacy_mode_without_config_uses_single_default_profile(monkeypatch, workdir):
    opened, published = _fake_world(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(workdir))
    monkeypatch.delenv(pa.ENV_JSON, raising=False)
    res = await pubmod.publish_everywhere(workdir / "v.mp4", "x")
    assert len(res) == 5 and opened == [None]  # morelogin_browser() без id: профиль берётся из MORELOGIN_PROFILE_ID


async def test_preflight_everywhere_uses_fake_pages_and_reports_codes(monkeypatch, workdir):
    opened = []

    @asynccontextmanager
    async def fake_browser(pid=None, **k):
        opened.append(pid)
        yield "B"

    class _Pub(Publisher):
        platform = "tiktok"

        async def preflight(self, *, open_page=None):
            return pubmod.PreflightResult("tiktok", False, "not_logged_in", pa.refusal_text("not_logged_in"))

    monkeypatch.setattr(pubmod, "ALL_PUBLISHERS", [_Pub])
    monkeypatch.setattr(pubmod, "morelogin_browser", fake_browser)
    cfg = pa.parse_config({"platforms": {"tiktok": {"profile_id": "prof-A-0001"}}})
    out = await pubmod.preflight_everywhere(accounts=cfg)
    assert [(r.platform, r.code, r.needs_human) for r in out] == [("tiktok", "not_logged_in", True)]
    assert opened == ["prof-A-0001"]
    cfg2 = pa.parse_config({"platforms": {"vk_clips": {"profile_id": "p"}}})
    out2 = await pubmod.preflight_everywhere(accounts=cfg2)
    assert out2[0].code == "profile_missing"
