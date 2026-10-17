from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sqlite3
import time
import zlib
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DAYS = 90
MIN_DAYS = 1
ARCHIVE_DIRNAME = "archive"
ARCHIVE_LOG_TABLE = "journal_archive_log"
_CHUNK = 500

ARCHIVE_LOG_DDL = (
    "CREATE TABLE IF NOT EXISTS journal_archive_log ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "created_at TEXT NOT NULL, table_name TEXT NOT NULL, cutoff TEXT NOT NULL, "
    "rows_archived INTEGER NOT NULL, rows_deleted INTEGER NOT NULL, "
    "min_id INTEGER, max_id INTEGER, sum_before REAL, sum_after REAL, rollup_rows INTEGER NOT NULL DEFAULT 0, "
    "archive_file TEXT NOT NULL, sha256 TEXT NOT NULL, note TEXT NOT NULL DEFAULT '')",
    "CREATE TRIGGER IF NOT EXISTS journal_archive_log_no_update BEFORE UPDATE ON "
    "journal_archive_log BEGIN SELECT RAISE(ABORT, 'journal archive log is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS journal_archive_log_no_delete BEFORE DELETE ON "
    "journal_archive_log BEGIN SELECT RAISE(ABORT, 'journal archive log is append-only'); END",
)


@dataclass(frozen=True)
class Rollup:
    group: tuple[str, ...]
    sums: tuple[str, ...]
    kind: str = "archive"
    kind_column: str = "kind"
    check_sum: str = ""
    take_max: tuple[str, ...] = ()
    constants: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class Spec:
    table: str
    ts: str
    where: str = ""
    rollup: Rollup | None = None
    why_ru: str = ""


SPECS: tuple[Spec, ...] = (
    Spec("spend_ledger", "created_at", where="kind <> 'archive'",
         rollup=Rollup(("day", "provider", "project_id", "run_id", "source"), ("usd", "credits"),
                       check_sum="usd", take_max=("created_at",),
                       constants=(("note", "архив: свёртка старых строк"),)),
         why_ru="траты: остаются свёрточные строки, потолки не ослабляются"),
    Spec("provider_task_ledger", "created_at",
         where="task_id NOT IN (SELECT task_id FROM provider_task_ledger WHERE created_at >= :cutoff)",
         why_ru="задачи провайдера: только те, у которых все события старые (состояние = последняя строка)"),
    Spec("action_layer_history", "created_at", why_ru="история слоя действий"),
    Spec("action_layer_spend", "created_at", where="kind <> 'archive'",
         rollup=Rollup(("session_key", "plan_id"), ("credits",), check_sum="credits", take_max=("created_at",),
                       constants=(("step_index", 0),)),
         why_ru="кредиты слоя действий: остаётся свёртка по сессии, потолок сессии сохраняется"),
    Spec("operator_audit", "ts", why_ru="аудит изменяющих запросов оператора"),
    Spec("observation_events", "created_at", why_ru="события наблюдения"),
    Spec("observation_outbox", "created_at",
         where="event_id IN (SELECT event_id FROM observation_events)",
         why_ru="исходящая очередь наблюдения: только уже доставленные в события"),
    Spec("decision_turns", "created_at",
         where="decision_id IN (SELECT decision_id FROM decision_records WHERE status = 'done')",
         why_ru="реплики решений: только завершённые решения"),
)


class ArchiveError(RuntimeError):
    pass


@dataclass
class TableResult:
    table: str
    status: str = "ok"
    candidates: int = 0
    min_id: int | None = None
    max_id: int | None = None
    sum_before: float | None = None
    sum_after: float | None = None
    rollup_rows: int = 0
    deleted: int = 0
    archive_file: str = ""
    sha256: str = ""
    error: str = ""
    why_ru: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ArchiveReport:
    apply: bool
    days: int
    cutoff: str
    db: str
    archive_dir: str
    tables: list[TableResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(t.status != "failed" for t in self.tables)

    def as_dict(self) -> dict:
        return {"apply": self.apply, "days": self.days, "cutoff": self.cutoff, "db": self.db,
                "archive_dir": self.archive_dir, "ok": self.ok, "tables": [t.as_dict() for t in self.tables]}

    def summary_ru(self) -> str:
        mode = "ПРИМЕНЕНО" if self.apply else "сухой прогон (ничего не изменено)"
        lines = [f"Архив журналов, {mode}. Строки старше {self.days} дн. (до {self.cutoff})."]
        for t in self.tables:
            if t.status == "missing":
                lines.append(f"  {t.table}: таблицы нет в этой базе")
            elif t.status == "failed":
                lines.append(f"  {t.table}: ОШИБКА, ничего не удалено: {t.error}")
            elif t.candidates == 0:
                lines.append(f"  {t.table}: архивировать нечего")
            elif self.apply:
                lines.append(f"  {t.table}: перенесено {t.deleted} строк в {t.archive_file}")
            else:
                lines.append(f"  {t.table}: к переносу {t.candidates} строк (id {t.min_id}..{t.max_id})")
        return "\n".join(lines)


def _cutoff_text(days: int, now: datetime | None) -> str:
    return ((now or datetime.utcnow()) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.%f")


def _qi(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ArchiveError(f"недопустимое имя: {name!r}")
    return '"' + name + '"'


def _has_table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(db: sqlite3.Connection, table: str) -> list[tuple[str, str, int, object]]:
    return [(r[1], (r[2] or "").upper(), r[3], r[4]) for r in db.execute(f"PRAGMA table_info({_qi(table)})")]


def _where(spec: Spec) -> str:
    extra = f" AND ({spec.where})" if spec.where else ""
    return f"{_qi(spec.ts)} < :cutoff{extra}"


def _select_ids(db: sqlite3.Connection, spec: Spec, cutoff: str) -> list[int]:
    return [r[0] for r in db.execute(
        f"SELECT id FROM {_qi(spec.table)} WHERE {_where(spec)} ORDER BY id", {"cutoff": cutoff})]


def _sum(db: sqlite3.Connection, table: str, col: str) -> float:
    return float(db.execute(f"SELECT COALESCE(SUM({_qi(col)}), 0.0) FROM {_qi(table)}").fetchone()[0] or 0.0)


def _jsonable(value):
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    return value


def _delete_triggers(db: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    out = []
    for name, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)):
        if sql and " DELETE " in " ".join(sql.upper().split()):
            out.append((name, sql))
    return out


def _all_triggers(db: sqlite3.Connection, table: str) -> dict[str, str]:
    return {n: " ".join((s or "").split()) for n, s in
            db.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,))}


def _export(db: sqlite3.Connection, spec: Spec, ids: list[int], cutoff: str, archive_dir: Path, stamp: str) -> dict:
    archive_dir.mkdir(parents=True, exist_ok=True)
    final = archive_dir / f"{spec.table}_{stamp}.jsonl.gz"
    part = final.with_name(final.name + ".part")
    if final.exists():
        raise ArchiveError(f"файл архива уже есть: {final.name}")
    cols = [c[0] for c in _columns(db, spec.table)]
    idset = set(ids)
    written = 0
    try:
        with open(part, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
                cur = db.execute(f"SELECT {', '.join(_qi(c) for c in cols)} FROM {_qi(spec.table)} "
                                 f"WHERE {_where(spec)} ORDER BY id", {"cutoff": cutoff})
                for row in cur:
                    rec = {c: _jsonable(v) for c, v in zip(cols, row)}
                    if rec["id"] not in idset:
                        raise ArchiveError("набор строк изменился во время экспорта")
                    gz.write((json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8"))
                    written += 1
            raw.flush()
            os.fsync(raw.fileno())
        if written != len(ids):
            raise ArchiveError(f"экспортировано {written} из {len(ids)} строк")
        digest = hashlib.sha256(part.read_bytes()).hexdigest()
        os.replace(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    manifest = {"table": spec.table, "file": final.name, "sha256": digest, "rows": written,
                "min_id": ids[0], "max_id": ids[-1], "cutoff": cutoff, "columns": cols,
                "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z", "format": "jsonl.gz/v1"}
    final.with_name(final.name + ".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": final, "sha256": digest, "manifest": manifest}


def verify_archive(path: Path, expected_ids: list[int] | None = None) -> dict:
    path = Path(path)
    problems: list[str] = []
    rows, ids = 0, []
    digest = ""
    manifest_path = path.with_name(path.name + ".manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None
        problems.append("манифест не читается")
    try:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if manifest and manifest.get("sha256") != digest:
            problems.append("sha256 не совпадает с манифестом: архив изменён или повреждён")
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as gz:
            for line in gz:
                rec = json.loads(line.decode("utf-8"))
                rows += 1
                ids.append(rec["id"])
    except (OSError, ValueError, EOFError, KeyError, zlib.error) as exc:
        problems.append(f"архив не читается: {type(exc).__name__}")
    if manifest and manifest.get("rows") != rows:
        problems.append(f"в манифесте {manifest.get('rows')} строк, в архиве {rows}")
    if expected_ids is not None and sorted(ids) != sorted(expected_ids):
        problems.append("набор id в архиве не равен набору id в базе")
    return {"ok": not problems, "problems": problems, "rows": rows, "sha256": digest}


def _rollup_rows(db: sqlite3.Connection, spec: Spec, cutoff: str) -> list[dict]:
    ru = spec.rollup
    assert ru is not None
    have = {c[0] for c in _columns(db, spec.table)}
    group = [g for g in ru.group if g in have]
    sums = [s for s in ru.sums if s in have]
    maxes = [m for m in ru.take_max if m in have]
    sel = ", ".join([*(_qi(g) for g in group), *(f"SUM({_qi(s)}) AS {_qi(s)}" for s in sums),
                     *(f"MAX({_qi(m)}) AS {_qi(m)}" for m in maxes), "COUNT(*) AS _n"])
    grp = f" GROUP BY {', '.join(_qi(g) for g in group)}" if group else ""
    out = []
    cur = db.execute(f"SELECT {sel} FROM {_qi(spec.table)} WHERE {_where(spec)}{grp}", {"cutoff": cutoff})
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur]


def _insert_rollups(db: sqlite3.Connection, spec: Spec, groups: list[dict], now_text: str) -> int:
    ru = spec.rollup
    assert ru is not None
    cols = _columns(db, spec.table)
    consts = dict(ru.constants)
    n = 0
    for g in groups:
        rec: dict = {}
        for name, ctype, notnull, default in cols:
            if name == "id":
                continue
            if name in g and name != "_n":
                rec[name] = g[name]
            elif name == ru.kind_column:
                rec[name] = ru.kind
            elif name in consts:
                rec[name] = consts[name]
                if name == "note":
                    rec[name] = f"{consts[name]} ({g['_n']} шт.)"
            elif name == spec.ts:
                rec[name] = now_text
            elif default is None and notnull:
                rec[name] = 0 if any(t in ctype for t in ("INT", "REAL", "FLOA", "NUM", "DOUB")) else ""
        names = list(rec)
        db.execute(f"INSERT INTO {_qi(spec.table)} ({', '.join(_qi(c) for c in names)}) "
                   f"VALUES ({', '.join('?' for _ in names)})", [rec[c] for c in names])
        n += 1
    return n


def _apply_one(db: sqlite3.Connection, spec: Spec, ids: list[int], cutoff: str, exported: dict,
               archive_dir: Path, res: TableResult) -> None:
    check = verify_archive(exported["path"], ids)
    if not check["ok"]:
        raise ArchiveError("экспорт не прошёл проверку: " + "; ".join(check["problems"]))
    for ddl in ARCHIVE_LOG_DDL:
        db.execute(ddl)
    db.execute("BEGIN IMMEDIATE")
    try:
        again = _select_ids(db, spec, cutoff)
        if again != ids:
            raise ArchiveError("набор строк изменился после экспорта")
        before_triggers = _all_triggers(db, spec.table)
        dropped = _delete_triggers(db, spec.table)
        ru = spec.rollup
        sum_before = _sum(db, spec.table, ru.check_sum) if ru and ru.check_sum else None
        rollup_groups = _rollup_rows(db, spec, cutoff) if ru else []
        for name, _sql in dropped:
            db.execute(f"DROP TRIGGER {_qi(name)}")
        deleted = 0
        for i in range(0, len(ids), _CHUNK):
            part = ids[i:i + _CHUNK]
            cur = db.execute(f"DELETE FROM {_qi(spec.table)} WHERE id IN ({','.join('?' for _ in part)})", part)
            deleted += cur.rowcount
        if deleted != len(ids):
            raise ArchiveError(f"удалено {deleted} строк вместо {len(ids)}")
        inserted = _insert_rollups(db, spec, rollup_groups, cutoff) if ru else 0
        sum_after = _sum(db, spec.table, ru.check_sum) if ru and ru.check_sum else None
        if sum_before is not None and abs(sum_before - (sum_after or 0.0)) > 1e-6 * max(1.0, abs(sum_before)):
            raise ArchiveError(f"сумма {ru.check_sum} изменилась: {sum_before} -> {sum_after}")
        for _name, sql in dropped:
            db.execute(sql)
        if _all_triggers(db, spec.table) != before_triggers:
            raise ArchiveError("триггеры после архивации не совпали с исходными")
        db.execute(
            f"INSERT INTO {ARCHIVE_LOG_TABLE} (created_at, table_name, cutoff, rows_archived, rows_deleted, "
            "min_id, max_id, sum_before, sum_after, rollup_rows, archive_file, sha256, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(timespec="seconds") + "Z", spec.table, cutoff, len(ids), deleted,
             ids[0], ids[-1], sum_before, sum_after, inserted, exported["path"].name, exported["sha256"],
             spec.why_ru))
        db.execute("COMMIT")
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    res.deleted, res.sum_before, res.sum_after, res.rollup_rows = deleted, sum_before, sum_after, inserted


def archive_journals(db_path: str | Path, *, days: int = DEFAULT_DAYS, apply: bool = False,
                     archive_dir: str | Path | None = None, tables: list[str] | None = None,
                     now: datetime | None = None) -> ArchiveReport:
    if days < MIN_DAYS:
        raise ArchiveError(f"days должно быть не меньше {MIN_DAYS}")
    db_path = Path(db_path)
    if not db_path.is_file():
        raise ArchiveError(f"базы нет: {db_path}")
    out_dir = Path(archive_dir) if archive_dir else db_path.parent / ARCHIVE_DIRNAME
    cutoff = _cutoff_text(days, now)
    report = ArchiveReport(apply=apply, days=days, cutoff=cutoff, db=str(db_path), archive_dir=str(out_dir))
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + f"-{os.getpid()}"
    uri = f"file:{db_path.as_posix()}?mode={'rw' if apply else 'ro'}"
    with closing(sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)) as db:
        for spec in SPECS:
            if tables and spec.table not in tables:
                continue
            res = TableResult(spec.table, why_ru=spec.why_ru)
            report.tables.append(res)
            if not _has_table(db, spec.table):
                res.status = "missing"
                continue
            try:
                ids = _select_ids(db, spec, cutoff)
                res.candidates = len(ids)
                if not ids:
                    res.status = "empty"
                    continue
                res.min_id, res.max_id = ids[0], ids[-1]
                if not apply:
                    res.status = "dry-run"
                    continue
                exported = _export(db, spec, ids, cutoff, out_dir, stamp)
                res.archive_file, res.sha256 = str(exported["path"]), exported["sha256"]
                _apply_one(db, spec, ids, cutoff, exported, out_dir, res)
            except Exception as exc:
                res.status, res.error = "failed", f"{type(exc).__name__}: {exc}"[:300]
    return report
