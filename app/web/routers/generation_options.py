"""REST: каталог настроек генерации (мастер проекта, как в Telegram)."""

from __future__ import annotations

from fastapi import APIRouter

from app.generation_options import (
    ASPECT_RATIOS,
    IMAGE_GENERATORS,
    IMAGE_QUALITIES,
    IMAGE_RESOLUTIONS,
    VIDEO_GENERATORS,
    VIDEO_RESOLUTIONS,
)
from app.telegram import wizard as wiz

router = APIRouter(prefix="/generation-options", tags=["generation-options"])


def _choices_to_dict(choices: list) -> list[dict]:
    return [
        {"id": c.id, "label": c.label, "description": c.short_desc or c.outsee_slug}
        for c in choices
    ]


@router.get("/wizard")
async def wizard_catalog() -> dict:
    """Вопросы мастера после создания проекта (8 шагов)."""
    questions = []
    for q in wiz._QUESTIONS:
        questions.append(
            {
                "field": q.field,
                "title": q.title.replace("<b>", "").replace("</b>", ""),
                "choices": _choices_to_dict(q.choices),
                "cols": q.cols,
            }
        )
    return {
        "questions": questions,
        "image_generators": _choices_to_dict(IMAGE_GENERATORS),
        "aspect_ratios": _choices_to_dict(ASPECT_RATIOS),
        "image_resolutions": _choices_to_dict(IMAGE_RESOLUTIONS),
        "image_qualities": _choices_to_dict(IMAGE_QUALITIES),
        "video_generators": _choices_to_dict(VIDEO_GENERATORS),
        "video_resolutions": _choices_to_dict(VIDEO_RESOLUTIONS),
        "boolean": _choices_to_dict(wiz.BOOLEAN_CHOICES),
    }
