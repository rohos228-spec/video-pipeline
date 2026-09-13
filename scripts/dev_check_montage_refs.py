"""Dev-проверка референсов кадра: полоса под картинкой, добавление и удаление.

Не часть пайплайна. Нужен бэкенд на :8765 и сид
``scripts/dev_seed_montage_scene.py``.

    python3 scripts/dev_check_montage_refs.py [project_id] [out_dir]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"


def _ref_png(path: Path) -> Path:
    from PIL import Image

    img = Image.new("RGB", (256, 256), (40, 80, 140))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


async def main(project_id: int, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ref_file = _ref_png(out_dir / "ref-source.png")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1680, "height": 1000})
        page.on("pageerror", lambda e: print("pageerror:", str(e)[:300]))
        await page.goto(f"{BASE}/?project={project_id}", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.get_by_role("button", name="Монтаж").first.click()
        await page.wait_for_timeout(3500)

        labels = await page.locator(
            "tbody tr > td:first-child button span"
        ).all_inner_texts()
        print("строки доски:", " → ".join(labels))
        print("отдельной строки референсов нет:", "Референсы" not in labels)

        add = page.get_by_role("button", name="Референсы кадра #1").first
        await add.scroll_into_view_if_needed()
        await page.screenshot(path=str(out_dir / "refs-strip.png"))
        await add.hover()
        await page.wait_for_timeout(500)
        panel = page.get_by_text("генератор берёт максимум два", exact=False).first
        print("панель открылась по наведению:", await panel.is_visible())
        await page.screenshot(path=str(out_dir / "refs-panel.png"))

        await page.get_by_role("button", name="персонаж", exact=True).first.click()
        await page.locator("textarea").last.fill(
            "следователь: 40 лет, серое пальто, стоит у окна"
        )
        async with page.expect_file_chooser() as fc:
            await page.get_by_role("button", name="выбрать файл").first.click()
        chooser = await fc.value
        await chooser.set_files(str(ref_file))
        await page.wait_for_timeout(300)
        await page.get_by_role("button", name="добавить", exact=True).first.click()
        await page.wait_for_timeout(2500)

        # Уводим курсор, иначе повторный hover не даёт mouseenter.
        await page.mouse.move(10, 10)
        await page.wait_for_timeout(400)
        await add.hover()
        await page.wait_for_timeout(700)
        added = page.get_by_text("следователь: 40 лет", exact=False).first
        print("реф с описанием в панели:", await added.is_visible())
        await page.screenshot(path=str(out_dir / "refs-added.png"))

        await page.get_by_role("button", name="Убрать реф").first.click()
        await page.wait_for_timeout(2500)
        await page.mouse.move(10, 10)
        await page.wait_for_timeout(400)
        await add.hover()
        await page.wait_for_timeout(700)
        print("после удаления рефов нет:", await page.get_by_text(
            "своих рефов у кадра нет", exact=False
        ).first.is_visible())
        await page.screenshot(path=str(out_dir / "refs-deleted.png"))
        await browser.close()


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/dev-checks/refs")
    asyncio.run(main(pid, out))
