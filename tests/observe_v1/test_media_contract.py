"""Offline adapter contracts; no provider or worker substitutions."""
import asyncio
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class Environment(unittest.TestCase):
    def test_record_existing_tools(self):
        tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
        modules = {name: importlib.util.find_spec(name) is not None
                   for name in ("PIL", "faster_whisper", "torch", "nemo")}
        # Inspect local cache names only, never download models.
        cache = Path.home() / ".cache/huggingface/hub"
        models = sorted(p.name for p in cache.glob("models--*")) if cache.exists() else []
        Path("/results/media-environment.json").write_text(json.dumps({
            "tools": tools, "modules": modules, "cached_asr_models": models,
            "network": "none", "new_dependencies": False}, indent=2))
        self.assertTrue(Path("/.dockerenv").exists())


class Capabilities(unittest.TestCase):
    def test_project_text_contract_uses_native_validation_and_preserves_hitl(self):
        from app.services.node_capabilities import capability_for,project_text_contract
        for typ,field in (('plan','general_plan'),('script','script_text')):
            cap=capability_for(typ)
            self.assertEqual(cap.result_kind,'project_text')
            self.assertTrue(cap.managed)
            self.assertNotIn('retry_same',cap.actions)
            self.assertNotIn('retry_profile',cap.actions)
            contract=project_text_contract(typ)
            self.assertEqual(contract['output_field'],field)
            self.assertTrue(contract['human_approval_preserved'])
            self.assertFalse(contract['semantic_quality_verified'])
        self.assertFalse(capability_for('sd_agent').managed)

    def test_project_text_verification_never_accepts_unknown_or_short_output(self):
        from types import SimpleNamespace
        from app.services.node_capabilities import verify_project_text
        for value in (None,'', 'short'):
            result=verify_project_text(SimpleNamespace(general_plan=value,script_text=value),'plan')
            self.assertEqual(result['status'],'failed')
        good='Один синтетический абзац для структурной проверки. '*6
        for typ in ('plan','script'):
            result=verify_project_text(SimpleNamespace(general_plan=good,script_text=good),typ)
            self.assertEqual(result['status'],'passed')
            self.assertEqual(len(result['sha256']),64)
            self.assertFalse(result['semantic_quality_verified'])
        with self.assertRaises(ValueError):verify_project_text(SimpleNamespace(),'sd_agent')

    def test_only_existing_types_and_no_generic_retry(self):
        from app.services.node_capabilities import capability_for
        from app.orchestrator.node_registry import WORK_NODES
        for typ, kind in (("images", "image"), ("videos", "video"),
                          ("audio", "audio"), ("assemble", "assembly")):
            cap = capability_for(typ)
            self.assertIn(typ, WORK_NODES)
            self.assertEqual(cap.result_kind, kind)
            self.assertNotIn("retry_same", cap.actions)
            self.assertIn("human_review", cap.actions)
        self.assertFalse(capability_for("imaginary").managed)
        self.assertEqual(capability_for("publish").actions, ("read_events", "read_contract", "human_review"))

    def test_text_contract_is_not_replaced_by_file_contract(self):
        from app.services.node_capabilities import capability_for
        self.assertEqual(capability_for("excel_gpt").result_kind, "structured")
        self.assertIn("retry_same", capability_for("excel_gpt").actions)


class Artifacts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_image_decode_dimensions_hash_and_scope(self):
        from PIL import Image
        from app.services.artifact_verifier import verify_artifact
        path = self.root / "image.png"
        Image.new("RGB", (64, 96), "blue").save(path)
        result = await verify_artifact(path, root=self.root, kind="image",
                                       expected={"width": 64, "height": 96})
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["semantic_quality_verified"])
        self.assertEqual(len(result["sha256"]), 64)
        bad = await verify_artifact(path, root=self.root, kind="image",
                                   expected={"width": 32})
        self.assertEqual(bad["status"], "failed")
        path.write_bytes(b"not an image")
        self.assertEqual((await verify_artifact(path, root=self.root, kind="image"))["status"], "failed")
        with self.assertRaises(ValueError):
            await verify_artifact(self.root / "../outside.png", root=self.root, kind="image")

    async def test_missing_or_unproven_artifact_is_not_success(self):
        from app.services.artifact_verifier import verify_artifact
        missing = await verify_artifact(self.root / "missing.mp4", root=self.root, kind="video")
        self.assertEqual(missing["status"], "failed")
        path = self.root / "opaque.bin"
        path.write_bytes(b"arbitrary")
        self.assertEqual((await verify_artifact(path, root=self.root, kind="unsupported"))["status"], "unknown")

    async def test_subtitle_intervals_and_structure(self):
        from app.services.assembly import make_simple_ass
        from app.services.artifact_verifier import verify_artifact
        path = self.root / "subs.ass"
        make_simple_ass([(0.0, 0.8, "Синтетический"), (0.9, 1.8, "текст")], path)
        self.assertEqual((await verify_artifact(path, root=self.root, kind="subtitles",
                                               expected={"max_duration": 2}))["status"], "passed")
        path.write_text(path.read_text().replace("0:00:00.80", "0:00:00.00"))
        self.assertEqual((await verify_artifact(path, root=self.root, kind="subtitles"))["status"], "failed")

    async def test_video_audio_streams_and_duration(self):
        from app.services.artifact_verifier import verify_artifact
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("existing image has no ffmpeg/ffprobe; recorded dependency blocker")
        path = self.root / "clip.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=64x96:r=10:d=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
        for kind in ("video", "assembly"):
            result = await verify_artifact(path, root=self.root, kind=kind,
                expected={"width": 64, "height": 96, "min_duration": 0.9, "max_duration": 1.2})
            self.assertEqual(result["status"], "passed", result)
        audio = self.root / "voice.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", str(audio)], check=True)
        self.assertEqual((await verify_artifact(audio, root=self.root, kind="audio"))["status"], "passed")
        self.assertEqual((await verify_artifact(audio, root=self.root, kind="video"))["status"], "failed")
        path.write_bytes(path.read_bytes()[:100])
        self.assertEqual((await verify_artifact(path, root=self.root, kind="video"))["status"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
