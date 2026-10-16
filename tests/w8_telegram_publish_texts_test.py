from __future__ import annotations

import pytest

from app.telegram import publish_texts as pt
from app.web.routers import publications as pub_router

OLD_PHRASE = "сейчас ничего не публикуется"


@pytest.mark.parametrize("queued, enabled, must, must_not", [
    (True, True, "Поставлено в очередь", "выключена"),
    (False, False, "Автопубликация выключена настройкой", "Поставлено в очередь"),
    (False, True, "шаг публикации уже идёт", "выключена"),
])
def test_done_text_says_exactly_one_true_thing(queued, enabled, must, must_not):
    txt = pt.publish_approval_done_text(7, "tiktok", "yes", "telegram:1", queued=queued, publish_enabled=enabled)
    assert must in txt and must_not not in txt
    assert OLD_PHRASE not in txt and txt.startswith("Разрешено (telegram:1): tiktok, проект #7. Одноразово.")


def test_done_text_agrees_with_panel_message_on_each_case():
    cases = [(True, True), (False, False), (False, True)]
    for queued, enabled in cases:
        tg = pt.publish_approval_done_text(1, "vk_clips", "yes", "a", queued=queued, publish_enabled=enabled)
        panel = pub_router._approve_message(queued, enabled)
        assert ("выключен" in tg) == ("выключен" in panel)
        assert ("очередь" in tg) == ("очередь" in panel)


def test_disabled_text_does_not_claim_publication_is_pending_or_running():
    txt = pt.publish_approval_done_text(1, "tiktok", "yes", "a", queued=False, publish_enabled=False)
    assert "ничего не опубликовано" in txt and "пока её не включат" in txt
    assert "идёт" not in txt


def test_no_button_text_unchanged_in_meaning():
    txt = pt.publish_approval_done_text(1, "tiktok", "no", "a", queued=False, publish_enabled=False)
    assert "закрыта человеком" in txt and "Автоповтора не будет" in txt and OLD_PHRASE not in txt


def test_request_text_says_publication_is_never_automatic():
    txt = pt.publish_approval_request_text("tiktok", "a" * 64, "подпись")
    assert "никогда не запускается сама" in txt and "ничего не публикуется" not in txt
    assert "подпись" in txt and "aaaaaaaaaaaa" in txt


def test_no_module_text_uses_the_old_phrase():
    import inspect
    assert OLD_PHRASE not in inspect.getsource(pt)
