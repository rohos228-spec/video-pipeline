"""Ревью #352 r4 п.23: «Включить ноду» рендерится во встроенном меню.

Недозакрыто после 02c13e3b: кнопка стояла после return null и
createPortal старой студии, то есть не достигалась.
"""

from pathlib import Path

STUDIO = Path("web/src/components/studio/node-studio.tsx").read_text(encoding="utf-8")


def _live_menu(src: str) -> str:
    start = src.index("if (inlineEl)")
    end = src.index("const closeNow")
    assert start < end
    return src[start:end]


def test_enable_button_is_in_the_rendered_inline_menu() -> None:
    assert "if (!inlineTargetId) return null;" in STUDIO
    live = _live_menu(STUDIO)
    assert "Включить ноду" in live
    assert "onEnableNode" in live
    assert "nodeDisabled" in live
    dead = STUDIO[STUDIO.index("const closeNow") :]
    assert "Включить ноду" not in dead


def test_inline_menu_renders_panels_missing_from_the_old_sheet() -> None:
    live = _live_menu(STUDIO)
    for name in (
        "SfxPlanSettingsPanel",
        "SfxGenSettingsPanel",
        "GptTextPanel",
        "FramePromptsPanel",
        "CheckNodePromptPanel",
    ):
        assert name in live
