import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app.services import gpt_api, gpt_operator_client as goc, observation
from app.services.apply_ops_batches import run_apply_ops_batched
from app.settings import settings

FRAME = {"uuid": "frame-one", "number": 1,
         "voiceover_text": "Синтетический диктор открывает дверь.",
         "fields": {"закадр": "Синтетический диктор открывает дверь."}}


def _chat(raw, finish=""):
    return SimpleNamespace(raw=raw, finish_reason=finish, text='{"ops":[]}')



def test_chat_completions_stop_is_complete_without_status():
    assert goc._qc_response_complete(_chat({"choices": [{"finish_reason": "stop"}]}))
    assert goc._qc_response_complete(_chat({"usage": {}}, "stop"))
    assert goc._qc_response_complete(_chat({"finish_reason": "stop"}))


@pytest.mark.parametrize("result", [
    _chat({"choices": [{"finish_reason": "length"}]}),
    _chat({"usage": {}}, "length"),
    _chat({"choices": [{"finish_reason": "stop"}], "sse_salvaged": True}),
    _chat({"choices": [{"finish_reason": "stop"}], "cf_continued": True}, "stream_continued"),
    _chat({}),
    _chat({"usage": {}}, ""),
    SimpleNamespace(text="x"),
])
def test_truncated_salvaged_continued_or_unknown_is_not_complete(result):
    assert not goc._qc_response_complete(result)


def test_responses_status_contract_is_unchanged():
    assert goc._qc_response_complete(_chat({"status": "completed"}))
    assert not goc._qc_response_complete(_chat({"status": "incomplete"}))
    assert not goc._qc_response_complete(_chat({"status": "completed", "sse_salvaged": True}))
    assert not goc._qc_response_complete(_chat({"status": "in_progress"}, "stop"))


def test_empty_ops_accepted_only_for_complete_answer():
    ok = _chat({"choices": [{"finish_reason": "stop"}]})
    assert goc._accepted_empty_ops(ok, {"ops": []}, allowed=True)
    assert not goc._accepted_empty_ops(ok, {"ops": []}, allowed=False)
    cut = _chat({"choices": [{"finish_reason": "length"}]})
    assert not goc._accepted_empty_ops(cut, {"ops": []}, allowed=True)


async def test_require_completed_raises_without_hold_outside_managed():
    await goc._require_completed_qc_response(_chat({"choices": [{"finish_reason": "stop"}]}))
    with pytest.raises(RuntimeError, match="incomplete/uncertain"):
        await goc._require_completed_qc_response(_chat({"choices": [{"finish_reason": "length"}]}))


async def test_managed_incomplete_answer_holds_with_shared_request_id(monkeypatch):
    held = []

    async def hold(ctx, request_id):
        held.append(request_id)
    ctx = SimpleNamespace(scope=SimpleNamespace(mode="managed"), last_request_id="req-1")
    monkeypatch.setattr(observation, "hold_uncertain_request", hold)
    token = observation._attempt.set(ctx)
    try:
        await goc._require_completed_qc_response(_chat({"choices": [{"finish_reason": "stop"}]}))
        assert held == []
        with pytest.raises(RuntimeError):
            await goc._require_completed_qc_response(_chat({"choices": [{"finish_reason": "length"}]}))
    finally:
        observation._attempt.reset(token)
    assert held == ["req-1"]



class Vibecode:
    def __init__(self, tmp_path, monkeypatch):
        self.posts = []
        self.answers = []
        self.finish = "stop"
        self.usage_chunk = False
        self.applied = []
        self.root = tmp_path
        self.context = {"frames": [dict(FRAME)]}
        self.path = tmp_path / "db_frames.json"
        self.path.write_text(json.dumps(self.context, ensure_ascii=False), encoding="utf-8")
        for name, value in [("text_llm_provider", "vibecode"), ("vibecode_api_key", "vk-synthetic"),
                            ("vibecode_base_url", "http://127.0.0.1:9/v1"), ("gpt_relay_token", ""),
                            ("gpt_max_retries", 0)]:
            monkeypatch.setattr(settings, name, value)
        monkeypatch.setattr("app.services.text_llm_catalog.read_choice", lambda *_a, **_k: {})
        monkeypatch.setattr(gpt_api, "_async_client", lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle), **kw))

    def _handle(self, request):
        assert request.url.host == "127.0.0.1" and request.url.path == "/v1/chat/completions"
        self.posts.append(json.loads(request.content))
        answer = self.answers.pop(0) if self.answers else '{"ops":[]}'
        cut = len(answer) // 2
        chunks = [
            {"id": "c1", "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer[:cut]}}]},
            {"id": "c1", "choices": [{"index": 0, "delta": {"content": answer[cut:]}}]},
        ]
        if self.finish:
            chunks.append({"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": self.finish}]})
        if self.usage_chunk:
            chunks.append({"id": "c1", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    async def batch(self):
        async def apply(payload):
            self.applied.append(payload)
        return await run_apply_ops_batched(
            project_dir=self.root, node_key="n_excel_gpt_fw_qc", role="assist", output_mode="project_file",
            prompt="qc", accompanying="", db_ctx=self.context, ctx_path=self.path, project_id=2,
            dense=False, footer_kind="shots_qc", force_full=True, allow_empty_ops=True, apply_fn=apply)


@pytest.fixture
def vibe(tmp_path, monkeypatch):
    return Vibecode(tmp_path, monkeypatch)


async def test_vibecode_noop_stop_is_accepted_with_one_post(vibe):
    result = await vibe.batch()
    assert len(vibe.posts) == 1 and vibe.posts[0]["stream"] is True and "messages" in vibe.posts[0]
    assert result.apply_ops == {"ops": []} and vibe.applied == []


async def test_vibecode_noop_with_trailing_usage_chunk_is_accepted(vibe):
    vibe.usage_chunk = True
    result = await vibe.batch()
    assert result.apply_ops == {"ops": []} and len(vibe.posts) == 1


async def test_vibecode_truncated_noop_fails_strictly_and_applies_nothing(vibe):
    vibe.finish = "length"
    with pytest.raises(RuntimeError, match="QC response incomplete"):
        await vibe.batch()
    assert vibe.applied == []


async def test_vibecode_real_correction_applies_once(vibe):
    vibe.answers = ['{"ops":[{"frame_uuid":"frame-one","fields":{"место":"улица"}}]}']
    await vibe.batch()
    assert len(vibe.posts) == 1 and len(vibe.applied) == 1



async def test_parse_is_linked_to_task_local_request_not_shared_last(monkeypatch):
    seen = []

    async def fake_fact(kind, payload=None, *, request_id=None, body=None):
        seen.append((kind, request_id))
    monkeypatch.setattr(observation, "record_fact", fake_fact)
    ctx = SimpleNamespace(scope=SimpleNamespace(mode="managed"), last_request_id="pack-B-request")
    token = observation._attempt.set(ctx)

    async def pack(own_id):
        observation._completed_model_request.set((ctx, own_id))
        await asyncio.sleep(0)
        assert goc._task_request_id() == own_id
        await goc._record_parse_for_task_request("{}", None)

    try:
        await asyncio.gather(asyncio.create_task(pack("pack-A-request")),
                             asyncio.create_task(pack("pack-C-request")))
    finally:
        observation._attempt.reset(token)
    assert sorted(r for _k, r in seen) == ["pack-A-request", "pack-C-request"]
    assert ctx.last_request_id == "pack-B-request"


async def test_parse_without_attempt_context_is_untouched():
    assert goc._task_request_id() is None
    with patch.object(observation, "record_parse") as rp:
        async def fake(*a, **k):
            return None
        rp.side_effect = fake
        await goc._record_parse_for_task_request("x", None)
        rp.assert_called_once()
