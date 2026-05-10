"""Диагностика и восстановление статуса проекта по фактическим данным БД.

Зачем: в _init_db есть миграция failed→*_ready, но она не учитывала случай,
когда `hero_description` заполнен, а в таблице `frames` нет записей. Тогда
проект попадал в `hero_ready`, и юзер мог тыкнуть «5. Промты картинок» —
который сразу падал с «нет кадров».

Этот скрипт идемпотентный — можно запускать на любом статусе:
    python -m recover_project_state 1                # для проекта #1
    python -m recover_project_state 1 --dry-run      # только показать

Логика та же, что в _init_db, но теперь:
    - hero_ready ставится ТОЛЬКО если fr_total > 0
    - image_prompts_ready — то же
    - всегда показываем before/after и причину
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select, func

from app.db import session_scope
from app.models import Frame, Project


async def _recover(project_id: int, dry_run: bool) -> int:
    async with session_scope() as session:
        project = (
            await session.execute(
                select(Project).where(Project.id == project_id)
            )
        ).scalar_one_or_none()
        if project is None:
            print(f"[!] проект #{project_id} не найден")
            return 1

        has_plan = bool(project.general_plan)
        has_script = bool(project.script_text)
        has_hero = bool(project.hero_description)
        fr_total = (
            await session.execute(
                select(func.count(Frame.id)).where(Frame.project_id == project_id)
            )
        ).scalar_one()
        fr_with_img_prompt = (
            await session.execute(
                select(func.count(Frame.id)).where(
                    Frame.project_id == project_id,
                    Frame.image_prompt.isnot(None),
                    Frame.image_prompt != "",
                )
            )
        ).scalar_one()

        print(f"проект #{project.id} title='{project.topic or project.slug}'")
        print(f"  current status     = {project.status.value}")
        print(f"  has general_plan   = {has_plan}")
        print(f"  has script_text    = {has_script}")
        print(f"  has hero_descr     = {has_hero}")
        print(f"  frames total       = {fr_total}")
        print(f"  frames with prompt = {fr_with_img_prompt}")

        # Корректная логика: для hero_ready/image_prompts_ready/frames_ready
        # ОБЯЗАТЕЛЬНО fr_total > 0. Без кадров hero_ready ставить нельзя — иначе
        # шаг 5 (image_prompts) сразу упадёт с «нет кадров».
        if fr_total > 0 and fr_with_img_prompt == fr_total:
            new_status = "image_prompts_ready"
            reason = (
                "все кадры уже имеют image_prompt → шаг 5 пройден"
            )
        elif fr_total > 0 and (has_hero or fr_with_img_prompt > 0):
            new_status = "hero_ready"
            reason = (
                "есть кадры + hero_description (или часть промтов) → шаги 3,4 ✅"
            )
        elif fr_total > 0:
            new_status = "frames_ready"
            reason = "есть кадры → шаг 3 ✅, но hero ещё не делали"
        elif has_script:
            new_status = "script_ready"
            reason = (
                "нет кадров → шаг 3 (split) НЕ выполнен, "
                "несмотря на наличие hero_description. Нужно сначала split."
            )
        elif has_plan:
            new_status = "plan_ready"
            reason = "есть план, но нет скрипта"
        else:
            new_status = "new"
            reason = "нет вообще ничего"

        print(f"  recommended status = {new_status}")
        print(f"  reason             = {reason}")

        if project.status.value == new_status:
            print("[=] статус уже корректный, ничего не делаем")
            return 0

        if dry_run:
            print(
                f"[dry-run] изменил бы {project.status.value} → {new_status}"
            )
            return 0

        await session.execute(
            Project.__table__.update()
            .where(Project.id == project_id)
            .values(status=new_status)
        )
        await session.commit()
        print(f"[ok] {project.status.value} → {new_status}")

        if new_status == "script_ready" and has_hero:
            print()
            print(
                "ВАЖНО: hero_description заполнен, но кадров нет. "
                "Сейчас в меню жми «3. Разбивка на блоки» — он создаст "
                "frames в БД. После этого hero уже не нужно перегенеривать "
                "(он берётся из artifacts), и можно сразу нажать "
                "«5. Промты картинок»."
            )

        return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("project_id", type=int)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    return asyncio.run(_recover(args.project_id, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
