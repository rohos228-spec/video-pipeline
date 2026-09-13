"""Dev-проверка: большой кадр доски подстраивается под формат картинки.

Не часть пайплайна. Нужен бэкенд на :8765 и сид
``scripts/dev_seed_montage_scene.py``.

    python3 scripts/dev_check_montage_frame_aspect.py [project_id] [out_dir]
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"


def board_from_api(project_id: int) -> dict:
    with urllib.request.urlopen(f"{BASE}/api/projects/{project_id}/montage-board") as r:
        return json.load(r)


async def main(project_id: int, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    board = board_from_api(project_id)
    print("frame_aspect из API:", board.get("frame_aspect"))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1680, "height": 1000})
        page.on("pageerror", lambda e: print("pageerror:", str(e)[:300]))
        await page.goto(f"{BASE}/?project={project_id}", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.get_by_role("button", name="Монтаж").first.click()
        await page.wait_for_timeout(3500)

        head = page.get_by_text("Кадр", exact=True).first
        await head.scroll_into_view_if_needed()
        row = head.locator("xpath=ancestor::tr[1]")
        img = row.locator("img").first
        cell = row.locator("td").nth(1)
        box = await img.bounding_box()
        cell_box = await cell.bounding_box()
        if box and cell_box:
            print(
                "картинка:",
                f"{box['width']:.0f}x{box['height']:.0f}",
                "· клетка:",
                f"{cell_box['width']:.0f}x{cell_box['height']:.0f}",
            )

        strip = page.get_by_role("button", name="покрытие кадра").first
        await strip.hover()
        await page.wait_for_timeout(500)
        print("подсказка на полосе:", "изменить" in (await strip.inner_text()))
        await page.screenshot(path=str(out_dir / "frame-aspect.png"))
        await browser.close()


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/dev-checks")
    asyncio.run(main(pid, out))
