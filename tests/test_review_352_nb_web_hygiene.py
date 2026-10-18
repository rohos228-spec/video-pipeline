"""Неблокирующее: BOM, пустой topbar, локальный шрифт вместо Google Fonts."""

from pathlib import Path


def test_web_hygiene_point_fixes() -> None:
    brand = Path("web/src/components/brand-scrub.tsx").read_bytes()
    assert not brand.startswith(b"\xef\xbb\xbf")
    assert brand.startswith(b'"use client"')
    assert not Path("web/src/topbar.tsx").exists()
    home = Path("web/src/app/home/page.tsx").read_text(encoding="utf-8")
    assert "next/font/google" not in home
    assert 'fontClass="font-sans"' in home
    css = Path("web/src/app/globals.css").read_text(encoding="utf-8")
    assert '@font-face { font-family: "Rubik"' in css
