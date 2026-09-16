"""Create / drop scenes_space and frames_space. Idempotent up; down drops only those."""

from __future__ import annotations

from typing import Any

from app.services.scene_space import models as _scene_space_models  # noqa: F401

_SQL_SCENES = """
CREATE TABLE IF NOT EXISTS scenes_space (
    scene_id TEXT NOT NULL PRIMARY KEY,
    space_json TEXT NOT NULL
)
"""

_SQL_FRAMES = """
CREATE TABLE IF NOT EXISTS frames_space (
    uuid TEXT NOT NULL PRIMARY KEY,
    scene_id TEXT NOT NULL,
    shot_order INTEGER NOT NULL,
    axis_pair TEXT,
    axis_side TEXT,
    screen_pos TEXT,
    screen_dir TEXT,
    shot_size TEXT,
    angle_v TEXT,
    angle_h TEXT,
    beat_role TEXT,
    crossing_method TEXT,
    space_delta_json TEXT,
    reestablish_due INTEGER NOT NULL DEFAULT 0,
    manual INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (scene_id) REFERENCES scenes_space(scene_id),
    CHECK (shot_order >= 1),
    CHECK (axis_side IN ('A', 'B') OR axis_side IS NULL),
    CHECK (reestablish_due IN (0, 1)),
    CHECK (manual IN (0, 1))
)
"""

_SQL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_frames_space_scene
ON frames_space (scene_id, shot_order)
"""

_SQL_MANUAL = (
    "ALTER TABLE frames_space ADD COLUMN manual "
    "INTEGER NOT NULL DEFAULT 0 CHECK (manual IN (0,1))"
)


async def migrate_scene_space_schema(conn: Any) -> None:
    await conn.exec_driver_sql(_SQL_SCENES)
    await conn.exec_driver_sql(_SQL_FRAMES)
    cols = await _column_names(conn, "frames_space")
    if "manual" not in cols:
        await conn.exec_driver_sql(_SQL_MANUAL)
    await conn.exec_driver_sql(_SQL_INDEX)


async def downgrade_scene_space_schema(conn: Any) -> None:
    """DROP only frames_space / scenes_space. Never frames, scenes, projects."""
    await conn.exec_driver_sql("DROP TABLE IF EXISTS frames_space")
    await conn.exec_driver_sql("DROP TABLE IF EXISTS scenes_space")


async def _column_names(conn: Any, table: str) -> set[str]:
    rows = (await conn.exec_driver_sql(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}
