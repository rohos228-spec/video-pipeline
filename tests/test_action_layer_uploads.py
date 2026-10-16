from __future__ import annotations

import base64
import dataclasses
import hashlib
import os

import pytest
from fastapi import FastAPI, File, UploadFile

from app.models import Project
from app.services.action_layer import context as c
from app.services.action_layer import executor
from app.services.action_layer.context import (
    ActionContext, FileUpload, UploadRejected, WriteNotAllowed)
from app.services.action_layer.spec import ActionRegistry
from tests.action_layer_support import env

BASE = "/api/action-layer"
MP3 = b"ID3" + bytes(400)
PNG = b"\x89PNG\r\n\x1a\n" + bytes(400)
MP4 = bytes(4) + b"ftypisom" + bytes(2000)
NODE = "n_excel_gpt_fw_shots"


@pytest.fixture(autouse=True)
def _wave3_registered():
    from app.services.action_layer.spec import ensure_builtin_actions
    ensure_builtin_actions()



def test_build_accepts_valid_and_sanitizes_name() -> None:
    up = FileUpload.build("voice", "../../my voice?.MP3", MP3)
    assert up.filename == "my voice_.MP3" and up.content_type == "audio/mpeg"
    assert up.policy == "voice" and repr(MP3) not in repr(up)


def test_build_rejects_type_size_empty_and_wrong_content(monkeypatch) -> None:
    with pytest.raises(UploadRejected, match="не разрешён"):
        FileUpload.build("voice", "a.exe", MP3)
    with pytest.raises(UploadRejected, match="не разрешён"):
        FileUpload.build("scene_image", "a.jpg", PNG)
    with pytest.raises(UploadRejected, match="пустой"):
        FileUpload.build("voice", "a.mp3", b"")
    with pytest.raises(UploadRejected, match="не похоже"):
        FileUpload.build("voice", "a.mp3", b"not audio at all")
    with pytest.raises(UploadRejected, match="не похоже"):
        FileUpload.build("node_file", "a.txt", b"\xff\xfe\x00bad")
    with pytest.raises(UploadRejected, match="неизвестная политика"):
        FileUpload.build("nope", "a.mp3", MP3)
    small = dataclasses.replace(c.UPLOAD_POLICIES["voice"], max_bytes=100)
    monkeypatch.setitem(c.UPLOAD_POLICIES, "voice", small)
    with pytest.raises(UploadRejected, match="больше лимита"):
        FileUpload.build("voice", "a.mp3", MP3)


def test_base64_source_limits() -> None:
    ok = FileUpload.from_base64("voice", "a.mp3", base64.b64encode(MP3).decode())
    assert ok.content == MP3
    with pytest.raises(UploadRejected, match="некорректный base64"):
        FileUpload.from_base64("voice", "a.mp3", "***не base64***")
    big = base64.b64encode(b"ID3" + bytes(c.MAX_BASE64_BYTES)).decode()
    with pytest.raises(UploadRejected, match="base64 больше"):
        FileUpload.from_base64("voice", "a.mp3", big)
    with pytest.raises(UploadRejected, match="ровно один"):
        c.source_to_upload("voice", {"source_path": "a", "data_base64": "b"})
    with pytest.raises(UploadRejected, match="ровно один"):
        c.source_to_upload("voice", {})



@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from app.settings import settings
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(settings, "data_dir", root)
    return root


def test_path_inside_data_dir_ok_relative_and_absolute(data_dir) -> None:
    (data_dir / "inbox").mkdir()
    f = data_dir / "inbox" / "v.mp3"
    f.write_bytes(MP3)
    assert FileUpload.from_data_path("voice", str(f)).content == MP3
    assert FileUpload.from_data_path("voice", "inbox/v.mp3").content == MP3
    assert FileUpload.check_data_path("voice", str(f)) == len(MP3)


def test_path_outside_traversal_hidden_dir_and_bad_type_rejected(data_dir, tmp_path) -> None:
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(MP3)
    with pytest.raises(UploadRejected, match="вне DATA_DIR"):
        FileUpload.from_data_path("voice", str(outside))
    (data_dir / "a").mkdir()
    with pytest.raises(UploadRejected, match=r"\.\."):
        FileUpload.from_data_path("voice", str(data_dir / "a" / ".." / ".." / "secret.mp3"))
    with pytest.raises(UploadRejected, match="не найден"):
        FileUpload.from_data_path("voice", "nope.mp3")
    with pytest.raises(UploadRejected, match="не файл"):
        FileUpload.from_data_path("voice", str(data_dir / "a"))
    (data_dir / ".git").mkdir()
    (data_dir / ".git" / "x.mp3").write_bytes(MP3)
    with pytest.raises(UploadRejected, match="скрытые"):
        FileUpload.from_data_path("voice", ".git/x.mp3")
    db = data_dir / "state.db"
    db.write_bytes(b"SQLite format 3\x00" + bytes(100))
    with pytest.raises(UploadRejected, match="не разрешён"):
        FileUpload.from_data_path("node_file", str(db))
    with pytest.raises(UploadRejected, match="не разрешён"):
        FileUpload.check_data_path("voice", str(db))
    (data_dir / "fake.mp3").write_bytes(b"MZ executable")
    with pytest.raises(UploadRejected, match="не похоже"):
        FileUpload.check_data_path("voice", "fake.mp3")


def test_symlink_escaping_data_dir_rejected(data_dir, tmp_path) -> None:
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(MP3)
    link = data_dir / "link.mp3"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink недоступен на этой системе")
    with pytest.raises(UploadRejected, match="вне DATA_DIR"):
        FileUpload.from_data_path("voice", str(link))


def test_path_size_limit(data_dir, monkeypatch) -> None:
    f = data_dir / "big.mp3"
    f.write_bytes(MP3)
    small = dataclasses.replace(c.UPLOAD_POLICIES["voice"], max_bytes=100)
    monkeypatch.setitem(c.UPLOAD_POLICIES, "voice", small)
    with pytest.raises(UploadRejected, match="больше лимита"):
        FileUpload.from_data_path("voice", str(f))
    with pytest.raises(UploadRejected, match="больше лимита"):
        FileUpload.check_data_path("voice", str(f))



def _echo_app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/echo")
    async def echo(q: str = "", file: UploadFile = File(...)) -> dict:
        data = await file.read()
        return {"name": file.filename, "type": file.content_type, "n": len(data),
                "sha": hashlib.sha256(data).hexdigest(), "q": q}

    @app.get("/api/ping")
    async def ping() -> dict:
        return {"ok": True}

    return app


async def test_ctx_call_sends_multipart_and_query() -> None:
    ctx = ActionContext(app=_echo_app())
    up = FileUpload.build("voice", "a.mp3", MP3)
    res = await ctx.call("POST", "/echo", params={"q": "7"}, files={"file": up})
    assert res == {"name": "a.mp3", "type": "audio/mpeg", "n": len(MP3),
                   "sha": hashlib.sha256(MP3).hexdigest(), "q": "7"}


async def test_ctx_call_rejects_raw_files_wrong_method_json_and_read_only() -> None:
    ctx = ActionContext(app=_echo_app())
    up = FileUpload.build("voice", "a.mp3", MP3)
    with pytest.raises(UploadRejected, match="только FileUpload"):
        await ctx.call("POST", "/echo", files={"file": ("a.mp3", MP3, "audio/mpeg")})
    with pytest.raises(UploadRejected, match="только FileUpload"):
        await ctx.call("POST", "/echo", files={"file": "C:/Windows/win.ini"})
    with pytest.raises(UploadRejected, match="только FileUpload"):
        await ctx.call("POST", "/echo", files={})
    with pytest.raises(UploadRejected, match="POST/PUT/PATCH"):
        await ctx.call("GET", "/ping", files={"file": up})
    with pytest.raises(UploadRejected, match="без json"):
        await ctx.call("POST", "/echo", json={"a": 1}, files={"file": up})
    with pytest.raises(WriteNotAllowed):
        await ctx.readonly_copy().call("POST", "/echo", files={"file": up})


async def test_ctx_call_revalidates_forged_upload_object() -> None:
    ctx = ActionContext(app=_echo_app())
    forged = FileUpload("file", "evil.exe", b"MZ", "application/x-msdownload", "voice")
    with pytest.raises(UploadRejected, match="не разрешён"):
        await ctx.call("POST", "/echo", files={"file": forged})
    wrong_body = FileUpload("file", "a.mp3", b"MZ not audio", "audio/mpeg", "voice")
    with pytest.raises(UploadRejected, match="не похоже"):
        await ctx.call("POST", "/echo", files={"file": wrong_body})


async def test_ctx_call_total_size_limit(monkeypatch) -> None:
    monkeypatch.setattr(c, "MAX_TOTAL_UPLOAD_BYTES", 600)
    ctx = ActionContext(app=_echo_app())
    a = FileUpload.build("voice", "a.mp3", MP3)
    b = FileUpload.build("voice", "b.mp3", MP3)
    with pytest.raises(UploadRejected, match="суммарный"):
        await ctx.call("POST", "/echo", files={"file": a, "second": b})



def test_upload_actions_flags() -> None:
    from app.services.action_layer.spec import registry
    for aid in ("montage.upload_voice", "montage.upload_music", "montage.upload_image",
                "montage.upload_video", "node.upload_file", "node.upload_check_agent"):
        spec = registry.get(aid)
        assert spec.risk == "destructive" and spec.destructive and not spec.spends_money, aid
        assert spec.reversible is False and spec.destructive_effects is not None, aid
        assert any(p.id == "upload_source" for p in spec.preconditions), aid
    ref = registry.get("montage.upload_ref")
    assert ref.risk == "local_safe" and ref.idempotent is False
    assert registry.get("node.clear_check_agent").destructive



async def _setup(env, frames=2, meta=None) -> tuple[int, object]:
    pid = await env.add_project(frames=frames)
    async with env.factory() as s:
        p = await s.get(Project, pid)
        if meta:
            p.meta = dict(meta)
        await s.commit()
        return pid, p.data_dir


async def _plan(env, steps, *, run=True):
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})
    if r.status_code != 200:
        return r, None
    plan = r.json()["plan_id"]
    if not run:
        return r, plan
    a = await env.client.post(f"{BASE}/plans/{plan}/approve")
    assert a.status_code == 200, a.text
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    return r, (await env.client.get(f"{BASE}/plans/{plan}")).json()


def _write(env, rel, data):
    p = env.tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return str(p)


async def test_upload_voice_overwrites_and_reports_digest(env) -> None:
    pid, ddir = await _setup(env)
    src = _write(env, "inbox/voice.mp3", MP3)
    r, view = await _plan(env, [{"action_id": "montage.upload_voice",
                                 "params": {"project_id": pid, "source_path": src}}])
    pv = r.json()["preview"]
    assert pv["needs_approval"] and pv["steps"][0]["risk"] == "destructive"
    assert pv["destructive_effects"] and pv["steps"][0]["spends_money"] is False
    assert view["status"] == "succeeded", view
    res = view["steps"][0]["result"]
    assert res["uploaded_sha256"] == hashlib.sha256(MP3).hexdigest()
    assert (ddir / "audio" / "voice_full.mp3").read_bytes() == MP3
    meta = (await env.client.get(f"/api/projects/{pid}")).json()["meta"]
    assert meta["montage_voice_path"].endswith("voice_full.mp3")
    second = MP3 + b"v2"
    src2 = _write(env, "inbox/voice2.mp3", second)
    _r, view = await _plan(env, [{"action_id": "montage.upload_voice",
                                  "params": {"project_id": pid, "source_path": src2}}])
    assert view["status"] == "succeeded"
    assert (ddir / "audio" / "voice_full.mp3").read_bytes() == second


async def test_upload_music_from_base64(env) -> None:
    pid, ddir = await _setup(env)
    r, view = await _plan(env, [{"action_id": "montage.upload_music", "params": {
        "project_id": pid, "filename": "bgm.mp3",
        "data_base64": base64.b64encode(MP3).decode()}}])
    assert view["status"] == "succeeded", (r.text, view)
    assert (ddir / "music" / "bgm.mp3").read_bytes() == MP3
    meta = (await env.client.get(f"/api/projects/{pid}")).json()["meta"]
    assert meta["bgm_path"].endswith("bgm.mp3")


async def test_upload_previews_refuse_bad_sources(env, tmp_path_factory) -> None:
    pid, _ = await _setup(env)
    outside = tmp_path_factory.mktemp("outside") / "v.mp3"
    outside.write_bytes(MP3)
    bad_type = _write(env, "inbox/notes.txt", b"hello")
    fake = _write(env, "inbox/fake.mp3", b"not audio")
    cases = [
        {"source_path": str(outside)},
        {"source_path": bad_type},
        {"source_path": fake},
        {"source_path": "../../etc/passwd"},
        {"data_base64": "!!!", "filename": "a.mp3"},
        {"data_base64": base64.b64encode(MP3).decode()},
        {"data_base64": base64.b64encode(MP3).decode(), "filename": "a.exe"},
        {"source_path": bad_type, "data_base64": "QQ==", "filename": "a.mp3"},
        {},
    ]
    for extra in cases:
        r, _ = await _plan(env, [{"action_id": "montage.upload_voice",
                                  "params": {"project_id": pid, **extra}}], run=False)
        assert r.status_code == 422, (extra, r.text)
        assert "upload_source" in str(r.json()), (extra, r.json())
    huge = "A" * 700001
    r, _ = await _plan(env, [{"action_id": "montage.upload_voice", "params": {
        "project_id": pid, "filename": "a.mp3", "data_base64": huge}}], run=False)
    assert r.status_code == 422


async def test_upload_image_archives_previous_and_refuses_missing_frame(env) -> None:
    pid, ddir = await _setup(env)
    src = _write(env, "inbox/a.png", PNG)
    steps = [{"action_id": "montage.upload_image",
              "params": {"project_id": pid, "frame_number": 1, "source_path": src}}]
    _r, view = await _plan(env, steps)
    assert view["status"] == "succeeded", view
    first = list((ddir / "scenes").glob("frame_001_*.png"))
    assert len(first) == 1 and first[0].read_bytes() == PNG
    src2 = _write(env, "inbox/b.png", PNG + b"2")
    _r, view = await _plan(env, [{"action_id": "montage.upload_image", "params": {
        "project_id": pid, "frame_number": 1, "source_path": src2}}])
    assert view["status"] == "succeeded", view
    now = list((ddir / "scenes").glob("frame_001_*.png"))
    assert len(now) == 1 and now[0].read_bytes() == PNG + b"2"
    archived = [p for p in ddir.rglob("*frame_001_*.png") if "old" in p.parts]
    assert len(archived) == 1
    r, _ = await _plan(env, [{"action_id": "montage.upload_image", "params": {
        "project_id": pid, "frame_number": 99, "source_path": src}}], run=False)
    assert r.status_code == 422 and "frame_numbers_exist" in str(r.json())
    jpg = _write(env, "inbox/c.jpg", b"\xff\xd8\xff" + bytes(300))
    r, _ = await _plan(env, [{"action_id": "montage.upload_image", "params": {
        "project_id": pid, "frame_number": 1, "source_path": jpg}}], run=False)
    assert r.status_code == 422 and "upload_source" in str(r.json())


async def test_upload_video_and_ref(env) -> None:
    pid, ddir = await _setup(env)
    src = _write(env, "inbox/c.mp4", MP4)
    _r, view = await _plan(env, [{"action_id": "montage.upload_video", "params": {
        "project_id": pid, "frame_number": 2, "shot": 1, "source_path": src}}])
    assert view["status"] == "succeeded", view
    clips = list((ddir / "videos").glob("clip_002_*.mp4"))
    assert len(clips) == 1 and clips[0].read_bytes() == MP4
    ref_src = _write(env, "inbox/r.png", PNG)
    r, view = await _plan(env, [{"action_id": "montage.upload_ref", "params": {
        "project_id": pid, "frame_number": 1, "kind": "item", "name": "чашка",
        "source_path": ref_src}}], run=False)
    assert r.status_code == 200 and r.json()["preview"]["needs_approval"] is False
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    view = (await env.client.get(f"{BASE}/plans/{plan}")).json()
    assert view["status"] == "succeeded", view
    assert any(p.read_bytes() == PNG for p in ddir.rglob("frame_001_item_*.png"))
    nameless = await env.client.post(f"{BASE}/plans/preview", json={"steps": [{
        "action_id": "montage.upload_ref", "params": {
            "project_id": pid, "frame_number": 1, "source_path": ref_src}}]})
    assert nameless.status_code == 422


CANVAS = {"canvas_graph": {"nodes": [{"id": NODE, "type": "excel_gpt", "data": {}}],
                           "edges": []}}


async def test_node_upload_file_and_check_agent(env) -> None:
    pid, ddir = await _setup(env, meta=CANVAS)
    txt = _write(env, "inbox/notes.txt", "привет".encode("utf-8"))
    _r, view = await _plan(env, [{"action_id": "node.upload_file", "params": {
        "project_id": pid, "node_key": NODE, "source_path": txt}}])
    assert view["status"] == "succeeded", view
    up = ddir / "excel_gpt_uploads" / NODE / "notes.txt"
    assert up.read_text(encoding="utf-8") == "привет"
    agent = _write(env, "inbox/agent.md", b"# check")
    _r, view = await _plan(env, [{"action_id": "node.upload_check_agent", "params": {
        "project_id": pid, "node_key": NODE, "source_path": agent}}])
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"]["file_name"] == "agent.md"
    _r, view = await _plan(env, [{"action_id": "node.clear_check_agent",
                                  "params": {"project_id": pid, "node_key": NODE}}])
    assert view["status"] == "succeeded", view
    assert not (ddir / "excel_gpt_uploads" / NODE / "agent.md").exists()
    r, _ = await _plan(env, [{"action_id": "node.upload_file", "params": {
        "project_id": pid, "node_key": "n_ghost", "source_path": txt}}], run=False)
    assert r.status_code == 422 and "node_exists" in str(r.json())
    r, _ = await _plan(env, [{"action_id": "node.upload_file", "params": {
        "project_id": pid, "node_key": "..", "source_path": txt}}], run=False)
    assert r.status_code == 422 and "node_key_safe" in str(r.json())



class RecCtx:
    def __init__(self, responses=None):
        self.calls, self.responses = [], responses or {}

    async def call(self, method, path, *, params=None, json=None, files=None, timeout=0):
        self.calls.append((method, path, params, files))
        return self.responses.get((method, path), {"ok": True})


async def test_executors_send_validated_file_and_query_only(data_dir) -> None:
    from app.services.action_layer import builtin_wave3 as w3
    reg = ActionRegistry()
    w3.register_wave3(reg)
    (data_dir / "v.mp3").write_bytes(MP3)
    ctx = RecCtx({("POST", "/projects/1/montage-board/upload-voice"):
                  {"ok": True, "path": "p"}})
    out = await reg.get("montage.upload_voice").executor(
        ctx, {"project_id": 1, "source_path": str(data_dir / "v.mp3")})
    method, path, query, files = ctx.calls[0]
    assert (method, path, query) == ("POST", "/projects/1/montage-board/upload-voice", None)
    assert isinstance(files["file"], FileUpload) and files["file"].content == MP3
    assert out["uploaded_sha256"] == hashlib.sha256(MP3).hexdigest() and out["ok"] is True
    ctx2 = RecCtx()
    await reg.get("montage.upload_image").executor(ctx2, {
        "project_id": 1, "frame_number": 3, "shot": 2, "slot": "parent",
        "data_base64": base64.b64encode(PNG).decode(), "filename": "x.png"})
    assert ctx2.calls[0][2] == {"frame_number": 3, "shot": 2, "slot": "parent"}
    with pytest.raises(UploadRejected):
        await reg.get("montage.upload_voice").executor(
            RecCtx(), {"project_id": 1, "source_path": "C:/Windows/win.ini"})
