"""POST /sidebar-layout/gen-queue/enqueue must be registered on router."""

from __future__ import annotations

from app.web.routers.sidebar_layout import router


def test_gen_queue_enqueue_route_registered() -> None:
    paths = [r.path for r in router.routes]
    assert "/sidebar-layout/gen-queue/enqueue" in paths
