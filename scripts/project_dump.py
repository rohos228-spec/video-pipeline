"""Полный JSON-дамп проекта пайплайна для post-mortem'ов / бэкапа.

Использование:
    python -m scripts.project_dump <project_id>             # JSON на stdout
    python -m scripts.project_dump <project_id> -o file.json
    python -m scripts.project_dump --list                   # последние 20
    python -m scripts.project_dump --list --status failed
    python -m scripts.project_dump --slug rachki-cyberpunk

Дампит из БД:
- Project + все поля.
- Frames (кадры).
- Artifacts (картинки, видео, аудио).
- HITLRequest (история одобрений).
- BatchProject (если проект — sub batch'а).

Read-only, не модифицирует БД.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import session_scope
from app.models import BatchProject, Project, ProjectStatus


def _json_safe(obj):  # noqa: ANN001
    """JSON-сериализация SQLAlchemy моделей и Enum'ов."""
    from datetime import datetime
    from pathlib import Path

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "value"):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(x) for x in obj]
    return str(obj)


def _model_to_dict(obj) -> dict:  # noqa: ANN001
    """Преобразовать ORM-объект в dict (только колонки)."""
    if obj is None:
        return {}
    out = {}
    for col in obj.__table__.columns:
        out[col.name] = _json_safe(getattr(obj, col.name, None))
    return out


async def _dump_project(*, project_id: int | None = None, slug: str | None = None) -> dict:
    """Полный дамп одного проекта (по id или slug)."""
    async with session_scope() as db:
        stmt = select(Project).options(
            selectinload(Project.frames),
            selectinload(Project.artifacts),
            selectinload(Project.hitl_requests),
        )
        if project_id is not None:
            stmt = stmt.where(Project.id == project_id)
        elif slug is not None:
            stmt = stmt.where(Project.slug == slug)
        else:
            return {"error": "project_id or slug required"}

        p = (await db.execute(stmt)).scalar_one_or_none()
        if p is None:
            return {"error": f"project not found (id={project_id}, slug={slug})"}

        result = {
            "project": _model_to_dict(p),
            "frames": [_model_to_dict(f) for f in (p.frames or [])],
            "artifacts": [_model_to_dict(a) for a in (p.artifacts or [])],
            "hitl_requests": [_model_to_dict(h) for h in (p.hitl_requests or [])],
        }

        # Batch parent (если есть)
        if getattr(p, "batch_id", None):
            batch = (
                await db.execute(
                    select(BatchProject).where(BatchProject.id == p.batch_id)
                )
            ).scalar_one_or_none()
            if batch:
                result["batch"] = _model_to_dict(batch)

        return result


async def _list_projects(
    *,
    limit: int = 20,
    status: str | None = None,
    batch_id: int | None = None,
) -> list[dict]:
    async with session_scope() as db:
        stmt = select(Project).order_by(Project.id.desc()).limit(limit)
        if status:
            try:
                stmt = stmt.where(Project.status == ProjectStatus(status))
            except ValueError:
                return [{"error": f"unknown status: {status}"}]
        if batch_id is not None:
            stmt = stmt.where(Project.batch_id == batch_id)

        rows = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id": p.id,
            "slug": p.slug,
            "status": getattr(p.status, "value", str(p.status)),
            "topic": (p.topic or "")[:80],
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "batch_id": getattr(p, "batch_id", None),
        }
        for p in rows
    ]


def _print_pretty(data: dict) -> None:
    if "error" in data:
        print(data["error"])
        return
    p = data["project"]
    print(f"== Project #{p['id']} (slug={p.get('slug')!r}) ==")
    print(f"  status   : {p.get('status')}")
    print(f"  topic    : {(p.get('topic') or '')[:200]}")
    print(f"  created  : {p.get('created_at')}")
    print(f"  updated  : {p.get('updated_at')}")
    if data.get("batch"):
        b = data["batch"]
        print(f"  batch    : #{b['id']} ({b.get('slug')!r}) status={b.get('status')}")
    print()
    print(f"  frames        : {len(data['frames'])}")
    print(f"  artifacts     : {len(data['artifacts'])}")
    print(f"  hitl_requests : {len(data['hitl_requests'])}")

    if data["frames"]:
        # Сводка по статусам кадров
        by_status: dict[str, int] = {}
        for f in data["frames"]:
            s = f.get("status") or "?"
            by_status[s] = by_status.get(s, 0) + 1
        print()
        print("  frames by status:")
        for s, c in sorted(by_status.items()):
            print(f"    {s}: {c}")


def _print_list_pretty(rows: list[dict]) -> None:
    if not rows:
        print("(no projects)")
        return
    print(f"{'id':>4} {'status':<25} {'slug':<30} batch  topic")
    for r in rows:
        if "error" in r:
            print(r["error"])
            return
        print(
            f"{r['id']:>4} {r['status']:<25} {(r['slug'] or '?')[:30]:<30} "
            f"{(r['batch_id'] or '—')!s:>5}  {r['topic']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Дамп проекта video-pipeline.")
    parser.add_argument("project_id", type=int, nargs="?", help="ID проекта.")
    parser.add_argument("--slug", help="Альтернатива — найти по slug.")
    parser.add_argument("--list", action="store_true", help="Список проектов.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--status", default=None)
    parser.add_argument("--batch", type=int, default=None)
    parser.add_argument("-o", "--output", default=None, help="Сохранить JSON в файл.")
    parser.add_argument("--json", action="store_true", help="JSON в stdout.")
    args = parser.parse_args(argv)

    if args.list:
        rows = asyncio.run(
            _list_projects(limit=args.limit, status=args.status, batch_id=args.batch)
        )
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _print_list_pretty(rows)
        return 0

    if args.project_id is None and not args.slug:
        parser.error("project_id (или --slug, или --list) required")
        return 2  # pragma: no cover

    data = asyncio.run(
        _dump_project(project_id=args.project_id, slug=args.slug)
    )

    if args.output:
        with open(args.output, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"saved to {args.output}", file=sys.stderr)
        return 0

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_pretty(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
