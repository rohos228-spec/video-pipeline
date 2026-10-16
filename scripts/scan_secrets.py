"""Сканер секретов по файлам и базам (шаг A16, живая часть): ищет СИНТАКСИС ключей, значения не печатает.

    python scripts/scan_secrets.py [папка ...] [--include-process-env] [--json out.json]

По умолчанию сканирует ``data`` и ``api-tracker`` репозитория. Бинарные медиа пропускаются; базы SQLite
читаются как байты (текстовые поля лежат в них открытым видом). Для каждого совпадения в отчёт идёт только
файл, вид и счётчик. ``--include-process-env`` дополнительно ищет значения переменных окружения ЭТОГО процесса
(длина от 12 символов, только имена с KEY/TOKEN/SECRET/PASSWORD); значения не печатаются и файл .env не читается.
Код выхода 0 - совпадений нет, 1 - есть.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_SUFFIXES = {
    ".mp4", ".mov", ".webm", ".mkv", ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".png", ".jpg", ".jpeg",
    ".webp", ".gif", ".zip", ".gz", ".woff", ".woff2", ".ttf", ".otf", ".ico", ".pyc",
}
MAX_BYTES = 256 * 1024 * 1024

PATTERNS: dict[str, re.Pattern[bytes]] = {
    "telegram_bot_token": re.compile(rb"(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    "jwt": re.compile(rb"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    "supabase_key": re.compile(rb"\bsb_(?:secret|publishable)_[A-Za-z0-9_-]{8,}|\bsbp_[A-Za-z0-9]{20,}"),
    "google_api_key": re.compile(rb"\bAIza[0-9A-Za-z_-]{35}"),
    "github_token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "sk_vk_key": re.compile(rb"\b(?:sk|vk)-[A-Za-z0-9_-]{16,}"),
    "bearer_token": re.compile(rb"(?i)Bearer\s+(?!\[redacted\])[A-Za-z0-9._~+/=-]{20,}"),
    "signed_query": re.compile(rb"(?i)X-Amz-Signature=[0-9a-f]{32,}"),
    "xi_api_key_value": re.compile(rb"(?i)xi-api-key[\"']?\s*[:=]\s*[\"']?(?!\[redacted\])[A-Za-z0-9_-]{16,}"),
}
ENV_NAME = re.compile(r"(?i)(key|token|secret|password)")


def _env_needles() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for name, value in os.environ.items():
        if ENV_NAME.search(name) and len(value) >= 12:
            out[name] = value.encode("utf-8", "ignore")
    return out


def scan_file(path: Path, needles: dict[str, bytes] | None = None) -> dict[str, int]:
    data = path.read_bytes()
    found: dict[str, int] = {}
    for kind, pat in PATTERNS.items():
        n = len(pat.findall(data))
        if n:
            found[kind] = n
    for name, needle in (needles or {}).items():
        n = data.count(needle)
        if n:
            found[f"env:{name}"] = n
    return found


def scan_tree(roots: list[Path], needles: dict[str, bytes] | None = None) -> dict:
    files = 0
    hits: list[dict] = []
    skipped: list[str] = []
    for root in roots:
        if not root.exists():
            skipped.append(f"{root}: нет такой папки")
            continue
        paths = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for p in paths:
            if p.suffix.lower() in SKIP_SUFFIXES:
                continue
            try:
                if p.stat().st_size > MAX_BYTES:
                    skipped.append(f"{p}: больше {MAX_BYTES // 2**20} МБ")
                    continue
                found = scan_file(p, needles)
            except OSError as exc:
                skipped.append(f"{p}: {type(exc).__name__}")
                continue
            files += 1
            if found:
                hits.append({"file": str(p), "found": found})
    return {"files_scanned": files, "files_with_hits": len(hits),
            "total_matches": sum(sum(h["found"].values()) for h in hits), "hits": hits, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--include-process-env", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    roots = [Path(p) for p in args.paths] or [ROOT / "data", ROOT / "api-tracker"]
    report = scan_tree(roots, _env_needles() if args.include_process_env else None)
    for h in report["hits"]:
        print(f"{h['file']}: " + ", ".join(f"{k}={v}" for k, v in sorted(h["found"].items())))
    for s in report["skipped"]:
        print("пропущено:", s)
    print(f"просмотрено файлов: {report['files_scanned']}; файлов с совпадениями: {report['files_with_hits']}; "
          f"совпадений всего: {report['total_matches']}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if report["total_matches"] else 0


if __name__ == "__main__":
    sys.exit(main())
