"""Per-project GPT lock: script не стартует пока plan не завершён."""

from __future__ import annotations

import asyncio

import pytest

from app.services.xlsx_gpt_flow import run_under_xlsx_lock


@pytest.mark.asyncio
async def test_project_gpt_lock_serializes_steps() -> None:
    order: list[str] = []
    plan_done = asyncio.Event()

    async def plan_fn() -> str:
        order.append("plan_start")
        await asyncio.sleep(0.15)
        order.append("plan_end")
        plan_done.set()
        return "plan"

    async def script_fn() -> str:
        await plan_done.wait()
        order.append("script")
        return "script"

    plan_task = asyncio.create_task(run_under_xlsx_lock(1, "plan", plan_fn))
    await asyncio.sleep(0.05)
    script_task = asyncio.create_task(run_under_xlsx_lock(1, "script", script_fn))

    await asyncio.gather(plan_task, script_task)
    assert order == ["plan_start", "plan_end", "script"]
