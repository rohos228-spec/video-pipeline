"""ChatGPT composer: fuzzy match for deduplicated attachment file names."""

from app.bots.chatgpt import attachment_name_visible_in_text


def test_exact_filename_visible() -> None:
    text = "frame_001_d04262f4.png\nframe_002_beb0c2be.png"
    assert attachment_name_visible_in_text("frame_001_d04262f4.png", text)


def test_dedup_suffix_visible() -> None:
    text = "frame_001_d04262f4(6).png"
    assert attachment_name_visible_in_text("frame_001_d04262f4.png", text)


def test_aria_label_group_visible() -> None:
    text = 'role="group" aria-label="frame_005_173c155f(3).png"'
    assert attachment_name_visible_in_text("frame_005_173c155f.png", text)


def test_missing_file_not_visible() -> None:
    assert not attachment_name_visible_in_text(
        "frame_099_missing.png", "frame_001_d04262f4.png"
    )
