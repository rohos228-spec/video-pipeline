"""Параметры шагов plan/script/split из Project.meta → блок в GPT-сообщении."""

from __future__ import annotations

from app.models import Project

CHARS_PER_SEC = 14
BLANK = "____"
_DURATION_STEPS = frozenset({"plan", "script"})


def _meta_params(project: Project) -> dict:
    meta = getattr(project, "meta", None) or {}
    raw = meta.get("node_step_params")
    return raw if isinstance(raw, dict) else {}


def _step_bucket(project: Project, step: str) -> dict:
    bucket = _meta_params(project).get(step)
    return bucket if isinstance(bucket, dict) else {}


def _parse_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n


def duration_seconds_for_step(project: Project, step_code: str) -> float | None:
    """Длительность для plan/script. Сценарий наследует план, если своё не задано."""
    if step_code not in _DURATION_STEPS:
        return None
    own = _parse_number(_step_bucket(project, step_code).get("duration_seconds"))
    if own is not None:
        return own
    if step_code == "script":
        return _parse_number(_step_bucket(project, "plan").get("duration_seconds"))
    return None


def _fmt_num(value: float | None) -> str:
    if value is None:
        return BLANK
    if value == int(value):
        return str(int(value))
    return str(value)


def build_duration_params_block(project: Project, step_code: str, *, header: str) -> str:
    dur = duration_seconds_for_step(project, step_code)
    dur_s = _fmt_num(dur)
    chars_s = _fmt_num(int(round(dur * CHARS_PER_SEC)) if dur is not None else None)
    return (
        f"{header}\n"
        f"Длина {dur_s} секунд\n"
        f"Количество символов (длина секунд × 14) = {chars_s}"
    )


def build_split_params_block(project: Project) -> str:
    split = _step_bucket(project, "split")

    def cell(key: str) -> str:
        return _fmt_num(_parse_number(split.get(key)))

    return (
        "Разбивка\n"
        f"Минимальное количество символов в ячейке {cell('cell_min_chars')}\n"
        f"Максимальное количество символов в ячейке {cell('cell_max_chars')}\n"
        f"Средние значения от {cell('cell_avg_min')} до {cell('cell_avg_max')}"
    )


def build_step_params_block(project: Project, step_code: str) -> str:
    if step_code == "plan":
        return build_duration_params_block(project, "plan", header="Сценарий")
    if step_code == "script":
        return build_duration_params_block(project, "script", header="Закадровый текст")
    if step_code == "split":
        return build_split_params_block(project)
    return ""


def send_to_main_pc_for_project(project: Project) -> bool:
    """Отправка на главный ПК для монтажа (meta.node_step_params.assemble.send_to_main_pc)."""
    val = _step_bucket(project, "assemble").get("send_to_main_pc")
    if val is False:
        return False
    return True


def subtitles_enabled_for_project(project: Project) -> bool:
    """Субтитры при сборке: meta.node_step_params.assemble.subtitles_enabled (по умолчанию вкл.)."""
    val = _step_bucket(project, "assemble").get("subtitles_enabled")
    if val is False:
        return False
    return True


def _parse_nonneg_seconds(value: object, *, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(120.0, n))


def post_voiceover_tail_seconds_for_project(project: Project) -> float:
    """Секунды видео после конца озвучки (заморозка последнего кадра)."""
    val = _step_bucket(project, "assemble").get("post_voiceover_tail_seconds")
    return _parse_nonneg_seconds(val)


def assemble_bgm_level_from_meta(meta: dict) -> int | None:
    """0..100 из node_step_params.assemble.bgm_level."""
    nsp = meta.get("node_step_params")
    if not isinstance(nsp, dict):
        return None
    assemble = nsp.get("assemble")
    if not isinstance(assemble, dict):
        return None
    val = assemble.get("bgm_level")
    if val is None or val == "":
        return None
    try:
        return max(0, min(100, int(val)))
    except (TypeError, ValueError):
        return None


def assemble_bgm_level_for_project(project: Project) -> int | None:
    return assemble_bgm_level_from_meta(getattr(project, "meta", None) or {})


def append_step_params_to_gpt_text(
    project: Project, step_code: str, base_text: str
) -> str:
    """Добавляет блок параметров к тексту, уходящему в ChatGPT."""
    block = build_step_params_block(project, step_code).strip()
    if not block:
        return base_text
    base = (base_text or "").rstrip()
    if not base:
        return block
    return f"{base}\n\n---\n{block}"
