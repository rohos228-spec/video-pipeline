"""REST: массовые проекты (батчи) — миграции и сводки для UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import batches as batches_svc
from app.web.deps import get_session

router = APIRouter(prefix="/batches", tags=["batches"])


@router.post("/migrate/clean-subproject-meta")
async def migrate_clean_subproject_meta(
    batch_id: int | None = Query(default=None, description="Только этот батч"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Очистить унаследованный runtime-мусор в meta подпроектов батчей."""
    result = await batches_svc.clean_subprojects_meta(session, batch_id=batch_id)
    await session.commit()
    return {"ok": True, **result}
