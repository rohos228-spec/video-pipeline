"""Диагностика и восстановление статуса проекта по фактическим данным БД.

Использует тот же алгоритм, что и `_init_db._recompute_all_projects` на
старте воркера — `app.services.project_state.compute_actual_status`.

Запуск:
    python -m recover_project_state 1                # для проекта #1
    python -m recover_project_state 1 --dry-run      # только показать

Перевычисляет project.status из реальных данных:
    - general_plan / script_text / hero_description (поля projects)
    - frames (кол-во и заполненность image_prompt / animation_prompt)
    - artifacts kind=hero_reference / scene_image / scene_video / audio /
      final_video (счётчики)

Смотрит ТОЛЬКО на БД. Если данные у тебя в xlsx (а в БД ещё нет) — сначала
нажми в TG-меню «🔄 Перечитать xlsx», это импортирует xlsx → БД и сразу
вызовет тот же recompute. Этот CLI-скрипт нужен в основном для проектов,
которые сидят в зависшем status и нужен ручной триггер без TG.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select

from app.db import session_scope
from app.models import Artifact, ArtifactKind, Frame, Project
from app.services.project_state import compute_actual_status, recompute_status


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

        # Диагностика — показать всё, что видим.
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
        fr_with_anim_prompt = (
            await session.execute(
                select(func.count(Frame.id)).where(
                    Frame.project_id == project_id,
                    Frame.animation_prompt.isnot(None),
                    Frame.animation_prompt != "",
                )
            )
        ).scalar_one()

        async def _count_kind(k: ArtifactKind) -> int:
            return (
                await session.execute(
                    select(func.count(Artifact.id)).where(
                        Artifact.project_id == project_id, Artifact.kind == k
                    )
                )
            ).scalar_one()

        hero_arts = await _count_kind(ArtifactKind.hero_reference)
        scene_image_arts = await _count_kind(ArtifactKind.scene_image)
        scene_video_arts = await _count_kind(ArtifactKind.scene_video)
        audio_arts = await _count_kind(ArtifactKind.audio)
        final_arts = await _count_kind(ArtifactKind.final_video)

        print(f"проект #{project.id} title='{project.topic or project.slug}'")
        print(f"  current status     = {project.status.value}")
        print()
        print("  --- БД: поля projects ---")
        print(f"  has general_plan         = {has_plan}")
        print(f"  has script_text          = {has_script}")
        print(f"  has hero_description     = {has_hero}")
        print()
        print("  --- БД: таблица frames ---")
        print(f"  frames total             = {fr_total}")
        print(f"  frames with image_prompt = {fr_with_img_prompt}")
        print(f"  frames with anim_prompt  = {fr_with_anim_prompt}")
        print()
        print("  --- БД: таблица artifacts (по kind) ---")
        print(f"  hero_reference           = {hero_arts}")
        print(f"  scene_image              = {scene_image_arts}")
        print(f"  scene_video              = {scene_video_arts}")
        print(f"  audio                    = {audio_arts}")
        print(f"  final_video              = {final_arts}")
        print()

        new_status = await compute_actual_status(session, project)
        print(f"  computed status (по данным) = {new_status.value}")

        if project.status == new_status:
            print("[=] статус уже корректный, ничего не делаем")
            return 0

        if dry_run:
            print(
                f"[dry-run] изменил бы {project.status.value} → {new_status.value}"
            )
            return 0

        old_value = project.status.value
        old, new, changed = await recompute_status(
            session, project, log_prefix="recover_project_state CLI"
        )
        if not changed:
            print("[=] нечего менять")
            return 0
        await session.commit()
        print(f"[ok] {old_value} → {new.value}")

        # Подсказки куда дальше
        hints = {
            "new": "В TG жми «1. План» — ChatGPT напишет план.",
            "plan_ready": "В TG жми «2. Закадровый текст».",
            "script_ready": "В TG жми «3. Разбивка на блоки» — frames появятся.",
            "frames_ready": "В TG жми «4. Hero-картинка».",
            "hero_ready": "В TG жми «5. Промты картинок».",
            "image_prompts_ready": "В TG жми «6. Картинки».",
            "images_ready": "В TG жми «7. Промты анимации».",
            "animation_prompts_ready": "В TG жми «8. Видео».",
            "videos_ready": "В TG жми «9. Аудио».",
            "audio_ready": "В TG жми «10. Финальная сборка».",
        }
        hint = hints.get(new.value)
        if hint:
            print()
            print(f"СЛЕДУЮЩИЙ ШАГ: {hint}")
        return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("project_id", type=int)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    return asyncio.run(_recover(args.project_id, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
