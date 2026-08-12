#!/usr/bin/env python3
"""Собрать базу для анализа / проверки / рецензии скелета (волна 0).

Пример:
  python scripts/skeleton_review_pack.py --project-id 61
  python scripts/skeleton_review_pack.py --slug ai-pack-smoke-2026-08-12-2015

Пишет в ``data/videos/<slug>/scene_design/review/``:
  - pack_meta.json       — статус, таймлайн логов, пути
  - frames.json          — номера/длина/время_сек VO
  - skeleton.json        — копия чекпоинта (если есть)
  - validate_gaps.json   — повторный прогон validate_skeleton
  - cells_sk.json        — sk_* ячейки из БД
  - log_excerpt.txt      — строки skeleton/wave из backend-лога
  - REVIEW.md            — чеклист рецензии + факты прогона
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


CODE_CHECKS = [
    ("1 покрытие", "каждый кадр ровно в одной сцене; соседние номера; порядок сцен"),
    ("2 якоря", "главное/биты/персонажи/локации — дословная подстрока закадра"),
    ("3 двойники loc", "похожие якоря/имена локаций слиты"),
    ("4 двойники char", "кореференция имён/ролей без дублей id"),
    ("5 тип связи", "начало / продолжение / сдвиг_времени / новое_место / возврат"),
    ("6 тайминг", "|vo/rate − Σкадры| / Σ ≤ 15%"),
    ("7 биты", "порядок битов = порядок якорей в тексте сцены"),
    ("8 нити", "открытая нить закрыта или явно в последней сцене"),
    ("9 разовая loc", "однокадровый фон → фон_разовый, не id в реестре"),
    ("10 реестр", "место_id и персонажи[].id есть в seed"),
]

HUMAN_REVIEW = [
    "Сцены сгруппированы по месту/времени/ходу, а не «по 1 кадру всегда»?",
    "Суть сцены читается без закадра? Главное — видимый объект/жест, не абстракция?",
    "Биты — смена состояния, а не пересказ VO?",
    "Персонажи: кто в кадре vs вне_кадра — правда по тексту?",
    "Локации: возвраты и новое_место не путаются?",
    "После скелета волна 1 (characters/world/style) стартовала только при gaps=0?",
]


def _repo_data() -> Path:
    from app.settings import settings

    return Path(settings.data_dir)


def _find_backend_log(data: Path) -> Path | None:
    logs = sorted(
        data.glob("backend-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in logs:
        if p.stat().st_size > 0:
            return p
    main = data / "backend.log"
    return main if main.is_file() else None


def _log_excerpt(log_path: Path | None, project_id: int, *, max_lines: int = 120) -> str:
    if log_path is None or not log_path.is_file():
        return ""
    pat = re.compile(
        rf"#\[?{project_id}\]?|skeleton:|scene_design wave|СКЕЛЕТ|editor round|draft ok|checkpoint",
        re.I,
    )
    hits: list[str] = []
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if pat.search(line):
                    hits.append(line.rstrip())
    except OSError as e:
        return f"(log read error: {e})"
    return "\n".join(hits[-max_lines:])


def _write_review_md(
    out: Path,
    *,
    project_id: int,
    slug: str,
    status: str,
    frame_n: int,
    gaps: list[dict],
    cells_n: int,
    skeleton_ok: bool,
    log_markers: list[str],
) -> None:
    lines = [
        f"# Рецензия скелета — проект #{project_id} `{slug}`",
        "",
        f"- status: `{status}`",
        f"- кадров: {frame_n}",
        f"- checkpoint skeleton.json: {'да' if skeleton_ok else 'нет'}",
        f"- sk_* ячеек: {cells_n}",
        f"- повторный validate_skeleton: **{len(gaps)} разрывов**",
        f"- собрано: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Лог-маркеры (ожидание)",
        "",
    ]
    for m in log_markers:
        lines.append(f"- `{m}`")
    lines += ["", "## Код-проверки (10)", ""]
    for title, desc in CODE_CHECKS:
        lines.append(f"- [ ] **{title}** — {desc}")
    lines += ["", "## Человеческая рецензия", ""]
    for q in HUMAN_REVIEW:
        lines.append(f"- [ ] {q}")
    lines += [
        "",
        "## Разрывы кода (если есть)",
        "",
    ]
    if not gaps:
        lines.append("_разрывов нет — можно рецензировать смысл_")
    else:
        for g in gaps[:40]:
            lines.append(
                f"- `{g.get('адрес')}`: {g.get('проблема')} → {g.get('как_исправить')}"
            )
    lines += [
        "",
        "## Файлы пакета",
        "",
        "- `pack_meta.json`",
        "- `frames.json`",
        "- `skeleton.json` (копия)",
        "- `validate_gaps.json`",
        "- `cells_sk.json`",
        "- `log_excerpt.txt`",
        "",
    ]
    (out / "REVIEW.md").write_text("\n".join(lines), encoding="utf-8")


async def _build(project_id: int | None, slug: str | None) -> Path:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Frame, Project, SceneDesignCell  # noqa: F401
    from app.settings import settings
    from app.services.scene_design import skeleton as sk
    from app.services.scene_design.context_builder import frame_seconds, full_voiceover

    engine = create_async_engine(settings.db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        q = select(Project)
        if project_id is not None:
            q = q.where(Project.id == project_id)
        elif slug:
            q = q.where(Project.slug == slug)
        else:
            raise SystemExit("нужен --project-id или --slug")
        project = (await session.execute(q)).scalar_one_or_none()
        if project is None:
            raise SystemExit("проект не найден")
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number)
                )
            )
            .scalars()
            .all()
        )
        cells = list(
            (
                await session.execute(
                    select(SceneDesignCell).where(
                        SceneDesignCell.project_id == project.id,
                        SceneDesignCell.agent == "skeleton",
                    )
                )
            )
            .scalars()
            .all()
        )

        out = project.data_dir / "scene_design" / "review"
        out.mkdir(parents=True, exist_ok=True)

        frames_payload = []
        for fr in frames:
            sec, src = frame_seconds(fr)
            frames_payload.append(
                {
                    "number": fr.number,
                    "uuid": fr.uuid,
                    "chars": len((fr.voiceover_text or "").strip()),
                    "время_сек": sec,
                    "время_источник": src,
                    "закадр": (fr.voiceover_text or "").strip(),
                }
            )

        sk_path = project.data_dir / "scene_design" / "skeleton.json"
        draft: dict | None = None
        if sk_path.is_file():
            draft = json.loads(sk_path.read_text(encoding="utf-8"))
            shutil.copy2(sk_path, out / "skeleton.json")
        else:
            (out / "skeleton.json").write_text(
                json.dumps({"_missing": True}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        gaps: list[dict] = []
        if draft and draft.get("scenes"):
            vo = full_voiceover(project, frames)
            gaps = sk.validate_skeleton(draft, frames, vo)

        cells_payload = [
            {
                "kind": c.kind,
                "target_key": c.target_key,
                "field": c.field,
                "status": c.status,
                "error": c.error,
                "value": (c.value or "")[:500],
            }
            for c in cells
        ]

        data = _repo_data()
        log_path = _find_backend_log(data)
        excerpt = _log_excerpt(log_path, project.id)
        (out / "log_excerpt.txt").write_text(excerpt, encoding="utf-8")

        meta_sd = (project.meta or {}).get("scene_design") or {}
        pack_meta = {
            "project_id": project.id,
            "slug": project.slug,
            "status": str(project.status.value if hasattr(project.status, "value") else project.status),
            "scene_design": {
                "status": meta_sd.get("status"),
                "agents": meta_sd.get("agents"),
            },
            "skeleton_checkpoint": sk_path.is_file(),
            "gaps_count": len(gaps),
            "cells_skeleton": len(cells),
            "frames": len(frames),
            "log_path": str(log_path) if log_path else None,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "code_head": "7a8390c1",
        }
        (out / "pack_meta.json").write_text(
            json.dumps(pack_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "frames.json").write_text(
            json.dumps(frames_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "validate_gaps.json").write_text(
            json.dumps(gaps, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "cells_sk.json").write_text(
            json.dumps(cells_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        markers = [
            "skeleton: draft GPT…",
            "skeleton: draft ok",
            "skeleton: editor round N",
            "skeleton: stored cells",
            "scene_design wave …: characters,world,style",
        ]
        _write_review_md(
            out,
            project_id=project.id,
            slug=project.slug,
            status=pack_meta["status"],
            frame_n=len(frames),
            gaps=gaps,
            cells_n=len(cells),
            skeleton_ok=sk_path.is_file(),
            log_markers=markers,
        )
        print(out)
        return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-id", type=int, default=None)
    ap.add_argument("--slug", type=str, default=None)
    args = ap.parse_args()
    asyncio.run(_build(args.project_id, args.slug))


if __name__ == "__main__":
    main()
