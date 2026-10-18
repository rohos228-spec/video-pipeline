from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.services import prompt_library
from app.services.action_layer import attachments as att
from app.services.action_layer import planner, store
from app.services.action_layer.spec import ensure_builtin_actions
from app.settings import settings
from tests.action_layer_support import env
from tests.test_action_layer_planner import OP, reply, res, use
from tests.test_action_planner_seed_context import load_seed

BASE = "/api/action-layer"
PID, OTHER = 2, 3
PNG = b"\x89PNG\r\n\x1a\n" + bytes(300)
MP3 = b"ID3" + bytes(300)
MP4 = bytes(4) + b"ftypisom" + bytes(300)
XLSX = b"PK\x03\x04" + bytes(300)


def st(aid: str, deps=None, **params) -> dict:
    return {"action_id": aid, "params": {"project_id": PID, **params}, "depends_on": deps or []}


@pytest_asyncio.fixture
async def live(env, tmp_path, monkeypatch):
    ensure_builtin_actions()
    monkeypatch.setattr(prompt_library, "PROMPTS_ROOT", prompt_library.PROMPTS_ROOT)
    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test-key")
    monkeypatch.setattr(settings, "vibecode_base_url", "https://vibecode.moe/v1")
    monkeypatch.setattr(settings, "action_layer_planner_max_catalog", 24)
    monkeypatch.setattr(att, "_last_cleanup", 0.0)
    await load_seed().seed_realistic(project_id=PID, factory=env.factory, prompts_dir=tmp_path / "prompts")
    async with env.factory() as s:
        from app.models import Project
        s.add(Project(id=OTHER, topic="other", slug="other-proj"))
        await s.commit()
    yield env
    planner.set_transport(None)


async def upload(env, name: str, data: bytes, ctype: str = "application/octet-stream", project_id: int = PID):
    return await env.client.post(f"{BASE}/attachments", data={"project_id": str(project_id)},
                                 files={"file": (name, data, ctype)})


async def ok_upload(env, name: str, data: bytes, ctype: str = "application/octet-stream",
                    project_id: int = PID) -> dict:
    r = await upload(env, name, data, ctype, project_id)
    assert r.status_code == 200, r.text
    return r.json()


async def from_text(env, text: str, ids: list[str], project_id: int = PID):
    return await env.client.post(f"{BASE}/plans/from-text",
                                 json={"text": text, "project_id": project_id, "attachments": ids})


async def rows(env) -> list[att.ActionAttachment]:
    async with env.factory() as s:
        return list((await s.scalars(select(att.ActionAttachment))).all())


def files_under(env) -> list:
    root = env.tmp_path / "attachments"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []



@pytest.mark.parametrize("raw,want", [
    ("../../etc/passwd.png", "passwd.png"),
    ("..\\..\\windows\\system32\\evil.png", "evil.png"),
    ("C:\\Users\\me\\фото кота.PNG", "фото кота.PNG"),
    ("a/b/../c.mp3", "c.mp3"),
    ("CON.png", "_CON.png"),
    ("nul", "_nul"),
    ("com1.txt", "_com1.txt"),
    ("Lpt9.md", "_Lpt9.md"),
    ("ok name?*<>|.wav", "ok name_____.wav"),
    ("  ..hidden..file..png ", "hidden.file.png"),
    ("trailing.dot.", "trailing.dot"),
])
def test_sanitize_name(raw, want) -> None:
    got = att.sanitize_name(raw)
    assert got == want
    assert "/" not in got and "\\" not in got and ".." not in got


@pytest.mark.parametrize("raw", ["", "..", "...", "/", "\\", "a\x00b.png", None])
def test_sanitize_name_rejects_unusable_names(raw) -> None:
    with pytest.raises(att.AttachmentError):
        att.sanitize_name(raw)


def test_sanitize_name_limits_length_and_keeps_extension() -> None:
    got = att.sanitize_name("x" * 400 + ".mp3")
    assert len(got) <= att.MAX_NAME_LEN and got.endswith(".mp3")



async def test_upload_stores_file_under_data_dir_and_returns_digest(live) -> None:
    out = await ok_upload(live, "voice.mp3", MP3, "audio/mpeg")
    assert set(out) == {"attachment_id", "name", "size", "kind", "sha256"}
    assert out["kind"] == "audio" and out["name"] == "voice.mp3" and out["size"] == len(MP3)
    assert out["sha256"] == hashlib.sha256(MP3).hexdigest()
    (path,) = files_under(live)
    assert path.parent == live.tmp_path / "attachments" / str(PID)
    assert path.name == f"{out['attachment_id']}_voice.mp3" and path.read_bytes() == MP3
    (row,) = await rows(live)
    assert row.project_id == PID and row.consumed_at is None and row.sha256 == out["sha256"]
    assert row.rel_path == f"attachments/{PID}/{path.name}"


async def test_upload_traversal_and_reserved_names_stay_inside_the_folder(live) -> None:
    for name in ("../../escape.png", "..\\..\\escape2.png", "CON.png", "/abs/path/x.png"):
        out = await ok_upload(live, name, PNG, "image/png")
        assert "/" not in out["name"] and ".." not in out["name"]
    root = (live.tmp_path / "attachments").resolve()
    for p in files_under(live):
        assert root in p.resolve().parents and p.parent.parent == root
    assert not (live.tmp_path.parent / "escape.png").exists() and not (live.tmp_path / "escape.png").exists()
    assert "_CON.png" in {r.name for r in await rows(live)}


async def test_upload_rejects_wrong_type_mime_and_content(live) -> None:
    r = await upload(live, "tool.exe", b"MZ" + bytes(100))
    assert r.status_code == 415 and r.json()["detail"]["code"] == "type_not_allowed"
    r = await upload(live, "noext", PNG)
    assert r.status_code == 415
    r = await upload(live, "pic.png", PNG, "video/mp4")
    assert r.status_code == 415 and r.json()["detail"]["code"] == "mime_not_allowed"
    r = await upload(live, "pic.png", b"this is not a png at all")
    assert r.status_code == 415 and r.json()["detail"]["code"] == "content_mismatch"
    r = await upload(live, "table.xlsx", b"not a zip")
    assert r.status_code == 415
    r = await upload(live, "data.json", b"{broken")
    assert r.status_code == 415
    r = await upload(live, "pic.png", b"")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "empty_file"
    assert files_under(live) == [] and await rows(live) == []


async def test_upload_accepts_every_allowed_kind(live) -> None:
    samples = {
        "a.png": PNG, "a.jpg": b"\xff\xd8\xff" + bytes(50), "a.webp": b"RIFF\0\0\0\0WEBP" + bytes(20),
        "a.mp4": MP4, "a.mov": bytes(4) + b"moov" + bytes(40), "a.webm": b"\x1a\x45\xdf\xa3" + bytes(40),
        "a.mp3": MP3, "a.wav": b"RIFF\0\0\0\0WAVE" + bytes(20), "a.m4a": MP4, "a.ogg": b"OggS" + bytes(40),
        "a.xlsx": XLSX, "a.xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(40),
        "a.json": b'{"a": 1}', "a.txt": "агент".encode(), "a.md": b"# agent"}
    kinds = {"png": "image", "jpg": "image", "webp": "image", "mp4": "video", "mov": "video", "webm": "video",
             "mp3": "audio", "wav": "audio", "m4a": "audio", "ogg": "audio", "xlsx": "excel", "xls": "excel",
             "json": "json", "txt": "text", "md": "text"}
    for name, data in samples.items():
        out = await ok_upload(live, name, data)
        assert out["kind"] == kinds[name.split(".")[1]], name


async def test_upload_oversize_is_refused_and_leaves_no_file(live, monkeypatch) -> None:
    monkeypatch.setattr(settings, "action_layer_attachment_max_mb", 1)
    big = PNG + bytes(att.MIB)
    r = await upload(live, "big.png", big, "image/png")
    assert r.status_code == 413 and r.json()["detail"]["code"] in ("too_large",)
    assert files_under(live) == [] and await rows(live) == []
    root = live.tmp_path / "attachments"
    assert not list(root.rglob(".*.part"))
    assert (await upload(live, "fits.png", PNG, "image/png")).status_code == 200


async def test_upload_per_kind_cap_is_smaller_than_the_global_one(live) -> None:
    r = await upload(live, "agent.txt", b"a" * (att.MIB + 1), "text/plain")
    assert r.status_code == 413
    assert att.max_bytes_for("text") == att.MIB and att.max_bytes_for("video") == 200 * att.MIB


async def test_content_length_over_the_limit_is_refused_before_parsing(live, monkeypatch) -> None:
    monkeypatch.setattr(att, "max_request_bytes", lambda: 500)
    r = await upload(live, "pic.png", PNG + bytes(2000), "image/png")
    assert r.status_code == 413 and files_under(live) == []


async def test_upload_unknown_project_and_missing_file(live) -> None:
    r = await upload(live, "pic.png", PNG, "image/png", project_id=999)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "project_not_found"
    r = await live.client.post(f"{BASE}/attachments", data={"project_id": "2"})
    assert r.status_code == 422
    r = await live.client.post(f"{BASE}/attachments", files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 422


async def test_rules_endpoint_lists_kinds_and_caps(live) -> None:
    r = await live.client.get(f"{BASE}/attachments/rules")
    assert r.status_code == 200
    kinds = r.json()["kinds"]
    assert set(kinds) == {"image", "video", "audio", "excel", "json", "text"}
    assert ".png" in kinds["image"]["extensions"] and kinds["video"]["max_bytes"] == 200 * att.MIB


async def test_delete_removes_unused_file_but_not_a_consumed_one(live) -> None:
    out = await ok_upload(live, "voice.mp3", MP3, "audio/mpeg")
    r = await live.client.delete(f"{BASE}/attachments/{out['attachment_id']}", params={"project_id": OTHER})
    assert r.status_code == 404 and len(files_under(live)) == 1
    r = await live.client.delete(f"{BASE}/attachments/{out['attachment_id']}", params={"project_id": PID})
    assert r.status_code == 200 and files_under(live) == [] and await rows(live) == []
    out = await ok_upload(live, "voice.mp3", MP3, "audio/mpeg")
    await att.claim([out["attachment_id"]], PID)
    r = await live.client.delete(f"{BASE}/attachments/{out['attachment_id']}")
    assert r.status_code == 409 and len(files_under(live)) == 1
    assert (await live.client.delete(f"{BASE}/attachments/{'0' * 32}")).status_code == 404
    assert (await live.client.delete(f"{BASE}/attachments/not-an-id")).status_code == 404



def sent(fake) -> dict:
    return json.loads(fake.calls[0]["body"]["messages"][1]["content"])


async def test_planner_fills_source_path_from_the_attachment_id(live) -> None:
    out = await ok_upload(live, "voice.mp3", MP3, "audio/mpeg")
    bad = st("montage.upload_voice", source_path="C:/secret/evil.mp3", filename="evil.mp3")
    fake = use(res(reply([bad], expl="Загружаю озвучку.")))
    r = await from_text(live, "загрузи озвучку", [out["attachment_id"]])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_id"] and body["preview"]["ok"], body["preview"]
    assert body["attachments_used"] == [out["attachment_id"]]
    (step,) = body["preview"]["steps"]
    assert not step["errors"] and not step["precondition_failures"]
    plan = (await live.client.get(f"{BASE}/plans/{body['plan_id']}")).json()
    params = plan["steps"][0]["params"]
    rel = f"attachments/{PID}/{out['attachment_id']}_voice.mp3"
    assert params == {"project_id": PID, "source_path": rel}
    assert "voice.mp3" in body["explanation_ru"]
    seen = sent(fake)["attachments"]
    assert seen == [{"id": out["attachment_id"], "name": "voice.mp3", "kind": "audio", "size": len(MP3)}]
    prompt = json.dumps(sent(fake), ensure_ascii=False)
    assert rel not in prompt and "attachments/" not in prompt and str(live.tmp_path) not in prompt
    assert "evil.mp3" not in json.dumps(fake.calls[0]["body"]["messages"][0], ensure_ascii=False)
    (row,) = await rows(live)
    assert row.consumed_at is not None and row.consumed_plan_id == body["plan_id"]


async def test_image_goes_to_the_named_frame(live) -> None:
    out = await ok_upload(live, "frame5.png", PNG, "image/png")
    use(res(reply([st("montage.upload_image", frame_number=5)])))
    r = await from_text(live, "загрузи картинку в кадр 5", [out["attachment_id"]])
    body = r.json()
    assert r.status_code == 200 and body["plan_id"], r.text
    plan = (await live.client.get(f"{BASE}/plans/{body['plan_id']}")).json()
    assert plan["steps"][0]["params"]["frame_number"] == 5
    assert plan["steps"][0]["params"]["source_path"].endswith("_frame5.png")


async def test_wrong_kind_gives_a_clear_russian_clarification_and_keeps_the_file(live) -> None:
    out = await ok_upload(live, "cat.png", PNG, "image/png")
    use(res(reply([st("montage.upload_voice")])))
    r = await from_text(live, "загрузи озвучку", [out["attachment_id"]])
    body = r.json()
    assert r.status_code == 200 and body["plan_id"] is None and body["preview"] is None
    q = body["needs_clarification"]
    assert "не подходит" in q and "cat.png" in q and "аудио" in q and "картинка" in q
    assert body["attachment_request"]["action_id"] == "montage.upload_voice"
    assert body["attachments_used"] == []
    (row,) = await rows(live)
    assert row.consumed_at is None


async def test_wrong_format_for_the_action_is_explained(live) -> None:
    out = await ok_upload(live, "clip.mov", bytes(4) + b"moov" + bytes(40))
    use(res(reply([st("montage.upload_video", frame_number=3)])))
    body = (await from_text(live, "загрузи видео в кадр 3", [out["attachment_id"]])).json()
    assert body["plan_id"] is None and "не подходит" in body["needs_clarification"]
    assert "MP4" in body["needs_clarification"]


async def test_without_attachment_the_old_clarification_stays(live) -> None:
    use(res(reply([st("montage.upload_voice")])))
    body = (await from_text(live, "загрузи озвучку", [])).json()
    assert body["plan_id"] is None and body["needs_clarification"].startswith("Прикрепите файл кнопкой загрузки")
    assert body["attachment_request"]["action_id"] == "montage.upload_voice"


async def test_model_question_is_replaced_by_a_step_when_the_file_is_attached(live) -> None:
    out = await ok_upload(live, "frame7.png", PNG, "image/png")
    use(res(reply([], clar="Прикрепите файл?")))
    body = (await from_text(live, "загрузи картинку в кадр 7", [out["attachment_id"]])).json()
    assert body["plan_id"], body
    plan = (await live.client.get(f"{BASE}/plans/{body['plan_id']}")).json()
    assert plan["steps"][0]["action_id"] == "montage.upload_image"
    assert plan["steps"][0]["params"]["frame_number"] == 7


async def test_missing_frame_is_asked_not_guessed(live) -> None:
    out = await ok_upload(live, "pic.png", PNG, "image/png")
    use(res(reply([], clar="Куда?")))
    body = (await from_text(live, "загрузи картинку", [out["attachment_id"]])).json()
    assert body["plan_id"] is None and "кадр" in body["needs_clarification"]
    (row,) = await rows(live)
    assert row.consumed_at is None


async def test_two_matching_files_ask_which_one(live) -> None:
    a = await ok_upload(live, "one.mp3", MP3, "audio/mpeg")
    b = await ok_upload(live, "two.mp3", MP3, "audio/mpeg")
    use(res(reply([st("montage.upload_voice")])))
    body = (await from_text(live, "загрузи озвучку", [a["attachment_id"], b["attachment_id"]])).json()
    assert body["plan_id"] is None and set(body["clarification_options"]) == {"one.mp3", "two.mp3"}
    use(res(reply([st("montage.upload_voice")])))
    body = (await from_text(live, "загрузи озвучку. Уточнение: two.mp3",
                            [a["attachment_id"], b["attachment_id"]])).json()
    assert body["plan_id"] and body["attachments_used"] == [b["attachment_id"]]
    rows_ = {r.name: r for r in await rows(live)}
    assert rows_["two.mp3"].consumed_at is not None and rows_["one.mp3"].consumed_at is None


async def test_cross_project_id_is_refused_without_calling_the_model(live) -> None:
    out = await ok_upload(live, "voice.mp3", MP3, "audio/mpeg", project_id=OTHER)
    fake = use(res(reply([st("montage.upload_voice")])))
    r = await from_text(live, "загрузи озвучку", [out["attachment_id"]], project_id=PID)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "attachment_wrong_project"
    assert fake.calls == []


async def test_unknown_malformed_and_projectless_ids_are_refused(live) -> None:
    fake = use(res(reply([st("montage.upload_voice")])))
    r = await from_text(live, "загрузи озвучку", ["0" * 32])
    assert r.status_code == 404 and r.json()["detail"]["code"] == "attachment_not_found"
    r = await from_text(live, "загрузи озвучку", ["../../etc/passwd"])
    assert r.status_code == 404
    out = await ok_upload(live, "voice.mp3", MP3, "audio/mpeg")
    r = await live.client.post(f"{BASE}/plans/from-text",
                               json={"text": "загрузи озвучку", "attachments": [out["attachment_id"]]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "attachments_need_project"
    r = await live.client.post(f"{BASE}/plans/from-text", json={
        "text": "x", "project_id": PID, "attachments": [out["attachment_id"]] * 1 + ["a" * 32] * 5})
    assert r.status_code == 422
    assert fake.calls == []


async def test_consumed_id_cannot_be_used_twice(live) -> None:
    out = await ok_upload(live, "voice.mp3", MP3, "audio/mpeg")
    use(res(reply([st("montage.upload_voice")])), res(reply([st("montage.upload_voice")])))
    first = await from_text(live, "загрузи озвучку", [out["attachment_id"]])
    assert first.status_code == 200 and first.json()["plan_id"]
    second = await from_text(live, "загрузи озвучку", [out["attachment_id"]])
    assert second.status_code == 409 and second.json()["detail"]["code"] == "attachment_consumed"
    assert (await live.client.delete(f"{BASE}/attachments/{out['attachment_id']}")).status_code == 409


async def test_claim_is_atomic_for_two_attachments(live) -> None:
    a = await ok_upload(live, "one.mp3", MP3, "audio/mpeg")
    b = await ok_upload(live, "two.mp3", MP3, "audio/mpeg")
    await att.claim([a["attachment_id"]], PID)
    with pytest.raises(att.AttachmentError) as exc:
        await att.claim([a["attachment_id"], b["attachment_id"]], PID)
    assert exc.value.code == "attachment_consumed"
    assert {r.name: r.consumed_at is not None for r in await rows(live)} == {"one.mp3": True, "two.mp3": False}


async def test_orphan_watcher_is_alive_until_the_fixture_drains_it(live) -> None:
    """Сторож сироты живёт до конца теста. Фикстура обязана его дождаться."""
    import asyncio

    from app.services.action_layer import executor
    from app.services.action_layer.spec import ActionSpec

    async def _noop(_ctx, _params):
        return {}

    spec = ActionSpec(
        id="zz.leak", title_ru="t", description_ru="t",
        params_schema={"type": "object"}, executor=_noop, poll_interval_s=30,
    )
    executor._hold_project(
        PID, "leak-plan", spec,
        {"start": {"id": "job"}, "ctx": None, "params": {}},
        lambda _state: None,
    )
    names = {task.get_name() for task in asyncio.all_tasks() if not task.done()}
    assert any(name.startswith("action-layer-orphan-") for name in names)


async def test_invalid_plan_releases_the_file(live) -> None:
    out = await ok_upload(live, "frame99.png", PNG, "image/png")
    use(res(reply([st("montage.upload_image", frame_number=99)])))
    r = await from_text(live, "загрузи картинку в кадр 99", [out["attachment_id"]])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "plan_invalid"
    (row,) = await rows(live)
    assert row.consumed_at is None


async def test_attachment_not_used_by_the_plan_is_not_consumed(live) -> None:
    out = await ok_upload(live, "voice.mp3", MP3, "audio/mpeg")
    use(res(reply([st("montage.get_board")], expl="Читаю доску.")))
    r = await from_text(live, "покажи доску", [out["attachment_id"]])
    assert r.status_code == 200 and r.json()["attachments_used"] == []
    (row,) = await rows(live)
    assert row.consumed_at is None


async def test_file_removed_from_disk_is_reported(live) -> None:
    out = await ok_upload(live, "voice.mp3", MP3, "audio/mpeg")
    files_under(live)[0].unlink()
    fake = use(res(reply([st("montage.upload_voice")])))
    r = await from_text(live, "загрузи озвучку", [out["attachment_id"]])
    assert r.status_code == 410 and r.json()["detail"]["code"] == "attachment_missing" and fake.calls == []


async def test_planner_function_fills_path_with_a_synthetic_attachment(live) -> None:
    ref = att.synthetic_ref(PID, "audio", "voice.mp3", 1000)
    use(res(reply([st("montage.upload_voice")])))
    out = await planner.plan_from_text("загрузи озвучку", operator=OP, project_id=PID, attachments=[ref])
    assert out.reply.steps[0]["params"]["source_path"] == ref.rel_path
    assert out.reply.attachments_used == [ref.id] and out.meta["attachments_used"] == [ref.id]


async def test_two_upload_steps_get_two_different_files(live) -> None:
    voice = att.synthetic_ref(PID, "audio", "voice.mp3", 1000)
    pic = att.synthetic_ref(PID, "image", "pic.png", 1000)
    use(res(reply([st("montage.upload_voice"), st("montage.upload_image", frame_number=4)])))
    out = await planner.plan_from_text("загрузи озвучку и картинку в кадр 4", operator=OP, project_id=PID,
                                       attachments=[pic, voice])
    paths = [s["params"]["source_path"] for s in out.reply.steps]
    assert paths == [voice.rel_path, pic.rel_path] and sorted(out.reply.attachments_used) == sorted(
        [voice.id, pic.id])
    use(res(reply([st("montage.upload_voice"), st("montage.upload_music")])))
    out = await planner.plan_from_text("загрузи озвучку и музыку", operator=OP, project_id=PID,
                                       attachments=[voice])
    assert out.reply.needs_clarification and "нужен ещё один файл" in out.reply.needs_clarification
    assert out.reply.steps == [] and out.reply.attachments_used == []



async def _age(env, att_id: str, days: float) -> None:
    async with env.factory() as s:
        row = await s.get(att.ActionAttachment, att_id)
        row.created_at = datetime.utcnow() - timedelta(days=days)
        await s.commit()


async def test_cleanup_removes_only_old_unused_attachments(live) -> None:
    old = await ok_upload(live, "old.mp3", MP3, "audio/mpeg")
    used = await ok_upload(live, "used.mp3", MP3, "audio/mpeg")
    fresh = await ok_upload(live, "fresh.mp3", MP3, "audio/mpeg")
    await att.claim([used["attachment_id"]], PID)
    await _age(live, old["attachment_id"], 10)
    await _age(live, used["attachment_id"], 10)
    part = live.tmp_path / "attachments" / str(PID) / ".dead.part"
    part.write_bytes(b"x")
    import os
    os.utime(part, (1, 1))
    res_ = await att.cleanup_unused()
    assert res_["removed"] == 1 and res_["bytes"] == len(MP3) and res_["orphans"] == 1
    names = {r.name for r in await rows(live)}
    assert names == {"used.mp3", "fresh.mp3"} and not part.exists()
    assert sorted(p.name for p in files_under(live)) == sorted(
        [f"{used['attachment_id']}_used.mp3", f"{fresh['attachment_id']}_fresh.mp3"])


async def test_cleanup_respects_settings_and_explicit_age(live, monkeypatch) -> None:
    a = await ok_upload(live, "a.mp3", MP3, "audio/mpeg")
    await _age(live, a["attachment_id"], 3)
    assert (await att.cleanup_unused())["removed"] == 0
    monkeypatch.setattr(settings, "action_layer_attachment_retention_days", 0)
    assert (await att.cleanup_unused())["removed"] == 0
    monkeypatch.setattr(settings, "action_layer_attachment_retention_days", 2)
    assert (await att.cleanup_unused())["removed"] == 1
    assert files_under(live) == []
    b = await ok_upload(live, "b.mp3", MP3, "audio/mpeg")
    await _age(live, b["attachment_id"], 1)
    assert (await att.cleanup_unused(max_age_days=0.5))["removed"] == 1


async def test_cleanup_tolerates_a_file_that_is_already_gone(live) -> None:
    a = await ok_upload(live, "a.mp3", MP3, "audio/mpeg")
    files_under(live)[0].unlink()
    await _age(live, a["attachment_id"], 30)
    assert (await att.cleanup_unused())["removed"] == 1 and await rows(live) == []


async def test_upload_triggers_throttled_cleanup(live) -> None:
    old = await ok_upload(live, "old.mp3", MP3, "audio/mpeg")
    await _age(live, old["attachment_id"], 30)
    att._last_cleanup = 0.0
    await ok_upload(live, "new.mp3", MP3, "audio/mpeg")
    assert {r.name for r in await rows(live)} == {"new.mp3"}



def test_fixture_has_attachment_cases_and_the_seed_builds_fake_attachments() -> None:
    cases = [c for c in json.loads((Path(__file__).parent / "fixtures" / "action_planner_eval.json").read_text(
        encoding="utf-8"))["cases"] if (c.get("context") or {}).get("attachments")]
    assert len(cases) >= 4 and {c["outcome"] for c in cases} == {"plan", "clarify"}
    seed = load_seed()
    for c in cases:
        refs = seed.fake_attachments(PID, c["context"]["attachments"])
        assert len(refs) == len(c["context"]["attachments"])
        assert all(r.rel_path.startswith(f"attachments/{PID}/") and r.project_id == PID for r in refs)
        assert refs == seed.fake_attachments(PID, c["context"]["attachments"])
    assert seed.fake_attachments(PID, None) == [] and seed.fake_attachments(PID, []) == []
