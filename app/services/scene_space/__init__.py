"""Cinematic scene space: geometry, schema, and DB access."""

from app.services.scene_space.errors import DegenerateAxisError
from app.services.scene_space.geom import apply_deltas, axis_side, normalize_pair
from app.services.scene_space.migrate import (
    downgrade_scene_space_schema,
    migrate_scene_space_schema,
)
from app.services.scene_space.store import (
    get_scene_space,
    list_frame_spaces,
    state_at,
    upsert_frame_space,
    upsert_scene_space,
)

__all__ = [
    "DegenerateAxisError",
    "apply_deltas",
    "axis_side",
    "downgrade_scene_space_schema",
    "get_scene_space",
    "list_frame_spaces",
    "migrate_scene_space_schema",
    "normalize_pair",
    "state_at",
    "upsert_frame_space",
    "upsert_scene_space",
]
