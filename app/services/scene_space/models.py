"""SQLAlchemy models for scenes_space / frames_space (cinematic scene space)."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class SceneSpace(Base):
    __tablename__ = "scenes_space"

    scene_id: Mapped[str] = mapped_column(Text, primary_key=True)
    space_json: Mapped[str] = mapped_column(Text, nullable=False)

    frames: Mapped[list[FrameSpace]] = relationship(back_populates="scene")


class FrameSpace(Base):
    __tablename__ = "frames_space"
    __table_args__ = (
        CheckConstraint("shot_order >= 1", name="ck_frames_space_shot_order"),
        CheckConstraint(
            "axis_side IN ('A', 'B') OR axis_side IS NULL",
            name="ck_frames_space_axis_side",
        ),
        CheckConstraint(
            "reestablish_due IN (0, 1)",
            name="ck_frames_space_reestablish_due",
        ),
        CheckConstraint("manual IN (0, 1)", name="ck_frames_space_manual"),
        Index("idx_frames_space_scene", "scene_id", "shot_order"),
    )

    uuid: Mapped[str] = mapped_column(Text, primary_key=True)
    scene_id: Mapped[str] = mapped_column(
        Text, ForeignKey("scenes_space.scene_id"), nullable=False
    )
    shot_order: Mapped[int] = mapped_column(Integer, nullable=False)
    axis_pair: Mapped[str | None] = mapped_column(Text, default=None)
    axis_side: Mapped[str | None] = mapped_column(Text, default=None)
    screen_pos: Mapped[str | None] = mapped_column(Text, default=None)
    screen_dir: Mapped[str | None] = mapped_column(Text, default=None)
    shot_size: Mapped[str | None] = mapped_column(Text, default=None)
    angle_v: Mapped[str | None] = mapped_column(Text, default=None)
    angle_h: Mapped[str | None] = mapped_column(Text, default=None)
    beat_role: Mapped[str | None] = mapped_column(Text, default=None)
    crossing_method: Mapped[str | None] = mapped_column(Text, default=None)
    space_delta_json: Mapped[str | None] = mapped_column(Text, default=None)
    reestablish_due: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    scene: Mapped[SceneSpace] = relationship(back_populates="frames")
