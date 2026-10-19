import asyncio
import json

import httpx
import test_actions_v1_contract as actions_tests
from fastapi import FastAPI


class Telemetry(actions_tests.DurableActions):
    async def client(self):
        app = FastAPI()
        from app.web.routers import observations as api
        app.include_router(api.router, prefix="/api")
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")

    async def test_empty_project_returns_zeroed_shape(self):
        async with await self.client() as client:
            response = await client.get("/api/projects/7/observations/telemetry")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["project_id"], 7)
            self.assertIsNone(body["since"])
            self.assertEqual(body["incidents"]["total"], 0)
            self.assertEqual(body["decisions"]["total"], 0)
            self.assertEqual(body["node_latency"]["by_node"], {})
            self.assertEqual(body["budgets_by_execution"], {})
            self.assertEqual(body["cost"]["total_calls"], 0)
            self.assertEqual(body["cost"]["total_cost_usd"], 0.0)

    async def test_reflects_seeded_incident_and_budget(self):
        await self.exhausted_fixture()
        async with await self.client() as client:
            response = await client.get("/api/projects/7/observations/telemetry")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["incidents"]["total"], 1)
            self.assertEqual(body["incidents"]["by_type"], {"missing_payload": 1})
            self.assertEqual(body["incidents"]["by_status"], {"human_review": 1})
            budget = body["budgets_by_execution"]["exec-durable"]
            self.assertEqual(budget["request_limit"], 20)
            self.assertEqual(budget["requests_used"], 0)
            self.assertFalse(budget["held"])
            self.assertTrue(budget["enabled"])

    async def test_since_hours_excludes_old_incidents(self):
        await self.exhausted_fixture()
        async with await self.client() as client:
            unbounded = await client.get("/api/projects/7/observations/telemetry")
            windowed = await client.get("/api/projects/7/observations/telemetry",
                params={"since_hours": 1})
            self.assertEqual(unbounded.json()["incidents"]["total"],
                windowed.json()["incidents"]["total"])
            self.assertEqual(windowed.json()["since"] is not None, True)

    async def test_foreign_project_sees_nothing(self):
        await self.exhausted_fixture()
        async with await self.client() as client:
            response = await client.get("/api/projects/8/observations/telemetry")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["incidents"]["total"], 0)

    async def test_rejects_out_of_range_since_hours(self):
        async with await self.client() as client:
            response = await client.get("/api/projects/7/observations/telemetry",
                params={"since_hours": 0})
            self.assertEqual(response.status_code, 422)
            response = await client.get("/api/projects/7/observations/telemetry",
                params={"since_hours": 100000})
            self.assertEqual(response.status_code, 422)

    async def test_generator_pushes_telemetry_then_keepalive_when_unchanged(self):
        from app.web.routers.observations import _telemetry_stream_events
        gen = _telemetry_stream_events(7, None)
        try:
            first = await asyncio.wait_for(gen.__anext__(), timeout=5)
            second = await asyncio.wait_for(gen.__anext__(), timeout=5)
        finally:
            await gen.aclose()
        self.assertTrue(first.startswith("data:"), first)
        payload = json.loads(first[len("data:"):].strip())
        self.assertEqual(payload["type"], "telemetry")
        self.assertEqual(payload["data"]["project_id"], 7)
        self.assertEqual(payload["data"]["incidents"]["total"], 0)
        self.assertEqual(second, ": keepalive\n\n")

    async def test_generator_pushes_a_new_telemetry_event_when_data_changes(self):
        from app.web.routers.observations import _telemetry_stream_events
        gen = _telemetry_stream_events(7, None)
        try:
            first = await asyncio.wait_for(gen.__anext__(), timeout=5)
            await self.exhausted_fixture()
            second = await asyncio.wait_for(gen.__anext__(), timeout=5)
        finally:
            await gen.aclose()
        first_payload = json.loads(first[len("data:"):].strip())
        self.assertEqual(first_payload["data"]["incidents"]["total"], 0)
        self.assertTrue(second.startswith("data:"), second)
        second_payload = json.loads(second[len("data:"):].strip())
        self.assertEqual(second_payload["type"], "telemetry")
        self.assertEqual(second_payload["data"]["incidents"]["total"], 1)

    async def test_stream_route_returns_event_stream_content_type(self):
        from app.web.routers.observations import telemetry_stream
        response = await telemetry_stream(project_id=7, since_hours=None)
        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        await response.body_iterator.aclose()
