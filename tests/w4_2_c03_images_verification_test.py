from __future__ import annotations

import os
import random
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, Project, ProjectStatus
from app.orchestrator import node_spec
from app.services import media_verification
from app.services.diagnosis import derive_incidents
from app.settings import settings

PID = 9
PROMPT = "Крупный план: старая библиотека, тёплый свет, пыль в воздухе, детальная текстура бумаги и дерева"
FFPROBE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def noise_png(path: Path, size=(640, 640)) -> bytes:
    rnd = random.Random(7)
    img = Image.frombytes("RGB", size, bytes(rnd.getrandbits(8) for _ in range(size[0] * size[1] * 3)))
    img.save(path, "PNG")
    return path.read_bytes()


class Env(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.tmp.name}/t.db")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        for patcher in (patch("app.db.SessionLocal", self.sessions),
                        patch.object(settings, "data_dir", Path(self.tmp.name) / "data")):
            patcher.start()
            self.addCleanup(patcher.stop)
        async with self.sessions() as s:
            s.add(Project(id=PID, topic="t", slug="w42-images", status=ProjectStatus.images_ready, meta={},
                          item_descriptions=[]))
            await s.commit()
        self.project = await self.get_project()
        self.root = self.project.data_dir
        (self.root / "scenes").mkdir(parents=True, exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def get_project(self):
        async with self.sessions() as s:
            return await s.get(Project, PID)

    async def add(self, *rows):
        async with self.sessions() as s:
            s.add_all(rows)
            await s.commit()
            for r in rows:
                await s.refresh(r)
        return rows

    async def frames(self, *prompts):
        rows = [Frame(project_id=PID, number=i, uuid=f"{i:032x}", sort_key=i, voiceover_text=f"текст {i}",
                      image_prompt=p, attrs={}) for i, p in enumerate(prompts, 1)]
        return await self.add(*rows)

    async def image(self, frame, *, data=None, bound=True, name=None):
        path = self.root / "scenes" / (name or f"frame_{frame.number:03d}_ab12cd34.png")
        if data is None:
            noise_png(path)
        else:
            path.write_bytes(data)
        if bound:
            await self.add(Artifact(project_id=PID, frame_id=frame.id, kind=ArtifactKind.scene_image,
                                    uuid=f"i{frame.number}", path=str(path), meta={"shot": 1}))
        return path

    async def proof(self, node_type="images"):
        async with self.sessions() as reader:
            return await media_verification.build_proof(reader, await self.get_project(), node_type)


class ImagesVerification(Env):
    async def test_images_are_a_media_node_with_the_same_named_hook(self):
        import app.services.decision_v1
        import app.services.node_capabilities
        self.assertIn("images", media_verification.MEDIA_NODE_TYPES)
        self.assertIn("images", media_verification._COLLECTORS)
        spec = node_spec.verifier_spec_for("images")
        self.assertEqual(spec.name, "media_result")
        self.assertFalse(spec.managed_only)

    async def test_every_frame_with_a_prompt_has_a_good_bound_image(self):
        f1, f2, f3 = await self.frames(PROMPT, PROMPT, "")
        await self.image(f1)
        await self.image(f2)
        proof = await self.proof()
        self.assertEqual((proof["status"], proof["items_total"], proof["items_passed"]), ("passed", 2, 2))
        self.assertEqual(proof["counts"]["targets"], 2)
        self.assertTrue(proof["counts"]["images_equal_targets"])
        first = proof["items"][0]
        self.assertEqual((first["key"], first["format"], first["width"], first["height"]),
                         ("frame_001", "PNG", 640, 640))
        self.assertTrue(first["artifact_bound"])
        self.assertEqual(first["provenance"]["reason"], "no_ledger_row")
        self.assertFalse(proof["semantic_quality_verified"])
        self.assertIn("качество не оценивалось", proof["summary"])

    async def test_a_corrupt_png_is_a_failed_item_and_a_media_failure_incident(self):
        (f1,) = await self.frames(PROMPT)
        good = noise_png(self.root / "scenes" / "tmp.png")
        await self.image(f1, data=good[:20] + os.urandom(len(good) - 20))
        proof = await self.proof()
        self.assertEqual(proof["status"], "failed")
        self.assertEqual(proof["items"][0]["key"], "frame_001")
        self.assertIn("frame_001", proof["summary"])
        scope = SimpleNamespace(execution_id="x", project_id=PID, node_keys=["images"], observer_state="active",
                                gaps=[])
        event = dict(id=1, event_id="e1", execution_id="x", project_id=PID, node_key="images", attempt_id="a",
                     request_id=None, kind="node_result_verification", payload=proof, db_scope="project",
                     created_at="2026-10-07 12:00:00")
        (item,) = derive_incidents(scope, [event], {})
        self.assertEqual((item["type"], item["blocking"], item["severity"]), ("media_failure", True, "error"))

    async def test_an_empty_file_fails(self):
        (f1,) = await self.frames(PROMPT)
        await self.image(f1, data=b"")
        proof = await self.proof()
        self.assertEqual((proof["status"], proof["items"][0]["reason"]), ("failed", "missing_empty_or_oversized_file"))

    async def test_a_text_file_with_a_png_name_fails(self):
        (f1,) = await self.frames(PROMPT)
        await self.image(f1, data=b"not an image" * 30000)
        proof = await self.proof()
        self.assertEqual(proof["status"], "failed")

    async def test_a_frame_without_any_image_fails_as_missing_image(self):
        f1, f2 = await self.frames(PROMPT, PROMPT)
        await self.image(f1)
        proof = await self.proof()
        self.assertEqual(proof["status"], "failed")
        self.assertEqual((proof["items_total"], proof["items_passed"]), (2, 1))
        self.assertFalse(proof["counts"]["images_equal_targets"])
        missing = [i for i in proof["items"] if i["status"] == "failed"]
        self.assertEqual([(i["key"], i["reason"]) for i in missing], [("frame_002", "missing_image")])

    async def test_a_small_scene_image_below_the_native_threshold_fails(self):
        (f1,) = await self.frames(PROMPT)
        path = self.root / "scenes" / "frame_001_ab12cd34.png"
        Image.new("RGB", (640, 640), "green").save(path)
        await self.add(Artifact(project_id=PID, frame_id=f1.id, kind=ArtifactKind.scene_image, uuid="i1",
                                path=str(path), meta={"shot": 1}))
        proof = await self.proof()
        self.assertEqual((proof["status"], proof["items"][0]["reason"]), ("failed", "image_too_small_for_scene"))

    async def test_tiny_dimensions_fail(self):
        (f1,) = await self.frames(PROMPT)
        path = self.root / "scenes" / "frame_001_ab12cd34.png"
        Image.new("RGB", (64, 96), "green").save(path)
        await self.add(Artifact(project_id=PID, frame_id=f1.id, kind=ArtifactKind.scene_image, uuid="i1",
                                path=str(path), meta={"shot": 1}))
        proof = await self.proof()
        self.assertEqual(proof["items"][0]["reason"], "image_dimensions_too_small")

    async def test_a_file_on_disk_without_an_artifact_row_is_unknown_not_a_pass(self):
        (f1,) = await self.frames(PROMPT)
        await self.image(f1, bound=False)
        proof = await self.proof()
        self.assertEqual(proof["status"], "unknown")
        self.assertEqual(proof["items"][0]["reason"], "no_artifact_row_for_frame")
        self.assertFalse(proof["items"][0]["artifact_bound"])

    async def test_an_artifact_of_another_frame_does_not_count_for_this_frame(self):
        f1, f2 = await self.frames(PROMPT, PROMPT)
        path = await self.image(f1)
        async with self.sessions() as s:
            art = await s.get(Artifact, 1)
            art.frame_id = f2.id
            await s.commit()
        proof = await self.proof()
        by_key = {i["key"]: i for i in proof["items"]}
        self.assertEqual(by_key["frame_002"]["reason"], "file_belongs_to_another_frame")
        self.assertFalse(by_key["frame_001"]["artifact_bound"])
        self.assertEqual(proof["status"], "failed")
        self.assertTrue(path.is_file())

    async def test_the_second_shot_file_is_not_the_first_shot_image(self):
        (f1,) = await self.frames(PROMPT)
        await self.image(f1, name="frame_001_s2_ab12cd34.png")
        proof = await self.proof()
        self.assertEqual((proof["status"], proof["items"][0]["reason"]), ("failed", "missing_image"))

    async def test_a_path_outside_the_project_is_failed(self):
        (f1,) = await self.frames(PROMPT)
        outside = Path(self.tmp.name) / "elsewhere.png"
        noise_png(outside)
        await self.add(Artifact(project_id=PID, frame_id=f1.id, kind=ArtifactKind.scene_image, uuid="i1",
                                path=str(outside), meta={"shot": 1}))
        proof = await self.proof()
        self.assertEqual((proof["status"], proof["items"][0]["reason"]), ("failed", "path_outside_project"))

    async def test_no_frame_with_a_prompt_is_unknown_not_passed(self):
        await self.frames("", "")
        proof = await self.proof()
        self.assertEqual((proof["status"], proof["reason"], proof["items_total"]), ("unknown", "no_targets", 0))


class ImagesHook(Env):
    def ctx(self):
        return SimpleNamespace(scope=SimpleNamespace(project_id=PID), media_result_observed=False)

    async def test_the_hook_journals_node_output_and_verification_and_never_raises(self):
        (f1,) = await self.frames(PROMPT)
        await self.image(f1)
        facts = []

        async def record(kind, payload=None, **kw):
            facts.append((kind, payload))

        with patch("app.services.observation._attempt") as attempt, \
                patch("app.services.observation.record_fact", record):
            attempt.get.return_value = self.ctx()
            out = await media_verification.observe_media_result(SimpleNamespace(bind=self.engine), None, "images")
        self.assertEqual([k for k, _ in facts], ["node_output", "node_result_verification"])
        self.assertEqual((out["status"], facts[1][1]["contract"]), ("passed", "native_media"))


@unittest.skipUnless(FFPROBE, "ffmpeg/ffprobe are required for the audio fixture")
class AudioContentCheckIsWired(Env):

    def wav(self, name, *, silent):
        path = self.root / "audio" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            if silent:
                w.writeframes(b"\x00\x00" * 16000)
            else:
                import math
                import struct
                w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 16000)))
                                       for i in range(16000)))
        return path

    async def voice(self, silent):
        await self.frames(PROMPT)
        path = self.wav("voice.wav", silent=silent)
        await self.add(Artifact(project_id=PID, kind=ArtifactKind.audio, uuid="a1", path=str(path),
                                meta={"clip_count": 1, "source": "elevenlabs"}))

    async def test_a_silent_voice_file_fails_with_the_silent_code(self):
        await self.voice(silent=True)
        proof = await self.proof("audio")
        item = proof["items"][0]
        self.assertEqual((proof["status"], item["reason"]), ("failed", "silent"))
        self.assertTrue(item["audio_check"]["silent"])

    async def test_a_voice_file_with_a_signal_passes(self):
        await self.voice(silent=False)
        proof = await self.proof("audio")
        self.assertEqual(proof["status"], "passed")
        self.assertTrue(proof["items"][0]["audio_check"]["ok"])


if __name__ == "__main__":
    unittest.main()
