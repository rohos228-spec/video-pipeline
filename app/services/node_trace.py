"""Дневник нод: что ответил GPT и что после него поменяла программа.

Прогон ноды → ``<data_dir>/node_trace/<node_key>/<время>/``::

    run.json            нода, вид пачек, время, итоги
    all_frames.json.gz  контекст всех кадров (для повтора без GPT)
    call_NN_LX/
        input.json      кадры пачки, что ушли в GPT
        reply.txt       сырой ответ GPT
        ops_gpt.json    ops как их вернул GPT
        ops_final.json  ops после правок программы (это пишется в БД)
        diary.jsonl     записи дневника: кадр, поле, было → стало, правило

Запись дневника никогда не роняет ноду: ошибки диска только в debug-лог.
"""

from __future__ import annotations

import copy
import gzip
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

TRACE_DIRNAME = "node_trace"
KEEP_RUNS_PER_NODE = 10
_run_seq = 0
_VALUE_MAX = 400
_SHOT_LIST_KEYS = ("кадры", "shots")


def trace_root(data_dir: Path | str) -> Path:
    return Path(data_dir) / TRACE_DIRNAME


def short_value(value: Any, limit: int = _VALUE_MAX) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def entry(
    *,
    rule: str,
    pass_name: str,
    kind: str = "fix",
    frame_uuid: str = "",
    shot: int | None = None,
    shot_id: str = "",
    field_name: str = "",
    before: Any = None,
    after: Any = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "frame_uuid": frame_uuid,
        "shot": shot,
        "shot_id": shot_id,
        "field": field_name,
        "before": short_value(before),
        "after": short_value(after),
        "rule": rule,
        "pass": pass_name,
        "kind": kind,
        "note": note,
    }


def _fields(op: Any) -> dict[str, Any]:
    if isinstance(op, dict) and isinstance(op.get("fields"), dict):
        return op["fields"]
    return {}


def _by_uid(ops: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for op in ops or []:
        if isinstance(op, dict):
            uid = str(op.get("frame_uuid") or "").strip()
            if uid:
                out[uid] = op
    return out


def _shot_list(fields: dict[str, Any]) -> list[dict[str, Any]] | None:
    for key in _SHOT_LIST_KEYS:
        raw = fields.get(key)
        if isinstance(raw, list):
            return [s if isinstance(s, dict) else {"значение": s} for s in raw]
    return None


def _shot_pairs(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[tuple[int | None, dict[str, Any] | None, int | None, dict[str, Any] | None]]:
    """Пары кадров до/после: по id, если он уникален в обоих списках, иначе по номеру."""
    ids_b = [str(s.get("id") or "") for s in before]
    ids_a = [str(s.get("id") or "") for s in after]
    unique = (
        all(ids_b)
        and all(ids_a)
        and len(set(ids_b)) == len(ids_b)
        and len(set(ids_a)) == len(ids_a)
    )
    pairs: list[tuple[int | None, dict[str, Any] | None, int | None, dict[str, Any] | None]] = []
    if unique:
        pos_b = {sid: i for i, sid in enumerate(ids_b)}
        for j, sid in enumerate(ids_a):
            i = pos_b.pop(sid, None)
            pairs.append((i, before[i] if i is not None else None, j, after[j]))
        for i in pos_b.values():
            pairs.append((i, before[i], None, None))
        return pairs
    for k in range(max(len(before), len(after))):
        b = before[k] if k < len(before) else None
        a = after[k] if k < len(after) else None
        pairs.append((k if b is not None else None, b, k if a is not None else None, a))
    return pairs


@dataclass
class Change:
    frame_uuid: str
    field: str
    before: Any
    after: Any
    shot: int | None = None
    shot_id: str = ""


def diff_ops(before: list[Any], after: list[Any]) -> list[Change]:
    """Что поменялось в ops: поле кадра, поле внутри dict (1 уровень), кадры[] по шагам."""
    changes: list[Change] = []
    b_by, a_by = _by_uid(before), _by_uid(after)
    for uid, a_op in a_by.items():
        fb, fa = _fields(b_by.get(uid)), _fields(a_op)
        for key in sorted(set(fb) | set(fa)):
            vb, va = fb.get(key), fa.get(key)
            if vb == va:
                continue
            if key in _SHOT_LIST_KEYS and isinstance(vb, list) and isinstance(va, list):
                changes.extend(_diff_shots(uid, _shot_list(fb) or [], _shot_list(fa) or []))
            elif isinstance(vb, dict) and isinstance(va, dict):
                for sub in sorted(set(vb) | set(va)):
                    if vb.get(sub) != va.get(sub):
                        changes.append(Change(uid, f"{key}.{sub}", vb.get(sub), va.get(sub)))
            else:
                changes.append(Change(uid, key, vb, va))
    for uid in b_by:
        if uid not in a_by:
            changes.append(Change(uid, "op", "есть", "удалён"))
    return changes


def _diff_shots(
    uid: str, before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[Change]:
    out: list[Change] = []
    for i, b, j, a in _shot_pairs(before, after):
        if a is None and b is not None:
            out.append(Change(uid, "кадр", b.get("действие") or "кадр", "удалён",
                              shot=(i or 0) + 1, shot_id=str(b.get("id") or "")))
            continue
        if a is None:
            continue
        n = (j or 0) + 1
        sid = str(a.get("id") or "")
        if b is None:
            out.append(Change(uid, "кадр", None, f"добавлен: {a.get('действие') or ''}".strip(),
                              shot=n, shot_id=sid))
            continue
        for key in sorted(set(b) | set(a)):
            if b.get(key) != a.get(key):
                out.append(Change(uid, key, b.get(key), a.get(key), shot=n, shot_id=sid))
    return out


def changes_to_entries(
    changes: list[Change], *, rule: str, pass_name: str
) -> list[dict[str, Any]]:
    return [
        entry(
            rule=rule,
            pass_name=pass_name,
            frame_uuid=c.frame_uuid,
            shot=c.shot,
            shot_id=c.shot_id,
            field_name=c.field,
            before=c.before,
            after=c.after,
        )
        for c in changes
    ]


_UUID_IN_TEXT_RE = re.compile(r"\b(?:uuid[:\s]+)?([0-9a-f]{6,64})\b", re.IGNORECASE)


def uuid_in_text(text: str, known: list[str]) -> str:
    """uuid кадра, упомянутый в тексте предупреждения (по префиксу)."""
    for m in _UUID_IN_TEXT_RE.finditer(text or ""):
        frag = m.group(1).lower()
        for uid in known:
            if uid.lower().startswith(frag) or frag.startswith(uid.lower()):
                return uid
    return ""


def snapshot(ops: list[Any]) -> list[Any]:
    return copy.deepcopy(ops)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", str(text or "node"))[:80] or "node"


@dataclass
class NodeRunRecorder:
    """Один прогон ноды. Все методы глотают ошибки диска."""

    data_dir: Path
    node_key: str
    kind: str
    dir: Path | None = None
    calls: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    started: str = ""

    @classmethod
    def start(
        cls,
        data_dir: Path | str,
        node_key: str,
        *,
        kind: str,
        all_frames: list[dict[str, Any]],
    ) -> NodeRunRecorder:
        rec = cls(Path(data_dir), node_key, kind)
        rec.started = datetime.now().isoformat(timespec="seconds")
        try:
            global _run_seq
            _run_seq += 1
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + f"-{_run_seq}"
            run_dir = trace_root(data_dir) / _safe_name(node_key) / stamp
            run_dir.mkdir(parents=True, exist_ok=True)
            with gzip.open(run_dir / "all_frames.json.gz", "wt", encoding="utf-8") as fh:
                json.dump(all_frames, fh, ensure_ascii=False)
            rec.dir = run_dir
            rec._write_run(status="running")
            _prune(run_dir.parent, keep=KEEP_RUNS_PER_NODE)
        except Exception:  # noqa: BLE001
            logger.debug("node_trace: start failed for {}", node_key, exc_info=True)
            rec.dir = None
        return rec

    def _write_run(self, *, status: str, error: str = "") -> None:
        if self.dir is None:
            return
        _write_json(
            self.dir / "run.json",
            {
                "node_key": self.node_key,
                "kind": self.kind,
                "started": self.started,
                "finished": (
                    datetime.now().isoformat(timespec="seconds")
                    if status != "running"
                    else None
                ),
                "status": status,
                "error": error,
                "calls": self.calls,
                "counts": self.counts,
            },
        )

    def record_call(
        self,
        *,
        call: int,
        level: int,
        chunk: list[dict[str, Any]],
        reply_text: str,
        ops_gpt: list[Any],
        ops_final: list[Any] | None,
        entries: list[dict[str, Any]],
        error: str = "",
    ) -> None:
        self.calls += 1
        for e in entries:
            self.counts[e["kind"]] = self.counts.get(e["kind"], 0) + 1
        if self.dir is None:
            return
        try:
            call_dir = self.dir / f"call_{int(call):02d}_L{int(level)}"
            call_dir.mkdir(parents=True, exist_ok=True)
            _write_json(call_dir / "input.json", chunk)
            (call_dir / "reply.txt").write_text(reply_text or "", encoding="utf-8")
            _write_json(call_dir / "ops_gpt.json", ops_gpt)
            if ops_final is not None:
                _write_json(call_dir / "ops_final.json", ops_final)
            if error:
                (call_dir / "error.txt").write_text(error, encoding="utf-8")
            with (call_dir / "diary.jsonl").open("w", encoding="utf-8") as fh:
                for e in entries:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            self._write_run(status="running")
        except Exception:  # noqa: BLE001
            logger.debug("node_trace: record_call failed for {}", self.node_key, exc_info=True)

    def finish(self, *, ok: bool, error: str = "") -> None:
        try:
            self._write_run(status="ok" if ok else "failed", error=error[:2000])
        except Exception:  # noqa: BLE001
            logger.debug("node_trace: finish failed for {}", self.node_key, exc_info=True)


def _prune(node_dir: Path, *, keep: int) -> None:
    runs = sorted(p for p in node_dir.iterdir() if p.is_dir() and not p.name.endswith(".del"))
    for old in runs[:-keep] if len(runs) > keep else []:
        _rmtree_one(old)


def _rmtree_one(path: Path) -> None:
    """Сначала убрать каталог из списка прогонов, потом удалить.

    Повторный rmtree по тому же пути на Windows попадает в ещё не отпущенный
    каталог и сносит соседние прогоны.
    """
    trash = path.with_name(path.name + ".del")
    try:
        if trash.exists():
            shutil.rmtree(trash, ignore_errors=True)
        path.rename(trash)
    except OSError:
        trash = path
    shutil.rmtree(trash, ignore_errors=True)


def read_run(run_dir: Path) -> dict[str, Any]:
    try:
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        meta = {}
    meta["dir"] = str(run_dir)
    meta.setdefault("node_key", run_dir.parent.name)
    return meta


def list_runs(data_dir: Path | str) -> list[dict[str, Any]]:
    """Все сохранённые прогоны, старые → новые."""
    root = trace_root(data_dir)
    if not root.is_dir():
        return []
    runs: list[tuple[str, Path]] = []
    for node_dir in root.iterdir():
        if node_dir.is_dir():
            runs.extend(
                (p.name, p)
                for p in node_dir.iterdir()
                if p.is_dir() and not p.name.endswith(".del")
            )
    return [read_run(p) for _stamp, p in sorted(runs)]


def latest_run_dirs(data_dir: Path | str) -> dict[str, Path]:
    """node_key → папка последнего прогона."""
    out: dict[str, Path] = {}
    for run in list_runs(data_dir):
        out[str(run["node_key"])] = Path(run["dir"])
    return out


def read_diary(run_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for call_dir in sorted(p for p in Path(run_dir).glob("call_*") if p.is_dir()):
        path = call_dir / "diary.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            item["call"] = call_dir.name
            items.append(item)
    return items


def latest_diary(data_dir: Path | str) -> list[dict[str, Any]]:
    """Дневник последнего прогона каждой ноды, в порядке нод по времени."""
    out: list[dict[str, Any]] = []
    for node_key, run_dir in sorted(
        latest_run_dirs(data_dir).items(), key=lambda kv: kv[1].name
    ):
        for item in read_diary(run_dir):
            item["node_key"] = node_key
            item["run"] = run_dir.name
            out.append(item)
    return out
