"""Tests of the pure part of ``scripts/e2e_full_chain.py`` (F10 dry). No server process, no network, nothing paid."""
from __future__ import annotations

import importlib.util
import json
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "e2e_full_chain.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("e2e_full_chain_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(url: str, *, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    r = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


# ───────── levels and statuses ─────────

def test_levels_parse_and_scope(mod):
    assert mod.LEVELS == ("A", "B", "C", "D", "E")
    assert mod.parse_until("c") == "C"
    with pytest.raises(ValueError):
        mod.parse_until("F")
    assert mod.node_in_scope("plan", "A") and not mod.node_in_scope("images", "A")
    assert mod.node_in_scope("images", "B") and not mod.node_in_scope("videos", "B")
    assert mod.node_in_scope("sfx_gen", "D") and not mod.node_in_scope("assemble", "D")
    assert mod.node_in_scope("assemble", "E")
    assert not mod.node_in_scope("topic", "E")      # the config node is not a work node


def test_every_work_node_of_the_default_graph_has_a_level(mod):
    from app.orchestrator.default_graph import default_graph
    nodes, _edges = default_graph()
    for n in nodes:
        if n["type"] in ("topic", "storage"):
            continue
        assert n["type"] in mod.NODE_LEVEL, f"node type {n['type']} of the default graph has no level in the stand"


def test_status_table_is_real_and_in_graph_order(mod):
    from app.models import ProjectStatus
    values = {s.value for s in ProjectStatus}
    table = mod.status_order_table()
    assert all(s in values for s in table), [s for s in table if s not in values]
    assert len(set(table)) == len(table)
    for lvl, end in mod.STATUS_END.items():
        assert end in table
    ends = [mod.status_rank(mod.STATUS_END[lvl]) for lvl in mod.LEVELS]
    assert ends == sorted(ends) and len(set(ends)) == 5
    # the graph order of the default workflow: excel_gpt(1) before hero, hero -> items -> excel_gpt(2) -> image prompts
    r = mod.status_rank
    assert r("frames_ready") < r("enriching_1") < r("generating_hero") < r("generating_items") < r("enriching_2") \
        < r("generating_image_prompts") < r("generating_images") < r("generating_animation_prompts") \
        < r("generating_videos") < r("generating_audio") < r("generating_music") < r("sfx_planning") \
        < r("generating_sfx") < r("assembling") < r("assembled")


def test_level_reached_and_final_needs_a_human(mod):
    assert mod.level_reached(["new", "planning"]) is None
    assert mod.level_reached(["new", "enrich_1_ready"]) == "A"
    assert mod.level_reached(["images_ready"]) == "B"
    assert mod.level_reached(["videos_ready", "images_ready"]) == "C"
    assert mod.level_reached(["sfx_ready"]) == "D"
    assert mod.level_reached(["assembled"]) == "E"
    # E only counts with a verified final AND a human approval
    assert mod.level_after(["assembled"], True, True) == "E"
    assert mod.level_after(["assembled"], True, False) == "D"
    assert mod.level_after(["assembled"], False, True) == "D"


def test_manual_start_map_matches_the_production_hold(mod):
    """After an excel_gpt node the production code parks the pipeline for the operator's start (by design)."""
    src = (ROOT / "app" / "orchestrator" / "steps" / "enrich_xlsx.py").read_text(encoding="utf-8")
    assert "def _hold_pipeline_after_excel_gpt" in src and "arm_auto_await_manual_start(project)" in src
    assert mod.Chain.MANUAL_START_AFTER == {"enrich_1_ready": "hero", "enrich_2_ready": "img_pr"}


# ───────── pure helpers ─────────

def test_russian_titles_and_counts(mod):
    assert mod.has_russian("Запуск ноды") and not mod.has_russian("Node started")
    items = [{"title_ru": "Нода готова"}, {"title_ru": "node done"}, {"title_ru": None}, {}]
    assert mod.english_only_titles(items) == ["node done", "None", "None"]
    assert mod.count_by([{"k": "a"}, {"k": "b"}, {"k": "a"}], "k") == {"a": 2, "b": 1}
    assert mod.duplicate_keys([{"ref": "x"}, {"ref": "x"}, {"ref": ""}, {"ref": "y"}], "ref") == ["x"]


def test_expected_counts_are_one_paid_request_per_unit(mod):
    e = mod.expected_counts_for("E", frames=3, sfx_events=3)
    assert e == {"outsee_image": 3, "outsee_video": 3, "tts": 1, "kie_music": 1, "sfx": 3}
    assert mod.expected_counts_for("A", 3, 3) == {"outsee_image": 0, "outsee_video": 0, "tts": 0, "kie_music": 0, "sfx": 0}
    b = mod.expected_counts_for("B", 3, 3)
    assert b["outsee_image"] == 3 and b["outsee_video"] == 0 and b["tts"] == 0
    c = mod.expected_counts_for("C", 3, 3)
    assert c["outsee_video"] == 3 and c["tts"] == 0
    # the live estimate gives the owner ceilings from the same numbers
    est = mod.live_estimate(3, 3)
    assert est["outsee_image"] == 3 and est["outsee_video"] == 3 and est["elevenlabs_tts"] == 1
    assert est["elevenlabs_sfx"] == 3 and est["kie_suno_music"] == 1
    assert est["text_llm"] == 16 == 9 + 2 * 3 + 1 and est["worst_case_if_a_result_is_rejected"]["elevenlabs_sfx"] == 9
    assert mod.live_estimate(10, 8)["text_llm"] == 9 + 2 * 10 + 2


def test_tts_is_one_request_for_the_whole_voiceover_in_production(mod):
    src = (ROOT / "app" / "services" / "frame_audio.py").read_text(encoding="utf-8")
    seg = src[src.index("async def synthesize_per_frame_audio"):]
    seg = seg[:seg.index("\nasync def align_existing_voice_full")]
    assert seg.count("synthesize_speech(") == 1 and "full_text" in seg


def test_compare_wire_to_fake(mod):
    assert mod.compare_wire_to_fake({"llm": 3}, {"llm": 3}) == {}
    assert mod.compare_wire_to_fake({"llm": 3, "tts": 1}, {"llm": 3, "tts": 2}) == {"tts": (1, 2)}
    assert mod.compare_wire_to_fake({}, {"outsee_image": 1}) == {"outsee_image": (0, 1)}


def test_audit_gaps_match_method_path_and_status(mod):
    muts = [{"method": "POST", "path": "/api/projects", "status": 201},
            {"method": "POST", "path": "/api/projects/2/steps/plan/run", "status": 200},
            {"method": "PATCH", "path": "/api/projects/2", "status": 200}]
    audit = [{"method": "POST", "path": "/api/projects", "status_code": 201, "actor": "operator", "outcome": "ok"},
             {"method": "POST", "path": "/api/projects/2/steps/plan/run", "status_code": 200, "actor": "operator", "outcome": "ok"}]
    gaps = mod.audit_gaps(muts, audit)
    assert gaps == [muts[2]]
    assert mod.audit_gaps(muts[:2], audit) == []
    # one audit row cannot cover two identical calls
    assert len(mod.audit_gaps([muts[0], muts[0]], audit)) == 1
    assert mod.audit_unattributed([{"actor": "", "outcome": "ok"}, {"actor": "operator", "outcome": "ok"}]) == \
        [{"actor": "", "outcome": "ok"}]


def test_ledger_summary_and_expectation(mod):
    rows = [
        {"kind": "estimated", "provider": "vibecode", "project_id": 2, "run_id": "native:2:a", "usd": 0.0, "credits": 0.0, "note": "chat", "ref": ""},
        {"kind": "estimated", "provider": "vibecode", "project_id": None, "run_id": "", "usd": 0.0, "credits": 0.0, "note": "chat", "ref": ""},
        {"kind": "estimated", "provider": "outsee", "project_id": 2, "run_id": "native:2:b", "usd": 0.0, "credits": 0.0,
         "note": "image gpt-image-2 ok cost_unknown=true", "ref": ""},
        {"kind": "event", "provider": "x", "project_id": None, "run_id": "", "usd": 1.0, "credits": 1.0, "note": "", "ref": ""},
    ]
    s = mod.summarize_ledger(rows, 2)
    assert s["rows"] == 3 and s["without_project"] == 1 and s["without_run"] == 1 and s["other_project"] == 0
    assert s["by_provider"] == {"outsee": 1, "vibecode": 2}
    assert mod.ledger_expectation({"llm": 12, "outsee_image": 3, "outsee_video": 3, "tts": 1, "sfx": 3, "kie_music": 1}) == \
        {"vibecode": 12, "outsee": 6, "elevenlabs": 4, "kie": 1}
    assert mod.note_family("tts mp3 dur=1") == "audio" and mod.note_family("sfx dur=1.0") == "sfx_gen"
    assert mod.note_family("create suno suno-music task=x") == "music"


def test_verdict_text(mod):
    assert mod.verdict_of([], {}, "E", "E") == "PASS"
    assert mod.verdict_of([], {"F10-D3": ["x"]}, "E", "E").startswith("PASS (with 1 KNOWN")
    assert mod.verdict_of(["x"], {}, "E", "E") == "FAIL"
    assert mod.verdict_of([], {}, "C", "E") == "FAIL"         # a lower level than asked is never a pass
    assert mod.verdict_of([], {}, None, "A") == "FAIL"


def test_report_known_defects_do_not_fail_unless_strict(mod):
    R = mod.Report()
    assert R.check("ordinary", True)
    assert R.check("known broken", False, "x", known="F10-D3")           # KNOWN is tolerated
    assert R.failed == [] and list(R.known_defects) == ["F10-D3"]
    strict = mod.Report(strict=True)
    strict.check("known broken", False, "x", known="F10-D3")
    assert strict.failed == ["known broken"]


# ───────── request classification and fake credentials ─────────

def test_classify_request(mod):
    c = mod.classify_request
    assert c("POST", "127.0.0.1", "/v1/chat/completions") == "llm"
    assert c("POST", "x", "/api/v1/images/generate") == "outsee_image"
    assert c("POST", "x", "/api/v1/videos/generate") == "outsee_video"
    assert c("POST", "x", "/v1/text-to-speech/abc123") == "tts"
    assert c("POST", "x", "/v1/sound-generation") == "sfx"
    assert c("POST", "x", "/api/v1/generate") == "kie_music"
    assert c("GET", "x", "/api/v1/generations/fake-1") == "outsee_free"
    assert c("GET", "x", "/api/v1/generate/record-info") == "kie_free"
    assert c("GET", "x", "/files/x.png") == "download"
    assert c("POST", "x", "/hostfake/catbox.moe") == "upload"
    assert c("GET", "x", "/api/v1/images/generate") == "other"          # a GET is not a paid POST


def test_fake_keys_are_not_secret_shaped(mod, tmp_path):
    (tmp_path / "k.txt").write_text(json.dumps(mod.FAKE_KEYS) + "\nBearer " + mod.FAKE_KEYS["outsee"], encoding="utf-8")
    rep = mod.secrets_in(tmp_path)
    assert rep["total_matches"] == 0, rep


def test_child_endpoints_are_the_production_ones(mod):
    """The paths the fake serves are the paths the production clients use (guards against drift)."""
    outsee = (ROOT / "app" / "bots" / "outsee_http.py").read_text(encoding="utf-8")
    assert mod.OUTSEE_IMAGES in outsee and mod.OUTSEE_VIDEOS in outsee and mod.OUTSEE_STATUS in outsee
    eleven = (ROOT / "app" / "services" / "elevenlabs_api.py").read_text(encoding="utf-8")
    assert "text-to-speech/" in eleven and 'ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io/v1"' in eleven
    assert mod.SFX_PATH.split("/")[-1] in (ROOT / "app" / "services" / "sfx_gen.py").read_text(encoding="utf-8")
    for host in mod.PUBLIC_IMAGE_HOSTS:
        assert host in outsee, host


# ───────── the fake provider server ─────────

@pytest.fixture()
def fake(mod, tmp_path):
    f = mod.FakeProviders(_free_port(), tmp_path, lambda ctx, srv: "x", voice_wav=None)
    f.start()
    yield f
    f.stop()


def _bearer(key):
    return {"Authorization": f"Bearer {key}"}


def test_fake_chat_streams_and_counts(mod, fake):
    body = json.dumps({"model": "m", "stream": True, "messages": [{"role": "user", "content": "привет"}]}).encode()
    code, raw, _ = _req(fake.base + "/v1/chat/completions", method="POST", body=body, headers=_bearer(mod.FAKE_KEYS["vibecode"]))
    assert code == 200 and b"data: [DONE]" in raw
    assert fake.counts["llm"] == 1
    code, raw, _ = _req(fake.base + "/v1/chat/completions", method="POST", body=body, headers=_bearer("wrong"))
    assert code == 401 and fake.counts["llm"] == 1 and fake.extra["auth_failures"] == 1
    # the request text was written for the report
    assert (fake.work / "llm_requests" / "0001.json").is_file()


def test_fake_outsee_image_and_video_lifecycle(mod, fake):
    h = _bearer(mod.FAKE_KEYS["outsee"])
    code, raw, _ = _req(fake.base + mod.OUTSEE_IMAGES, method="POST", body=b'{"model":"gpt-image-2","prompt":"p"}', headers=h)
    assert code == 200 and json.loads(raw)["id"].startswith("fakeimg-")
    code, raw, _ = _req(fake.base + mod.OUTSEE_VIDEOS, method="POST", body=b'{"model":"veo-3-1-lite","prompt":"p"}', headers=h)
    vid = json.loads(raw)["id"]
    assert vid.startswith("fakevid-")
    code, raw, _ = _req(fake.base + mod.OUTSEE_STATUS + vid, headers=h)
    url = json.loads(raw)["result_urls"][0]
    assert url.endswith(".mp4")
    code, data, _ = _req(url)
    assert code == 200 and len(data) > 1000
    assert fake.counts["outsee_image"] == 1 and fake.counts["outsee_video"] == 1
    code, _raw, _ = _req(fake.base + mod.OUTSEE_IMAGES, method="POST", body=b"{}", headers=_bearer("nope"))
    assert code == 401 and fake.counts["outsee_image"] == 1


def test_fake_kie_music_lifecycle(mod, fake):
    h = _bearer(mod.FAKE_KEYS["kie"])
    code, raw, _ = _req(fake.base + mod.KIE_BALANCE, headers=h)
    assert json.loads(raw)["data"] > 1000
    code, raw, _ = _req(fake.base + mod.KIE_CREATE, method="POST", body=b'{"model":"V5_5","instrumental":true}', headers=h)
    task = json.loads(raw)["data"]["taskId"]
    code, raw, _ = _req(fake.base + mod.KIE_POLL + "?taskId=" + task, headers=h)
    assert json.loads(raw)["data"]["status"] == "PENDING"
    code, raw, _ = _req(fake.base + mod.KIE_POLL + "?taskId=" + task, headers=h)
    d = json.loads(raw)["data"]
    assert d["status"] == "SUCCESS" and d["response"]["sunoData"][0]["audioUrl"].endswith(".mp3")
    assert fake.counts["kie_music"] == 1


def test_fake_elevenlabs_sfx_and_tts(mod, fake):
    h = {**_bearer(mod.FAKE_KEYS["elevenlabs"]), "Content-Type": "application/json"}
    code, data, _ = _req(fake.base + mod.SFX_PATH, method="POST", body=b'{"text":"whoosh","duration_seconds":1}', headers=h)
    assert code == 200 and len(data) > 500 and fake.counts["sfx"] == 1
    clip = fake.work / "sfx_probe.mp3"
    clip.write_bytes(data)
    assert abs(mod.loc.ffprobe(clip)["duration"] - 1.0) < 0.35          # production verifies the clip length it asked for
    # the music clip is a different, longer file (they used to share one cache: SFX came back 12 s long)
    code, music, _ = _req(fake.base + "/files/music.mp3")
    clip.write_bytes(music)
    assert mod.loc.ffprobe(clip)["duration"] > 10
    code, data, _ = _req(fake.base + "/v1/text-to-speech/voiceid", method="POST",
                         body=json.dumps({"text": "Привет, мир.", "model_id": "m"}).encode(), headers=h)
    assert code == 200 and len(data) > 500 and fake.counts["tts"] == 1


def test_fake_public_image_host_answers_every_upload_shape(mod, fake):
    code, raw, _ = _req(fake.base + "/hostfake/catbox.moe", method="POST", body=b"--x\r\n", headers={})
    url = raw.decode()
    assert code == 200 and url.startswith("http://127.0.0.1:")
    code, data, _ = _req(url)
    assert code == 200 and data[:4] == b"\x89PNG"
    code, raw, _ = _req(fake.base + "/hostfake/uguu.se", method="POST", body=b"--x\r\n")
    assert json.loads(raw)["files"][0]["url"].startswith("http://127.0.0.1:")
    assert fake.extra["upload"] == 2 and sum(fake.counts.values()) == 0     # uploads are free, never a paid bucket


def test_fake_unknown_path_is_counted_not_silent(mod, fake):
    code, _raw, _ = _req(fake.base + "/nope")
    assert code == 404 and fake.extra["not_found"] == 1


def test_oracle_error_is_visible_not_silent(mod, tmp_path):
    def boom(ctx, srv):
        raise RuntimeError("oracle bug")
    f = mod.FakeProviders(_free_port(), tmp_path, boom)
    f.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": "x"}]}).encode()
        code, raw, _ = _req(f.base + "/v1/chat/completions", method="POST", body=body, headers=_bearer(mod.FAKE_KEYS["vibecode"]))
        assert code == 200 and b"ORACLE ERROR" in raw
    finally:
        f.stop()


# ───────── the text oracle answers in the shape production parses ─────────

def test_oracle_plan_script_split_shapes(mod):
    o = mod.TextOracle()
    plan = json.loads(o.answer_plan("", {}))
    assert plan["ops"][0]["target"] == "project" and "общий_план" in plan["ops"][0]["fields"]
    from app.services import xlsx_step_runners as xsr
    assert len(xsr.extract_general_plan_from_gpt_reply(o.answer_plan("", {}))) > 200
    script = json.loads(o.answer_script("", {}))
    assert len(script["ops"][0]["fields"]["закадровый_текст"]) >= 200      # make_script refuses shorter
    split = json.loads(o.answer_split("", {}))
    frames = split["ops"][0]["frames"]
    assert split["ops"][0]["target"] == "replace_frames" and len(frames) == 3 and all(f["закадр"] for f in frames)
    # the split covers the script word for word (production checks coverage)
    assert " ".join(f["закадр"] for f in frames) == mod.SCRIPT_TEXT


def test_oracle_classifies_by_the_hints_production_appends(mod):
    from app.services import xlsx_step_runners as xsr
    c = mod.TextOracle.classify
    assert c("тема\n" + xsr._PLAN_DB_HINT) == "plan"
    assert c("тема\n" + xsr._SCRIPT_DB_HINT) == "script"
    assert c("тема\n" + xsr._SPLIT_DB_HINT) == "split"
    assert c("тема\n" + xsr._IMG_PR_DB_HINT) == "image_prompts"
    assert c("# Доп. работа с Excel #\n\nx") == "excel_gpt"
    assert c("# Агент: промты анимации Veo 3.1 Fast (Relax) → DB\nx") == "animation_prompts"
    assert c('{"decision": "approved" | "regen"}') == "unknown"            # needs its criteria block too
    assert c('"decision": "approved" ... "criteria": {...}') == "verdict"
    assert c("что-то другое") == "unknown"


def test_oracle_frame_uuids_only_from_attachments(mod):
    text = ('Пример: "uuid": "aaaaaaaaaaaaaaaaaaaaaaaa"\n## Приложенные файлы\n===== f.json =====\n'
            '{"frames":[{"number":1,"uuid":"0123456789abcdef01234567"},{"number":2,"uuid":"fedcba9876543210fedcba98"}]}')
    assert mod.TextOracle.frame_uuids(text) == ["0123456789abcdef01234567", "fedcba9876543210fedcba98"]
    o = mod.TextOracle()
    ops = json.loads(o.answer_image_prompts(text, {}))["ops"]
    assert [x["frame_uuid"] for x in ops] == ["0123456789abcdef01234567", "fedcba9876543210fedcba98"]
    assert all(len(x["fields"]["промт_картинки"]) >= 200 for x in ops)


def test_oracle_animation_two_phases(mod):
    o = mod.TextOracle()
    assert o.answer_animation_prompts("мастер-промт без кадров", {}).startswith("Ready")
    batch = "Позиция 1 метка=F001\nframe_uuid: 0123456789abcdef01234567\nID изображения: x"
    ops = json.loads(o.answer_animation_prompts(batch, {}))["ops"]
    assert ops[0]["frame_uuid"] == "0123456789abcdef01234567" and ops[0]["fields"]["промт_видео"]


def test_oracle_verdict_quotes_the_checked_text_literally(mod):
    text = ('{"decision": "approved" | "regen",\n "criteria": {\n "time_structure": {"verdict": "pass|weak|fail"},'
            '\n "hook_strength": {"verdict": "..."}}}\nПЛАН:\n' + mod.PLAN_TEXT)
    v = json.loads(mod.TextOracle.answer_verdict(text, {}))
    assert v["decision"] == "approved" and set(v["criteria"]) == {"time_structure", "hook_strength"}
    for c in v["criteria"].values():
        assert c["evidence_quote"] and c["evidence_quote"] in text and c["verdict"] == "pass"
    from app.services.gpt_verdict_review import parse_gpt_verdict
    assert parse_gpt_verdict(json.dumps(v)).approved is True


def test_stand_text_is_not_rejected_by_production_validators(mod):
    from app.services.plan_validation import is_meaningful_general_plan
    assert is_meaningful_general_plan(mod.PLAN_TEXT)
    assert '"' not in mod.IMAGE_PROMPT_TEXT                       # the img_pr contract forbids ASCII double quotes
    assert len(mod.IMAGE_PROMPT_TEXT) >= 200
    assert "NO VOICE" in mod.ANIMATION_PROMPT_TEXT


# ───────── the seams of the stand are bound to real production names ─────────

def test_seam_targets_exist_in_production(mod):
    from app.orchestrator import auto_advance, pipeline
    from app.services import advance_runner
    import app.project_db as pdb
    assert callable(auto_advance.maybe_auto_advance) and callable(pipeline.advance_project)
    assert advance_runner.advance_project is pipeline.advance_project
    for name in ("project_db_session_scope", "pull_master_runtime_into_project", "push_runtime_to_master"):
        assert callable(getattr(pdb, name))
    assert mod.SEAMS == ("auto_on_project_db", "store_mirror", "hitl_to_master")
    cfg = mod.build_cfg(Path("w"), 1, 2, 3.0)
    assert cfg["seams"] == list(mod.SEAMS)
    assert mod.build_cfg(Path("w"), 1, 2, 3.0, seams=[])["seams"] == []


def test_findings_point_at_lines_that_still_say_that(mod):
    """A finding without a living reference is noise: each ``where`` line must still contain the quoted code."""
    # F10-D3, D4, D9 и D10 исправлены (W32): ссылки на строки больше не нужны, стенд сам покажет возврат дефекта.
    checks = {
        "F10-D1": [("app/main.py", "maybe_auto_advance_for_worker(s, ap, bot)"),
                   ("app/orchestrator/auto_advance.py", "await clamp_status_to_data(session, project)"),
                   ("app/services/step_data_guard.py", "статус опережал данные")],
        "F10-D2": [("app/orchestrator/steps/generate_videos.py", "if project is None or fr is None:"),
                   ("app/orchestrator/steps/generate_videos.py", "async with SessionLocal() as session:")],
        # F10-D5 исправлен (W44): решение идёт в проектную базу, ссылки на строки больше не нужны
        "F10-D6": [("app/orchestrator/auto_advance.py", "visual, no GPT check"),
                   ("app/orchestrator/auto_advance.py", "async def _record_unchecked_auto_approve")],
        "F10-D8": [("app/web/routers/projects.py", "pulled = await _heal_get_runtime_from_master(session, p)"),
                   ("app/project_db.py", "— берём master")],
        "F10-D7": [("app/orchestrator/steps/enrich_xlsx.py", "def _hold_pipeline_after_excel_gpt"),
                   ("prompts/05_excel_gpt/default.md", "{{VAR:EXCEL_GPT_TASK}}")],
    }
    ids = {f["id"] for f in mod.FINDINGS}
    assert set(checks) <= ids
    for fid, pairs in checks.items():
        for rel, needle in pairs:
            lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
            hits = [i + 1 for i, ln in enumerate(lines) if needle in ln]
            assert hits, f"{fid}: {rel} no longer contains {needle!r}"
            cited = [f for f in mod.FINDINGS if f["id"] == fid][0]["where"]
            cited_lines = []
            for w in cited:
                m = re.match(re.escape(rel) + r":(\d+)", w)
                if m:
                    cited_lines.append(int(m.group(1)))
            if cited_lines:
                assert any(abs(h - c) <= 30 for h in hits for c in cited_lines), (fid, rel, hits, cited_lines)


# ───────── safety of the launcher ─────────

def test_preflight_refuses_unsafe_work_dirs(mod, tmp_path, monkeypatch):
    class A:  # noqa: D401
        until = "A"
        asr_model = "medium"
        work_dir = str(ROOT / "_f10_inside")
    work, why = mod.preflight(A())
    assert work is None and "ВНЕ репозитория" in why
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x.txt").write_text("1", encoding="utf-8")
    A.work_dir = str(busy)
    work, why = mod.preflight(A())
    assert work is None and "не пуст" in why
    ok = tmp_path / "fresh"
    A.work_dir = str(ok)
    work, why = mod.preflight(A())
    if work is None:                       # only a missing ffmpeg / ffprobe may refuse a fresh folder
        assert "ffmpeg" in why.lower() or "ffprobe" in why.lower()
    else:
        assert work == ok.resolve() and why == ""


def test_no_dotenv_and_no_env_file_option_in_the_stand_source(mod):
    src = SCRIPT.read_text(encoding="utf-8")
    # the stand never reads an env file (the live whitelist loaders of the sibling stands are NOT used) and has no live mode
    assert "load_env_whitelist" not in src and "--env-file" not in src and "--live" not in src
    assert not re.search(r"open\([^)]*\.env", src)


def test_arguments(mod):
    a = mod.parse_args(["--dry", "--until", "b", "--work-dir", "x"])
    assert a.until == "B" and a.dry is True and a.no_seams is False and a.without_seam == []
    b = mod.parse_args(["--dry", "--work-dir", "x", "--without-seam", "hitl_to_master", "--without-seam", "store_mirror"])
    assert b.without_seam == ["hitl_to_master", "store_mirror"]
    with pytest.raises(SystemExit):
        mod.parse_args(["--dry", "--without-seam", "no_such_seam"])
    with pytest.raises(SystemExit):
        mod.parse_args(["--until", "A"])                   # --dry is mandatory: there is no live mode here
    with pytest.raises(ValueError):
        mod.parse_args(["--dry", "--work-dir", "x", "--until", "Z"])
    assert mod.main(["--dry", "--work-dir", "x", "--until", "Z"]) == mod.EXIT_ARGS


def test_mirror_statuses_are_exactly_the_steps_that_open_the_master_store(mod):
    """F10-D2: only these step modules use app.db.SessionLocal; the seam mirrors only around them."""
    steps = ROOT / "app" / "orchestrator" / "steps"
    users = sorted(p.stem for p in steps.glob("*.py") if "SessionLocal" in p.read_text(encoding="utf-8"))
    assert users == ["generate_hero", "generate_videos"]
    assert mod.MIRROR_STATUSES == frozenset({"generating_hero", "generating_videos"})
    from app.models import ProjectStatus
    assert {s.value for s in (ProjectStatus.generating_hero, ProjectStatus.generating_videos)} == set(mod.MIRROR_STATUSES)


def test_hitl_seam_target_and_result_shape(mod):
    from app.services import advance_runner
    assert callable(advance_runner.advance_project_job)
    assert "new_status" in advance_runner.AdvanceJobResult.__dataclass_fields__


def test_data_fingerprint_ignores_logs_and_sqlite_sidecars_but_sees_real_changes(mod, tmp_path):
    (tmp_path / "videos").mkdir()
    a = tmp_path / "videos" / "p.txt"
    a.write_text("1", encoding="utf-8")
    base = mod.data_fingerprint(tmp_path)
    (tmp_path / "studio-live.log").write_text("noise", encoding="utf-8")
    (tmp_path / "state.db-wal").write_bytes(b"x")
    (tmp_path / "state.db-shm").write_bytes(b"x")
    assert mod.data_fingerprint(tmp_path) == base
    a.write_text("22", encoding="utf-8")
    assert mod.data_fingerprint(tmp_path) != base
    assert mod.data_fingerprint(tmp_path / "missing") is None


def test_peek_reads_the_project_db_read_only(mod, tmp_path):
    import sqlite3
    folder = tmp_path / "data" / "videos" / "slug-x"
    folder.mkdir(parents=True)
    con = sqlite3.connect(folder / "project.db")
    con.execute("CREATE TABLE projects (id INTEGER, status TEXT, auto_mode INTEGER, meta TEXT)")
    con.execute("INSERT INTO projects VALUES (2, 'plan_ready', 1, ?)", (json.dumps({"auto_await_manual_start": True}),))
    con.commit()
    con.close()
    chain = mod.Chain.__new__(mod.Chain)
    chain.work, chain.slug, chain.pid = tmp_path, "slug-x", 2
    got = mod.Chain.peek(chain)
    assert got == {"status": "plan_ready", "auto_mode": True, "meta": {"auto_await_manual_start": True}}
    chain.pid = 3
    assert mod.Chain.peek(chain) == {}
    chain.slug = "no-such"
    assert mod.Chain.peek(chain) == {}


def test_operator_start_only_when_the_production_hold_flag_is_set(mod, monkeypatch):
    calls = []
    waited = []
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    class Db:
        def q(self, sql, args=()):
            waited.append(args[1])
            return [{"status": "done"}]

    class Api:
        def post(self, path, body=None):
            calls.append(path)
            return 200, {}

    class R:
        def check(self, *a, **k):
            return True

    chain = mod.Chain.__new__(mod.Chain)
    chain.api, chain.R, chain.pid, chain.operator_starts, chain.db = Api(), R(), 7, set(), Db()
    mod.Chain.operator_start_if_held(chain, {"meta": {}}, "enrich_1_ready")
    mod.Chain.operator_start_if_held(chain, {"meta": {"auto_await_manual_start": True}}, "plan_ready")
    assert calls == []
    mod.Chain.operator_start_if_held(chain, {"meta": {"auto_await_manual_start": True}}, "enrich_1_ready")
    mod.Chain.operator_start_if_held(chain, {"meta": {"auto_await_manual_start": True}}, "enrich_1_ready")   # once
    mod.Chain.operator_start_if_held(chain, {"meta": {"auto_await_manual_start": True}}, "enrich_2_ready")
    assert calls == ["/api/projects/7/steps/hero/run", "/api/projects/7/steps/img_pr/run"]
    assert waited == ["n_excel_gpt_1", "n_excel_gpt_2"]       # the next start waits until the previous node is done


def test_sfx_plan_answer_is_valid_for_the_production_validator(mod):
    from app.services import sfx_plan
    timeline = [{"frame_number": i + 1, "start": 6.0 * i, "end": 6.0 * (i + 1)} for i in range(3)]
    prompt = ("Ты — звукорежиссёр короткого вертикального ролика. x\n\n# ТАЙМЛАЙН КАДРОВ (JSON)\n"
              + json.dumps(timeline) + "\n\n# СЦЕНЫ/ДЕЙСТВИЕ (JSON, может быть пусто)\n[]\n\n# ПРАВИЛА\n1.\n\n"
              "kind — СТРОГО из: " + ", ".join(sfx_plan.SFX_KINDS) + ". t_start — секунды.")
    o = mod.TextOracle()
    assert mod.TextOracle.classify(prompt) == "sfx_plan"
    payload = json.loads(o.answer_sfx_plan(prompt, {}))
    events = sfx_plan._parse_events(payload)
    assert len(events) == 3 and o.sfx_events == 3
    assert sfx_plan.validate_events(events, total_duration=18.0) == []
    assert mod.TextOracle.classify("Верни ТОЛЬКО текст. составь один промт для генерации фоновой инструментальной музыки в Suno x") == "music_prompt"
    assert len(mod.MUSIC_PROMPT_TEXT) >= 20            # generate_music refuses a shorter Suno prompt


def test_expected_llm_calls_match_the_default_graph(mod):
    a = mod.expected_llm_calls("A", 3)
    assert a == {"plan": 1, "script": 1, "verdict": 2, "split": 1, "excel_gpt": 3}
    e = mod.expected_llm_calls("E", 3)
    assert e["excel_gpt"] == 6 and e["image_prompts"] == 1 and e["animation_prompts"] == 2
    assert e["music_prompt"] == 1 and e["sfx_plan"] == 1
    assert mod.expected_llm_calls("C", 7)["animation_prompts"] == 3          # a ready + two batches of up to 5 frames


def test_scope_is_per_node_key_in_the_default_graph(mod):
    from app.orchestrator.default_graph import default_graph
    nodes, _ = default_graph()
    for n in nodes:
        if n["type"] in ("topic", "storage"):
            continue
        assert n["id"] in mod.NODE_KEY_LEVEL, f"node {n['id']} of the default graph has no level"
    assert mod.node_in_scope("excel_gpt", "A", "n_excel_gpt_1") and not mod.node_in_scope("excel_gpt", "A", "n_excel_gpt_2")
    assert mod.node_in_scope("excel_gpt", "B", "n_excel_gpt_2")
    assert not mod.node_in_scope("image_prompts", "A", "n_image_prompts") and mod.node_in_scope("image_prompts", "B", "n_image_prompts")
    assert not mod.node_in_scope("sfx_plan", "C", "n_sfx_plan") and mod.node_in_scope("sfx_plan", "D", "n_sfx_plan")
    assert mod.node_in_scope("plan", "A")                    # without a key the type level is used


def test_last_running_status_ends_each_level(mod):
    for lvl, running in mod.LAST_RUNNING.items():
        assert mod.status_rank(running) < mod.status_rank(mod.STATUS_END[lvl])
        # the node that runs in that status is the last one of the level: nothing of the level runs after it
        assert mod.status_rank(running) > mod.status_rank(mod.STATUS_END[mod.LEVELS[mod.LEVELS.index(lvl) - 1]])             if lvl != "A" else True


def test_stop_auto_is_one_patch_at_the_last_node_only(mod):
    calls = []

    class Api:
        def req(self, method, path, json_body=None, **kw):
            calls.append((method, path, json_body))
            return 200, {}

    class R:
        def check(self, *a, **k):
            return True

    chain = mod.Chain.__new__(mod.Chain)
    chain.api, chain.R, chain.pid, chain.auto_off = Api(), R(), 5, False
    mod.Chain.stop_auto_at_last_node(chain, "C", "generating_animation_prompts")
    assert calls == []
    mod.Chain.stop_auto_at_last_node(chain, "C", "generating_videos")
    mod.Chain.stop_auto_at_last_node(chain, "C", "videos_ready")
    assert calls == [("PATCH", "/api/projects/5", {"auto_mode": False})] and chain.auto_off is True


ATTACH = "## Приложенные файлы" + chr(10) + '{"frames":[{"uuid":"0123456789abcdef01234567"}]}'


def test_with_hero_adds_one_reference_image_and_one_prompt_request(mod):
    assert mod.expected_counts_for("B", 3, 3, hero=1)["outsee_image"] == 4
    assert mod.expected_counts_for("A", 3, 3, hero=1)["outsee_image"] == 0
    assert mod.expected_llm_calls("B", 3, hero=1)["hero_prompt"] == 1 and "hero_prompt" not in mod.expected_llm_calls("B", 3)
    est = mod.live_estimate(3, 3, hero=1)
    assert est["outsee_image"] == 4 and est["text_llm"] == 17
    o = mod.TextOracle(with_hero=True)
    ans = json.loads(o.answer_excel_gpt(ATTACH, {}))
    assert ans["characters"][0]["id"] == "c01" and ans["ops"][0]["fields"]["персонажи"] == "c01"
    plain = json.loads(mod.TextOracle().answer_excel_gpt(ATTACH, {}))
    assert "characters" not in plain and "персонажи" not in plain["ops"][0]["fields"]
    assert mod.TextOracle.classify("Сделай промт для генерации персонажа, который описан ниже. x") == "hero_prompt"
    assert 100 <= len(mod.HERO_PROMPT_TEXT) <= 4900          # excel_hero refuses a reply under 100 chars or over the Outsee limit


def test_uploads_expected(mod):
    assert mod.uploads_expected(3) == 3 and mod.uploads_expected(3, hero=1) == 6
