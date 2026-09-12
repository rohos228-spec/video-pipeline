"""Сбор референсов стиля: поиск картинок → папка + manifest.csv.

Источники (--source):
    bing      — выдача Bing (работает с домашнего IP, из облака отдаёт мусор)
    openverse — открытый API Creative Commons, без ключа
    wallhaven — открытый API обоев, без ключа
    pexels    — нужен PEXELS_API_KEY
    unsplash  — нужен UNSPLASH_ACCESS_KEY
    pixabay   — нужен PIXABAY_API_KEY
    serper    — Google Images через serper.dev, нужен SERPER_API_KEY

Примеры:
    python scripts/fetch_style_refs.py --preset all --out data/refs --limit 200
    python scripts/fetch_style_refs.py --source bing --out data/refs/anime \
        --limit 150 -q "modern anime art style 2024"

Файлы проверяются (реальная картинка, не мельче --min-side), дубликаты
отсекаются по sha1, сохраняется JPEG с длинной стороной --max-side.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"

Hit = dict[str, str]

# Запросы под категории: тренды 2020–2026, английский даёт более чистую выдачу.
PRESETS: dict[str, list[str]] = {
    "animation": [
        "modern anime art style 2024 screencap",
        "anime key visual 2023 studio",
        "arcane netflix animation style",
        "spider-verse animation style frame",
        "3d stylised character render pixar style",
        "cel shaded animation still",
        "webtoon illustration style",
        "ghibli style background art",
        "retro 90s anime aesthetic cel",
        "cartoon flat character design 2024",
        "claymation stop motion still",
        "cut-out paper animation style",
    ],
    "infographic": [
        "marketplace product card infographic",
        "amazon listing infographic design",
        "wildberries карточка товара инфографика",
        "flat vector infographic 2024",
        "isometric data infographic",
        "timeline process infographic design",
        "dark slide deck line icons",
        "instagram carousel infographic design",
        "youtube thumbnail infographic",
        "data visualization editorial poster",
        "how it works diagram illustration",
        "comparison chart infographic design",
    ],
    "photo": [
        "cinematic film still anamorphic",
        "teal and orange color grade photo",
        "kodak portra 400 portrait",
        "cinestill 800t night photography",
        "moody portrait lightroom preset",
        "golden hour backlit portrait",
        "documentary 35mm street photography",
        "high contrast black and white portrait",
        "a24 film still aesthetic",
        "wes anderson symmetrical frame",
        "neon night street photography",
        "macro product photography studio light",
    ],
    "retro": [
        "90s disposable camera flash photo",
        "polaroid snapshot 1995",
        "vhs still frame 1994",
        "y2k aesthetic 2000s photo",
        "kodachrome 1970s photo",
        "retro print advertisement 90s",
        "old family photo 1998 flash",
        "film grain scan 2000s camera",
        "vintage newspaper halftone print",
        "8mm home movie frame",
    ],
    "misc": [
        "risograph print poster",
        "collage zine aesthetic poster",
        "brutalist graphic design poster",
        "pixel art scene detailed",
        "blueprint technical drawing",
        "trash polka poster illustration",
        "vaporwave aesthetic artwork",
        "textile felt illustration children book",
    ],
}


# На Are.na ищут по коротким «эстетикам», а не по поисковым фразам.
ARENA_PRESETS: dict[str, list[str]] = {
    "animation": [
        "anime",
        "animation stills",
        "cel animation",
        "character design",
        "cartoon",
        "motion graphics",
        "storyboard",
    ],
    "infographic": [
        "infographic",
        "diagrams",
        "data visualization",
        "editorial layout",
        "charts",
        "instructional graphics",
        "packaging design",
    ],
    "photo": [
        "film stills",
        "cinematography",
        "portrait photography",
        "color grading",
        "photography",
        "35mm",
        "studio lighting",
    ],
    "retro": [
        "y2k",
        "90s",
        "vhs",
        "analog photography",
        "retro graphics",
        "2000s web",
        "vintage advertising",
    ],
    "misc": [
        "risograph",
        "collage",
        "brutalist design",
        "pixel art",
        "poster design",
        "typography poster",
    ],
}


def _get_json(url: str, *, headers: dict[str, str] | None = None, data: bytes | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})}, data=data)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def _need_key(name: str) -> str:
    key = os.environ.get(name, "").strip()
    if not key:
        raise SystemExit(f"Нужен ключ в переменной окружения {name}")
    return key


def search_bing(query: str, want: int) -> list[Hit]:
    out: list[Hit] = []
    seen: set[str] = set()
    for page in range(0, 8):
        if len(out) >= want:
            break
        params = urllib.parse.urlencode(
            {"q": query, "first": page * 35 + 1, "count": 35, "mmasync": 1}
        )
        req = urllib.request.Request(
            f"https://www.bing.com/images/async?{params}", headers={"User-Agent": UA}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            print(f"  bing: {e}", file=sys.stderr)
            break
        chunk = 0
        for m in re.finditer(r'm="(\{[^"]+\})"', body):
            try:
                meta = json.loads(html.unescape(m.group(1)))
            except json.JSONDecodeError:
                continue
            url = meta.get("murl") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"url": url, "page": meta.get("purl", ""), "title": meta.get("t", "")})
            chunk += 1
        if not chunk:
            break
        time.sleep(0.6)
    return out[:want]


def search_openverse(query: str, want: int) -> list[Hit]:
    out: list[Hit] = []
    for page in range(1, 6):
        if len(out) >= want:
            break
        url = (
            "https://api.openverse.org/v1/images/?q="
            + urllib.parse.quote(query)
            + f"&page_size=20&page={page}"
        )
        try:
            data = _get_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"  openverse: {e}", file=sys.stderr)
            break
        results = data.get("results") or []
        if not results:
            break
        for r in results:
            out.append(
                {
                    "url": r.get("url", ""),
                    "page": r.get("foreign_landing_url", ""),
                    "title": f"{r.get('title', '')} ({r.get('license', '')})",
                }
            )
        time.sleep(0.5)
    return out[:want]


def search_wallhaven(query: str, want: int) -> list[Hit]:
    out: list[Hit] = []
    for page in range(1, 4):
        if len(out) >= want:
            break
        url = f"https://wallhaven.cc/api/v1/search?q={urllib.parse.quote(query)}&purity=100&page={page}"
        try:
            data = _get_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"  wallhaven: {e}", file=sys.stderr)
            break
        items = data.get("data") or []
        if not items:
            break
        for r in items:
            out.append({"url": r.get("path", ""), "page": r.get("url", ""), "title": "wallhaven"})
        time.sleep(0.5)
    return out[:want]


def search_arena(query: str, want: int) -> list[Hit]:
    """Are.na: кураторские каналы дизайнеров — картинки отобраны людьми."""
    out: list[Hit] = []
    seen: set[str] = set()
    try:
        found = _get_json(
            "https://api.are.na/v2/search/channels?per=20&q=" + urllib.parse.quote(query)
        )
    except Exception as e:  # noqa: BLE001
        print(f"  are.na поиск: {e}", file=sys.stderr)
        return out
    channels = [c for c in found.get("channels", []) if (c.get("length") or 0) >= 25]
    channels.sort(key=lambda c: c.get("length") or 0, reverse=True)
    # Из одного канала берём немного: иначе вся пачка — один чужой мудборд.
    per_channel = max(4, want // 5)
    for ch in channels[:12]:
        if len(out) >= want:
            break
        slug = ch.get("slug")
        taken = 0
        for page in range(1, 4):
            if len(out) >= want or taken >= per_channel:
                break
            try:
                data = _get_json(
                    f"https://api.are.na/v2/channels/{slug}/contents?per=60&page={page}&direction=desc"
                )
            except Exception:  # noqa: BLE001 — приватный или удалённый канал
                break
            blocks = data.get("contents") or []
            if not blocks:
                break
            for b in blocks:
                if taken >= per_channel:
                    break
                image = (b.get("image") or {}).get("original") or {}
                url = image.get("url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                taken += 1
                out.append(
                    {
                        "url": url,
                        "page": f"https://are.na/block/{b.get('id')}",
                        "title": f"{ch.get('title', '')}: {(b.get('title') or '')[:60]}",
                    }
                )
            time.sleep(0.4)
    return out[:want]


def search_pexels(query: str, want: int) -> list[Hit]:
    key = _need_key("PEXELS_API_KEY")
    url = f"https://api.pexels.com/v1/search?query={urllib.parse.quote(query)}&per_page={min(want, 80)}"
    data = _get_json(url, headers={"Authorization": key})
    return [
        {
            "url": p.get("src", {}).get("large2x", ""),
            "page": p.get("url", ""),
            "title": p.get("alt", ""),
        }
        for p in data.get("photos", [])
    ][:want]


def search_unsplash(query: str, want: int) -> list[Hit]:
    key = _need_key("UNSPLASH_ACCESS_KEY")
    url = (
        "https://api.unsplash.com/search/photos?query="
        + urllib.parse.quote(query)
        + f"&per_page={min(want, 30)}"
    )
    data = _get_json(url, headers={"Authorization": f"Client-ID {key}"})
    return [
        {
            "url": p.get("urls", {}).get("regular", ""),
            "page": p.get("links", {}).get("html", ""),
            "title": p.get("description") or p.get("alt_description") or "",
        }
        for p in data.get("results", [])
    ][:want]


def search_pixabay(query: str, want: int) -> list[Hit]:
    key = _need_key("PIXABAY_API_KEY")
    url = (
        f"https://pixabay.com/api/?key={key}&q="
        + urllib.parse.quote(query)
        + f"&per_page={min(max(want, 3), 200)}"
    )
    data = _get_json(url)
    return [
        {"url": h.get("largeImageURL", ""), "page": h.get("pageURL", ""), "title": h.get("tags", "")}
        for h in data.get("hits", [])
    ][:want]


def search_serper(query: str, want: int) -> list[Hit]:
    """Google Images через serper.dev — самая чистая выдача, нужен ключ."""
    key = _need_key("SERPER_API_KEY")
    out: list[Hit] = []
    for page in range(1, 4):
        if len(out) >= want:
            break
        payload = json.dumps({"q": query, "page": page}).encode()
        data = _get_json(
            "https://google.serper.dev/images",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            data=payload,
        )
        images = data.get("images") or []
        if not images:
            break
        for im in images:
            out.append(
                {
                    "url": im.get("imageUrl", ""),
                    "page": im.get("link", ""),
                    "title": im.get("title", ""),
                }
            )
        time.sleep(0.4)
    return out[:want]


SOURCES = {
    "bing": search_bing,
    "arena": search_arena,
    "openverse": search_openverse,
    "wallhaven": search_wallhaven,
    "pexels": search_pexels,
    "unsplash": search_unsplash,
    "pixabay": search_pixabay,
    "serper": search_serper,
}


def save_image(raw: bytes, dst: Path, *, min_side: int, max_side: int) -> bool:
    """Проверка размера и сохранение JPEG. False — картинка не подошла."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:  # noqa: BLE001 — битые и не-картинки просто пропускаем
        return False
    if min(img.size) < min_side:
        return False
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    img.convert("RGB").save(dst, "JPEG", quality=85, optimize=True)
    return True


def fetch(
    queries: list[str],
    out_dir: Path,
    *,
    source: str,
    limit: int,
    min_side: int,
    max_side: int,
    prefix: str = "ref",
) -> int:
    search = SOURCES[source]
    out_dir.mkdir(parents=True, exist_ok=True)
    hashes: set[str] = set()
    rows: list[dict[str, str]] = []
    per_query = max(8, limit * 3 // max(1, len(queries)))
    saved = 0

    for query in queries:
        if saved >= limit:
            break
        hits = search(query, per_query)
        print(f"«{query}»: {len(hits)} ссылок")
        for hit in hits:
            if saved >= limit:
                break
            if not hit.get("url"):
                continue
            req = urllib.request.Request(hit["url"], headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    raw = r.read(12_000_000)
            except Exception:  # noqa: BLE001 — мёртвые ссылки в выдаче обычны
                continue
            digest = hashlib.sha1(raw).hexdigest()
            if digest in hashes:
                continue
            dst = out_dir / f"{prefix}_{saved:03d}.jpg"
            if not save_image(raw, dst, min_side=min_side, max_side=max_side):
                continue
            hashes.add(digest)
            rows.append(
                {
                    "file": dst.name,
                    "query": query,
                    "image_url": hit["url"],
                    "source_page": hit.get("page", ""),
                    "title": hit.get("title", ""),
                }
            )
            saved += 1
            if saved % 10 == 0:
                print(f"  сохранено {saved}/{limit}")

    manifest = out_dir / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["file", "query", "image_url", "source_page", "title"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Готово: {saved} картинок в {out_dir}")
    return saved


def main() -> int:
    ap = argparse.ArgumentParser(description="Скачать референсы стиля по поисковым запросам")
    ap.add_argument("-q", "--query", action="append", default=[], help="поисковый запрос")
    ap.add_argument(
        "--preset",
        choices=[*PRESETS, "all"],
        help="готовый набор запросов; all — все категории по папкам",
    )
    ap.add_argument("--source", choices=[*SOURCES], default="bing")
    ap.add_argument("--out", required=True, type=Path, help="папка назначения")
    ap.add_argument("--limit", type=int, default=150, help="сколько картинок сохранить")
    ap.add_argument("--min-side", type=int, default=600, help="минимальная сторона оригинала")
    ap.add_argument("--max-side", type=int, default=1600, help="длинная сторона после сжатия")
    args = ap.parse_args()

    presets = ARENA_PRESETS if args.source == "arena" else PRESETS

    if args.preset == "all":
        total = 0
        per_cat = max(1, args.limit // len(presets))
        for cat, queries in presets.items():
            print(f"\n=== {cat} ===")
            total += fetch(
                queries,
                args.out / cat,
                source=args.source,
                limit=per_cat,
                min_side=args.min_side,
                max_side=args.max_side,
                prefix=cat,
            )
        print(f"\nВсего: {total}")
        return 0 if total else 1

    queries = args.query or presets.get(args.preset or "", [])
    if not queries:
        ap.error("нужен --query или --preset")
    saved = fetch(
        queries,
        args.out,
        source=args.source,
        limit=args.limit,
        min_side=args.min_side,
        max_side=args.max_side,
    )
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
