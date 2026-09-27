"""VO-родитель без STYLE-lock не должен уходить в генератор голым."""

from types import SimpleNamespace

from app.services.img_pr_style import (
    ensure_style_lock,
    prompt_has_style_lock,
    split_style_lock,
    style_lock_from_frames,
)


def _fr(
    number: int,
    *,
    role: str,
    prompt: str,
    uuid: str = "",
    leftover: bool = False,
) -> SimpleNamespace:
    uid = uuid or f"{number:024d}"
    cs: dict = {
        "role": role,
        "parent_uuid": uid,
    }
    if leftover:
        cs["leftover"] = True
    return SimpleNamespace(
        number=number,
        uuid=uid,
        image_prompt=prompt,
        attrs={"camera_subdivide": cs},
    )


_STYLE = (
    "STYLE: noir watercolour and ink illustration.\n"
    "Final style lock: archival noir watercolor.\n"
    "Negative: photorealism, glossy 3D."
)
_PARENT = f"Full-bleed frame: investigator office.\n\n{_STYLE}"
_CHILD = (
    "сцена с референса (кадр 7), крупность ДЕТАЛЬ "
    "Сейчас в кадре: архивист переворачивает страницу."
)


def test_style_lock_from_text_keeps_tail() -> None:
    scene, style = split_style_lock(_PARENT)
    assert "Full-bleed" in scene
    assert style.startswith("STYLE:")
    assert "Negative:" in style
    assert prompt_has_style_lock(_PARENT)
    assert not prompt_has_style_lock(_CHILD)


def test_ensure_style_lock_grafts_on_vo_parent_without_style() -> None:
    donor = _fr(1, role="vo_parent", prompt=_PARENT, uuid="a" * 24)
    orphan = _fr(9, role="vo_parent", prompt=_CHILD, uuid="b" * 24)
    out = ensure_style_lock(_CHILD, [donor, orphan], orphan)
    assert out.startswith("сцена с референса")
    assert "STYLE:" in out
    assert "Negative:" in out
    assert "investigator office" not in out


def test_ensure_style_lock_skips_shot_child() -> None:
    donor = _fr(1, role="vo_parent", prompt=_PARENT, uuid="a" * 24)
    child = _fr(2, role="shot", prompt=_CHILD, uuid="c" * 24)
    child.attrs["camera_subdivide"]["parent_uuid"] = donor.uuid
    out = ensure_style_lock(_CHILD, [donor, child], child)
    assert out == _CHILD


def test_style_lock_from_frames_skips_leftover() -> None:
    leftover = _fr(200, role="vo_parent", prompt=_PARENT, leftover=True)
    live = _fr(1, role="vo_parent", prompt=_PARENT)
    assert "STYLE:" in style_lock_from_frames([leftover, live])
