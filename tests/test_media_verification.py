"""Result verification of the paid media nodes (observation level) and its typed diagnosis.

Real: sqlite databases, real files (ffmpeg / PIL fixtures), verify_artifact (ffprobe), the provider
task ledger, the verifier hook and the diagnosis projection. No provider, no network, no paid call.
"""
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Artifact, ArtifactKind, Base, Frame, HITLKind, HITLRequest, Project, ProjectStatus)
from app.orchestrator import node_spec
from app.services import media_verification, spend_guard
from app.services.diagnosis import TITLES, derive_incidents
from app.settings import settings

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
PID = 5


def _ffmpeg(args, out: Path) -> bytes:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(out)], check=True, timeout=60)
    return out.read_bytes()


class Env(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = Path(tempfile.mkdtemp(prefix="media-verify-fixtures-"))
        cls.addClassCleanup(shutil.rmtree, cls.fixtures, True)
        if FFMPEG:
            cls.mp4 = _ffmpeg(["-f", "lavfi", "-i", "color=blue:s=64x96:r=10:d=1", "-f", "lavfi", "-i",
                               "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                               "-c:a", "aac", "-shortest"], cls.fixtures / "v.mp4")
            cls.silent = _ffmpeg(["-f", "lavfi", "-i", "color=red:s=64x96:r=10:d=1", "-c:v", "libx264",
                                  "-pix_fmt", "yuv420p"], cls.fixtures / "s.mp4")
            cls.mp3 = _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=330:duration=1"], cls.fixtures / "a.mp3")
        Image.new("RGB", (64, 96), "green").save(cls.fixtures / "i.png")
        cls.png = (cls.fixtures / "i.png").read_bytes()

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
            s.add(Project(id=PID, topic="t", slug="verify", status=ProjectStatus.videos_ready, meta={},
                          item_descriptions=[]))
            await s.commit()
        self.project = await self.get_project()
        self.root = self.project.data_dir
        self.root.mkdir(parents=True, exist_ok=True)

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

    def write(self, rel: str, data: bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    async def ledger(self, key, task_id, status, *, provider="kie", event="poll", api="jobs"):
        async with self.sessions() as s:
            await spend_guard.append_task_event(s, project_id=PID, key=key, task_id=task_id, provider=provider,
                                                api=api, model="m", event=event, status=status)
            await s.commit()

    async def proof(self, node_type):
        async with self.sessions() as reader:
            return await media_verification.build_proof(reader, await self.get_project(), node_type)

    async def frames(self, *prompts):
        rows = [Frame(project_id=PID, number=i, uuid=f"{i:032x}", sort_key=i, voiceover_text=f"текст {i}",
                      animation_prompt=p, attrs={}) for i, p in enumerate(prompts, 1)]
        return await self.add(*rows)


@unittest.skipUnless(FFMPEG, "ffmpeg/ffprobe are required for the media fixtures")
class VideosVerification(Env):
    async def clip(self, frame, data=None, name=None):
        path = self.write(name or f"videos/clip_{frame.number:03d}_ab12cd34.mp4", self.mp4 if data is None else data)
        await self.add(Artifact(project_id=PID, frame_id=frame.id, kind=ArtifactKind.scene_video,
                                uuid=f"u{frame.number}{len(path.name)}", path=str(path), meta={}))
        return path

    async def test_good_clips_pass_and_the_provider_trail_is_recorded(self):
        f1, f2, f3 = await self.frames("anim 1", "anim 2", "")  # frame 3 has no animation prompt: not a target
        await self.clip(f1)
        await self.clip(f2)
        await self.ledger("clip_001", "T-1", "downloaded")
        proof = await self.proof("videos")
        self.assertEqual((proof["status"], proof["items_total"], proof["items_passed"]), ("passed", 2, 2))
        first, second = proof["items"]
        self.assertEqual(first["provenance"], {"provider": "kie", "durable_task_id": True, "task_id": "T-1",
                                               "task_status": "downloaded"})
        self.assertFalse(second["provenance"]["durable_task_id"])  # no ledger row: untracked / Outsee
        self.assertEqual((first["streams"], first["width"], first["height"]), (["audio", "video"], 64, 96))
        self.assertGreater(first["bytes"], 0)
        self.assertEqual(first["sha256"], hashlib.sha256(self.mp4).hexdigest())
        self.assertFalse(proof["semantic_quality_verified"] or proof["business_commit_claim"])
        self.assertEqual(proof["counts"]["targets"], 2)

    async def test_missing_empty_garbage_and_foreign_files_fail_with_typed_reasons(self):
        f1, f2, f3, f4 = await self.frames("a", "b", "c", "d")
        await self.clip(f1, b"")  # empty
        await self.clip(f2, b"not a video at all " * 100)  # garbage
        foreign = Path(self.tmp.name) / "elsewhere.mp4"
        foreign.write_bytes(self.mp4)
        await self.add(Artifact(project_id=PID, frame_id=f3.id, kind=ArtifactKind.scene_video, uuid="foreign",
                                path=str(foreign), meta={}))
        # frame 4 has no clip at all
        proof = await self.proof("videos")
        self.assertEqual(proof["status"], "failed")
        reasons = {i["key"]: i.get("reason") for i in proof["items"]}
        self.assertEqual(reasons["clip_001"], "missing_empty_or_oversized_file")
        self.assertEqual(reasons["clip_002"], "ValueError")  # ffprobe rejected the bytes
        self.assertEqual(reasons["clip_003"], "path_outside_project")
        self.assertEqual(reasons["clip_004"], "missing_clip")
        self.assertIn("не прошли проверку 4 из 4", proof["summary"])

    async def test_a_clip_without_a_video_stream_is_not_a_clip(self):
        (f1,) = await self.frames("a")
        await self.clip(f1, self.mp3, name="videos/clip_001_ab12cd34.mp4")
        self.assertEqual((await self.proof("videos"))["status"], "failed")

    async def test_unavailable_probe_is_unknown_never_passed(self):
        (f1,) = await self.frames("a")
        await self.clip(f1)
        with patch("app.services.artifact_verifier.shutil.which", return_value=None):
            proof = await self.proof("videos")
        self.assertEqual((proof["status"], proof["reason"]), ("unknown", "ffprobe_unavailable"))

    async def test_no_target_frames_is_unknown_not_a_pass(self):
        await self.frames("", "")
        proof = await self.proof("videos")
        self.assertEqual((proof["status"], proof["reason"], proof["items_total"]), ("unknown", "no_targets", 0))

    async def test_a_second_shot_clip_does_not_replace_the_first(self):
        (f1,) = await self.frames("a")
        await self.clip(f1, name="videos/clip_001_s2_ab12cd34.mp4")  # shot 2 only
        proof = await self.proof("videos")
        self.assertEqual(proof["items"][0]["reason"], "missing_clip")

    async def test_unknown_submit_outcomes_are_listed_per_provider_and_do_not_fake_a_failure(self):
        f1, f2 = await self.frames("a", "b")
        await self.clip(f1)
        await self.clip(f2)
        await self.ledger("clip_001", "intent:k1", "submit_unknown", event="intent")
        await self.ledger("clip_002", "intent:o1", "submit_unknown", provider="outsee", event="intent", api="outsee")
        proof = await self.proof("videos")
        self.assertEqual(proof["status"], "passed")  # files are fine; the unknown payment is reported separately
        self.assertEqual({(u["key"], u["provider"]) for u in proof["outcome_unknown"]},
                         {("clip_001", "kie"), ("clip_002", "outsee")})
        self.assertIn("исход прошлой платной отправки неизвестен", proof["summary"])

    async def test_a_resolved_intent_is_not_reported(self):
        (f1,) = await self.frames("a")
        await self.clip(f1)
        await self.ledger("clip_001", "intent:k1", "submit_unknown", event="intent")
        await self.ledger("clip_001", "intent:k1", "bound", event="intent_done")
        self.assertEqual((await self.proof("videos"))["outcome_unknown"], [])

    async def test_unreadable_ledger_is_a_fact_and_provenance_says_so(self):
        (f1,) = await self.frames("a")
        await self.clip(f1)
        with patch("app.db.SessionLocal", side_effect=RuntimeError("db down")):
            proof = await self.proof("videos")
        self.assertEqual((proof["status"], proof["ledger"]), ("passed", "unavailable"))
        self.assertEqual(proof["items"][0]["provenance"]["reason"], "ledger_unavailable")


@unittest.skipUnless(FFMPEG, "ffmpeg/ffprobe are required for the media fixtures")
class OtherMediaNodes(Env):
    async def test_skipped_hero_step_is_not_a_failure(self):
        """F10-D3: в листе нет описания героя - шаг законно пропущен, независимая проверка не поднимает сбой."""
        async with self.sessions() as s:
            project = await s.get(Project, PID)
            project.meta = {"hero_skipped_empty": True}
            await s.commit()
        proof = await self.proof("hero")
        self.assertEqual((proof["status"], proof["reason"]), ("passed", "hero_skipped_empty"))

    async def test_no_hero_mode_and_zero_count_are_legal_skips(self):
        """N-13: шаг пропускается и по режиму «без героя», и по hero_count=0, даже без флага в meta."""
        for field, value in (("hero_mode", "no_hero"), ("hero_count", 0)):
            async with self.sessions() as s:
                project = await s.get(Project, PID)
                project.meta = {}
                project.hero_mode, project.hero_count = "auto", None
                setattr(project, field, value)
                await s.commit()
            proof = await self.proof("hero")
            self.assertEqual((proof["status"], proof["reason"]), ("passed", "hero_skipped_empty"), field)

    async def test_hero_images(self):
        self.assertEqual((await self.proof("hero"))["reason"], "no_hero_artifact")
        self.assertNotEqual((await self.proof("hero"))["status"], "passed")
        path = self.write("characters/hero_1_v1_ab12cd34.png", self.png)
        await self.add(Artifact(project_id=PID, kind=ArtifactKind.hero_reference, uuid="h1", path=str(path), meta={}))
        await self.ledger("hero_1_v1", "T-9", "downloaded")
        proof = await self.proof("hero")
        self.assertEqual(proof["status"], "passed")
        self.assertEqual(proof["items"][0]["provenance"]["task_id"], "T-9")
        path.write_bytes(b"broken")
        self.assertEqual((await self.proof("hero"))["status"], "failed")

    async def test_items_need_a_file_per_description(self):
        async with self.sessions() as s:
            pr = await s.get(Project, PID)
            pr.item_descriptions = ["кубок", "ключ"]
            await s.commit()
        path = self.write("items/predmet1_ab12cd34.png", self.png)
        await self.add(Artifact(project_id=PID, kind=ArtifactKind.item_reference, uuid="i1", path=str(path),
                                meta={"item_index": 1}))
        proof = await self.proof("items")
        self.assertEqual(proof["status"], "failed")
        self.assertEqual([(i["key"], i["status"]) for i in proof["items"]],
                         [("predmet1", "passed"), ("predmet2", "failed")])
        self.assertEqual(proof["items"][1]["reason"], "missing_item")

    async def test_items_with_no_descriptions_is_a_pass_with_nothing_to_do(self):
        proof = await self.proof("items")
        self.assertEqual((proof["status"], proof["reason"]), ("passed", "no_item_descriptions"))

    async def test_music(self):
        self.assertEqual((await self.proof("music"))["status"], "failed")
        path = self.write("music/music_ab12cd34.mp3", self.mp3)
        await self.add(Artifact(project_id=PID, kind=ArtifactKind.music, uuid="m1", path=str(path), meta={}))
        await self.ledger("music", "S-1", "downloaded", api="suno")
        proof = await self.proof("music")
        self.assertEqual((proof["status"], proof["items"][0]["provenance"]["task_id"]), ("passed", "S-1"))
        self.assertIn("audio", proof["items"][0]["streams"])

    async def test_audio_counts_and_the_uncertain_marker(self):
        await self.frames("a", "b")
        path = self.write("audio/voice_full.mp3", self.mp3)
        await self.add(Artifact(project_id=PID, kind=ArtifactKind.audio, uuid="a1", path=str(path),
                                meta={"source": "elevenlabs", "clip_count": 2}))
        proof = await self.proof("audio")
        self.assertEqual((proof["status"], proof["outcome_unknown"]), ("passed", []))
        self.write("audio/.tts_uncertain_0123456789abcdef", b"ReadTimeout")
        marked = await self.proof("audio")
        self.assertEqual([(u["provider"], u["key"]) for u in marked["outcome_unknown"]],
                         [("elevenlabs", ".tts_uncertain_0123456789abcdef")])
        async with self.sessions() as s:
            art = await s.get(Artifact, 1)
            art.meta = {"source": "elevenlabs", "clip_count": 5}
            await s.commit()
        self.assertEqual((await self.proof("audio"))["reason"], "clip_count_differs_from_spoken_frames")

    async def test_assemble_final_digest_and_the_approval_barrier_are_reported(self):
        path = self.write("final/verify.mp4", self.mp4)
        sha = hashlib.sha256(self.mp4).hexdigest()
        await self.add(Artifact(project_id=PID, kind=ArtifactKind.final_video, uuid="f1", path=str(path),
                                meta={"sha256": sha}),
                       HITLRequest(project_id=PID, kind=HITLKind.approve_final, payload={"sha256": sha}))
        proof = await self.proof("assemble")
        item = proof["items"][0]
        self.assertEqual(proof["status"], "passed")
        self.assertEqual((item["approval"]["requests_for_this_file"], item["approval"]["pending"],
                          item["approval"]["human_barrier"]), (1, 1, "approve_final"))
        async with self.sessions() as s:  # the recorded digest no longer matches the file
            art = await s.get(Artifact, 1)
            art.meta = {"sha256": "0" * 64}
            await s.commit()
        self.assertEqual((await self.proof("assemble"))["items"][0]["reason"], "digest_differs_from_recorded")
        path.write_bytes(self.silent)
        again = await self.proof("assemble")
        self.assertEqual(again["status"], "failed")  # no audio stream in an assembled video


class Hook(Env):
    """observe_media_result: journals the proof, never raises, never holds a run."""

    def ctx(self):
        return SimpleNamespace(scope=SimpleNamespace(project_id=PID), media_result_observed=False)

    async def run_hook(self, node_type, ctx=None, **patches):
        ctx = ctx or self.ctx()
        facts = []

        async def record(kind, payload=None, **kw):
            facts.append((kind, payload, kw))

        with patch("app.services.observation._attempt") as attempt, \
                patch("app.services.observation.record_fact", record):
            attempt.get.return_value = ctx
            out = await media_verification.observe_media_result(SimpleNamespace(bind=self.engine), None, node_type)
        return out, facts, ctx

    async def test_records_output_and_verification_once(self):
        out, facts, ctx = await self.run_hook("music")
        self.assertEqual([k for k, *_ in facts], ["node_output", "node_result_verification"])
        self.assertEqual(facts[1][1]["contract"], "native_media")
        self.assertEqual(out["status"], "failed")  # no music artifact
        self.assertTrue(ctx.media_result_observed)
        again, more, _ = await self.run_hook("music", ctx=ctx)
        self.assertEqual((again, more), (None, []))

    async def test_unreadable_project_is_unknown_and_does_not_raise(self):
        async with self.sessions() as s:
            await s.delete(await s.get(Project, PID))
            await s.commit()
        out, facts, _ = await self.run_hook("videos")
        self.assertEqual(out["status"], "unknown")
        self.assertEqual(facts[1][1]["status"], "unknown")

    async def test_other_node_types_and_no_attempt_are_ignored(self):
        out, facts, _ = await self.run_hook("plan")
        self.assertEqual((out, facts), (None, []))
        self.assertIsNone(await media_verification.observe_media_result(SimpleNamespace(bind=self.engine), None,
                                                                         "music"))  # no attempt context


class Registry(unittest.TestCase):
    def test_every_media_node_resolves_to_the_same_named_hook(self):
        import app.services.decision_v1  # noqa: F401
        import app.services.node_capabilities  # noqa: F401
        for node_type in ("videos", "audio", "assemble", "hero", "items", "music"):
            spec = node_spec.verifier_spec_for(node_type)
            self.assertEqual(spec.name, "media_result", node_type)
            self.assertTrue(callable(spec.resolve()))
            self.assertFalse(spec.managed_only)  # observation only: it never holds or fails a run
        self.assertEqual(node_spec.verifier_spec_for("sfx_gen").name, "sfx_result")
        self.assertIsNone(node_spec.verifier_spec_for("publish"))

    def test_publish_stays_human_only_with_no_verifier_hook_or_recovery(self):
        import app.services.node_capabilities  # noqa: F401
        spec = node_spec.capability_spec_for("publish")
        self.assertEqual(node_spec.coverage_level(spec), "human_only")
        self.assertEqual(spec.forbidden_code, "external_irreversible")
        self.assertFalse(spec.effective_managed or spec.recovery_actions or spec.verifier or spec.observation_hook)
        self.assertEqual(set(spec.actions), {"read_events", "read_contract", "human_review"})
        self.assertNotIn("publish", media_verification.MEDIA_NODE_TYPES)


SCOPE = SimpleNamespace(execution_id="execution", project_id=2, node_keys=["videos", "sfx"],
                        observer_state="active", gaps=[])
_n = [0]


def ev(event_kind, /, *, node="videos", attempt="a1", **payload):
    _n[0] += 1
    return dict(id=_n[0], event_id=f"pe{_n[0]}", execution_id="execution", project_id=2, node_key=node,
                attempt_id=attempt, request_id=None, kind=event_kind, payload=payload, db_scope="project",
                created_at="2026-10-06 12:00:00")


class Diagnosis(unittest.TestCase):
    def verification(self, **kw):
        base = dict(contract="native_media", node_type="videos", status="failed", summary="клипы: не прошли проверку 1 из 2: clip_002 (missing_clip)",
                    outcome_unknown=[])
        return ev("node_result_verification", **{**base, **kw})

    def test_failed_verification_is_a_blocking_media_failure_with_the_plain_summary(self):
        (item,) = derive_incidents(SCOPE, [self.verification()], {})
        self.assertEqual((item["type"], item["blocking"], item["status"]), ("media_failure", True, "human_review"))
        self.assertIn("clip_002", item["fact"])
        self.assertEqual(item["recommendation"]["automatic_action"], False)
        self.assertFalse(item["semantic_quality_verified"])

    def test_unknown_verification_is_a_non_blocking_warning(self):
        (item,) = derive_incidents(SCOPE, [self.verification(status="unknown")], {})
        self.assertEqual((item["type"], item["blocking"], item["severity"]), ("media_failure", False, "warning"))

    def test_passed_verification_is_silent(self):
        self.assertEqual(derive_incidents(SCOPE, [self.verification(status="passed")], {}), [])

    def test_unknown_outcome_is_a_typed_uncertain_request_per_provider(self):
        rows = [self.verification(status="passed", outcome_unknown=[
            {"key": "clip_001", "provider": "kie"}, {"key": "clip_002", "provider": "outsee"}])]
        out = derive_incidents(SCOPE, rows, {})
        self.assertEqual(sorted((i["type"], i["provider"]) for i in out),
                         [("uncertain_request", "kie"), ("uncertain_request", "outsee")])
        self.assertTrue(all("Новый submit запрещён" in i["fact"] for i in out))

    def test_sfx_fallback_is_a_visible_degradation_not_an_error(self):
        rows = [ev("provider_fallback", node="sfx", idx=3, kind="whoosh", provider="elevenlabs",
                   error_class="ReadTimeout", may_have_charged="uncertain"),
                ev("provider_fallback", node="sfx", idx=4, kind="hit", provider="elevenlabs",
                   error_class="RuntimeError", may_have_charged="no")]
        out = derive_incidents(SCOPE, rows, {})
        self.assertEqual([i["type"] for i in out], ["provider_degraded", "provider_degraded"])
        self.assertTrue(all(i["blocking"] is False and i["status"] == "human_review" for i in out))
        facts = sorted(i["fact"] for i in out)
        self.assertTrue(any("неизвестно (таймаут или обрыв)" in f for f in facts))
        self.assertTrue(any("нет (получен ответ провайдера)" in f for f in facts))
        self.assertEqual(out[0]["title"], TITLES["provider_degraded"])

    def test_assemble_retry_fact_is_not_an_incident(self):
        self.assertEqual(derive_incidents(SCOPE, [ev("assemble_retry", attempt_no=1)], {}), [])


if __name__ == "__main__":
    unittest.main()
