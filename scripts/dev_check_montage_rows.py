"""Dev-проверка строк сцены на доске: клик по чипу = правка в очереди.

Не часть пайплайна. Нужен бэкенд на :8765 и сид
``scripts/dev_seed_montage_scene.py``.

    python3 scripts/dev_check_montage_rows.py [project_id] [out_dir]
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"


def queue_from_api(project_id: int) -> list[dict]:
    with urllib.request.urlopen(f"{BASE}/api/projects/{project_id}/montage-board") as r:
        board = json.load(r)
    return list(board.get("meta", {}).get("pending_ops") or [])


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

        async def row_cells(label: str):
            head = page.get_by_text(label, exact=True).first
            await head.scroll_into_view_if_needed()
            return head.locator("xpath=ancestor::tr[1]")

        # Крупность кадра #1: ДАЛЬНИЙ вместо ОБЩИЙ.
        plan_row = await row_cells("Крупность")
        await plan_row.get_by_role("button", name="ДАЛЬНИЙ").first.click()
        await page.wait_for_timeout(400)

        # Ракурс кадра #2.
        angle_row = await row_cells("Ракурс")
        await angle_row.get_by_role("button", name="с плеча").nth(1).click()
        await page.wait_for_timeout(400)

        # Стык кадра #1.
        stitch_row = await row_cells("Стык / переход")
        await stitch_row.get_by_role("button", name="по действию").first.click()
        await page.wait_for_timeout(400)

        # Свет — общий на ячейку.
        light_row = await row_cells("Свет")
        await light_row.get_by_role("button", name="контровой").first.click()
        await page.wait_for_timeout(400)

        # Действие кадра #1 — правка текстом (коммит по blur).
        action_row = await row_cells("Действие кадра")
        area = action_row.locator("textarea").first
        await area.click()
        await area.fill("кабинет следователя: гость ставит портфель на стол")
        await action_row.locator("textarea").nth(1).click()
        await page.wait_for_timeout(500)

        # Якорь кадра #2 — правка «было → стало».
        anchor_row = await row_cells("Якорь кадра")
        change = anchor_row.locator("input").nth(3)
        await change.click()
        await change.fill("папка в портфеле → папка на столе")
        await anchor_row.locator("input").first.click()
        await page.wait_for_timeout(600)

        await page.screenshot(path=str(out_dir / "10-queued.png"))
        apply_btn = page.get_by_role("button", name="Применить правки")
        print("apply button:", (await apply_btn.first.inner_text()).strip())

        # Очередь долетает до сервера (debounce 400 мс + сохранение).
        await page.wait_for_timeout(2500)
        ops = queue_from_api(project_id)
        print("ops in queue:", len(ops))
        for op in ops:
            print(" ", op.get("type"), op.get("frame_number"), {
                k: v
                for k, v in op.items()
                if k not in ("type", "frame_number", "shot")
            })
        await browser.close()


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/montage-shots")
    asyncio.run(main(pid, out))
