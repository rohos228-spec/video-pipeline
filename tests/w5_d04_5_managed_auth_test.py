"""D04 п.5: управляемый `json_request` обязан нести Authorization, а журнал фактов не должен видеть заголовки.

Класс: U на заменителе (httpx.send подменён, loopback-адрес, ключ синтетический). Сети нет.
До правки: json_request не принимал headers, createTask и опрос уходили без Authorization (боевой Kie ответил бы 401).
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.bots import kie_http
from app.services import media_transport

ORIGIN = "http://127.0.0.1:18766"
KEY = "synthetic-d04-secret-key"


class Job:
    job_id = "j1"
    config = {"media": {"origin": ORIGIN, "timeout_s": 5, "poll_interval_s": 0, "max_polls": 3}}

    async def reserve(self, category):
        return "r1"

    async def settle(self, request_id, state):
        pass


class ManagedAuth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen, self.facts = [], []

        async def send(client, request, **kw):
            self.seen.append(request)
            await request.aread()
            return httpx.Response(200, json={"code": 200, "data": {"taskId": "T-1"}}, request=request)

        async def fact(kind, payload=None, *, request_id=None, body=None):
            self.facts.append((kind, payload, body))

        scope = SimpleNamespace(execution_id="e1")
        ctx = SimpleNamespace(scope=scope, observer=SimpleNamespace(failures=set()))
        for p in (patch.object(httpx.AsyncClient, "send", send),
                  patch.object(media_transport, "record_fact", fact),
                  patch.object(media_transport, "_attempt", SimpleNamespace(get=lambda: ctx)),
                  patch.object(kie_http, "kie_auth_headers", lambda **k: {"Authorization": "Bearer " + KEY})):
            p.start()
            self.addCleanup(p.stop)
        tok = media_transport.current_job.set(Job())
        self.addCleanup(media_transport.current_job.reset, tok)

    async def test_json_request_forwards_headers_and_journal_never_sees_them(self):
        await media_transport.json_request("POST", "/api/v1/jobs/createTask", payload={"a": 1},
                                           headers={"Authorization": "Bearer " + KEY})
        self.assertEqual(self.seen[0].headers["authorization"], "Bearer " + KEY)
        self.assertNotIn(KEY, repr(self.facts))

    async def test_poll_and_create_helpers_pass_auth(self):
        calls = []

        async def fake(method, path, **kw):
            calls.append((path, kw.get("headers")))
            raise RuntimeError("stop")

        with patch.object(media_transport, "json_request", fake):
            with self.assertRaises(RuntimeError):
                await kie_http.poll_task("jobs", "T-1")
        self.assertEqual(calls[0][1], {"Authorization": "Bearer " + KEY})

    async def test_create_task_passes_auth_headers_to_managed_transport(self):
        """Поведение вместо поиска строки в исходнике: в управляемом режиме createTask отдаёт транспорту
        Authorization (платный шов снят через unwrap: нужен именно вызывающий код create_task)."""
        import inspect
        calls = []

        async def fake(method, path, **kw):
            calls.append((method, path, kw.get("headers")))
            return httpx.Response(200, json={"code": 200, "data": {"taskId": "T-9"}},
                                  request=httpx.Request(method, ORIGIN + path))

        raw_create = inspect.unwrap(kie_http.create_task)
        with patch.object(media_transport, "json_request", fake):
            task_id = await raw_create("jobs", None, {"model": "m"})
        self.assertEqual(task_id, "T-9")
        self.assertEqual(calls, [("POST", "/api/v1/jobs/createTask", {"Authorization": "Bearer " + KEY})])


if __name__ == "__main__":
    unittest.main()
