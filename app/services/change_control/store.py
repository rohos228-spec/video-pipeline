"""Таблицы контура изменений (только новые) и канонический хэш.

``config_versions`` append-only (триггеры SQLite). Указатель ``config_pointers``
меняется только сравнением с ожидаемой версией (CAS).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

ST_PROPOSED = "proposed"
ST_APPROVED = "approved"
ST_APPLIED = "applied"
ST_REJECTED = "rejected"
ST_ROLLED_BACK = "rolled_back"
ST_FAILED = "failed"
ST_DEVELOPER_REVIEW = "developer_review"
STATUSES = (ST_PROPOSED, ST_APPROVED, ST_APPLIED, ST_REJECTED, ST_ROLLED_BACK, ST_FAILED,
            ST_DEVELOPER_REVIEW)

VK_BASELINE = "baseline"
VK_APPLY = "apply"
VK_ROLLBACK = "rollback"
VK_AUTO_ROLLBACK = "auto_rollback"


def new_id() -> str:
    return uuid.uuid4().hex


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(value: Any) -> str:
    """Хэш содержимого без редактирования секретов: он должен отражать реальный текст."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class ChangeProposal(Base):
    __tablename__ = "change_proposals"
    __table_args__ = (Index("ix_change_proposals_target", "target", "status"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tier: Mapped[str] = mapped_column(String(4))
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    node_key: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(24))
    target: Mapped[str] = mapped_column(String(240), default="")
    base_version: Mapped[int] = mapped_column(Integer, default=0)
    base_digest: Mapped[str] = mapped_column(String(64), default="")
    candidate: Mapped[dict] = mapped_column(JSON, default=dict)
    diff: Mapped[str] = mapped_column(Text, default="")
    digest: Mapped[str] = mapped_column(String(64), index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    proposer: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(16), default=ST_PROPOSED, index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    approved_by: Mapped[str] = mapped_column(String(120), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shadow: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejected_by: Mapped[str] = mapped_column(String(120), default="")
    reject_reason: Mapped[str] = mapped_column(Text, default="")
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    __table_args__ = (UniqueConstraint("target", "version", name="uq_config_version"),
                      Index("ix_config_versions_proposal", "source_proposal_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(String(240))
    version: Mapped[int] = mapped_column(Integer)
    parent_version: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16))
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_key: Mapped[str] = mapped_column(String(120), default="")
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    digest: Mapped[str] = mapped_column(String(64))
    source_proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    applied_by: Mapped[str] = mapped_column(String(120), default="")
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConfigPointer(Base):
    __tablename__ = "config_pointers"
    target: Mapped[str] = mapped_column(String(240), primary_key=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_TABLES = [ChangeProposal.__table__, ConfigVersion.__table__, ConfigPointer.__table__]

_ADDED_COLUMNS = (("rejected_by", "rejected_by VARCHAR(120) DEFAULT ''"),
                  ("reject_reason", "reject_reason TEXT DEFAULT ''"),
                  ("rejected_at", "rejected_at DATETIME"))

_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS config_versions_no_update BEFORE UPDATE ON "
    "config_versions BEGIN SELECT RAISE(ABORT, 'config versions are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS config_versions_no_delete BEFORE DELETE ON "
    "config_versions BEGIN SELECT RAISE(ABORT, 'config versions are append-only'); END",
)


async def ensure_change_control_schema(conn) -> None:
    """Идемпотентно создаёт таблицы контура (вызывается из стартового пути)."""
    await conn.run_sync(lambda sync_conn: Base.metadata.create_all(
        sync_conn, tables=_TABLES, checkfirst=True))
    if conn.dialect.name == "sqlite":
        # create_all не добавляет колонки в уже существующую таблицу.
        have = {r[1] for r in (await conn.exec_driver_sql(
            "PRAGMA table_info(change_proposals)")).fetchall()}
        for name, ddl in _ADDED_COLUMNS:
            if name not in have:
                await conn.exec_driver_sql(f"ALTER TABLE change_proposals ADD COLUMN {ddl}")
        for ddl in _TRIGGERS:
            await conn.exec_driver_sql(ddl)


def sessions():
    """Те же сессии мастер-БД, что у слоя действий (тесты подменяют их там же)."""
    from app.services.action_layer import store as al_store
    return al_store.sessions()
