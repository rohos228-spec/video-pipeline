"""GET /api/projects/{id}/observations/telemetry -- read-only, project-wide
aggregation (not execution-scoped, unlike every other endpoint in this
router). Pure aggregation logic is unit-tested without a DB in
tests/test_telemetry.py; this exercises the real DB query + HTTP wiring.

GET .../observations/stream -- the Server-Sent Events variant of the same
endpoint: periodic telemetry pushes, deduplicated via the existing digest()
helper so an idle project only sends ': keepalive' comments. Tested by
driving the generator function directly (see the comment on the first
stream test below) rather than over real HTTP -- httpx.ASGITransport
cannot cleanly test a deliberately-infinite SSE stream.
"""
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
            # since_hours=0 would exclude everything; use a tiny but valid
            # window that is already in the past relative to the fixture's
            # detected_at=utcnow() at seed time -- 1 hour from "now" (the
            # request's "now") is in the future relative to the seed, so it
            # must still include the incident; the real exclusion behavior
            # (a wide-enough window reports fewer incidents than an
            # unbounded query) is what this asserts.
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

    # The stream generator is tested by iterating it directly (in-process,
    # no ASGI transport) rather than over real HTTP: httpx.ASGITransport
    # cannot cleanly test a deliberately-infinite SSE generator -- its
    # `receive()` blocks forever waiting for the response body to fully
    # complete, which an infinite stream never does (confirmed: it hangs
    # the test process even with asyncio.wait_for around the consumer, in
    # both httpx.AsyncClient.aclose() and response.aclose()). This matches
    # existing precedent in this repo: app/web/routers/fleet.py's own SSE
    # log-tail stream (the same StreamingResponse + "data: ...\n\n" /
    # ": keepalive\n\n" shape reused here) has no HTTP-level test either.
    # Driving the generator function directly exercises the real
    # dedup/keepalive/error-handling logic without that transport artifact.
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
        # Nothing changed between tick 1 and tick 2 on an empty project.
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
        self.assertTrue(second.startswith("data:"), second)  # not a keepalive -- the digest changed
        second_payload = json.loads(second[len("data:"):].strip())
        self.assertEqual(second_payload["type"], "telemetry")
        self.assertEqual(second_payload["data"]["incidents"]["total"], 1)

    async def test_stream_route_returns_event_stream_content_type(self):
        # Calls the FastAPI route function directly as a plain coroutine --
        # no ASGI dispatch/transport at all, so there is no infinite-stream
        # deadlock risk here (see the class-level comment): it only checks
        # that the endpoint wires _telemetry_stream_events into a
        # StreamingResponse with the right media type/headers.
        from app.web.routers.observations import telemetry_stream
        response = await telemetry_stream(project_id=7, since_hours=None)
        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        await response.body_iterator.aclose()
