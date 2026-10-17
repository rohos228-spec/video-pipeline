"""Dev-проверка: якорь дописывается в клетке кадра и создаёт шот.

Не часть пайплайна. Нужен бэкенд на :8765 и сид
``scripts/dev_seed_montage_scene.py``.

    python3 scripts/dev_check_montage_anchor_add.py [project_id] [out_dir]
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
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1680, "height": 1000})
        page.on("pageerror", lambda e: print("pageerror:", str(e)[:300]))
        await page.goto(f"{BASE}/?project={project_id}", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.get_by_role("button", name="Монтаж").first.click()
        await page.wait_for_timeout(3500)

        head = page.get_by_text("Якорь кадра", exact=True).first
        await head.scroll_into_view_if_needed()
        anchor_row = head.locator("xpath=ancestor::tr[1]")
        add = anchor_row.get_by_role("button", name="Дописать якорь", exact=True)
        print("кнопок «+ якорь» в строке якорей:", await add.count())

        # Кадр #3 — последний шот первой ячейки. Оператор укорачивает свой
        # якорь и дописывает второй: по нему и родится новый шот.
        cell = anchor_row.locator("td").filter(has=page.locator("input")).nth(2)
        add_here = cell.get_by_role("button", name="Дописать якорь", exact=True)
        await cell.locator("input").first.fill("На первой странице")
        await add_here.click()
        await page.wait_for_timeout(400)
        inputs = cell.locator("input")
        n = await inputs.count()
        # Последняя пара инпутов — новая строка якоря.
        await inputs.nth(n - 2).fill("стояло чужое имя")
        await inputs.nth(n - 1).fill("чужое имя → узнал")
        await cell.locator("input").first.click()
        await page.wait_for_timeout(900)

        print("подсказка в клетке:", (await cell.inner_text()).replace("\n", " | "))
        await page.screenshot(path=str(out_dir / "anchor-appended.png"))
        await cell.screenshot(path=str(out_dir / "anchor-cell.png"))

        apply_btn = page.get_by_role("button", name="Применить правки").first
        print("кнопка применения:", (await apply_btn.inner_text()).strip())

        await page.wait_for_timeout(2500)
        ops = board_from_api(project_id)["meta"]["pending_ops"]
        for op in ops:
            if op.get("type") == "coverage_anchors":
                print("в очереди якоря кадра", op["frame_number"], "→", [
                    (r["якорь"], r.get("cell_index"), r.get("frame_number"))
                    for r in op["anchors"]
                ])

        # Применяем и смотрим, что доска не мигает пустой, а кадр появился.
        await apply_btn.click()
        await page.wait_for_timeout(1200)
        await page.screenshot(path=str(out_dir / "apply-running.png"))
        empty = await page.get_by_text("Кадров нет").count()
        print("пустой экран во время применения:", empty)
        for _ in range(30):
            await page.wait_for_timeout(1000)
            if await page.get_by_role("button", name="Применить правки").first.is_enabled():
                break
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(out_dir / "after-apply.png"))
        board = board_from_api(project_id)
        print("кадров после применения:", board["frame_count"])
        for fr in board["frames"]:
            print(
                " ",
                fr["number"],
                fr["shot_kind"],
                "сцена",
                fr["vo_scene_number"],
                repr(fr["voiceover_text"][:44]),
            )
        await browser.close()


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/montage-anchor")
    asyncio.run(main(pid, out))
