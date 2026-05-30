"""Standalone-скрипт для теста шага 11 (финальная сборка) с готовыми материалами.

Что делает:
  1. Открывает SQLite-базу бота (`data/state.db`).
  2. Находит проект по `--slug` (он должен УЖЕ существовать в базе — т.е. вы
     заранее создали его через бота и хотя бы импортировали xlsx, чтобы появились
     кадры с `voiceover_text`).
  3. Регистрирует видео-клипы из папки `--videos` как Artifact(scene_video),
     привязывая их к кадрам по номеру (первый файл → кадр №1, и т.д.).
  4. Регистрирует mp3 из `--voice` как Artifact(audio).
  5. Запускает Whisper по этому mp3, получает word-level таймкоды и
     проставляет `Frame.start_ts/end_ts/duration_seconds`.
  6. Меняет статус проекта на `assembling` и вызывает шаг 11 напрямую.
  7. На выходе: `data/videos/<slug>/final/<slug>.mp4`.

Использование:
  python test_step11.py ^
      --slug my-project ^
      --videos C:\path\to\clips ^
      --voice C:\path\to\voiceover.mp3 ^
      [--bgm C:\path\to\bgm.mp3]

Требования:
  - В папке `--videos` ровно столько mp4-файлов, сколько кадров у проекта.
    Имена не важны — сортируются по имени (используйте `clip_001.mp4`,
    `clip_002.mp4` и т.д., чтобы порядок совпал с порядком кадров).
  - Проект в боте уже создан, xlsx уже импортирован (есть кадры с текстом
    озвучки на листе «план»).
  - Bot НЕ должен быть запущен одновременно (иначе он может перехватить
    управление проектом). Перед запуском скрипта остановите бота.

Положите этот файл в корень репо `video-pipeline` и запускайте оттуда:
  cd C:\Users\<вы>\video-pipeline
  python test_step11.py --slug ... --videos ... --voice ...
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import uuid
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Тест шага 11 (финальная сборка)")
    p.add_argument("--slug", required=True, help="slug проекта в боте")
    p.add_argument(
        "--videos",
        required=True,
        help="Папка с готовыми видео-клипами кадров (.mp4). Сортируются по имени.",
    )
    p.add_argument("--voice", required=True, help="Путь к mp3-озвучке.")
    p.add_argument(
        "--bgm",
        default=None,
        help="(опц.) Путь к фоновой музыке. Если не задан — пробуем xlsx «Общий план» R33 / диск.",
    )
    p.add_argument(
        "--copy",
        action="store_true",
        help="Скопировать клипы и mp3 в `data/videos/<slug>/...` перед регистрацией "
        "(чтобы файлы были внутри папки проекта). Иначе — регистрируются in-place.",
    )
    return p.parse_args()


async def main() -> int:
    args = _parse_args()

    # Импортируем приложение — поэтому скрипт должен запускаться из корня репо
    sys.path.insert(0, str(Path.cwd()))
    from loguru import logger
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Artifact, ArtifactKind, Frame, Project, ProjectStatus
    from app.orchestrator.steps.assemble import run as run_assemble
    from app.services.mapper import map_frames
    from app.services.whisper import dump_words_json, transcribe_words
    from app.settings import settings

    slug = args.slug
    videos_dir = Path(args.videos).resolve()
    voice_path_src = Path(args.voice).resolve()
    bgm_path_src = Path(args.bgm).resolve() if args.bgm else None

    if not videos_dir.is_dir():
        print(f"ERROR: --videos {videos_dir} не папка", file=sys.stderr)
        return 1
    if not voice_path_src.is_file():
        print(f"ERROR: --voice {voice_path_src} не файл", file=sys.stderr)
        return 1
    if bgm_path_src is not None and not bgm_path_src.is_file():
        print(f"ERROR: --bgm {bgm_path_src} не файл", file=sys.stderr)
        return 1

    clip_files = sorted(
        [p for p in videos_dir.iterdir() if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".webm")],
        key=lambda p: p.name,
    )
    if not clip_files:
        print(f"ERROR: в {videos_dir} нет видео-файлов", file=sys.stderr)
        return 1

    project_dir = Path(settings.data_dir) / "videos" / slug
    videos_target_dir = project_dir / "videos"
    audio_target_dir = project_dir / "audio"
    final_dir = project_dir / "final"
    for d in (videos_target_dir, audio_target_dir, final_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1. находим проект и кадры
    async with session_scope() as session:
        project = (
            await session.execute(select(Project).where(Project.slug == slug))
        ).scalar_one_or_none()
        if project is None:
            print(
                f"ERROR: проект со slug='{slug}' не найден в БД. "
                f"Создайте его в боте и импортируйте xlsx, потом запустите скрипт.",
                file=sys.stderr,
            )
            return 2

        frames = (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars().all()
        if not frames:
            print("ERROR: у проекта нет кадров. Импортируйте xlsx через бота.", file=sys.stderr)
            return 2
        if not any(fr.voiceover_text for fr in frames):
            print("ERROR: ни у одного кадра нет voiceover_text. Проверьте xlsx лист «план» строку 49.", file=sys.stderr)
            return 2

        if len(clip_files) != len(frames):
            print(
                f"WARNING: клипов в папке {len(clip_files)} ≠ кадров в проекте {len(frames)}. "
                f"Возьму первые {min(len(clip_files), len(frames))}.",
                file=sys.stderr,
            )

        n = min(len(clip_files), len(frames))

        # 2. регистрируем клипы как scene_video, привязывая к кадрам по номеру
        for i in range(n):
            fr = frames[i]
            src = clip_files[i]
            if args.copy:
                dst = videos_target_dir / f"clip_{fr.number:03d}_{uuid.uuid4().hex[:8]}{src.suffix}"
                shutil.copy2(src, dst)
                target_path = dst
            else:
                target_path = src
            # удалим прежние scene_video для этого кадра (если были)
            old = (
                await session.execute(
                    select(Artifact).where(
                        Artifact.project_id == project.id,
                        Artifact.frame_id == fr.id,
                        Artifact.kind == ArtifactKind.scene_video,
                    )
                )
            ).scalars().all()
            for a in old:
                await session.delete(a)
            session.add(Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_video,
                uuid=uuid.uuid4().hex,
                path=str(target_path),
            ))
        await session.flush()
        logger.info("Зарегистрировано {} видео-клипов как scene_video", n)

        # 3. регистрируем mp3 как audio
        if args.copy:
            voice_target = audio_target_dir / f"voice_{uuid.uuid4().hex[:8]}.mp3"
            shutil.copy2(voice_path_src, voice_target)
            voice_path = voice_target
        else:
            voice_path = voice_path_src
        # удалим прежний audio артефакт если был
        old_audio = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.kind == ArtifactKind.audio,
                )
            )
        ).scalars().all()
        for a in old_audio:
            await session.delete(a)
        session.add(Artifact(
            project_id=project.id,
            kind=ArtifactKind.audio,
            uuid=uuid.uuid4().hex,
            path=str(voice_path),
        ))
        await session.flush()
        logger.info("Зарегистрирована mp3 как audio: {}", voice_path)

        # 4. если --bgm указан и --copy — скопируем bgm в проект.
        # Не регистрируем в БД: шаг 11 ищет bgm через xlsx или диск.
        if bgm_path_src is not None:
            if args.copy:
                bgm_target = audio_target_dir / f"bgm{bgm_path_src.suffix}"
                shutil.copy2(bgm_path_src, bgm_target)
                logger.info("bgm скопирован в {}", bgm_target)
            else:
                # положим симлинк / копию под именем bgm.<ext> в audio/
                bgm_target = audio_target_dir / f"bgm{bgm_path_src.suffix}"
                if bgm_target.exists():
                    bgm_target.unlink()
                shutil.copy2(bgm_path_src, bgm_target)
                logger.info("bgm скопирован в {} (для disk-fallback)", bgm_target)

        # 5. Whisper-алайнмент текстов кадров с реальной mp3
        logger.info("Whisper: транскрибируем {} ...", voice_path)
        words = transcribe_words(voice_path, language="ru", model_name=settings.whisper_model)
        words_path = audio_target_dir / f"words_{uuid.uuid4().hex[:8]}.json"
        dump_words_json(words, words_path)
        session.add(Artifact(
            project_id=project.id,
            kind=ArtifactKind.whisper_words,
            uuid=uuid.uuid4().hex,
            path=str(words_path),
        ))

        cells = [(fr.number, fr.voiceover_text or "") for fr in frames[:n]]
        timings = map_frames(cells, words)
        by_num = {t.frame_number: t for t in timings}
        n_aligned = 0
        for fr in frames[:n]:
            t = by_num.get(fr.number)
            if t and t.duration > 0:
                fr.start_ts = t.start_ts
                fr.end_ts = t.end_ts
                fr.duration_seconds = t.duration
                n_aligned += 1
        await session.flush()
        logger.info("Алайнмент: {} / {} кадров получили тайминги", n_aligned, n)

        if n_aligned == 0:
            print(
                "ERROR: Whisper не смог сопоставить ни одного кадра с озвучкой. "
                "Проверьте что mp3 действительно содержит текст из voiceover ячеек xlsx.",
                file=sys.stderr,
            )
            return 3

        # 6. ставим статус assembling и запускаем шаг 11
        project.status = ProjectStatus.assembling
        await session.flush()
        logger.info("Статус проекта → assembling, запускаем шаг 11 ...")
        await run_assemble(session, project, bot=None)  # type: ignore[arg-type]
        # bot=None ок: send_hitl_video в самом конце упадёт, но к тому моменту
        # mp4 уже будет на диске. Если упадёт — поймаем и сообщим.

    print(f"\nГотово! Финальный ролик: {Path(settings.data_dir) / 'videos' / slug / 'final' / f'{slug}.mp4'}")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception as e:
        from loguru import logger as _lg
        _lg.exception("test_step11 завершился с ошибкой")
        # Спецкейс: если упало именно на send_hitl_video — mp4 уже есть на диске.
        if "send_hitl_video" in repr(e) or "bot" in repr(e).lower():
            print(
                "\nINFO: HITL-карточка не отправлена (bot=None), но финальный mp4 "
                "должен лежать в data/videos/<slug>/final/<slug>.mp4 — проверьте.",
                file=sys.stderr,
            )
            rc = 0
        else:
            rc = 1
    sys.exit(rc)
