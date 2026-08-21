"""Мульти-агентная система создания сцен (scene_design).

Веер нод на канвасе между `split` и `hero`: 5 нод ``sd_agent``
(``data.agent`` = characters/world/style/camera/action) запускаются
параллельно одной фазой (статус ``scene_designing`` → ``scene_agents_ready``),
затем нода ``sd_assemble`` (``scene_assembling`` → ``scene_design_ready``)
гоняет финального агента-сборщика. Выход — тот же контракт
`scene_registry` + apply-ops, что у scene_grammar, поэтому downstream
(hero/items/img_pr) не меняется.
"""

from app.services.scene_design.runner import (  # noqa: F401
    agents_all_done,
    invalidate_agent,
    scene_design_done,
    scene_design_enabled,
)
