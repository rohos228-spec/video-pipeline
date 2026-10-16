import importlib
import shutil
import unittest
from pathlib import Path

from sqlalchemy import update

from app.models import Artifact, ArtifactKind, Frame, Project
from app.services import media_soft_checks as soft
from tests import test_media_verification as tmv

FFMPEG = tmv.FFMPEG


class PureHelpers(unittest.TestCase):
    def test_parse_aspect(self):
        self.assertAlmostEqual(soft.parse_aspect("9:16"), 9 / 16)
        self.assertAlmostEqual(soft.parse_aspect("16_9"), 16 / 9)
        self.assertIsNone(soft.parse_aspect("auto"))
        self.assertIsNone(soft.parse_aspect(None))
        self.assertIsNone(soft.parse_aspect("0:5"))

    def test_aspect_mismatch_is_conservative(self):
        self.assertFalse(soft.aspect_mismatch(1080, 1920, 9 / 16))
        self.assertFalse(soft.aspect_mismatch(1080, 1912, 9 / 16))
        self.assertTrue(soft.aspect_mismatch(1920, 1080, 9 / 16))
        self.assertFalse(soft.aspect_mismatch(1920, 1080, None))
        self.assertFalse(soft.aspect_mismatch(None, None, 9 / 16))

    def test_final_duration_rules(self):
        probe = {"width": 1080, "height": 1920, "duration": 30.0}
        asp = 9 / 16
        self.assertEqual(soft.final_soft_issues_from(probe, asp, [10.0, 10.0, 10.0]), [])
        self.assertEqual(soft.final_soft_issues_from(probe, asp, [40.0, 40.0]), ["final_shorter_than_scenes"])
        self.assertEqual(soft.final_soft_issues_from(probe, asp, [1.0, 1.0]), ["final_longer_than_scenes"])
        self.assertEqual(soft.final_soft_issues_from(probe, asp, [40.0, None, None, None]), [])
        self.assertEqual(soft.final_soft_issues_from(probe, asp, []), [])
        self.assertEqual(soft.final_soft_issues_from({**probe, "width": 1920, "height": 1080}, asp, [10.0] * 3),
                         ["final_aspect_mismatch"])


class DeadCodeRemoved(unittest.TestCase):
    def test_legacy_worker_and_wait_for_decision_are_gone(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "app" / "worker.py").exists())
        from app.services import hitl
        self.assertFalse(hasattr(hitl, "wait_for_decision"))
        importlib.import_module("app.main")


@unittest.skipUnless(FFMPEG, "ffmpeg/ffprobe are required for the media fixtures")
class SoftMediaChecks(tmv.Env):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.black = tmv._ffmpeg(["-f", "lavfi", "-i", "color=black:s=64x96:r=10:d=2", "-c:v", "libx264",
                                 "-pix_fmt", "yuv420p"], cls.fixtures / "black.mp4")
        cls.quiet = tmv._ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1"],
                                cls.fixtures / "quiet.mp3")

    async def set_aspect(self, value):
        async with self.sessions() as s:
            await s.execute(update(Project).where(Project.id == tmv.PID).values(aspect_ratio=value))
            await s.commit()

    async def clip(self, frame, data=None):
        path = self.write(f"videos/clip_{frame.number:03d}_ab12cd34.mp4", self.mp4 if data is None else data)
        await self.add(Artifact(project_id=tmv.PID, frame_id=frame.id, kind=ArtifactKind.scene_video,
                                uuid=f"u{frame.number}", path=str(path), meta={}))

    async def test_clip_aspect_mismatch_is_soft_not_failed(self):
        (f1,) = await self.frames("a")
        await self.clip(f1)
        await self.set_aspect("9:16")
        proof = await self.proof("videos")
        item = proof["items"][0]
        self.assertEqual((item["status"], item["reason"]), ("unknown", "soft_clip_aspect_mismatch"))
        self.assertNotEqual(proof["status"], "failed")
        self.assertEqual(proof["soft_warnings"], ["clip_aspect_mismatch"])

    async def test_clip_with_matching_or_unset_aspect_passes(self):
        (f1,) = await self.frames("a")
        await self.clip(f1)
        self.assertEqual((await self.proof("videos"))["status"], "passed")
        await self.set_aspect("2:3")
        proof = await self.proof("videos")
        self.assertEqual((proof["status"], proof["soft_warnings"]), ("passed", []))

    async def test_all_black_clip_is_soft(self):
        f1, f2 = await self.frames("a", "b")
        await self.clip(f1, self.black)
        await self.clip(f2)
        proof = await self.proof("videos")
        by_key = {i["key"]: i for i in proof["items"]}
        self.assertEqual((by_key["clip_001"]["status"], by_key["clip_001"]["reason"]),
                         ("unknown", "soft_clip_black"))
        self.assertEqual(by_key["clip_002"]["status"], "passed")
        self.assertNotEqual(proof["status"], "failed")

    async def test_hard_failures_stay_hard(self):
        (f1,) = await self.frames("a")
        await self.clip(f1, b"")
        self.assertEqual((await self.proof("videos"))["status"], "failed")

    async def _music(self, data):
        path = self.write("music/music.mp3", data)
        await self.add(Artifact(project_id=tmv.PID, kind=ArtifactKind.music, uuid="m1", path=str(path), meta={}))

    async def test_silent_music_is_soft(self):
        await self._music(self.quiet)
        proof = await self.proof("music")
        item = proof["items"][0]
        self.assertEqual((item["status"], item["reason"]), ("unknown", "soft_music_silent"))
        self.assertNotEqual(proof["status"], "failed")

    async def test_audible_music_passes(self):
        await self._music(self.mp3)
        proof = await self.proof("music")
        self.assertEqual((proof["status"], proof["soft_warnings"]), ("passed", []))

    async def _final(self, durations):
        path = self.write("final/verify.mp4", self.mp4)
        await self.add(Artifact(project_id=tmv.PID, kind=ArtifactKind.final_video, uuid="f1", path=str(path),
                                meta={}))
        rows = [Frame(project_id=tmv.PID, number=i, uuid=f"{i:032x}", sort_key=i, voiceover_text="т",
                      duration_seconds=d, attrs={}) for i, d in enumerate(durations, 1)]
        await self.add(*rows)
        return path

    async def test_final_much_shorter_than_scenes_is_soft(self):
        await self._final([20.0, 20.0])
        proof = await self.proof("assemble")
        item = proof["items"][0]
        self.assertEqual((item["status"], item["reason"]), ("unknown", "soft_final_shorter_than_scenes"))
        self.assertNotEqual(proof["status"], "failed")

    async def test_final_matching_scenes_passes(self):
        await self._final([0.5, 0.5])
        self.assertEqual((await self.proof("assemble"))["status"], "passed")

    async def test_auto_approve_of_final_is_blocked_by_soft_issue(self):
        from app.orchestrator import auto_advance as aa
        path = await self._final([20.0, 20.0])
        async with self.sessions() as s:
            project = await s.get(Project, tmv.PID)
            self.assertIs(await aa._final_verified(s, project), False)
        async with self.sessions() as s:
            for fr in (await s.scalars(__import__("sqlalchemy").select(Frame))).all():
                fr.duration_seconds = 0.5
            await s.commit()
        async with self.sessions() as s:
            project = await s.get(Project, tmv.PID)
            self.assertIs(await aa._final_verified(s, project), True)
            project.aspect_ratio = "9:16"
            await s.flush()
            self.assertIs(await aa._final_verified(s, project), False)
            project.aspect_ratio = "2:3"
            await s.flush()
            self.assertIs(await aa._final_verified(s, project), True)
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
