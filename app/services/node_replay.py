"""Повтор сохранённого прогона ноды без GPT.

Берёт из ``node_trace`` ответ GPT (``ops_gpt.json``) и заново прогоняет
программу (``run_code_passes``). Если итог не совпал с ``ops_final.json`` —
значит правила поменялись с того прогона, и видно что именно.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.node_trace import Change, diff_ops, latest_run_dirs, read_run


@dataclass
class CallReplay:
    call: str
    ops_final: list[Any] | None
    ops_now: list[Any] | None
    entries: list[dict[str, Any]] = field(default_factory=list)
    diff: list[Change] = field(default_factory=list)
    stopped: str = ""
    was_stopped: str = ""

    @property
    def same(self) -> bool:
        if self.ops_final is None:
            return bool(self.stopped) and bool(self.was_stopped)
        return not self.stopped and not self.diff


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_run_dir(path: Path | str, *, node: str = "") -> Path:
    """Папка прогона или папка проекта + ``node`` (последний прогон этой ноды)."""
    p = Path(path)
    if (p / "run.json").is_file():
        return p
    runs = latest_run_dirs(p)
    if not runs:
        raise FileNotFoundError(f"нет прогонов в {p}")
    if node:
        hits = [d for nk, d in runs.items() if nk == node or nk.endswith(f"_fw_{node}")]
        if not hits:
            raise FileNotFoundError(f"нет прогонов ноды {node!r}; есть: {', '.join(sorted(runs))}")
        return hits[-1]
    if len(runs) == 1:
        return next(iter(runs.values()))
    raise ValueError(f"укажите --node, прогоны есть у: {', '.join(sorted(runs))}")


def replay_run(run_dir: Path | str, *, call: str = "") -> list[CallReplay]:
    from app.services.apply_ops_batches import PassCtx, run_code_passes

    run_dir = Path(run_dir)
    meta = read_run(run_dir)
    kind = str(meta.get("kind") or "")
    all_frames: list[dict[str, Any]] = []
    frames_gz = run_dir / "all_frames.json.gz"
    if frames_gz.is_file():
        with gzip.open(frames_gz, "rt", encoding="utf-8") as fh:
            all_frames = json.load(fh)
    out: list[CallReplay] = []
    for call_dir in sorted(p for p in run_dir.glob("call_*") if p.is_dir()):
        if call and call not in call_dir.name:
            continue
        chunk = _load_json(call_dir / "input.json", [])
        ops_gpt = _load_json(call_dir / "ops_gpt.json", [])
        final_path = call_dir / "ops_final.json"
        ops_final = _load_json(final_path, None) if final_path.is_file() else None
        err_path = call_dir / "error.txt"
        was_stopped = err_path.read_text(encoding="utf-8") if err_path.is_file() else ""
        rep = CallReplay(call_dir.name, ops_final, None, was_stopped=was_stopped)
        try:
            res = run_code_passes(
                ops_gpt,
                chunk,
                kind=kind,
                all_frames=all_frames or None,
                ctx=PassCtx(0, str(meta.get("node_key") or "replay"), 0, 0),
            )
        except Exception as exc:  # noqa: BLE001
            rep.stopped = str(exc)
            rep.entries = list(getattr(exc, "diary", []) or [])
        else:
            rep.ops_now = res.ops
            rep.entries = res.entries
            if ops_final is not None:
                rep.diff = diff_ops(ops_final, res.ops)
        out.append(rep)
    return out
