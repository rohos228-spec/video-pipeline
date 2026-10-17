from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

MARKER = ".e2e_scratch_marker"
REPO_ROOT = Path(__file__).resolve().parent.parent


def refuse(message: str) -> "SystemExit":
    return SystemExit("ОТКАЗ e2e: " + message)


def live_data_dirs(repo_root: Path = REPO_ROOT, env: dict | None = None) -> list[Path]:
    env = os.environ if env is None else env
    out = [repo_root / "data"]
    if env.get("DATA_DIR"):
        out.append(Path(env["DATA_DIR"]))
    if env.get("SQLITE_PATH"):
        out.append(Path(env["SQLITE_PATH"]).parent)
    return [p.resolve() for p in out]


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _overlap(a: Path, b: Path) -> bool:
    return a == b or _inside(a, b) or _inside(b, a)


def assert_safe_layout(seed: Path, work: Path, *, repo_root: Path = REPO_ROOT, env: dict | None = None) -> Path:
    seed, work = Path(seed).resolve(), Path(work).resolve()
    data = work / "data"
    for live in live_data_dirs(repo_root, env):
        if _overlap(work, live) or _overlap(data, live):
            raise refuse(f"рабочая папка {work} пересекается с боевыми данными {live}. "
                         "Укажите отдельную пустую папку вне репозитория и вне data.")
    if _overlap(data, seed) or work == seed or _inside(seed, work):
        raise refuse(f"рабочая папка {work} пересекается с семенем {seed}: копирование затёрло бы исходные данные.")
    if work == Path(work.anchor) or work == Path.home().resolve() or work == repo_root.resolve():
        raise refuse(f"рабочая папка {work} слишком общая (корень диска, домашняя папка или репозиторий).")
    if data.exists() and any(data.iterdir()) and not (work / MARKER).exists():
        raise refuse(f"{data} уже существует и создана не e2e-скриптами (нет маркера {MARKER}). "
                     "Удалите её вручную или выберите другую рабочую папку.")
    return data


def prepare_scratch_data(seed: Path, work: Path, *, repo_root: Path = REPO_ROOT, env: dict | None = None) -> Path:
    data = assert_safe_layout(seed, work, repo_root=repo_root, env=env)
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if data.exists():
        shutil.rmtree(data)
    shutil.copytree(Path(seed).resolve(), data)
    (work / MARKER).write_text("created by scripts/e2e_*.py; only <work>/data may be removed by them\n", encoding="utf-8")
    return data


def reset_scratch_data(work: Path, *, repo_root: Path = REPO_ROOT, env: dict | None = None) -> Path:
    work = Path(work).resolve()
    data = work / "data"
    for live in live_data_dirs(repo_root, env):
        if _overlap(work, live) or _overlap(data, live):
            raise refuse(f"рабочая папка {work} пересекается с боевыми данными {live}.")
    if work == Path(work.anchor) or work == Path.home().resolve() or work == repo_root.resolve():
        raise refuse(f"рабочая папка {work} слишком общая.")
    if data.exists() and any(data.iterdir()) and not (work / MARKER).exists():
        raise refuse(f"{data} уже существует и создана не e2e-скриптами (нет маркера {MARKER}).")
    work.mkdir(parents=True, exist_ok=True)
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    (work / MARKER).write_text("created by scripts/e2e_*.py\n", encoding="utf-8")
    return data


def assert_scratch_file(path: Path, work: Path, *, repo_root: Path = REPO_ROOT, env: dict | None = None) -> Path:
    path, work = Path(path).resolve(), Path(work).resolve()
    if not _inside(path, work):
        raise refuse(f"запись в {path} запрещена: файл вне рабочей папки {work}.")
    for live in live_data_dirs(repo_root, env):
        if _inside(path, live) or path == live:
            raise refuse(f"запись в {path} запрещена: это боевые данные ({live}).")
    return path


class _ClosingConnection(sqlite3.Connection):

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect_scratch(path: Path, work: Path, **kw) -> sqlite3.Connection:
    return sqlite3.connect(assert_scratch_file(path, work), factory=_ClosingConnection, **kw)


def assert_scratch_data_dir(data_dir: Path, *, repo_root: Path = REPO_ROOT, env: dict | None = None) -> Path:
    d = Path(data_dir).resolve()
    for live in live_data_dirs(repo_root, env):
        if _overlap(d, live):
            raise refuse(f"папка данных {d} совпадает с боевой или пересекается с ней ({live}). "
                         "Скрипт работает только с копией: не указывайте --data-dir или дайте другую папку.")
    return d


def new_scratch_dir(prefix: str = "e2e_") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def resolve_project_id(state_db: Path, slug: str | None, explicit: int | None = None) -> int:
    if explicit is not None:
        return int(explicit)
    if not slug:
        raise refuse("не указан ни --project-id, ни --slug.")
    with sqlite3.connect(f"file:{Path(state_db).as_posix()}?mode=ro", uri=True) as db:
        row = db.execute("SELECT id FROM projects WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise refuse(f"в {state_db} нет проекта со slug={slug!r}; укажите --project-id или --slug.")
    return int(row[0])
