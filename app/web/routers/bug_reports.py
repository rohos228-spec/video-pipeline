"""API багрепортов: описание + окно логов → ``баги/``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.bug_report import (
    VALID_WINDOWS_MIN,
    collect_log_snippets,
    write_bug_report,
)

router = APIRouter(prefix="/bug-reports", tags=["bug-reports"])


class BugReportCreate(BaseModel):
    description: str = Field(..., min_length=1, max_length=20_000)
    minutes: int = Field(5, description="1 | 5 | 30 | 60")
    projectId: int | None = None
    projectSlug: str | None = None
    studioVersion: str | None = None


@router.get("/preview")
async def bug_report_preview(minutes: int = 5) -> dict[str, Any]:
    if minutes not in VALID_WINDOWS_MIN:
        raise HTTPException(status_code=400, detail="minutes must be 1, 5, 30 or 60")
    snippets = collect_log_snippets(minutes=minutes)
    return {
        "minutes": minutes,
        "files": [
            {
                "name": s["name"],
                "chars": s["chars"],
                "preview": (s["text"] or "")[-4000:],
            }
            for s in snippets
        ],
    }


@router.post("")
async def create_bug_report(body: BugReportCreate) -> dict[str, Any]:
    if body.minutes not in VALID_WINDOWS_MIN:
        raise HTTPException(status_code=400, detail="minutes must be 1, 5, 30 or 60")
    try:
        return write_bug_report(
            description=body.description,
            minutes=body.minutes,
            project_id=body.projectId,
            project_slug=body.projectSlug,
            studio_version=body.studioVersion,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
