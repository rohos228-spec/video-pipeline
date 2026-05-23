"""Удаляет неправильный hero v=3 артефакт (тот, что сохранил референс
вместо результата генерации) и возвращает проект в состояние готовности
к перегенерации v=3.

Запуск из корня репо:
    python -m cleanup_hero_v3 1            # для проекта #1

Что делает:
    1. Находит для проекта последний artifact kind=hero_reference
       с meta.variation_index = 3 (или указанный --variation).
    2. Удаляет файл с диска (если существует).
    3. Удаляет запись Artifact из БД.
    4. Сбрасывает project.status в hero_ready (чтобы пайплайн
       заново предложил «▶ Продолжить» или регенерацию).
    5. Удаляет approved/regenerate-HITL для (hero_idx=1, var_idx=3),
       чтобы pair заново попал в очередь.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    Artifact,
    ArtifactKind,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)


async def _cleanup_v3(
    session: AsyncSession,
    project_id: int,
    hero_idx: int,
    var_idx: int,
) -> None:
    project = (
        await session.execute(
            select(Project).where(Project.id == project_id)
        )
    ).scalar_one_or_none()
    if project is None:
        print(f"[!] проект #{project_id} не найден")
        return

    print(
        f"проект #{project_id} '{project.title}' "
        f"slug={project.slug} status={project.status.value}"
    )

    arts = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.kind == ArtifactKind.hero_reference,
            )
            .order_by(Artifact.id.desc())
        )
    ).scalars().all()

    matched: list[Artifact] = []
    for a in arts:
        meta = a.meta or {}
        if (
            meta.get("hero_index") == hero_idx
            and meta.get("variation_index") == var_idx
        ):
            matched.append(a)
    if not matched:
        print(
            f"[!] hero_reference (hero={hero_idx}, var={var_idx}) "
            f"не найден — нечего удалять"
        )
    for a in matched:
        path = Path(a.path) if a.path else None
        print(f"  удаляю Artifact id={a.id} path={a.path}")
        if path and path.exists():
            try:
                path.unlink()
                print(f"    ✓ файл удалён: {path}")
            except Exception as e:  # noqa: BLE001
                print(f"    [!] не смог удалить файл {path}: {e}")
        elif path:
            print(f"    (файла на диске уже нет: {path})")
        await session.delete(a)

    # удаляем HITL-карточки для этой пары (approved/regenerate/edit_prompt)
    # — иначе pair будет считаться уже одобренной и v=3 не сгенерится снова.
    hitls = (
        await session.execute(
            select(HITLRequest)
            .where(
                HITLRequest.project_id == project_id,
                HITLRequest.kind == HITLKind.approve_hero,
            )
        )
    ).scalars().all()
    removed_hitl = 0
    for h in hitls:
        p = h.payload or {}
        if (
            p.get("hero_index") == hero_idx
            and p.get("variation_index", 1) == var_idx
        ):
            print(
                f"  удаляю HITLRequest id={h.id} "
                f"decision={h.decision.value if h.decision else None}"
            )
            await session.delete(h)
            removed_hitl += 1
    print(f"  удалено HITL-карточек для пары: {removed_hitl}")

    # Возвращаем проект в hero_ready — пайплайн в advance_project увидит
    # неполный список одобренных пар и перейдёт обратно в generating_hero
    # для недостающего v=3.
    if project.status not in (
        ProjectStatus.hero_ready,
        ProjectStatus.generating_hero,
    ):
        print(
            f"  status: {project.status.value} → hero_ready"
        )
        project.status = ProjectStatus.hero_ready
    else:
        print(f"  status уже {project.status.value} (не трогаю)")

    await session.commit()
    print("готово.")


async def main() -> None:
    if len(sys.argv) < 2:
        print(
            "usage: python -m cleanup_hero_v3 <project_id> "
            "[hero_idx=1] [var_idx=3]"
        )
        sys.exit(2)
    project_id = int(sys.argv[1])
    hero_idx = int(sys.argv[2]) if len(sys.argv) >= 3 else 1
    var_idx = int(sys.argv[3]) if len(sys.argv) >= 4 else 3

    async with session_scope() as s:
        await _cleanup_v3(s, project_id, hero_idx, var_idx)


if __name__ == "__main__":
    asyncio.run(main())
