"""Native video selection through the original retry ladder and HTTP clients.

Application imports are deferred until the parent's offline test runtime.
Only external HTTP is substituted. No provider, retry, sleep, planner, FSM,
verification or business-state method is replaced; no managed grant is made.
This target covers routing, not native observation or production readiness.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


ORIGIN = "http://127.0.0.1:18766"
SEEDANCE_SLUG = "seedance-1-5-pro"
SEEDANCE_MODEL = "bytedance/seedance-1.5-pro"
KLING_SLUG = "kling-2-6"
KLING_MODEL = "kling-2.6/text-to-video"
REFERENCE_URL = "https://native-routing.invalid/synthetic-input.png"
PROMPT = "A geometric object rotates slowly. One steady continuous shot in soft light."


class NativeVideoRouting(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixture = tempfile.TemporaryDirectory(prefix="native-video-routing-media-")
        cls.addClassCleanup(fixture.cleanup)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("parent diagnostic FFmpeg is required for the external MP4 fixture")
        path = Path(fixture.name) / "provider.mp4"
        subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=2:duration=1",
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
            check=True, capture_output=True, timeout=15)
        cls.video = path.read_bytes()
        if len(cls.video) < 1024:
            raise RuntimeError("external MP4 fixture must satisfy the original native download threshold")

    async def asyncSetUp(self):
        # Standard-library configuration is established before application imports.
        folder = tempfile.TemporaryDirectory(prefix="native-video-routing-case-")
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name) / "clip_001_routing.mp4"
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {
            "KIE_API_KEY": "synthetic-native-routing", "GPT_API_KEY": "synthetic-native-routing",
            "KIE_API_BASE_URL": ORIGIN, "GPT_BASE_URL": ORIGIN, "GPT_RELAY_TOKEN": "",
            "OUTSEE_API_KEY": "", "VIDEO_PROVIDER": "kie", "DATA_DIR": folder.name,
        }))
        import httpx
        from app.settings import settings

        for key, value in {
            "kie_api_key": "synthetic-native-routing", "gpt_api_key": "synthetic-native-routing",
            "kie_api_base_url": ORIGIN, "gpt_base_url": ORIGIN, "gpt_relay_token": "",
            "outsee_api_key": "", "video_provider": "kie", "data_dir": Path(folder.name),
        }.items():
            stack.enter_context(patch.object(settings, key, value))
        from app.bots import kie_kling
        from app.services import observation, outsee_retry
        from app.services.media_transport import current_job

        stack.enter_context(patch.object(kie_kling, "_WORKING_KEY", ""))
        stack.enter_context(patch.object(kie_kling, "_WORKING_SOURCE", ""))
        self.assertIsNone(observation._attempt.get(), "native routing requires no inherited managed attempt")
        self.assertIsNone(current_job.get(), "native routing requires no inherited managed job")
        self.retry = outsee_retry
        self.httpx = httpx
        self.requests = []
        self.jobs = {}
        self.failed_submissions = 0
        self.submit_rejection = None
        self.poll_timeout_once = False
        self.poll_timeout_delivered = False
        self.transport = httpx.MockTransport(self._external_request)
        self.addAsyncCleanup(self.transport.aclose)

        async def send(client, request, **kwargs):
            self.assertEqual((request.url.scheme, request.url.host, request.url.port),
                ("http", "127.0.0.1", 18766), "routing fixture refuses external HTTP")
            await request.aread()
            return await self.transport.handle_async_request(request)

        stack.enter_context(patch.object(httpx.AsyncClient, "send", send))

    def _external_request(self, request):
        """Provider transport fixture; original clients own parsing and retries."""
        httpx = self.httpx
        record = {"method": request.method, "path": request.url.path}
        self.requests.append(record)
        if request.method == "POST" and request.url.path == "/api/v1/jobs/createTask":
            self.assertTrue(request.headers.get("Authorization", "").startswith("Bearer synthetic"),
                "routing fixture requires synthetic credentials")
            body = json.loads(request.content)
            record["body"] = body
            if self.submit_rejection is not None:
                return httpx.Response(self.submit_rejection, request=request,
                    json={"code": self.submit_rejection, "msg": "synthetic credit refusal"})
            number = len(self.submits)
            job_id = f"native-routing-job-{number}"
            record["job_id"] = job_id
            self.jobs[job_id] = {"failed": number <= self.failed_submissions,
                "url": ORIGIN + f"/video/{job_id}.mp4"}
            return httpx.Response(200, request=request, json={"code": 200, "data": {"taskId": job_id}})
        if request.method == "GET" and request.url.path == "/api/v1/jobs/recordInfo":
            job_id = request.url.params.get("taskId")
            self.assertIn(job_id, self.jobs, "poll must bind an actual accepted submit")
            record["job_id"] = job_id
            if self.poll_timeout_once and not self.poll_timeout_delivered:
                self.poll_timeout_delivered = True
                record["outcome"] = "read_timeout"
                raise httpx.ReadTimeout("synthetic status timeout for a known job", request=request)
            job = self.jobs[job_id]
            state = "fail" if job["failed"] else "success"
            record["outcome"] = state
            data = {"taskId": job_id, "state": state}
            if job["failed"]:
                data.update(failCode="501", failMsg="synthetic generation failed")
            else:
                data["resultJson"] = json.dumps({"resultUrls": [job["url"]]})
            return httpx.Response(200, request=request, json={"code": 200, "data": data})
        if request.method == "GET" and request.url.path.startswith("/video/"):
            job_id = Path(request.url.path).stem
            self.assertIn(job_id, self.jobs)
            self.assertFalse(self.jobs[job_id]["failed"], "a failed provider job has no output")
            record["job_id"] = job_id
            return httpx.Response(200, request=request, content=self.video,
                headers={"Content-Type": "video/mp4"})
        self.fail("unexpected original native HTTP route")

    @property
    def submits(self):
        return [item for item in self.requests if item["method"] == "POST"]

    @property
    def models(self):
        return [item["body"]["model"] for item in self.submits]

    async def generate(self, model_slug=SEEDANCE_SLUG, **kwargs):
        from app.bots.outsee import OutseeImageError
        from app.services.video_error_policy import VideoLadderExhaustedError

        options = {"model_slug": model_slug, "resolution": "720p", "aspect_ratio": "9:16",
            "duration": 5, "timeout": 12, "generate_audio": False}
        options.update(kwargs)
        try:
            result = await asyncio.wait_for(self.retry.generate_video_with_retries(None, None,
                prompt=PROMPT, out_path=self.output, gpt_rewrite=False, **options), timeout=45)
            return result, None
        except (OutseeImageError, VideoLadderExhaustedError) as error:
            return None, error
        except TimeoutError:
            self.fail("original native routing exceeded the bounded 45-second test deadline")

    def assert_downloaded(self, result, error):
        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result.file_path, self.output)
        self.assertEqual(self.output.read_bytes(), self.video)
        # The original KIE client uses a locally generated ``gen_id``; taskId
        # remains provider transport state.  The returned URL must still be one
        # of the accepted provider-job URLs.
        self.assertIn(result.raw_url, {job["url"] for job in self.jobs.values()})
        self.assertEqual(len([r for r in self.requests if r["path"].startswith("/video/")]), 1)

    async def test_registered_seedance_is_the_first_primary_request(self):
        from app.services.kie_catalog import get_model
        from app.services.media_route import video_provider_for

        self.assertEqual(video_provider_for(SEEDANCE_SLUG), "kie")
        self.assertEqual(get_model(SEEDANCE_SLUG)["model"], SEEDANCE_MODEL)
        result, error = await self.generate()
        self.assertEqual(self.models, [SEEDANCE_MODEL], "selected primary must precede any fallback")
        self.assert_downloaded(result, error)

    async def test_registered_seedance_studio_id_preserves_the_same_primary(self):
        result, error = await self.generate("seedance_1_5_pro")
        self.assertEqual(self.models, [SEEDANCE_MODEL])
        self.assert_downloaded(result, error)

    async def test_seedance_primary_preserves_declared_media_parameters(self):
        result, error = await self.generate(resolution="1080p", aspect_ratio="4:3", duration=7)
        self.assertEqual(self.models, [SEEDANCE_MODEL])
        inputs = self.submits[0]["body"]["input"]
        self.assertEqual((inputs["resolution"], inputs["aspect_ratio"], inputs["duration"]),
            ("1080p", "4:3", 7))
        self.assertIs(inputs["generate_audio"], False)
        self.assertIn(PROMPT, inputs["prompt"])
        self.assert_downloaded(result, error)

    async def test_seedance_reference_remains_bound_to_the_selected_primary(self):
        # The original public-URL path passes this provider input through without
        # fetching it. Any real HTTP to this .invalid host is rejected above.
        result, error = await self.generate(start_frame=REFERENCE_URL)
        self.assertEqual(self.models, [SEEDANCE_MODEL])
        self.assertEqual(self.submits[0]["body"]["input"]["image_urls"], [REFERENCE_URL])
        self.assert_downloaded(result, error)

    async def test_registered_kling_keeps_its_existing_native_request_contract(self):
        result, error = await self.generate(KLING_SLUG)
        self.assertEqual(self.models, [KLING_MODEL])
        inputs = self.submits[0]["body"]["input"]
        self.assertEqual(set(inputs), {"prompt", "sound", "duration", "aspect_ratio"})
        self.assertEqual((inputs["duration"], inputs["aspect_ratio"]), ("5", "9:16"))
        self.assertIs(inputs["sound"], False)
        self.assert_downloaded(result, error)

    async def test_known_kling_job_status_timeout_polls_same_job_without_resubmit(self):
        self.poll_timeout_once = True
        result, error = await self.generate(KLING_SLUG)
        self.assertEqual(self.models, [KLING_MODEL])
        polls = [r for r in self.requests if r["path"] == "/api/v1/jobs/recordInfo"]
        self.assertEqual([r["outcome"] for r in polls], ["read_timeout", "success"])
        self.assertEqual({r["job_id"] for r in polls}, {self.submits[0]["job_id"]})
        self.assert_downloaded(result, error)

    async def test_seedance_credit_refusal_stops_without_model_fallback(self):
        self.submit_rejection = 402
        result, error = await self.generate()
        self.assertEqual(self.models, [SEEDANCE_MODEL])
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error.context["provider_code"], 402)
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.output.exists())

    async def test_seedance_terminal_failures_use_primary_allowance_before_kling(self):
        # The declared ladder allows four primary burns, then a distinct fallback.
        # Provider failures are actual parsed job responses, not injected errors.
        self.failed_submissions = 4
        result, error = await self.generate()
        self.assertEqual(self.models, [SEEDANCE_MODEL] * 4 + [KLING_MODEL])
        polls = [r for r in self.requests if r["path"] == "/api/v1/jobs/recordInfo"]
        self.assertEqual([r["outcome"] for r in polls], ["fail"] * 4 + ["success"])
        self.assertEqual([r["job_id"] for r in polls], [r["job_id"] for r in self.submits])
        self.assert_downloaded(result, error)

    async def test_registered_kling_retains_three_attempt_exhaustion_bound(self):
        from app.services.video_error_policy import VideoLadderExhaustedError

        self.failed_submissions = 3
        result, error = await self.generate(KLING_SLUG)
        self.assertEqual(self.models, [KLING_MODEL] * 3)
        self.assertIsNone(result)
        self.assertIsInstance(error, VideoLadderExhaustedError)
        self.assertIs(error.context["video_ladder_exhausted"], True)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
