"""Agent loop must import pipeline builder."""

from __future__ import annotations

import importlib


def test_agent_loop_imports_build_pipeline_payload() -> None:
    mod = importlib.import_module("app.fleet.agent_loop")
    assert hasattr(mod, "build_pipeline_payload") or "build_pipeline_payload" in dir(mod)
    from app.fleet.pipeline_list import build_pipeline_payload

    assert mod.build_pipeline_payload is build_pipeline_payload
