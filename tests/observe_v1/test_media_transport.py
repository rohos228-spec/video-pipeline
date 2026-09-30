"""Original Kie HTTP functions; only the remote HTTP server is a fixture."""
import asyncio
import io
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from PIL import Image
from sqlalchemy import select

from test_media_jobs import MediaJobs, policy
from app.services import observation
from app.services.actions_v1 import reload_permissions


class MediaHTTP(MediaJobs):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.posts = 0
        self.status_gets = 0
        self.downloads = 0
        self.case = "success"
        self.release = False
        stream = io.BytesIO()
        Image.new("RGB", (64, 96), "blue").save(stream, "PNG")
        self.image = stream.getvalue()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def respond(self, body, code=200, content_type="application/json"):
                content = json.dumps(body).encode() if isinstance(body, dict) else body
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                owner.posts += 1
                if owner.case == "uncertain":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.respond({"code": 200, "data": {"taskId": "fixture-job"}})
            def do_GET(self):
                if self.path.startswith("/api/v1/jobs/recordInfo"):
                    owner.status_gets += 1
                    state = "generating" if owner.case == "restart" and not owner.release else "success"
                    if owner.case == "failed":
                        state = "fail"
                    self.respond({"code": 200, "data": {"state": state,
                        "resultJson": json.dumps({"resultUrls": [owner.origin + "/artifact.png"]})}})
                elif self.path == "/artifact.png":
                    owner.downloads += 1
                    if owner.case == "redirect":
                        self.send_response(302)
                        self.send_header("Location", "http://169.254.169.254/secret")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                    else:
                        self.respond(b"not-valid-image" * 8 if owner.case == "corrupt" else owner.image,
                                     content_type="image/png")
                else:
                    self.respond({}, 404)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.cfg.write_text(json.dumps(policy(self.origin)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        from app.settings import settings
        self.settings_patch = patch.object(settings, "kie_api_base_url", self.origin)
        self.settings_patch.start()
        # Safety at actual HTTP boundary even before implementation (RED).
        import httpx
        original = httpx.AsyncClient.send
        async def confined(client, request, **kwargs):
            if request.url.host != "127.0.0.1" or request.url.port != self.server.server_port:
                raise AssertionError("test forbids any external HTTP")
            return await original(client, request, **kwargs)
        self.transport = patch.object(httpx.AsyncClient, "send", confined)
        self.transport.start()

    async def asyncTearDown(self):
        self.transport.stop()
        self.settings_patch.stop()
        self.server.shutdown()
        self.server.server_close()
        await super().asyncTearDown()

    async def generate(self):
        from app.bots.kie_http import run_generation
        from app.services.kie_catalog import get_model
        return await asyncio.wait_for(run_generation(get_model("z-image"),
            {"model": "z-image", "input": {"prompt": "synthetic"}}, self.output), 5)

    async def test_http_success_duplicate_uses_verified_file_and_one_submit(self):
        from app.services.media_jobs import MediaJob, MediaRequest
        result = await self.generate()
        self.assertEqual(result.gen_id, "fixture-job")
        second = await self.generate()
        self.assertEqual(second.file_path, result.file_path)
        self.assertEqual((self.posts, self.status_gets, self.downloads), (1, 1, 1))
        async with self.sessions() as s:
            job = (await s.scalars(select(MediaJob))).one()
            self.assertEqual(job.phase, "verified")
            self.assertEqual(job.verification["width"], 64)
            requests = (await s.scalars(select(MediaRequest))).all()
            self.assertEqual([r.category for r in requests], ["generation_submit", "status", "download"])
            self.assertTrue(all(r.status == "received" for r in requests))
        await self.observer.consume("52b01b6a-8625-47f1-a605-8596dc45dad2", "master", self.sessions)
        from app.services.observation import ObservationEvent
        async with self.sessions() as s:
            events = (await s.scalars(select(ObservationEvent))).all()
            self.assertTrue(any(e.kind == "artifact_verified" for e in events))

    async def test_http_restart_after_known_job_polls_without_resubmit(self):
        self.case = "restart"
        cfg = policy(self.origin)
        cfg["media"]["poll_interval_s"] = 0.5
        self.cfg.write_text(json.dumps(cfg))
        task = asyncio.create_task(self.generate())
        try:
            from app.services.observation import ObservationOutbox
            durable_running = False
            for _ in range(100):
                async with self.sessions() as s:
                    rows = (await s.scalars(select(ObservationOutbox).where(
                        ObservationOutbox.kind == "media_phase"))).all()
                    durable_running = any(r.payload.get("phase") == "running" for r in rows)
                if durable_running:
                    break
                await asyncio.sleep(0.02)
            self.assertTrue(durable_running, "cancel local wait only after running was durably observed")
            self.assertGreater(self.status_gets, 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.observer.reload()
        self.release = True
        observation._attempt.get().attempt_id = "attempt-after-restart"
        result = await self.generate()
        self.assertTrue(result.file_path.is_file())
        self.assertEqual(self.posts, 1)
        self.assertGreaterEqual(self.status_gets, 2)

    async def test_http_uncertain_no_second_post(self):
        self.case = "uncertain"
        with self.assertRaises(Exception):
            await self.generate()
        await reload_permissions(self.sessions)
        with self.assertRaises(RuntimeError):
            await self.generate()
        self.assertEqual(self.posts, 1)

    async def test_http_corrupt_blocks_result_and_repeat(self):
        self.case = "corrupt"
        with self.assertRaises(RuntimeError):
            await self.generate()
        with self.assertRaises(RuntimeError):
            await self.generate()
        self.assertEqual(self.posts, 1)

    async def test_http_failed_job_is_not_retried(self):
        self.case = "failed"
        with self.assertRaises(RuntimeError):
            await self.generate()
        with self.assertRaises(RuntimeError):
            await self.generate()
        self.assertEqual(self.posts, 1)
        self.assertEqual(self.downloads, 0)

    async def test_http_download_redirect_never_followed(self):
        self.case = "redirect"
        with self.assertRaises(RuntimeError):
            await self.generate()
        self.assertEqual(self.downloads, 1)

    async def test_http_existing_image_wrapper_reaches_managed_adapter(self):
        from app.services.outsee_retry import generate_image_with_retries
        result = await generate_image_with_retries(None, None, prompt="synthetic",
            out_path=self.output, project_id=7, model_slug="z-image", gpt_rewrite=False)
        self.assertTrue(result.file_path.is_file())
        self.assertEqual(self.posts, 1)

    async def test_http_legacy_direct_service_cannot_bypass_project_permission(self):
        from app.bots.kie_http import generate_kie_image
        token = observation._attempt.set(None)
        try:
            with self.assertRaisesRegex(RuntimeError, "context"):
                await generate_kie_image("synthetic", self.output, project_id=7, model_slug="z-image")
        finally:
            observation._attempt.reset(token)
        self.assertEqual(self.posts, 0)

    async def test_http_verified_artifact_applies_once_in_business_transaction(self):
        from app.models import Artifact, ArtifactKind
        from app.services.media_jobs import persist_artifact
        result = await self.generate()
        def artifact(uid):
            return Artifact(project_id=7, kind=ArtifactKind.scene_image,
                uuid=uid, path=str(result.file_path), meta={})
        async with self.sessions() as s:
            first = await persist_artifact(s, artifact("first-artifact"))
            await s.commit()
        async with self.sessions() as s:
            second = await persist_artifact(s, artifact("duplicate-artifact"))
            self.assertEqual(second.uuid, first.uuid)
            await s.commit()
            rows = (await s.scalars(select(Artifact))).all()
            self.assertEqual(len(rows), 1)
            proof = rows[0].meta["orchestrator"]
            self.assertEqual(proof["execution_id"], self.scope.execution_id)
            self.assertEqual(proof["node_key"], "image")
            self.assertTrue(proof["attempt_id"])
            self.assertEqual(proof["verification"]["status"], "passed")
        self.assertEqual(self.posts, 1)

    async def test_http_artifact_apply_rollback_and_tampering(self):
        from app.models import Artifact, ArtifactKind
        from app.services.media_jobs import persist_artifact
        result = await self.generate()
        async with self.sessions() as s:
            await persist_artifact(s, Artifact(project_id=7, kind=ArtifactKind.scene_image,
                uuid="rolled-back", path=str(result.file_path), meta={}))
            await s.rollback()
        async with self.sessions() as s:
            self.assertIsNone(await s.scalar(select(Artifact)))
            result.file_path.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "verification"):
                await persist_artifact(s, Artifact(project_id=7, kind=ArtifactKind.scene_image,
                    uuid="bad-artifact", path=str(result.file_path), meta={}))
            self.assertIsNone(await s.scalar(select(Artifact)))

    async def test_http_preview_rechecks_hash_and_project_containment(self):
        from app.models import Artifact, ArtifactKind
        from app.services.media_jobs import persist_artifact
        from app.web.routers.artifacts import download_artifact
        from fastapi import HTTPException
        result = await self.generate()
        async with self.sessions() as s:
            art = await persist_artifact(s, Artifact(project_id=7, kind=ArtifactKind.scene_image,
                uuid="preview", path=str(result.file_path), meta={}))
            await s.commit()
            response = await download_artifact(art.uuid, session=s)
            self.assertEqual(str(response.path), str(result.file_path))
            # Still a decodable PNG, but not the previously verified content.
            Image.new("RGB", (64, 96), "red").save(result.file_path, "PNG")
            with self.assertRaises(HTTPException) as changed:
                await download_artifact(art.uuid, session=s)
            self.assertEqual(changed.exception.status_code, 409)
            outside = self.root / "outside.png"
            outside.write_bytes(self.image)
            art.path = str(outside)
            await s.commit()
            with self.assertRaises(HTTPException) as escaped:
                await download_artifact(art.uuid, session=s)
            self.assertEqual(escaped.exception.status_code, 403)

    async def test_http_disk_recovery_requires_and_preserves_job_provenance(self):
        import random
        from app.models import Artifact, Frame
        from app.services.artifact_recovery import recover_scene_images_from_disk
        self.output = self.project.data_dir / "scenes" / "frame_001_12345678.png"
        stream = io.BytesIO()
        Image.frombytes("RGB", (256, 384), random.Random(43).randbytes(256*384*3)).save(stream, "PNG")
        self.image = stream.getvalue()
        result = await self.generate()
        async with self.sessions() as s:
            s.add(Frame(project_id=7, number=1, voiceover_text="synthetic",
                        image_prompt="synthetic", uuid="synthetic-frame-1"))
            await s.commit()
            await recover_scene_images_from_disk(s, self.project)
            await s.commit()
            art = (await s.scalars(select(Artifact))).one()
            self.assertEqual(art.meta["orchestrator"]["execution_id"], self.scope.execution_id)
            self.assertEqual(art.meta["orchestrator"]["verification"]["status"], "passed")
            await recover_scene_images_from_disk(s, self.project)
            await s.commit()
            self.assertEqual(len((await s.scalars(select(Artifact))).all()), 1)
            self.assertEqual(self.posts, 1, "disk recovery must not submit another generation")
            # A plausible local file alone does not authorize application.
            other = result.file_path.with_name("frame_002_abcdef12.png")
            other.write_bytes(self.image)
            s.add(Frame(project_id=7, number=2, voiceover_text="synthetic",
                        image_prompt="synthetic", uuid="synthetic-frame-2"))
            await s.commit()
            with self.assertRaisesRegex(RuntimeError, "verified job"):
                await recover_scene_images_from_disk(s, self.project)
            await s.rollback()
            self.assertEqual(len((await s.scalars(select(Artifact))).all()), 1)

if __name__ == "__main__":
    # Inherited state tests are run separately, once.
    suite = unittest.TestSuite(MediaHTTP(name) for name in unittest.defaultTestLoader.getTestCaseNames(MediaHTTP)
                               if name.startswith("test_http_"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
