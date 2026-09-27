"""stop / subagentStop: проверочный шлюз «не закончил, пока не зелёное».

1. Смотрит этот checkout и `.worktrees/*`. Чужие linked checkout (другой ПК,
   отдельная папка вне `.worktrees`) не сканирует.
2. Первый запуск запоминает текущий HEAD и не разбирает историю ветки против
   merge-base с main. Дальше — незакоммиченные .py и коммиты после этой отметки.
   Для subagentStop список файлов берётся из modified_files.
3. ruff E9,F63,F7,F82 по этим файлам.
4. pytest по изменённым тестам и тестам, упоминающим изменённые модули (до MAX_TESTS файлов).
5. Каждое падение перепроверяется на базовой версии кода (git archive <base> во временную
   папку, git только читается). Агента просят чинить только НОВЫЕ поломки — старые красные
   тесты репозитория не зацикливают агента.
6. Результат кэшируется по подписи содержимого. Повторный stop с тем же деревом
   не гоняет проверки и не шлёт followup ещё раз.

Отключить: переменная окружения VP_HOOKS_VERIFY=0.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vp_common import (  # noqa: E402
    RUFF_SELECT, git, lint_errors, log, main_checkout, project_python, project_root,
    read_input, respond, state_dir, toplevel,
)

MAX_TESTS = 15
PYTEST_TIMEOUT = int(os.environ.get("VP_HOOKS_PYTEST_TIMEOUT", "600"))
NODE_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$", re.M)


# ---------------------------------------------------------------- сбор изменений
def repo_roots(data: dict) -> dict[Path, set[str] | None]:
    """{корень worktree: явный список файлов или None (вычислить по git)}."""
    root = project_root()
    main = main_checkout(root)
    roots: dict[Path, set[str] | None] = {}
    files = data.get("modified_files")
    if isinstance(files, list) and files:
        for f in files:
            p = Path(f) if Path(f).is_absolute() else root / f
            top = toplevel(p) if p.exists() else toplevel(p.parent) if p.parent.exists() else None
            if top:
                roots.setdefault(top, set()).add(p.resolve().relative_to(top).as_posix())
        return roots
    roots[main] = None
    code, out = git(main, "worktree", "list", "--porcelain")
    if code == 0:
        for line in out.splitlines():
            if line.startswith("worktree "):
                wt = Path(line[9:]).resolve()
                if wt != main and wt.exists() and include_worktree(main, wt):
                    roots[wt] = None
    return roots


def include_worktree(main: Path, wt: Path) -> bool:
    """Только основной checkout и папки внутри `.worktrees/`."""
    wt = wt.resolve()
    main = main.resolve()
    if wt == main:
        return True
    try:
        wt.relative_to((main / ".worktrees").resolve())
    except ValueError:
        return False
    return True


def head_sha(root: Path) -> str:
    code, out = git(root, "rev-parse", "HEAD")
    return out.splitlines()[0].strip() if code == 0 and out.strip() else "HEAD"


def mark_state(cache: dict) -> dict:
    rec = cache.get("_marks")
    if not isinstance(rec, dict) or not isinstance(rec.get("roots"), dict):
        rec = {"ts": time.time(), "roots": {}}
        cache["_marks"] = rec
    return rec


def advance_mark(root: Path, cache: dict) -> None:
    rec = mark_state(cache)
    rec["roots"][str(root.resolve())] = {"head": head_sha(root)}
    rec["ts"] = time.time()


def porcelain_paths(root: Path) -> set[str]:
    files: set[str] = set()
    code, out = git(root, "status", "--porcelain", "--untracked-files=all")
    if code != 0:
        return files
    for line in out.splitlines():
        if len(line) > 3 and "D" not in line[:2]:
            files.add(line[3:].split(" -> ")[-1].strip('"'))
    return files


def diff_paths(root: Path, base: str) -> set[str]:
    code, out = git(root, "diff", "--name-only", "--diff-filter=ACMR", base, "HEAD")
    if code != 0:
        return set()
    return {x for x in out.splitlines() if x}


def py_existing(root: Path, names: set[str]) -> set[str]:
    return {
        f for f in names
        if f.endswith(".py")
        and not f.startswith((".worktrees/", ".cursor/"))
        and (root / f).exists()
    }


def scope_files(root: Path, cache: dict, explicit: set[str] | None) -> tuple[set[str], str]:
    """Файлы этого захода и база сравнения.

    Первый запуск запоминает HEAD: история ветки не становится списком долгов.
    Дальше в список попадают незакоммиченное и коммиты после отметки.
    """
    head = head_sha(root)
    rec = mark_state(cache)
    key = str(root.resolve())
    slot = rec["roots"].get(key)
    prev = slot.get("head") if isinstance(slot, dict) else None
    if not prev:
        rec["roots"][key] = {"head": head}
        rec["ts"] = time.time()
        names = set(explicit) if explicit is not None else porcelain_paths(root)
        return py_existing(root, names), "HEAD"
    names = set(explicit) if explicit is not None else porcelain_paths(root)
    base = "HEAD"
    if prev != head:
        names |= diff_paths(root, prev)
        base = prev
    return py_existing(root, names), base


def deliver(cache: dict, sig: str, report: str, old: list[str]) -> str:
    """Один и тот же отчёт не повторяем на следующем stop."""
    if sig in cache:
        return ""
    cache[sig] = {"report": report, "ts": time.time(), "old_failures": old[:50]}
    return report


def select_tests(root: Path, files: set[str]) -> list[str]:
    tests_dir = root / "tests"
    chosen = [f for f in sorted(files) if re.match(r"tests/(.+/)?test_[^/]+\.py$", f)]
    modules = sorted({
        Path(f).stem for f in files
        if not f.startswith("tests/") and Path(f).stem not in ("__init__", "conftest") and len(Path(f).stem.strip("_")) >= 3
    })
    if not modules or not tests_dir.is_dir():
        return chosen[:MAX_TESTS]
    heads: dict[str, str] = {}
    for t in sorted(tests_dir.rglob("test_*.py")):
        try:
            heads[t.relative_to(root).as_posix()] = t.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            pass
    per_module: list[list[str]] = []
    for m in modules:
        name_rx = re.compile(rf"(^|_){re.escape(m.strip('_'))}(_|$)")
        import_rx = re.compile(rf"^\s*(from|import)\s[^\n]*\b{re.escape(m)}\b", re.M)
        by_name = [r for r in heads if name_rx.search(Path(r).stem[5:])]
        by_import = [r for r, h in heads.items() if r not in by_name and import_rx.search(h)]
        per_module.append(by_name + by_import)
    while len(chosen) < MAX_TESTS and any(per_module):
        for queue in per_module:
            while queue:
                cand = queue.pop(0)
                if cand not in chosen:
                    chosen.append(cand)
                    break
    return chosen[:MAX_TESTS]


# ---------------------------------------------------------------- проверки
def run_pytest(py: list[str], cwd: Path, targets: list[str], timeout: int) -> tuple[dict[str, str], str, bool]:
    """{node_id: краткая причина}, хвост вывода, timed_out."""
    if not targets:
        return {}, "", False
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    cmd = [*py, "-m", "pytest", "-q", "--no-header", "-rfE", "--tb=short", "--disable-warnings", "-p", "no:cacheprovider", "--color=no", *targets]
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {}, f"pytest не уложился в {timeout} c", True
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    fails = {m.group(2): (m.group(3) or "")[:300] for m in NODE_RE.finditer(out)}
    if proc.returncode not in (0, 1, 5) and not fails:
        fails = {"<pytest>": f"код выхода {proc.returncode}"}
    tail = "\n".join(out.strip().splitlines()[-40:])
    return fails, tail, False


def extract_base(root: Path, base: str) -> Path | None:
    tmp = Path(tempfile.mkdtemp(prefix="vp-base-"))
    archive = tmp / "base.tar"
    code, _ = git(root, "archive", "--format=tar", "-o", str(archive), base, timeout=120)
    if code != 0 or not archive.exists():
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    with tarfile.open(archive) as tar:
        try:
            tar.extractall(tmp / "src", filter="data")
        except TypeError:
            tar.extractall(tmp / "src")
    archive.unlink()
    return tmp


def signature(root: Path, base: str, files: set[str]) -> str:
    h = hashlib.sha256(f"{root}|{base}".encode() + Path(__file__).read_bytes())
    for f in sorted(files):
        try:
            h.update(f.encode() + b"\0" + (root / f).read_bytes())
        except OSError:
            h.update(f.encode() + b"\0missing")
    return h.hexdigest()


def load_cache() -> dict:
    try:
        return json.loads((state_dir() / "verify_cache.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict) -> None:
    marks = cache.get("_marks")
    items = [(k, v) for k, v in cache.items() if k != "_marks" and isinstance(v, dict)]
    items = sorted(items, key=lambda kv: kv[1].get("ts", 0))[-100:]
    out = dict(items)
    if isinstance(marks, dict):
        marks["ts"] = time.time()
        out["_marks"] = marks
    (state_dir() / "verify_cache.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def verify_root(root: Path, explicit: set[str] | None, cache: dict) -> str:
    files, base = scope_files(root, cache, explicit)
    if not files:
        advance_mark(root, cache)
        return ""
    sig = signature(root, base, files)
    if sig in cache:
        advance_mark(root, cache)
        log("verify", f"{root} cache_hit suppress")
        return ""

    py = project_python(root)
    tests = select_tests(root, files)
    ruff_now = lint_errors(py, root, sorted(files))
    fails, tail, timed_out = run_pytest(py, root, tests, PYTEST_TIMEOUT)

    new_ruff, new_fails = set(ruff_now), dict(fails)
    if ruff_now or fails:
        tmp = extract_base(root, base)
        if tmp:
            try:
                src = tmp / "src"
                in_base = [f for f in sorted(files) if (src / f).exists()]
                new_ruff -= lint_errors(py, src, in_base)
                old_nodes = [n for n in fails if n != "<pytest>" and (src / n.split("::")[0]).exists()]
                if old_nodes:
                    base_fails, _, _ = run_pytest(py, src, old_nodes, max(120, PYTEST_TIMEOUT // 2))
                    for node in old_nodes:
                        if node in base_fails:
                            new_fails.pop(node, None)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    lines = []
    if new_ruff:
        lines.append("Ошибки синтаксиса/неопределённые имена (ruff " + RUFF_SELECT + "):")
        lines += [f"  - {p}" for p in sorted(new_ruff)[:30]]
    if timed_out:
        lines.append(f"pytest не уложился в {PYTEST_TIMEOUT} с на тестах: {' '.join(tests)}")
    if new_fails:
        lines.append("НОВЫЕ падения тестов (на базовой версии эти тесты проходили):")
        lines += [f"  - {n} {('— ' + r) if r else ''}" for n, r in sorted(new_fails.items())[:30]]
        lines.append("Хвост вывода pytest:\n" + tail)
    report = ""
    if lines:
        where = root.as_posix()
        report = (
            f"[{where}] база сравнения: {base[:10]}, тесты: {' '.join(tests) or 'не найдены'}\n" + "\n".join(lines)
        )
    old = sorted(set(fails) - set(new_fails))
    log("verify", f"{root} files={len(files)} tests={len(tests)} new_fail={len(new_fails)} old_fail={len(old)} ruff_new={len(new_ruff)}")
    advance_mark(root, cache)
    return deliver(cache, sig, report, old)


def main() -> None:
    data = read_input()
    skip = os.environ.get("VP_HOOKS_VERIFY", "1") == "0" or data.get("status") not in (None, "completed")
    if "subagent_type" in data and not data.get("modified_files"):
        skip = True
    if skip:
        respond({})
        return
    cache = load_cache()
    reports = []
    try:
        for root, explicit in repo_roots(data).items():
            report = verify_root(root, explicit, cache)
            if report:
                reports.append(report)
    except Exception as exc:
        log("verify", f"error: {exc!r}")
    finally:
        save_cache(cache)
    if not reports:
        respond({})
        return
    loop = int(data.get("loop_count") or 0)
    who = "Субагент завершился" if "subagent_type" in data else "Ты завершил ход"
    msg = (
        f"vp-verify: {who}, но проверка нашла новые проблемы. Исправь их (superpowers:systematic-debugging), "
        "перезапусти эти тесты и только потом заявляй о готовности.\n\n" + "\n\n".join(reports)
    )
    if loop >= 2:
        msg += (
            "\n\nЭто уже повторная попытка. Если причина вне твоей задачи или починить нельзя — "
            "не маскируй тест, а честно опиши в отчёте, что осталось красным и почему."
        )
    respond({"followup_message": msg[:12000]})


if __name__ == "__main__":
    main()
