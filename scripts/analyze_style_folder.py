"""Пакетный анализ референсов стилей.

Раскладка папки:  data/style-lab/<имя-стиля>/*.png|jpg|webp  (подпапка = один стиль)
Результат:        data/style-lab/out/styles.json + categories.json

Запуск из корня репо:
    .venv\\Scripts\\python.exe scripts\\analyze_style_folder.py data/style-lab
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.services.style_analyzer import IMG_EXTS, analyze_style_images, categorize_styles


async def main(root: Path) -> int:
    if not root.is_dir():
        print(f"Папка не найдена: {root}")
        return 1
    subs = sorted(p for p in root.iterdir() if p.is_dir() and p.name != "out")
    if not subs:
        print(f"В {root} нет подпапок стилей (<имя-стиля>/*.png)")
        return 1

    entries: list[dict] = []
    for sub in subs:
        images = [p for p in sorted(sub.rglob("*")) if p.suffix.lower() in IMG_EXTS]
        if not images:
            print(f"— {sub.name}: нет изображений, пропуск")
            continue
        print(f"… {sub.name}: {len(images)} изображений, анализ")
        try:
            entry = await analyze_style_images(images, name_hint=sub.name)
        except Exception as e:  # noqa: BLE001
            print(f"✗ {sub.name}: {e}")
            continue
        entries.append(entry)
        print(f"✓ {sub.name} → «{entry['name']}» ({entry['category']})")

    if not entries:
        print("Ни один стиль не проанализирован")
        return 1

    out_dir = root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "styles.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nЗаписи стилей: {out_dir / 'styles.json'}")

    if len(entries) >= 2:
        cats = await categorize_styles(entries)
        (out_dir / "categories.json").write_text(
            json.dumps(cats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Категории: {out_dir / 'categories.json'}")
        for c in cats["categories"]:
            print(f"  · {c['name']} — {', '.join(c.get('style_names') or [])}")
    return 0


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/style-lab")
    raise SystemExit(asyncio.run(main(root)))
