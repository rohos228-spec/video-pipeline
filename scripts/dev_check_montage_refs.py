"""Dev-проверка референсов кадра: полоса под картинкой и окно «приложить».

Не часть пайплайна. Нужен бэкенд на :8765 и сид
``scripts/dev_seed_montage_scene.py`` (он же кладёт готовых персонажей).

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

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (256, 256), (40, 80, 140)).save(path)
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
        print("строки референсов нет:", "Референсы" not in labels)
        print("строки «Изображение 2» нет:", "Изображение 2" not in labels)

        add = page.get_by_role("button", name="Референсы кадра #1").first
        await add.scroll_into_view_if_needed()
        print("на кнопке нет слова «реф»:", (await add.inner_text()).strip() == "")
        await page.screenshot(path=str(out_dir / "refs-strip.png"))

        await add.click()
        await page.wait_for_timeout(1200)
        print(
            "окно с готовыми рефами проекта:",
            await page.get_by_text("уже есть в проекте", exact=False).first.is_visible(),
        )
        await page.screenshot(path=str(out_dir / "refs-window.png"))

        # 1) приложить готового персонажа проекта
        await page.get_by_title("Приложить персонаж: следователь Лавров").first.click()
        await page.wait_for_timeout(2500)
        print(
            "готовый персонаж приложен:",
            await page.get_by_text("следователь Лавров", exact=False).first.is_visible(),
        )

        # 2) загрузить новый файл под своим именем
        async with page.expect_file_chooser() as fc:
            await page.get_by_role("button", name="выбрать файл").first.click()
        chooser = await fc.value
        await chooser.set_files(str(ref_file))
        await page.wait_for_timeout(300)
        await page.get_by_placeholder("имя для", exact=False).first.fill("окно кабинета")
        await page.get_by_role("button", name="приложить", exact=True).first.click()
        await page.wait_for_timeout(2500)
        print(
            "загруженный реф с именем:",
            await page.get_by_text("окно кабинета", exact=False).first.is_visible(),
        )
        await page.screenshot(path=str(out_dir / "refs-attached.png"))

        # 3) убрать оба
        for _ in range(2):
            await page.get_by_role("button", name="Убрать реф").first.click()
            await page.wait_for_timeout(2000)
        print(
            "после удаления пусто:",
            await page.get_by_text("пока ничего не приложено", exact=False).first.is_visible(),
        )
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)

        gutters = await page.locator('tbody td[title^="Кадр после"]').count()
        print("плюсиков в строках таблицы:", gutters)
        await page.screenshot(path=str(out_dir / "refs-deleted.png"))
        await browser.close()


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/dev-checks/refs")
    asyncio.run(main(pid, out))
