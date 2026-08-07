"""Мульти-агентная система создания сцен (scene_design).

Одна нода оркестратора между `split` и `hero`. Внутри — параллельный
fan-out категорийных GPT-агентов (characters/world/style/camera/action)
+ финальный агент-сборщик. Выход — тот же контракт `scene_registry` +
apply-ops, что у scene_grammar, поэтому downstream (hero/items/img_pr)
не меняется.
"""

from app.services.scene_design.runner import (  # noqa: F401
    scene_design_done,
    scene_design_enabled,
)
