"""SQLAlchemy models: проект, кадр, артефакт, попытка, HITL-запрос, мастер-промт."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ProjectStatus(str, enum.Enum):
    new = "new"
    # «running» статусы — воркер видит их и запускает соответствующий шаг.
    planning = "planning"  # шаг 1: план
    scripting = "scripting"  # шаг 2: сценарий → закадровые тексты
    splitting = "splitting"  # шаг 3: разбивка на кадры
    generating_hero = "generating_hero"  # шаг 4: hero-картинка
    generating_image_prompts = "generating_image_prompts"  # шаг 5: промты картинок
    generating_images = "generating_images"  # шаг 6: картинки
    generating_animation_prompts = "generating_animation_prompts"  # шаг 7
    generating_videos = "generating_videos"  # шаг 8
    generating_audio = "generating_audio"  # шаг 9
    assembling = "assembling"  # шаг 10
    publishing = "publishing"
    # «ready» статусы — воркер их игнорирует, ждём действия пользователя из бота.
    plan_ready = "plan_ready"
    script_ready = "script_ready"
    frames_ready = "frames_ready"
    hero_ready = "hero_ready"
    image_prompts_ready = "image_prompts_ready"
    images_ready = "images_ready"
    animation_prompts_ready = "animation_prompts_ready"
    videos_ready = "videos_ready"
    audio_ready = "audio_ready"
    assembled = "assembled"
    published = "published"
    paused = "paused"
    failed = "failed"


class FrameStatus(str, enum.Enum):
    planned = "planned"
    image_prompt_ready = "image_prompt_ready"
    image_generated = "image_generated"
    image_approved = "image_approved"
    animation_prompt_ready = "animation_prompt_ready"
    video_generated = "video_generated"
    video_approved = "video_approved"
    done = "done"
    failed = "failed"


class ArtifactKind(str, enum.Enum):
    hero_reference = "hero_reference"
    scene_image = "scene_image"
    scene_video = "scene_video"
    audio = "audio"
    subtitle = "subtitle"
    whisper_words = "whisper_words"
    final_video = "final_video"
    excel_export = "excel_export"


class PromptKey(str, enum.Enum):
    PLAN_SHORTS = "PLAN_SHORTS"
    SCRIPT_SHORTS = "SCRIPT_SHORTS"
    IMAGE_SHORTS = "IMAGE_SHORTS"
    VIDEO_SHORTS = "VIDEO_SHORTS"
    IMAGE_CHECK = "IMAGE_CHECK"
    VIDEO_CHECK = "VIDEO_CHECK"
    HERO_SHORTS = "HERO_SHORTS"
    RAZBIVKA_SLOV = "RAZBIVKA_SLOV"


class AttemptResult(str, enum.Enum):
    ok = "ok"
    transient_fail = "transient_fail"
    permanent_fail = "permanent_fail"
    needs_human = "needs_human"


class HITLKind(str, enum.Enum):
    approve_plan = "approve_plan"
    approve_script = "approve_script"
    approve_hero = "approve_hero"
    approve_images = "approve_images"
    approve_videos = "approve_videos"
    approve_final = "approve_final"


class HITLDecision(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    regenerate = "regenerate"
    edit_prompt = "edit_prompt"
    rejected = "rejected"


def _now() -> datetime:
    return datetime.utcnow()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    topic: Mapped[str] = mapped_column(Text)
    hero_mode: Mapped[str] = mapped_column(String(20), default="auto")  # hero | no_hero | auto
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status"), default=ProjectStatus.new, index=True
    )
    general_plan: Mapped[str | None] = mapped_column(Text, default=None)
    hero_description: Mapped[str | None] = mapped_column(Text, default=None)
    script_text: Mapped[str | None] = mapped_column(Text, default=None)
    # Настройки генерации — задаются в мастере после /new (5 вопросов).
    # Если пусто — проект в статусе `new` и ждёт ответов.
    image_generator: Mapped[str | None] = mapped_column(String(40), default=None)
    aspect_ratio: Mapped[str | None] = mapped_column(String(10), default=None)
    image_resolution: Mapped[str | None] = mapped_column(String(10), default=None)
    video_generator: Mapped[str | None] = mapped_column(String(40), default=None)
    video_resolution: Mapped[str | None] = mapped_column(String(10), default=None)
    # Сколько персонажей-героев генерировать в шаге 4 (0..9). None — ещё не
    # выбрано (бот спросит кнопками при клике на «4. Hero»). 0 — без героев.
    hero_count: Mapped[int | None] = mapped_column(default=None)
    # Текстовые описания героев (по одному на каждого). Юзер пишет их по
    # очереди в TG; шаг 4 обрабатывает по индексу: descriptions[i-1] для
    # героя i.
    hero_descriptions: Mapped[list] = mapped_column(JSON, default=list)
    # Выбранный для каждого шага вариант мастер-промта (имя файла без .md).
    # Пример: {"plan": "default", "script": "horror_v2", "hero": "girl_v3"}.
    # Если ключа нет — берётся `default.md` из соответствующей папки.
    prompt_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    frames: Mapped[list[Frame]] = relationship(back_populates="project", cascade="all,delete-orphan")
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="project", cascade="all,delete-orphan"
    )
    hitl_requests: Mapped[list[HITLRequest]] = relationship(
        back_populates="project", cascade="all,delete-orphan"
    )


class Frame(Base):
    __tablename__ = "frames"
    __table_args__ = (UniqueConstraint("project_id", "number", name="uq_frame_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column()  # уникальный номер внутри проекта (1..N)
    voiceover_text: Mapped[str] = mapped_column(Text)
    meaning: Mapped[str | None] = mapped_column(Text, default=None)
    transition_from: Mapped[str | None] = mapped_column(Text, default=None)
    transition_to: Mapped[str | None] = mapped_column(Text, default=None)
    duration_seconds: Mapped[float | None] = mapped_column(default=None)
    start_ts: Mapped[float | None] = mapped_column(default=None)
    end_ts: Mapped[float | None] = mapped_column(default=None)
    image_prompt: Mapped[str | None] = mapped_column(Text, default=None)
    animation_prompt: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[FrameStatus] = mapped_column(
        Enum(FrameStatus, name="frame_status"), default=FrameStatus.planned, index=True
    )
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)  # персонажи, палитра, стиль, планы
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    project: Mapped[Project] = relationship(back_populates="frames")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="frame", cascade="all,delete-orphan")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[int | None] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    kind: Mapped[ArtifactKind] = mapped_column(Enum(ArtifactKind, name="artifact_kind"), index=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    path: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    project: Mapped[Project] = relationship(back_populates="artifacts")
    frame: Mapped[Frame | None] = relationship(back_populates="artifacts")


class MasterPrompt(Base):
    __tablename__ = "master_prompts"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_prompt_key_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[PromptKey] = mapped_column(Enum(PromptKey, name="prompt_key"), index=True)
    version: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[int | None] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    task_name: Mapped[str] = mapped_column(String(120), index=True)
    n: Mapped[int] = mapped_column(default=1)
    result: Mapped[AttemptResult] = mapped_column(Enum(AttemptResult, name="attempt_result"))
    error: Mapped[str | None] = mapped_column(Text, default=None)
    logs: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class HITLRequest(Base):
    __tablename__ = "hitl_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[int | None] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    kind: Mapped[HITLKind] = mapped_column(Enum(HITLKind, name="hitl_kind"), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    tg_message_id: Mapped[int | None] = mapped_column(default=None)
    decision: Mapped[HITLDecision] = mapped_column(
        Enum(HITLDecision, name="hitl_decision"), default=HITLDecision.pending, index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    project: Mapped[Project] = relationship(back_populates="hitl_requests")
