"""Тесты формирования виртуальной Excel-сетки напрямую из базы данных SQLite."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Entity, Frame, FrameText, Project, ProjectStatus, PromptVersion
from app.services.db_virtual_xlsx import build_virtual_project_sheets
from app.services.xlsx_v8_import import (
    ROW_DURATION_V8,
    ROW_IMAGE_PROMPT_V8,
    ROW_TIMECODE_V8,
    ROW_VIDEO_PROMPT_V8,
    ROW_VOICEOVER_V8,
    SHEET_GENERAL_V8,
    SHEET_PLAN_V8,
)


@pytest.mark.asyncio
async def test_build_virtual_project_sheets_empty_project():
    """Проверяет создание виртуальных листов для нового проекта без кадров."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        project = Project(
            title="Тестовый проект 2050",
            slug="test-2050",
            topic="Один день из жизни человека в 2050 году",
            status=ProjectStatus.new,
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        sheets = await build_virtual_project_sheets(session, project)
        assert SHEET_PLAN_V8 in sheets
        assert SHEET_GENERAL_V8 in sheets
        assert "Персонажи" in sheets
        assert "Фоны" in sheets
        assert "Предметы" in sheets

        general = sheets[SHEET_GENERAL_V8]
        assert any("Один день из жизни человека в 2050 году" in str(row) for row in general)


@pytest.mark.asyncio
async def test_build_virtual_project_sheets_with_frames_and_entities():
    """Проверяет корректное распределение кадров, таймкодов, закадра, промптов и аналитики по строкам."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        project = Project(
            title="Проект с кадрами",
            slug="project-with-frames",
            topic="Будущее искусственного интеллекта",
            status=ProjectStatus.script_ready,
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        char = Entity(
            project_id=project.id,
            type="character",
            code="c01",
            name="Алекс",
            attrs={"description": "Инженер нейроинтерфейсов"},
        )
        session.add(char)

        f1 = Frame(
            project_id=project.id,
            number=1,
            duration_seconds=4.0,
            image_prompt="A futuristic neon city in 2050, cinematic",
            animation_prompt="Camera pans down showing flying cars",
            voiceover_text="В 2050 году мир изменился до неузнаваемости.",
            attrs={
                "timecode": "00:00 - 00:04",
                "place": "Неоновый мегаполис",
                "main_action": "Пролёт камеры над летающими авто",
                "accent": "Голографические билборды",
                "scene_sense": "Введение в мир будущего",
                "characters": "c01",
                "visual_type": "Экспозиция",
                "cluster": "Город",
            },
        )
        f2 = Frame(
            project_id=project.id,
            number=2,
            duration_seconds=4.0,
            image_prompt="Alex working at a holographic workstation",
            animation_prompt="Alex swipes holographic displays with gesture",
            voiceover_text="Каждое утро Алекса начинается с анализа нейросетей.",
            attrs={
                "timecode": "00:04 - 00:08",
                "place": "Лаборатория",
                "main_action": "Работа за голографическим интерфейсом",
                "accent": "Светящиеся проекции данных",
                "scene_sense": "Демонстрация технологий",
                "characters": "c01",
                "visual_type": "Действие",
                "cluster": "Работа",
            },
        )
        session.add_all([f1, f2])
        await session.commit()

        sheets = await build_virtual_project_sheets(session, project)
        plan = sheets[SHEET_PLAN_V8]

        assert plan[0][2] == "1"
        assert plan[0][3] == "2"

        assert plan[3][2] == "Неоновый мегаполис"
        assert plan[3][3] == "Лаборатория"
        assert plan[4][2] == "Пролёт камеры над летающими авто"
        assert plan[6][2] == "Введение в мир будущего"

        assert plan[ROW_TIMECODE_V8 - 1][2] == "00:00 - 00:04"
        assert plan[ROW_TIMECODE_V8 - 1][3] == "00:04 - 00:08"

        assert "futuristic neon city" in plan[ROW_IMAGE_PROMPT_V8 - 1][2]
        assert "holographic workstation" in plan[ROW_IMAGE_PROMPT_V8 - 1][3]
        assert "Camera pans down" in plan[ROW_VIDEO_PROMPT_V8 - 1][2]

        assert "В 2050 году" in plan[ROW_VOICEOVER_V8 - 1][2]
        assert "Каждое утро Алекса" in plan[ROW_VOICEOVER_V8 - 1][3]

        assert plan[ROW_DURATION_V8 - 1][2] == "4.00"

        chars_sheet = sheets["Персонажи"]
        assert any("Алекс" in str(row) and "c01" in str(row) for row in chars_sheet)


@pytest.mark.asyncio
async def test_build_virtual_project_sheets_large_frame_count():
    """Проверяет стабильность генерации виртуальной сетки для больших батчей (200 кадров)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        project = Project(
            title="Большой батч #17",
            slug="batch-17-large",
            topic="Масштабная раскадровка",
            status=ProjectStatus.script_ready,
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        # Создаем 198 кадров как в проекте #17
        frames = [
            Frame(
                project_id=project.id,
                number=i,
                duration_seconds=2.5,
                voiceover_text=f"Фраза кадра номер {i}",
                attrs={"place": f"Локация {i % 5}", "main_action": f"Действие {i}"},
            )
            for i in range(1, 199)
        ]
        session.add_all(frames)
        await session.commit()

        sheets = await build_virtual_project_sheets(session, project)
        plan = sheets[SHEET_PLAN_V8]

        # 198 кадров + 2 служебные колонки = 200 колонок
        assert len(plan[0]) == 200
        assert plan[0][2] == "1"
        assert plan[0][199] == "198"
        assert plan[4][199] == "Действие 198"
        assert plan[ROW_VOICEOVER_V8 - 1][199] == "Фраза кадра номер 198"