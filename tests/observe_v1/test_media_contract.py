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
    def test_native_split_contract_preserves_fallback_without_generic_recovery(self):
        from app.services.node_capabilities import capability_for, split_contract
        cap = capability_for('split')
        self.assertEqual(cap.result_kind, 'frame_set')
        self.assertEqual(cap.actions, ('read_events','read_contract','wait','human_review'))
        contract = split_contract()
        self.assertEqual(contract['minimum_frames'], 2)
        self.assertIn('native_local_voiceover_fallback', contract['formats'])
        self.assertFalse(contract['semantic_quality_verified'])

    def test_split_verifies_identifiers_output_and_complete_voiceover_coverage(self):
        from types import SimpleNamespace
        from app.services.node_capabilities import verify_split_frames
        def frame(uid, number, vo):
            return SimpleNamespace(project_id=2,uuid=uid,number=number,voiceover_text=vo)
        good = [frame('id-a',1,'Первый кадр.'),frame('id-b',2,'Второй кадр.')]
        result = verify_split_frames(good,project_id=2,voiceover='Первый кадр. Второй кадр.')
        self.assertEqual(result['status'],'passed')
        self.assertTrue(result['voiceover_preserved'])
        self.assertFalse(result['semantic_quality_verified'])
        self.assertEqual(result['frame_count'],2)
        for frames in (good[:1],good+[frame('id-b',3,'лишний')],
                       [frame('id-a',1,''),good[1]], [frame('id-a',1,'Первый кадр.'),frame('id-b',1,'Второй кадр.')],
                       [frame('id-a',1,'Первый кадр.'),SimpleNamespace(project_id=1,uuid='id-b',number=2,voiceover_text='Второй кадр.')]):
            self.assertEqual(verify_split_frames(frames,project_id=2,voiceover='Первый кадр. Второй кадр.')['status'],'failed')
        self.assertEqual(verify_split_frames(good,project_id=2,voiceover='Другой исходный текст')['status'],'failed')
        self.assertEqual(verify_split_frames(good,project_id=2,voiceover=None)['status'],'unknown')

    def test_native_parse_distinguishes_json_syntax_from_required_plan_field(self):
        from app.services.node_capabilities import project_text_parse_evidence
        missing=project_text_parse_evidence('{"ops":[]}', '', 'plan')
        self.assertTrue(missing['syntax_valid'])
        self.assertTrue(missing['parsed'])
        self.assertFalse(missing['payload_present'])
        self.assertFalse(missing['schema_valid'])
        self.assertTrue(missing['payload_required'])
        malformed=project_text_parse_evidence('{"ops":}', '', 'plan')
        self.assertFalse(malformed['syntax_valid'])
        self.assertFalse(malformed['parsed'])

    def test_native_voiceover_is_not_misdiagnosed_as_required_json(self):
        from app.services.node_capabilities import project_text_parse_evidence
        text='Синтетический закадр. '*20
        proof=project_text_parse_evidence('<<<VOICEOVER>>>'+text+'<<<END>>>',text,'script')
        self.assertFalse(proof['syntax_valid'])
        self.assertTrue(proof['parsed'])
        self.assertTrue(proof['schema_valid'])
        self.assertFalse(proof['payload_required'])
        self.assertFalse(proof['semantic_quality_verified'])
        self.assertFalse(project_text_parse_evidence('short','short','script')['schema_valid'])

    def test_registered_kie_node_choice_binds_exact_api_model_and_route(self):
        from types import SimpleNamespace
        from app.models import ProjectStatus
        from app.services.vibecode_catalog import find_model
        from app.services.llm_override import bind_project_llm
        from app.services import gpt_api
        choice=find_model('gpt-kie')
        self.assertIsNotNone(choice,'native node catalog must recognize the existing Kie text choice')
        self.assertEqual(choice['provider'],'kie')
        self.assertEqual(choice['api_model'],'gpt-5-6-sol')
        project=SimpleNamespace(id=2,meta={'canvas_graph':{'nodes':[{'id':'native-A','type':'plan','data':{'modelId':'gpt-kie'}}]}})
        with bind_project_llm(project,ProjectStatus.planning) as bound:
            self.assertEqual(bound.provider,'kie')
            self.assertEqual(bound.model_id,'gpt-5-6-sol')
            self.assertEqual(gpt_api._chat_url(bound.model_id),'http://127.0.0.1:18766/codex/v1/responses')
            self.assertTrue(gpt_api.is_responses_mode())

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


class ImagePromptsContract(unittest.TestCase):
    """Observe native img_pr output without granting another write/retry path."""

    @staticmethod
    def _frames():
        from types import SimpleNamespace

        parent_uuid = "a1" * 12
        return [
            SimpleNamespace(project_id=2, uuid=parent_uuid, number=1,
                voiceover_text="Мастер открывает ящик и достаёт инструмент.",
                image_prompt="", attrs={"camera_subdivide": {
                    "role": "vo_parent", "parent_uuid": parent_uuid,
                    "shot_index": 1, "shots_in_beat": 2}}),
            SimpleNamespace(project_id=2, uuid="c3" * 12, number=2,
                voiceover_text="Достаёт инструмент.", image_prompt="",
                attrs={"camera_subdivide": {
                    "role": "shot", "parent_uuid": parent_uuid,
                    "shot_index": 2, "shots_in_beat": 2}}),
            # Native parents do not need explicit vo_parent metadata.
            SimpleNamespace(project_id=2, uuid="b2" * 12, number=3,
                voiceover_text="После работы он возвращает инструмент на место.",
                image_prompt="", attrs={}),
        ]

    @staticmethod
    def _prompt(length):
        scene = ("A sunlit workshop with a wooden workbench, an open drawer, "
                 "and a worker holding a metal tool. Soft daylight reveals "
                 "the grain of the wood and the texture of the fabric. ")
        return (scene * 10)[:length]

    def _completed_frames(self, frames):
        from copy import deepcopy
        from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS

        result = deepcopy(frames)
        for index in (0, 2):
            result[index].image_prompt = self._prompt(IMG_PR_MIN_WRITE_CHARS)
        result[1].image_prompt = "Close detail."
        return result

    def _assert_verification(self, frames, snapshot, status):
        from copy import deepcopy
        from app.services.node_capabilities import verify_image_prompts

        before = deepcopy((frames, snapshot))
        result = verify_image_prompts(frames, project_id=2, snapshot=snapshot)
        self.assertEqual(result["status"], status, result)
        self.assertFalse(result["semantic_quality_verified"])
        self.assertEqual((frames, snapshot), before, "verification must be read-only")
        return result

    def test_native_contract_uses_existing_executor_and_read_actions_only(self):
        from app.orchestrator.node_registry import WORK_NODES
        from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS
        from app.services.node_capabilities import (
            READ_ACTIONS, capability_for, image_prompts_contract,
            require_managed_capability,
        )

        self.assertIn("image_prompts", WORK_NODES)
        cap = capability_for("image_prompts")
        self.assertEqual(cap.result_kind, "frame_prompts")
        self.assertTrue(cap.managed)
        self.assertEqual(require_managed_capability("image_prompts"), cap)
        self.assertIn("generate_image_prompts.run", cap.executor)
        self.assertIn("xlsx_step_runners.run_img_pr_xlsx", cap.executor)
        self.assertEqual(cap.actions, READ_ACTIONS)
        for action in ("retry_same", "retry_profile", "finish"):
            self.assertNotIn(action, cap.actions)
        contract = image_prompts_contract()
        self.assertEqual(IMG_PR_MIN_WRITE_CHARS, 200)
        self.assertEqual(contract["min_characters"], IMG_PR_MIN_WRITE_CHARS)
        self.assertFalse(contract["semantic_quality_verified"])

    def test_snapshot_retains_all_rows_and_captures_values_before_writes(self):
        from copy import deepcopy
        from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS
        from app.services.node_capabilities import image_prompt_input_snapshot

        frames = self._frames()
        frames[2].image_prompt = self._prompt(IMG_PR_MIN_WRITE_CHARS)
        before = deepcopy(frames)
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        self.assertIsInstance(snapshot, dict)
        self.assertEqual(snapshot["project_id"], 2)
        self.assertIsInstance(snapshot["frames"], list)
        self.assertEqual(len(snapshot["frames"]), 3)
        for row, frame, required in zip(snapshot["frames"], frames, (True, False, True)):
            self.assertEqual(row["uuid"], frame.uuid)
            self.assertEqual(row["number"], frame.number)
            self.assertEqual(row["voiceover_text"], frame.voiceover_text)
            self.assertIs(row["required_prompt"], required)
        self.assertEqual(frames, before)
        captured = deepcopy(snapshot)
        frames[0].voiceover_text = "Позднее изменение закадра."
        frames[0].attrs["camera_subdivide"]["role"] = "shot"
        frames[1].number = 99
        frames.pop()
        self.assertEqual(snapshot, captured)

    def test_snapshot_required_prompt_matches_native_parent_rules(self):
        from copy import deepcopy
        from app.services.node_capabilities import image_prompt_input_snapshot
        from app.services.vo_shot_expand import is_img_pr_vo_parent

        parent, child, implicit_parent = self._frames()
        silent = deepcopy(implicit_parent)
        silent.uuid, silent.number, silent.voiceover_text = "d4" * 12, 4, " \t "
        no_uuid = deepcopy(implicit_parent)
        no_uuid.uuid, no_uuid.number = "", 5
        # Still suppression does not change the img_pr VO-parent predicate.
        parent.attrs["camera_subdivide"]["use_parent_still"] = False
        frames = [parent, child, implicit_parent, silent, no_uuid]
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        self.assertEqual(len(snapshot["frames"]), len(frames))
        for row, frame, expected in zip(snapshot["frames"], frames,
                                        (True, False, True, False, False)):
            self.assertIs(is_img_pr_vo_parent(frame), expected)
            self.assertIs(row["required_prompt"], expected)

    def test_complete_two_parent_output_allows_short_or_empty_actual_child_prompt(self):
        from app.services.node_capabilities import image_prompt_input_snapshot

        frames = self._frames()
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        for child_prompt in (None, "", "Close detail."):
            with self.subTest(child_prompt=child_prompt):
                output = self._completed_frames(frames)
                output[1].image_prompt = child_prompt
                self._assert_verification(output, snapshot, "passed")

    def test_native_second_shot_threshold_is_accepted_with_nonempty_primary(self):
        from app.services.db_apply import FIELD_ALIASES, normalize_fields
        from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS, writeable_img_pr_ops
        from app.services.node_capabilities import image_prompt_input_snapshot
        from app.services.node_write_contract import filter_ops_for_node
        from app.services.plan_shot2 import SHOT2_PROMPT_ATTR

        frames = self._frames()
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        output = self._completed_frames(frames)
        # Actual apply-ops aliases become the primary column and this attrs key.
        fields = {"промт_картинки": "Primary scene.",
                  "промт_картинки_2": self._prompt(IMG_PR_MIN_WRITE_CHARS),
                  "voiceover_text": "Недопустимая перезапись закадра."}
        ops = filter_ops_for_node(
            [{"target": "frame", "frame_uuid": frames[0].uuid, "fields": fields}],
            node_kind="img_pr",
        )
        self.assertEqual(len(ops), 1)
        self.assertNotIn("voiceover_text", ops[0]["fields"])
        self.assertEqual(len(writeable_img_pr_ops(ops)), 1)
        native = normalize_fields(ops[0]["fields"], FIELD_ALIASES, scope="test frame")
        output[0].image_prompt = native["image_prompt"]
        output[0].attrs[SHOT2_PROMPT_ATTR] = native["image_prompt_shot2"]
        self._assert_verification(output, snapshot, "passed")

    def test_second_shot_alone_cannot_satisfy_native_worker_completion(self):
        from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS, writeable_img_pr_ops
        from app.services.node_capabilities import image_prompt_input_snapshot
        from app.services.plan_shot2 import SHOT2_PROMPT_ATTR

        frames = self._frames()
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        full = self._prompt(IMG_PR_MIN_WRITE_CHARS)
        # Native batch acceptance alone does not prove _finish_success can finish.
        self.assertEqual(len(writeable_img_pr_ops([
            {"target": "frame", "frame_uuid": frames[0].uuid,
             "fields": {"image_prompt_shot2": full}},
        ])), 1)
        for primary in (None, "", " \n\t "):
            with self.subTest(primary=primary):
                output = self._completed_frames(frames)
                output[0].image_prompt = primary
                output[0].attrs[SHOT2_PROMPT_ATTR] = full
                self._assert_verification(output, snapshot, "failed")

    def test_required_parent_rejects_empty_short_or_combined_short_prompts(self):
        from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS
        from app.services.node_capabilities import image_prompt_input_snapshot
        from app.services.plan_shot2 import SHOT2_PROMPT_ATTR

        frames = self._frames()
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        short = self._prompt(IMG_PR_MIN_WRITE_CHARS - 1)
        for primary, second in ((None, None), ("", ""), ("short", ""),
                                (short, ""), (short, short),
                                (" \n" + short + "\t ", "")):
            with self.subTest(primary=primary, second=second):
                output = self._completed_frames(frames)
                output[0].image_prompt = primary
                output[0].attrs[SHOT2_PROMPT_ATTR] = second
                self._assert_verification(output, snapshot, "failed")
        output = self._completed_frames(frames)
        output[0].image_prompt = ""
        output[0].attrs["camera_subdivide"]["role"] = "shot"
        self._assert_verification(output, snapshot, "failed")

    def test_unstored_prompt_aliases_do_not_count_as_db_output(self):
        from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS
        from app.services.node_capabilities import image_prompt_input_snapshot

        frames = self._frames()
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        output = self._completed_frames(frames)
        output[0].image_prompt = "Primary scene."
        full = self._prompt(IMG_PR_MIN_WRITE_CHARS)
        output[0].image_prompt_shot2 = full
        output[0].attrs["промт_картинки_2"] = full
        self._assert_verification(output, snapshot, "failed")

    def test_complete_scoped_frame_identity_and_numbers_must_be_retained(self):
        from copy import deepcopy
        from app.services.node_capabilities import image_prompt_input_snapshot

        frames = self._frames()
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        cases = ("missing_parent", "missing_child", "empty_output", "duplicate_uuid",
                 "duplicate_child", "foreign_uuid", "extra_uuid", "foreign_project",
                 "foreign_child_project", "empty_uuid", "missing_uuid",
                 "duplicate_number", "changed_number", "swapped_numbers")
        for case in cases:
            with self.subTest(case=case):
                output = self._completed_frames(frames)
                if case == "missing_parent":
                    output.pop(0)
                elif case == "missing_child":
                    output.pop(1)
                elif case == "empty_output":
                    output.clear()
                elif case == "duplicate_uuid":
                    output[2].uuid = output[0].uuid
                elif case == "duplicate_child":
                    output.append(deepcopy(output[1]))
                elif case == "foreign_uuid":
                    output[0].uuid = "d4" * 12
                elif case == "extra_uuid":
                    extra = deepcopy(output[2])
                    extra.uuid, extra.number = "d4" * 12, 4
                    output.append(extra)
                elif case == "foreign_project":
                    output[0].project_id = 3
                elif case == "foreign_child_project":
                    output[1].project_id = 3
                elif case == "empty_uuid":
                    output[0].uuid = ""
                elif case == "missing_uuid":
                    output[0].uuid = None
                elif case == "duplicate_number":
                    output[2].number = output[0].number
                elif case == "changed_number":
                    output[1].number = 4
                elif case == "swapped_numbers":
                    output[0].number, output[2].number = output[2].number, output[0].number
                self._assert_verification(output, snapshot, "failed")

    def test_parent_and_child_voiceover_must_remain_bound_to_each_uuid(self):
        from app.services.node_capabilities import image_prompt_input_snapshot

        frames = self._frames()
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        for index in (0, 1, 2):
            for voiceover in (None, "", "Изменённый закадровый текст."):
                with self.subTest(index=index, voiceover=voiceover):
                    output = self._completed_frames(frames)
                    output[index].voiceover_text = voiceover
                    self._assert_verification(output, snapshot, "failed")
        output = self._completed_frames(frames)
        output[0].voiceover_text, output[2].voiceover_text = (
            output[2].voiceover_text, output[0].voiceover_text)
        self._assert_verification(output, snapshot, "failed")

    def test_unavailable_or_incomplete_input_never_becomes_success(self):
        from copy import deepcopy
        from app.services.node_capabilities import image_prompt_input_snapshot, verify_image_prompts

        frames = self._frames()
        output = self._completed_frames(frames)
        self._assert_verification(output, None, "unknown")
        unavailable = [{}, {"project_id": 2}, {"project_id": 2, "frames": []}]
        complete = image_prompt_input_snapshot(frames, project_id=2)
        for field in ("uuid", "number", "voiceover_text", "required_prompt"):
            incomplete = deepcopy(complete)
            incomplete["frames"][0].pop(field)
            unavailable.append(incomplete)
        unproven_target = deepcopy(complete)
        unproven_target["frames"][0]["required_prompt"] = None
        unavailable.append(unproven_target)
        for index, snapshot in enumerate(unavailable):
            with self.subTest(snapshot_case=index):
                before = deepcopy((output, snapshot))
                proof = verify_image_prompts(output, project_id=2, snapshot=snapshot)
                self.assertIn(proof["status"], ("failed", "unknown"), proof)
                self.assertFalse(proof["semantic_quality_verified"])
                self.assertEqual((output, snapshot), before)
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        snapshot["frames"].pop(1)  # A targets-only snapshot cannot prove all-row integrity.
        self._assert_verification(output, snapshot, "failed")
        snapshot = image_prompt_input_snapshot(frames, project_id=2)
        snapshot["project_id"] = 3
        self._assert_verification(output, snapshot, "failed")


class Artifacts(unittest.IsolatedAsyncioTestCase):
    async def test_split_parse_observes_native_alias_precedence_without_false_failure(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock,patch
        from app.services.node_capabilities import record_split_parse
        from app.services.observation import _attempt
        ctx=SimpleNamespace(scope=SimpleNamespace(project_id=2),last_request_id='actual-model-request',
            split_input_voiceover='Первая строка. Вторая строка.')
        frames=[{'закадр':None,'voiceover_text':'Первая строка.'},{'voiceover':'Вторая строка.'}]
        token=_attempt.set(ctx)
        try:
            with patch('app.services.observation.record_fact',new_callable=AsyncMock) as fact:
                await record_split_parse(json.dumps({'ops':[{'target':'replace_frames','frames':frames}]}),frames)
                parse=fact.call_args_list[0]
                self.assertTrue(parse.args[1]['schema_valid'])
                self.assertEqual(parse.kwargs['request_id'],'actual-model-request')
                self.assertEqual(fact.call_args_list[1].args[1]['status'],'passed')
        finally:_attempt.reset(token)

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
