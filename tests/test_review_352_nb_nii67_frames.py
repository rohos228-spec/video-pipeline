"""Неблокирующее: правки кадров NII67 не затираются рефетчем карточки."""

from pathlib import Path


def test_frame_edits_use_their_own_dirty_flag() -> None:
    src = Path("web/src/components/canvas/nii67-card-panel.tsx").read_text(encoding="utf-8")
    assert "const framesDirtyRef = useRef(false);" in src
    assert "if (!framesDirtyRef.current && Array.isArray(cardQ.data.frames))" in src
    assert "onChange={onFramesChange}" in src
    assert "framesDirtyRef.current = true" in src
    assert "onChange={setFrames}" not in src
