"""Хранилище проектных данных в xlsx (по шаблону пользователя).

Каждый проект получает копию `templates/project_template.xlsx` в папке
`data/videos/<slug>/project.xlsx`. Туда пишутся все текстовые поля
(промты, диалоги, пути картинок/видео, gen_id-ы). SQLite остаётся источником
правды для статусов и HITL — xlsx это «человекочитаемая» зеркальная копия,
которую владелец может править руками между запусками.
"""

from app.storage.project_sheet import ProjectSheet, for_project, sheet_for_slug

__all__ = ["ProjectSheet", "for_project", "sheet_for_slug"]
