from pathlib import Path

from PIL import Image

from app.services.image_ref_lock import (
    classify_attached_ref,
    crop_identity_refs,
    prepare_refs_and_prompt,
    strip_leading_attach_lock,
    with_attached_ref_lock,
)


def _wide_sheet(path: Path) -> Path:
    im = Image.new("RGB", (2048, 1152), (240, 240, 240))
    for x in range(30, 280):
        for y in range(40, 1110):
            im.putpixel((x, y), (200, 20, 20))
    im.save(path)
    return path


def test_classify_character_and_style(tmp_path: Path) -> None:
    char = tmp_path / "characters"
    char.mkdir()
    sheet = char / "c02.png"
    sheet.write_bytes(b"x")
    style = tmp_path / "refs" / "frame_007_other_ab12.png"
    style.parent.mkdir()
    style.write_bytes(b"x")
    scene = tmp_path / "scenes" / "frame_003_deadbeef.png"
    scene.parent.mkdir()
    scene.write_bytes(b"x")

    assert classify_attached_ref(sheet) == ("identity", "c02")
    assert classify_attached_ref(style) == ("other", None)
    assert classify_attached_ref(scene) == ("parent_still", None)


def test_lock_two_sheets_maps_image_n(tmp_path: Path) -> None:
    a = tmp_path / "characters" / "c02.png"
    b = tmp_path / "characters" / "c03.png"
    a.parent.mkdir()
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    locked = with_attached_ref_lock("сцена слева чиновник", [a, b], sheet_ids=["c02", "c03"])
    assert locked.startswith("Image 1 is the identity reference of c02")
    assert "Image 2 is the identity reference of c03" in locked
    assert "TWO different people" in locked
    assert locked.endswith("сцена слева чиновник")


def test_lock_style_ref_forbids_cloning_people(tmp_path: Path) -> None:
    mood = tmp_path / "refs" / "frame_007_other_aa.png"
    mood.parent.mkdir()
    mood.write_bytes(b"x")
    char = tmp_path / "characters" / "c02.png"
    char.parent.mkdir(exist_ok=True)
    char.write_bytes(b"x")
    locked = with_attached_ref_lock(
        "Reference: official c02 at the desk",
        [mood, char],
        sheet_ids=["c02"],
    )
    assert "Image 1 is style" in locked or "style / mood" in locked
    assert "Do not clone faces" in locked
    assert "Image 2 is the identity reference of c02" in locked
    assert "ignore that person" in locked


def test_prepare_crops_sheet_and_keeps_single_body(tmp_path: Path) -> None:
    chars = tmp_path / "characters"
    chars.mkdir()
    sheet = _wide_sheet(chars / "c02.png")
    cache = tmp_path / "crops"
    prompt, refs = prepare_refs_and_prompt(
        "official at the desk",
        [sheet],
        sheet_ids=["c02"],
        cache_dir=cache,
    )
    assert refs[0] != sheet
    assert refs[0].name == "c02_front.png"
    assert "identity reference of c02" in prompt
    assert "Exactly one living body of c02" in prompt
    assert "HARD CAST LOCK:" in prompt


def test_strip_replaces_wrong_image_n_lock() -> None:
    raw = (
        "Image 1 is the identity reference of c02 — old.\n\n"
        "HARD CAST LOCK: leftover\n\n"
        "сцена"
    )
    assert strip_leading_attach_lock(raw) == "сцена"
