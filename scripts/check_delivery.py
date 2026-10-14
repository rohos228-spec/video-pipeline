r"""Автопроверка поставки (G-05): годится ли набор файлов репозитория для передачи заказчику.

    python scripts/check_delivery.py [--root DIR] [--walk] [--max-total-mb 150] [--max-file-mb 20]
                                     [--strict-tests] [--check-push-url ИМЯ_УДАЛЁННОГО] [--expected-push-url URL]
                                     [--forbid-push-substr ПОДСТРОКА ...] [--json out.json]

Что проверяется (ошибка = код выхода 1):
  1. нет запрещённых файлов и папок: ``.env`` и ``.env.*`` (кроме ``*.example``), базы ``*.db/*.sqlite``, ключи ``*.pem``,
     ``data/``, ``exports/``, ``logs/``, ``web/.next/``, ``node_modules/``, ``__pycache__/``, ``*.pyc``, ``*.egg-info/``,
     служебные файлы агентов (``.clinerules``, ``.clineignore``, ``.skill*.txt``, ``START_AI_ORCHESTRA_PROMPT.txt``,
     ``.ai-orchestra-mcp/``);
  2. в содержимом нет секретов (шаблоны ``scripts/scan_secrets.py``; значения не печатаются);
  3. нет личных путей владельца и профиля песочницы агента (шаблоны в PERSONAL_PATTERNS);
     чужие ``C:\Users\<имя>`` дают предупреждение, не ошибку;
  4. нет данных заказчика: файлы с именами проектов заказчика в пути;
  5. размер: общий и самого большого файла в пределах лимитов;
  6. на месте обязательные документы (``REQUIRED_DOCS``);
  7. (по запросу) push-URL удалённого равен ожидаемому ТОЧНО и не содержит запрещённых подстрок
     (``git remote get-url --push``, G-01).

Набор файлов: по умолчанию ``git ls-files`` (то, что попадёт в поставку); без git или с ``--walk`` - обход папки
с пропуском ``.git``. Содержимое ``.env`` никогда не читается. Сеть и платные вызовы не используются.
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_DOCS = (
    "README.md",
    ".gitignore",
    ".env.example",
    "docs/TESTING.md",
    "docs/ARCHITECTURE.md",
    "docs/SECURITY_DEFAULTS.md",
    "docs/PUBLISH_SETUP.md",
    "docs/ORCHESTRATOR_QUICKSTART.md",
    "docs/CUSTOMER_REPORT.md",
    "docs/audit/W40_FINAL_PLAN.md",
    "docs/audit/W49_DELIVERY_CLEANUP.md",
)

# Запрещённые каталоги (по компоненту пути, на любой глубине, если не сказано иное).
FORBIDDEN_DIR_NAMES = {"__pycache__", "node_modules", ".next", ".ai-orchestra-mcp"}
# Запрещённые каталоги только в корне.
FORBIDDEN_TOP_DIRS = {"data", "exports", "logs"}
FORBIDDEN_DIR_SUFFIXES = (".egg-info",)
# Запрещённые файлы по маске имени.
FORBIDDEN_FILE_GLOBS = (
    ".env", ".env.*", "*.db", "*.db-shm", "*.db-wal", "*.sqlite", "*.sqlite3", "*.pem", "*.pyc",
    ".clinerules", ".clineignore", ".skill*.txt", "START_AI_ORCHESTRA_PROMPT.txt",
    "Data_video_pipeline.env", "STUDIO-LOCAL.cmd",
)
ENV_ALLOWED_SUFFIXES = (".example", ".sample", ".template")

# Личные пути собираются из частей, чтобы сам скрипт и его тест не срабатывали на себя.
_OWNER = "Ник" + "ита"
PERSONAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "owner_profile_path": re.compile(r"Users[\\/]+" + _OWNER),
    "sandbox_user": re.compile("Codex" + "SandboxOnline"),
    "codex_documents": re.compile(r"Documents[\\/]+Cod" + "ex[\\\\/]"),
}
FOREIGN_USER_PATH = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<|\{|%|\$|Public\b|Default\b|All Users\b)[^\\/\s`'\"<>*]+")
# Имена проектов заказчика в пути файла (данные заказчика не поставляются).
CUSTOMER_PATH_RE = re.compile(r"(?i)spesiv|спесив|tkach|ткач")

TEXT_SUFFIX_SKIP = {
    ".mp4", ".mov", ".webm", ".mkv", ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".png", ".jpg", ".jpeg", ".webp",
    ".gif", ".zip", ".gz", ".woff", ".woff2", ".ttf", ".otf", ".ico", ".pyc", ".xlsx", ".pdf", ".docx",
}
MAX_TEXT_BYTES = 8 * 1024 * 1024


def _load_scanner():
    spec = importlib.util.spec_from_file_location("_vp_scan_secrets", Path(__file__).with_name("scan_secrets.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_vp_scan_secrets", mod)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def list_files(root: Path, walk: bool = False) -> list[str]:
    """Относительные пути (через '/') файлов, которые войдут в поставку."""
    if not walk:
        try:
            out = subprocess.run(
                ["git", "-c", "safe.directory=*", "-C", str(root), "ls-files", "-z"],
                capture_output=True, timeout=60, check=True).stdout
            names = [n.decode("utf-8", "replace") for n in out.split(b"\0") if n]
            if names:
                return sorted(n for n in names if (root / n).exists())
        except (OSError, subprocess.SubprocessError):
            pass
    files: list[str] = []
    for p in root.rglob("*"):
        if ".git" in p.relative_to(root).parts:
            continue
        if p.is_file():
            files.append(p.relative_to(root).as_posix())
    return sorted(files)


def is_forbidden(rel: str) -> str | None:
    """Причина запрета для относительного пути или None."""
    parts = rel.split("/")
    name = parts[-1]
    if parts[0] in FORBIDDEN_TOP_DIRS and len(parts) > 1:
        return f"каталог {parts[0]}/ не поставляется"
    for d in parts[:-1]:
        if d in FORBIDDEN_DIR_NAMES:
            return f"каталог {d}/ не поставляется"
        if d.endswith(FORBIDDEN_DIR_SUFFIXES):
            return f"каталог {d}/ не поставляется"
    for g in FORBIDDEN_FILE_GLOBS:
        if fnmatch.fnmatchcase(name, g):
            if name.startswith(".env") and name.endswith(ENV_ALLOWED_SUFFIXES):
                return None
            return f"файл по маске {g} не поставляется"
    return None


def check_content(root: Path, rel: str, scanner) -> dict[str, list]:
    """Секреты и личные пути в одном файле. Возвращает {'secrets': [...], 'personal': [...], 'foreign': [...]}."""
    res: dict[str, list] = {"secrets": [], "personal": [], "foreign": []}
    path = root / rel
    if path.suffix.lower() in TEXT_SUFFIX_SKIP:
        return res
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return res
        found = scanner.scan_file(path)
        data = path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return res
    res["secrets"] = sorted(found)
    for kind, pat in PERSONAL_PATTERNS.items():
        if pat.search(data):
            res["personal"].append(kind)
    if FOREIGN_USER_PATH.search(data):
        res["foreign"].append("foreign_user_path")
    return res


def check_push_url(push_url: str, expected: str | None, forbidden_substrings: list[str]) -> list[str]:
    """Точное сравнение push-URL (G-01): совпадение подстроки в любой строке `git remote -v` недостаточно."""
    errors: list[str] = []
    url = (push_url or "").strip()
    if expected is not None and url != expected:
        errors.append("push-URL не равен ожидаемому (сверка точная, не по подстроке)")
    for sub in forbidden_substrings:
        if sub and sub in url:
            errors.append(f"push-URL содержит запрещённую цель {sub!r}")
    return errors


def get_push_url(root: Path, remote: str) -> str:
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(root), "remote", "get-url", "--push", remote],
                             capture_output=True, timeout=30, check=True)
        return out.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def run_checks(root: Path, files: list[str], *, max_total_mb: float = 150.0, max_file_mb: float = 20.0,
               strict_tests: bool = False, required_docs: tuple[str, ...] = REQUIRED_DOCS) -> dict:
    """Чистая функция по заданному списку файлов; возвращает отчёт с ключами errors/warnings."""
    errors: list[str] = []
    warnings: list[str] = []
    scanner = _load_scanner()
    total = 0
    biggest = ("", 0)
    for rel in files:
        reason = is_forbidden(rel)
        if reason:
            errors.append(f"запрещённый файл: {rel} ({reason})")
        if CUSTOMER_PATH_RE.search(rel):
            # код/тесты с именем проекта заказчика - предупреждение; любые другие файлы (данные) - ошибка
            if rel.split("/")[0] in ("scripts", "tests"):
                warnings.append(f"имя проекта заказчика в имени кода: {rel}")
            else:
                errors.append(f"данные заказчика в пути: {rel}")
        p = root / rel
        try:
            size = p.stat().st_size
        except OSError:
            continue
        total += size
        if size > biggest[1]:
            biggest = (rel, size)
        if size > max_file_mb * 2**20:
            errors.append(f"файл больше {max_file_mb:g} МБ: {rel} ({size // 2**20} МБ)")
        if reason:
            continue  # содержимое запрещённых файлов (в первую очередь .env) не читаем
        c = check_content(root, rel, scanner)
        if c["secrets"]:
            msg = f"возможный секрет в {rel}: {', '.join(c['secrets'])}"
            # в тестах лежат синтетические ключи (проверки редакции) - предупреждение, если не --strict-tests
            if rel.startswith("tests/") and not strict_tests:
                warnings.append(msg + " (тест, вероятно синтетический ключ)")
            else:
                errors.append(msg)
        if c["personal"]:
            errors.append(f"личный путь в {rel}: {', '.join(c['personal'])}")
        if c["foreign"]:
            warnings.append(f"путь чужого профиля C:\\Users\\<имя> в {rel}")
    if total > max_total_mb * 2**20:
        errors.append(f"общий размер {total // 2**20} МБ больше лимита {max_total_mb:g} МБ")
    present = set(files)
    for doc in required_docs:
        if doc not in present:
            errors.append(f"нет обязательного файла: {doc}")
    return {"files": len(files), "total_bytes": total, "largest": biggest[0], "largest_bytes": biggest[1],
            "errors": errors, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--walk", action="store_true", help="не git ls-files, а обход папки")
    ap.add_argument("--max-total-mb", type=float, default=150.0)
    ap.add_argument("--max-file-mb", type=float, default=20.0)
    ap.add_argument("--check-push-url", metavar="REMOTE")
    ap.add_argument("--expected-push-url")
    ap.add_argument("--forbid-push-substr", action="append", default=[])
    ap.add_argument("--strict-tests", action="store_true", help="секреты в tests/ - ошибка, не предупреждение")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    report = run_checks(root, list_files(root, args.walk), max_total_mb=args.max_total_mb, max_file_mb=args.max_file_mb,
                        strict_tests=args.strict_tests)
    if args.check_push_url:
        report["errors"] += check_push_url(get_push_url(root, args.check_push_url), args.expected_push_url,
                                           args.forbid_push_substr)
    for w in report["warnings"]:
        print("предупреждение:", w)
    for e in report["errors"]:
        print("ОШИБКА:", e)
    print(f"файлов: {report['files']}; размер: {report['total_bytes'] // 2**20} МБ; "
          f"ошибок: {len(report['errors'])}; предупреждений: {len(report['warnings'])}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
