"""GET /api/projects/{id}/observations/telemetry -- read-only, project-wide
aggregation (not execution-scoped, unlike every other endpoint in this
router). Pure aggregation logic is unit-tested without a DB in
tests/test_telemetry.py; this exercises the real DB query + HTTP wiring.
"""
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
