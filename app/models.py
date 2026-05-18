"""SQLAlchemy models: проект, кадр, артефакт, попытка, HITL-запрос, мастер-промт."""

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.settings import settings


class Base(DeclarativeBase):
    pass


class ProjectStatus(str, enum.Enum):
    new = "new"
    # «running» статусы — воркер видит их и запускает соответствующий шаг.
    planning = "planning"  # шаг 1: план
    scripting = "scripting"  # шаг 2: сценарий → закадровые тексты
    splitting = "splitting"  # шаг 3: разбивка на кадры
    # ───── Шаг 4: «Объекты» — sub-menu внутри ────
    generating_hero = "generating_hero"  # шаг 4a: персонажи (former hero)
    generating_items = "generating_items"  # шаг 4b: предметы (new)
    # ───── Шаги 5-N: «Доп работа с EXCEL» (xlsx round-trip), N до 5 ────
    enriching_1 = "enriching_1"
    enriching_2 = "enriching_2"
    enriching_3 = "enriching_3"
    enriching_4 = "enriching_4"
    enriching_5 = "enriching_5"
    # ───── Шаги после enrich-слотов ────
    generating_image_prompts = "generating_image_prompts"  # промты картинок
    generating_images = "generating_images"  # картинки
    generating_animation_prompts = "generating_animation_prompts"
    generating_videos = "generating_videos"
    generating_audio = "generating_audio"
    assembling = "assembling"
    publishing = "publishing"
    # «ready» статусы — воркер их игнорирует, ждём действия пользователя из бота.
    plan_ready = "plan_ready"
    script_ready = "script_ready"
    frames_ready = "frames_ready"
    hero_ready = "hero_ready"  # персонажи готовы
    items_ready = "items_ready"  # предметы готовы (new)
    enrich_1_ready = "enrich_1_ready"
    enrich_2_ready = "enrich_2_ready"
    enrich_3_ready = "enrich_3_ready"
    enrich_4_ready = "enrich_4_ready"
    enrich_5_ready = "enrich_5_ready"
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
    item_reference = "item_reference"  # реф-картинка предмета
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
    approve_blocks = "approve_blocks"  # шаг 3: разбивка на блоки
    approve_hero = "approve_hero"
    approve_excel_extra = "approve_excel_extra"  # шаг 5: доп работа с excel
    approve_image_prompts = "approve_image_prompts"  # шаг 6: промты картинок
    approve_images = "approve_images"
    approve_animation_prompts = "approve_animation_prompts"  # шаг 8: промты анимации
    approve_videos = "approve_videos"
    approve_audio = "approve_audio"  # шаг 10: озвучка
    approve_final = "approve_final"


class HITLDecision(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    regenerate = "regenerate"
    edit_prompt = "edit_prompt"
    rejected = "rejected"


class BatchStatus(str, enum.Enum):
    """Состояние массового проекта (BatchProject).

    new      — создан, но тем ещё нет / не запущен
    running  — очередь идёт (в PR #2 это использует воркер)
    paused   — юзер поставил на паузу
    done     — все подпроекты в published/paused/failed
    """

    new = "new"
    running = "running"
    paused = "paused"
    done = "done"


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
    # None = вопрос ещё не задан, False/True — ответ юзера.
    image_relax: Mapped[bool | None] = mapped_column(default=None)
    video_generator: Mapped[str | None] = mapped_column(String(40), default=None)
    video_resolution: Mapped[str | None] = mapped_column(String(10), default=None)
    # Relax поддерживается outsee только для veo-3-1-fast (на 2025-Q4).
    # None = вопрос ещё не задан / не применим.
    video_relax: Mapped[bool | None] = mapped_column(default=None)
    # Сколько персонажей-героев генерировать в шаге 4 (0..9). None — ещё не
    # выбрано (бот спросит кнопками при клике на «4. Hero»). 0 — без героев.
    hero_count: Mapped[int | None] = mapped_column(default=None)
    # Текстовые описания героев (по одному на каждого). Юзер пишет их по
    # очереди в TG; шаг 4 обрабатывает по индексу: descriptions[i-1] для
    # героя i.
    hero_descriptions: Mapped[list] = mapped_column(JSON, default=list)
    # Кол-во вариаций для каждого героя (parallel со hero_descriptions).
    # variations[i-1] = N означает: для героя i сгенерировать N изображений.
    # Первая вариация — без референса, варианты 2..N — с первой как референс
    # (через input[type=file] на странице outsee.io).
    hero_variations: Mapped[list] = mapped_column(JSON, default=list)
    # Текстовые «отличия» для вариаций 2..N каждого героя — что юзер
    # хочет изменить относительно вариации 1 (поза/ракурс/одежда/etc.).
    # Структура: list[list[str]] длиной n_heroes; внутри — список из
    # (variations[i-1] - 1) строк (если variations[i-1]=3, то 2 модификатора:
    # для v2 и v3). hero_variation_modifiers[i-1][j-1] = текст для вариации
    # j+1 героя i+1 (т.е. варианты с 2 по N).
    hero_variation_modifiers: Mapped[list] = mapped_column(JSON, default=list)
    # Сколько слотов «Доп работа с EXCEL» (xlsx round-trip с ChatGPT)
    # активно для этого проекта. По умолчанию 3, можно увеличить кнопкой
    # «➕ Добавить слот» в TG-меню (до 5 — лимит ProjectStatus.enriching_N).
    # Ссылка на массовый проект (BatchProject). NULL = одиночный проект.
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("batch_projects.id", ondelete="SET NULL"), default=None, index=True
    )
    # Позиция в очереди внутри массового (1..N, сортировка).
    batch_position: Mapped[int | None] = mapped_column(default=None, index=True)
    # Денормализованный slug массового — чтобы project.data_dir работал
    # без лишнего запроса в базу. Обновляется при создании
    # подпроекта и никогда не меняется (slug батча иммутабельный).
    batch_slug: Mapped[str | None] = mapped_column(String(120), default=None)
    # Авто-режим: если True, воркер сам продвигает проект по шагам
    # (PR #2). По умолчанию False — ручной режим.
    auto_mode: Mapped[bool] = mapped_column(default=False)
    enrich_slots_count: Mapped[int] = mapped_column(default=1)
    # Описания предметов (по одному на каждый id в листе «Предметы»).
    # Аналог hero_descriptions, заполняется юзером в xlsx; шаг 4b
    # «Предметы» генерит по одному изображению на каждое непустое описание.
    item_descriptions: Mapped[list] = mapped_column(JSON, default=list)
    # Вариации предметов (parallel со item_descriptions). По 1 по умолчанию.
    item_variations: Mapped[list] = mapped_column(JSON, default=list)
    # Выбранный для каждого шага вариант мастер-промта (имя файла без .md).
    # Пример: {"plan": "default", "script": "horror_v2", "hero": "girl_v3"}.
    # Если ключа нет — берётся `default.md` из соответствующей папки.
    prompt_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    # Per-project override полного «сопр. сообщения», которое уходит в ChatGPT
    # на конкретном шаге. Ключ — step_code (`plan`, `script`, `split`,
    # `hero`, `img_pr`, `anim_pr`). Значение — отредактированный пользователем
    # текст. Если ключа нет/значение пусто — собирается дефолт из мастер-
    # промта + контекста проекта (см. `app.services.gpt_text_builder`).
    gpt_text_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
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

    @property
    def data_dir(self) -> Path:
        """Корневая папка файлов проекта на диске.

        Для бэтч-подпроекта (batch_id задан): вложена в папку массового —
          data/batches/<batch_slug>/sub/<slug>/
        Для одиночного проекта — как раньше:
          data/videos/<slug>/

        batch_slug денормализован на этой же записи, чтобы не дёргать
        relationship batch при каждом вызове (он может быть не lazy-loaded).
        """
        if self.batch_id is not None and self.batch_slug:
            return Path(settings.data_dir) / "batches" / self.batch_slug / "sub" / self.slug
        return Path(settings.data_dir) / "videos" / self.slug


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


class BatchProject(Base):
    """Массовый проект — контейнер для группы подпроектов (роликов).

    Полностью изолирован:
      - собственная папка на диске (data/batches/<slug>/)
      - снапшот промптов (data/batches/<slug>/prompts/)
      - общий topics.xlsx с перечнем тем
      - снапшот настроек эталонного проекта в JSON (settings_snapshot)
      - каждый подпроект — обычная запись в projects с batch_id=этого
        массового + batch_position (порядок 1..N).
    """

    __tablename__ = "batch_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Человеческое название (вводит юзер в TG, любые символы).
    name: Mapped[str] = mapped_column(String(120))
    # Слаг для путей на диске: latin/cyrillic→ASCII, без пробелов, unique.
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    status: Mapped[BatchStatus] = mapped_column(
        Enum(BatchStatus, name="batch_status"), default=BatchStatus.new, index=True
    )
    # Шаблон-проект: ID из projects, из которого скопированы настройки.
    # NULL — настройки пустые (юзер задаст вручную позже).
    template_project_id: Mapped[int | None] = mapped_column(default=None)
    # JSON-снапшот всех настроек (image_generator, hero_mode, hero_count,
    # hero_descriptions, prompt_overrides, gpt_text_overrides, и т.д.) —
    # это именно те значения, которые будут применены ко всем
    # подпроектам при их создании. Изменение settings_snapshot
    # НЕ меняет уже созданные подпроекты.
    settings_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    # Свободные поля для будущих расширений (PR #2 — авто/GPT-апрувы).
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    @property
    def data_dir(self) -> Path:
        """Базовая папка массового проекта на диске.

        Структура:
          data/batches/<slug>/
            topics.xlsx           — общий список тем
            prompts/              — снапшот промптов
            sub/<sub_slug>/       — папки подпроектов
        """
        return Path(settings.data_dir) / "batches" / self.slug

    @property
    def prompts_dir(self) -> Path:
        """Папка со снапшотом промптов этого массового."""
        return self.data_dir / "prompts"

    @property
    def topics_xlsx_path(self) -> Path:
        return self.data_dir / "topics.xlsx"

    @property
    def sub_root(self) -> Path:
        """Корневая папка для всех подпроектов этого массового."""
        return self.data_dir / "sub"


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


class TestPromptProject(Base):
    """Отдельный «тестовый» проект — итеративный цикл доводки одного
    визуального промта через ChatGPT и Nano Banana Pro (Relax).

    Флоу (см. app/services/test_prompt.py):
      1. Юзер задаёт `visual_prompt` (стартовый промт) и `system_prompt`
         (инструкция для ChatGPT, как обрабатывать промт).
      2. Нажимает «▶ Поехали».
      3. Цикл: ChatGPT(system+visual) → txt; Outsee(banana-pro, relax,
         prompt из txt) → картинка; присылаем юзеру с кнопкой
         «✏ Добавить критику».
      4. Юзер пишет критику → ChatGPT(system + критика + предыдущий
         txt-вложение) → новый txt → Outsee → картинка → и т.д.

    Артефакты на диске: data/test_prompts/<slug>/iter_<N>/{prompt.txt,
    image.jpg, critique.txt}. Параллельно можно запускать только ОДИН
    цикл — лочится по `status='running_*'` в этой таблице.
    """

    __tablename__ = "test_prompt_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Стартовый визуальный промт от юзера. Используется в первой
    # итерации; в последующих итерациях GPT работает поверх predыдущего
    # txt + критики, но visual_prompt остаётся в проекте как «семя».
    visual_prompt: Mapped[str | None] = mapped_column(Text, default=None)
    # Инструкция для ChatGPT, как обрабатывать промт. Например:
    # «Переформулируй этот визуальный промт так, чтобы он был
    # максимально подробный и кинематографичный для генерации в
    # Banana Pro. Верни ответ как .txt файл.»
    system_prompt: Mapped[str | None] = mapped_column(Text, default=None)
    # Номер текущей (последней) итерации. 0 — ещё не запускали.
    current_iter: Mapped[int] = mapped_column(default=0)
    # 'idle' | 'running_gpt' | 'running_outsee' | 'waiting_critique'
    # | 'stopped' | 'error'
    status: Mapped[str] = mapped_column(String(30), default="idle")
    # Произвольные метаданные итераций (ошибки, last error, etc.).
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    @property
    def data_dir(self) -> Path:
        return Path(settings.data_dir) / "test_prompts" / self.slug

    def iter_dir(self, n: int) -> Path:
        return self.data_dir / f"iter_{n:03d}"
