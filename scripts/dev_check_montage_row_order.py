"""Dev-проверка порядка строк доски: кадр сверху, ниже сцена, потом данные кадра.

Не часть пайплайна. Нужен бэкенд на :8765 и сид
``scripts/dev_seed_montage_scene.py``.

    python3 scripts/dev_check_montage_row_order.py [project_id] [out_dir]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"


async def main(project_id: int, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1680, "height": 1000})
        page.on("pageerror", lambda e: print("pageerror:", str(e)[:300]))
        await page.goto(f"{BASE}/?project={project_id}", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.get_by_role("button", name="Монтаж").first.click()
        await page.wait_for_timeout(3500)

        labels = await page.locator("tbody tr > td:first-child button span").all_inner_texts()
        print("строки доски:", " → ".join(labels))

        scene = page.get_by_text("последовательность кадров", exact=True).first
        await scene.scroll_into_view_if_needed()
        print("формат сцены с описанием:", await scene.is_visible())
        light = page.get_by_role("button", name="свет сцены").first
        print("свет у сцены:", await light.count() > 0)
        strip = page.get_by_role("button", name="покрытие кадра").first
        print("в полосе кадра нет света:", "свет" not in (await strip.inner_text()).lower())
        await page.screenshot(path=str(out_dir / "rows-top.png"))

        anchor = page.get_by_text("Якорь кадра", exact=True).first
        await anchor.scroll_into_view_if_needed()
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(out_dir / "rows-frame-data.png"))
        await browser.close()


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/dev-checks/rows")
    asyncio.run(main(pid, out))
