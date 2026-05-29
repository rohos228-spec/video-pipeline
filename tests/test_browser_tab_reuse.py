"""Проверка сопоставления URL при reuse вкладок Chrome."""

from app.bots.browser import page_url_matches_target, url_base_for_reuse


def test_url_base_strips_query_and_normalizes_host() -> None:
    assert url_base_for_reuse(
        "https://www.outsee.io/image?model=nano-banana-2"
    ) == "https://outsee.io/image"


def test_page_matches_same_outsee_image_tab() -> None:
    target = "https://outsee.io/image?model=nano-banana-2"
    assert page_url_matches_target("https://www.outsee.io/image", target)
    assert page_url_matches_target(
        "https://outsee.io/image?model=other", target
    )


def test_page_does_not_match_different_outsee_section() -> None:
    target = "https://outsee.io/image?model=nano-banana-2"
    assert not page_url_matches_target("https://outsee.io/video", target)


def test_page_matches_chatgpt_conversation() -> None:
    target = "https://chatgpt.com/"
    assert page_url_matches_target("https://chatgpt.com/c/abc-123", target)
    assert page_url_matches_target("https://www.chatgpt.com/", target)
