"""F10 option ``--prompt-group script_frames_qc`` of BOTH stands (``scripts/e2e_full_chain.py`` dry, ``scripts/e2e_full_chain_live.py``).

Offline: no server process, no network, nothing paid. The default behaviour of both stands (option off) is covered by the
w5o_full_chain* tests and must not move: the first tests here only pin that the option is off by default.
"""
from __future__ import annotations

import importlib.util
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
DRY = ROOT / "scripts" / "e2e_full_chain.py"
LIVE = ROOT / "scripts" / "e2e_full_chain_live.py"
GROUP = "script_frames_qc"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def dry():
    return _load("e2e_full_chain_prompt_group_under_test", DRY)


@pytest.fixture(scope="module")
def live():
    return _load("e2e_full_chain_live_prompt_group_under_test", LIVE)


def _group_def():
    from app.services.node_groups import get_node_group
    g = get_node_group(GROUP)
    assert g is not None
    return g


# ───────── the option is OFF by default and only knows the real group ─────────

def test_option_is_off_by_default_in_both_stands(dry, live):
    assert dry.parse_args(["--dry", "--work-dir", "x"]).prompt_group is None
    assert live.parse_args(["--live-preflight"]).prompt_group is None
    assert dry.PROMPT_GROUPS == live.fc.PROMPT_GROUPS == (GROUP,)
    # without the option every number is the one the w5o tests pin
    assert dry.expected_llm_calls("E", 3) == dry.expected_llm_calls("E", 3, 0, None)
    assert dry.expected_llm_calls("E", 3)["excel_gpt"] == 6 and not any(k.startswith("group_") for k in dry.expected_llm_calls("E", 3))
    est = live.estimate(live.parse_args(["--live-preflight", "--until", "E"]))
    assert est["requests"]["llm"] == 10 and est["prompt_group"] is None and est["public_file_host_uploads"] == 1
    assert dry.live_estimate(3, 3)["text_llm"] == 16 and dry.live_estimate(3, 3)["prompt_group"] is None
    assert dry.uploads_expected(3) == 3


def test_option_accepts_only_known_groups(dry, live):
    assert dry.parse_args(["--dry", "--work-dir", "x", "--until", "B", "--prompt-group", GROUP]).prompt_group == GROUP
    assert live.parse_args(["--live-preflight", "--until", "E", "--prompt-group", GROUP]).prompt_group == GROUP
    with pytest.raises(SystemExit):
        dry.parse_args(["--dry", "--work-dir", "x", "--prompt-group", "no_such_group"])
    with pytest.raises(SystemExit):
        live.parse_args(["--live-preflight", "--prompt-group", "scene_design_fanout"])      # a real group, but not this option's


def test_level_a_is_refused_with_a_group(dry, live):
    # level A ends at the first enrich_1_ready; every node of the group runs in the enrich_1 slot -> A would stop after its first node
    with pytest.raises(ValueError, match="уровня A"):
        dry.parse_args(["--dry", "--work-dir", "x", "--until", "A", "--prompt-group", GROUP])
    assert dry.main(["--dry", "--work-dir", "x", "--until", "A", "--prompt-group", GROUP]) == dry.EXIT_ARGS
    with pytest.raises(SystemExit):
        live.parse_args(["--live-preflight", "--until", "A", "--prompt-group", GROUP])


# ───────── the stand's literals are bound to the production group ─────────

def test_stand_literals_match_the_production_group(dry):
    g = _group_def()
    assert tuple(n.preferred_id for n in g.nodes) == dry.GROUP_NODE_KEYS[GROUP]
    model_nodes = [n for n in g.nodes if (n.operator_config or {}).get("transport") != "code"]
    assert len(model_nodes) == len(dry.GROUP_LLM_KINDS[GROUP]) == 4          # skeleton, site, shots, QC; the report is code
    assert [m for m in dry.GROUP_NODE_KEYS[GROUP] if m.endswith("_fw_report")] == [g.nodes[-1].preferred_id]
    assert (g.nodes[-1].operator_config or {}).get("transport") == "code"


def test_anchor_is_the_first_generic_node_and_the_chain_hangs_on_it(dry):
    """The worker starts a group node only along an excel_gpt arrow, so the stand hangs the group on n_excel_gpt_1."""
    from app.orchestrator.default_graph import default_graph
    nodes, edges = default_graph()
    by_id = {n["id"]: n for n in nodes}
    assert dry.PROMPT_GROUP_ANCHOR == "n_excel_gpt_1" and by_id["n_excel_gpt_1"]["type"] == "excel_gpt"
    assert [e["target"] for e in edges if e["source"] == "n_excel_gpt_1"] == ["n_hero"]


@pytest.fixture
async def mem_db(monkeypatch, tmp_path):
    from app.models import Base
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    from app.services import node_groups as ng
    monkeypatch.setattr(ng, "custom_groups_dir", lambda: tmp_path / "node_groups")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.db.session_scope", _scope)
    yield _scope
    await engine.dispose()


async def test_group_wires_into_the_real_default_graph_at_the_anchor(mem_db):
    """The production insert on the REAL default graph: n_excel_gpt_1 -> Каркас -> ... -> Отчёт -> n_hero."""
    from app.models import Project, ProjectStatus, Workflow, WorkflowRun, WorkflowRunStatus
    from app.orchestrator.default_graph import default_graph
    from app.services.node_groups import insert_node_group
    stand = _load("e2e_full_chain_wiring_under_test", DRY)
    nodes, edges = default_graph()
    async with mem_db() as session:
        wf = Workflow(name=f"wf-{uuid.uuid4().hex[:8]}", is_default=True, nodes=nodes, edges=edges)
        session.add(wf)
        await session.flush()
        project = Project(slug=f"pg-{uuid.uuid4().hex[:8]}", topic="t", status=ProjectStatus.new,
                          meta={"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": edges}})
        session.add(project)
        await session.flush()
        session.add(WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.new,
                                nodes_snapshot=nodes, edges_snapshot=edges))
        await session.commit()
        res = await insert_node_group(session, project, GROUP, after=stand.PROMPT_GROUP_ANCHOR)
    assert res["after"] == stand.PROMPT_GROUP_ANCHOR and set(res["nodes"]) == set(stand.GROUP_NODE_KEYS[GROUP])
    cg = project.meta["canvas_graph"]
    pairs = {(e["source"], e["target"]) for e in cg["edges"]}
    chain = ["n_excel_gpt_1", *stand.GROUP_NODE_KEYS[GROUP], "n_hero"]
    for a, b in zip(chain, chain[1:], strict=False):
        assert (a, b) in pairs, (a, b)
    assert ("n_excel_gpt_1", "n_hero") not in pairs                      # the old arrow is re-hung behind the group
    by_id = {n["id"]: n for n in cg["nodes"]}
    # every node of the group is an excel_gpt node outside the five enrich slots (they run in the enrich_1 status)
    for key in stand.GROUP_NODE_KEYS[GROUP]:
        assert by_id[key]["type"] == "excel_gpt" and by_id[key]["data"]["slotOverflow"] is True


# ───────── the oracle answers the group in the shape its executors parse ─────────

def _attach(*uuids_numbers):
    items = ",".join(f'{{"number":{n},"uuid":"{u}","voiceover_text":"x"}}' for u, n in uuids_numbers)
    return "## Приложенные файлы\n\n===== db_frames.json =====\n" + '{"frames":[' + items + "]}"


def test_oracle_markers_open_the_real_group_prompts(dry):
    """The oracle tells the group's requests apart by the first line of each master prompt; a rewritten prompt must fail here."""
    from app.services.prompt_library import resolve_script_frames_qc_prompt_path
    g = _group_def()
    model_nodes = [n for n in g.nodes if (n.operator_config or {}).get("transport") != "code"]
    for node, kind in zip(model_nodes, dry.GROUP_LLM_KINDS[GROUP], strict=True):
        text = resolve_script_frames_qc_prompt_path(node.prompt_variant).read_text(encoding="utf-8")
        assert dry.TextOracle.classify(text) == kind, (node.prompt_variant, dry.TextOracle.classify(text))
        assert dry.TextOracle.classify("служебное вступление\n\n" + text) == kind                # tolerant to a short preamble
    # and they do not steal the generic / native requests
    assert dry.TextOracle.classify("# Доп. работа с Excel #\n\nx") == "excel_gpt"
    assert dry.TextOracle.classify("# Агент: промты анимации Veo 3.1 Fast (Relax) → DB\nx") == "animation_prompts"


def test_oracle_group_answers_are_ops_for_the_cells_of_the_batch(dry):
    o = dry.TextOracle(prompt_group=GROUP)
    text = "мастер-промт, в котором есть \"uuid\": \"aaaaaaaaaaaaaaaaaaaaaaaa\"\n" + _attach(("0123456789abcdef01234567", 1), ("fedcba9876543210fedcba98", 2))
    for kind, field in (("group_skeleton", "биты"), ("group_action", "площадка"), ("group_shots", "кадры")):
        ops = json.loads(getattr(o, "answer_" + kind)(text, {}))["ops"]
        assert [x["frame_uuid"] for x in ops] == ["0123456789abcdef01234567", "fedcba9876543210fedcba98"], kind
        assert all(list(x["fields"]) == [field] for x in ops), kind
    shots = json.loads(o.answer_group_shots(text, {}))["ops"]
    assert shots[0]["fields"]["кадры"][0]["id"].startswith("1-S1-") and shots[1]["fields"]["кадры"][0]["id"].startswith("2-S1-")
    assert json.loads(o.answer_group_qc(text, {})) == {"ops": []}
    # the answers are the validated ones of the group's own stand (same objects, not a copy)
    assert dry.GROUP_VO == dry.qc.FULL_VO and dry.qc.valid_answer


def test_group_story_is_the_script_and_the_split_covers_it(dry):
    plain = dry.TextOracle()
    assert plain.script_text == dry.SCRIPT_TEXT and plain.frame_texts == dry.FRAME_TEXTS            # default untouched
    o = dry.TextOracle(prompt_group=GROUP)
    script = json.loads(o.answer_script("", {}))["ops"][0]["fields"]["закадровый_текст"]
    frames = json.loads(o.answer_split("", {}))["ops"][0]["frames"]
    assert script == dry.GROUP_VO and len(script) >= 200                                           # make_script refuses shorter
    assert " ".join(f["закадр"] for f in frames) == script and len(frames) == len(dry.GROUP_SCENE_VOS) == 5


def test_group_story_text_passes_the_production_checks(dry):
    from app.services.plan_validation import is_meaningful_general_plan
    assert is_meaningful_general_plan(dry.PLAN_TEXT)
    v = json.loads(dry.TextOracle.answer_verdict("ПЛАН:\n" + dry.GROUP_VO + '\n"criteria": {"x": {"verdict": "pass"}}', {}))
    assert all(c["evidence_quote"] and c["evidence_quote"] in dry.GROUP_VO for c in v["criteria"].values())


# ───────── numbers: the group adds requests, the frame count comes from the group ─────────

def test_expected_llm_calls_with_a_group(dry):
    base = dry.expected_llm_calls("E", 9, 0)
    withg = dry.expected_llm_calls("E", 9, 0, GROUP, 5, 5)
    extra = {k: v for k, v in withg.items() if k not in base}
    assert extra == {"group_skeleton": 1, "group_action": 1, "group_shots": 1, "group_qc": 1}
    # measured in the dry run: n_excel_gpt_1 runs on the split's 5 frames, n_excel_gpt_2 on the group's 5 VO parents (the 4 shot
    # children are not sent), 25 requests in all
    assert withg["excel_gpt"] == 5 + 5
    assert {k: v for k, v in withg.items() if k not in extra and k != "excel_gpt"} == {k: v for k, v in base.items() if k != "excel_gpt"}
    assert sum(withg.values()) == 25
    # without the measured counts the estimate is the upper bound (both generic nodes at the cap), and it never undershoots
    assert dry.expected_llm_calls("E", 9, 0, GROUP)["excel_gpt"] == 18 >= withg["excel_gpt"]
    assert dry.expected_llm_calls("E", 9, 0)["excel_gpt"] == 18                    # option off: 2 * frames, as before
    assert dry.uploads_expected(9, 0, children=4) == 13
    est = dry.live_estimate(9, 9, prompt_group=GROUP, children=4, split_frames=5, group_parents=5)
    assert est["prompt_group"] == GROUP and est["free_but_external"]["public_file_host_uploads"] == 13
    assert est["text_llm"] == sum(est["text_llm_by_node"].values()) == 25 and est["text_llm_by_node"]["group_shots"] == 1
    assert "группы" in est["note"]


def test_live_estimate_and_caps_count_the_group(live):
    plain = live.parse_args(["--live-preflight", "--until", "E", "--frames", "5"])
    grp = live.parse_args(["--live-preflight", "--until", "E", "--frames", "5", "--prompt-group", GROUP])
    a, b = live.estimate(plain), live.estimate(grp)
    assert b["requests"]["llm"] == a["requests"]["llm"] + 4
    assert {k: v for k, v in b["llm_by_node"].items() if k.startswith("group_")} == {
        "group_skeleton": 1, "group_action": 1, "group_shots": 1, "group_qc": 1}
    assert b["text_usd"] == pytest.approx(a["text_usd"] + 4 * live.PRICE["llm_request"])
    assert b["media_usd"] == a["media_usd"] and b["requests"]["image"] == a["requests"]["image"] == 5      # frames stay capped by --frames
    assert a["public_file_host_uploads"] == 5 and b["public_file_host_uploads"] == 5 + 4              # + one reference per shot child (< frames)
    assert live.wire_caps(grp)["llm"] == live.wire_caps(plain)["llm"] + 4 * 4                          # est * 4 + 12: the 4 requests raise it
    assert live.wire_caps(grp)["outsee_image"] == live.wire_caps(plain)["outsee_image"] == 7
    # the owner's ceilings are not touched by the option: the group costs a few cents of text, no media
    assert live.estimate_problem(b, 2.0, 6.0) is None and live.estimate_problem(a, 2.0, 6.0) is None
    eight = live.estimate(live.parse_args(["--live-preflight", "--until", "E", "--frames", "8", "--owner-approved-frames", "8",
                                           "--prompt-group", GROUP]))
    assert eight["requests"]["llm"] == 29 and eight["text_usd"] <= 2.0 and eight["media_usd"] == live.estimate(
        live.parse_args(["--live-preflight", "--until", "E", "--frames", "8", "--owner-approved-frames", "8"]))["media_usd"]
    assert live.estimate(live.parse_args(["--live-preflight", "--until", "B", "--frames", "5", "--prompt-group", GROUP]))["public_file_host_uploads"] == 4


def test_live_preflight_with_the_group_pays_nothing_and_prints_the_group(live, tmp_path, capsys, monkeypatch):
    """The owner's command line, dry-checked: keys by NAME only (synthetic file), no network, no child process."""
    import socket
    import subprocess
    key = "k" + "_" + "SECRETVALUE1234567890"            # built from parts: no secret-shaped literal in the source
    env = tmp_path / "keys.env"
    env.write_text("\n".join(f"{n}={key}{n}" for n in ("VIBECODE_API_KEY", "OUTSEE_API_KEY", "ELEVENLABS_API_KEY")) + "\n", encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("network or process used in the preflight")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    argv = ["--live-preflight", "--until", "E", "--frames", "8", "--owner-approved-frames", "8", "--max-text-usd", "2",
            "--max-media-usd", "6", "--owner-approved-media-usd", "6", "--prompt-group", GROUP, "--env-file", str(env)]
    assert live.main(argv) == live.EXIT_PASS
    out = capsys.readouterr()
    assert "группа промтов: script_frames_qc" in out.out and "число кадров задаёт группа" in out.out and "group_shots" in out.out
    assert key not in out.out + out.err
    # and without the option the same command line is the old one
    assert live.main([a for a in argv if a not in ("--prompt-group", GROUP)]) == live.EXIT_PASS
    plain = capsys.readouterr().out
    assert "группа промтов: нет" in plain and "group_shots" not in plain and "число кадров задаёт группа" not in plain


# ───────── the check that would have caught the live defect ─────────

HOLLOW = ("Фон: нет исходных данных для заполнения; Действие: нет исходных данных для заполнения; "
          "Свет: нет исходных данных для заполнения; STYLE: Archival Noir Watercolor")
REAL = "Кинематографичный кадр: человек в сером пальто стоит у старого деревянного стола, мягкий тёплый свет из окна слева."


def _row(number, prompt, uuid_="", role=None, parent=""):
    attrs = {}
    if role:
        attrs["camera_subdivide"] = {"role": role, "parent_uuid": parent}
    return {"number": number, "uuid": uuid_ or f"{number:024x}", "image_prompt": prompt, "attrs": json.dumps(attrs)}


def test_frames_prompt_report(dry):
    ok = [_row(1, REAL, "p1", "vo_parent", "p1"), _row(2, "", "c2", "shot", "p1"), _row(3, REAL, "p3")]
    r = dry.frames_prompt_report(ok)
    assert r == {"frames": 3, "parents": 2, "children": 1, "empty": [], "hollow": []}
    bad = [_row(1, "", "p1"), _row(2, HOLLOW, "p2"), _row(3, "", "c3", "shot", "p2"), _row(4, "", "c4", "shot", "p1")]
    r = dry.frames_prompt_report(bad)
    assert r["empty"] == [1, 4] and r["hollow"] == [2, 3]                  # a child of a hollow / empty parent is no better than it
    assert dry.frames_prompt_report([])["frames"] == 0
    assert dry.frames_prompt_report([{"number": 1, "uuid": "u", "image_prompt": None, "attrs": None}])["empty"] == [1]


def test_hollow_rule_is_the_production_one(dry):
    from app import generation_options as go
    assert dry.HOLLOW_PHRASE in go._PLACEHOLDER_PHRASES
    assert go.is_scene_prompt_hollow.__kwdefaults__["min_placeholders"] == dry.HOLLOW_MIN_PLACEHOLDERS
    for text in (HOLLOW, REAL, "", "нет исходных данных для заполнения " * 2):
        mine = dry.frames_prompt_report([_row(1, text)])
        prod_hollow = go.is_scene_prompt_hollow(text)
        assert (mine["hollow"] == [1]) == prod_hollow, text[:40]
        assert (mine["empty"] == [1]) == (not text.strip()), text[:40]


# ───────── the stand drives the production routes in the order the UI uses ─────────

class _Api:
    def __init__(self, workflows, post_code=200, post_body=None):
        self.calls = []
        self.workflows = workflows
        self.post_code, self.post_body = post_code, post_body

    def get(self, path, **kw):
        self.calls.append(("GET", path, None))
        if path == "/api/workflows":
            return 200, [{"id": 7, "is_default": False}, {"id": 9, "is_default": True}]
        if path == "/api/workflows/9":
            return 200, self.workflows
        return 404, None

    def req(self, method, path, json_body=None, **kw):
        self.calls.append((method, path, json_body))
        return 200, {}

    def post(self, path, body=None, **kw):
        self.calls.append(("POST", path, body))
        self.params = kw.get("params")
        return self.post_code, self.post_body


class _R:
    def __init__(self):
        self.checks = []
        self.advice = []

    def check(self, name, ok, detail=None, **kw):
        self.checks.append((name, bool(ok)))
        return bool(ok)

    def advise(self, text):
        self.advice.append(text)


def _chain(dry, api, group="script_frames_qc"):
    c = dry.Chain.__new__(dry.Chain)
    c.api, c.R, c.pid, c.args = api, _R(), 5, type("A", (), {"prompt_group": group})()
    c.operator_starts, c.interventions = set(), []
    return c


def test_apply_prompt_group_saves_the_canvas_first_then_inserts_the_group(dry):
    wf = {"nodes": [{"id": "n_split", "type": "split"}], "edges": [{"id": "e", "source": "a", "target": "b"}]}
    ok_body = {"after": "n_excel_gpt_1", "nodes": list(dry.GROUP_NODE_KEYS[GROUP])}
    api = _Api(wf, 200, ok_body)
    c = _chain(dry, api)
    assert c.apply_prompt_group(GROUP) is True
    methods = [(m, p) for m, p, _ in api.calls]
    assert methods == [("GET", "/api/workflows"), ("GET", "/api/workflows/9"), ("PATCH", "/api/projects/5"),
                       ("POST", f"/api/projects/5/canvas/groups/{GROUP}")]
    patch = api.calls[2][2]["meta"]["canvas_graph"]
    assert patch["workflow_id"] == 9 and patch["nodes"] == wf["nodes"] and patch["edges"] == wf["edges"] and patch["saved_at"]
    assert api.calls[3][2] == {"after": "n_excel_gpt_1"}
    assert all(ok for _, ok in c.R.checks)


def test_apply_prompt_group_fails_loudly_when_the_route_refuses_or_misses_a_node(dry):
    wf = {"nodes": [{"id": "n"}], "edges": []}
    refused = _chain(dry, _Api(wf, 400, {"detail": "insert_group: нет ни canvas_graph, ни default workflow"}))
    assert refused.apply_prompt_group(GROUP) is False
    partial = _chain(dry, _Api(wf, 200, {"after": "n_excel_gpt_1", "nodes": list(dry.GROUP_NODE_KEYS[GROUP])[:-1]}))
    assert partial.apply_prompt_group(GROUP) is False
    no_default = _chain(dry, _Api(wf))
    no_default.api.get = lambda path, **kw: (200, [{"id": 1, "is_default": False}])
    assert no_default.apply_prompt_group(GROUP) is False


def test_create_touches_the_graph_only_when_the_option_is_set(dry):
    class Api(_Api):
        def post(self, path, body=None, **kw):
            self.calls.append(("POST", path, body))
            if path == "/api/projects":
                return 201, {"id": 5, "slug": "s", "auto_mode": True}
            return 200, {"after": "n_excel_gpt_1", "nodes": list(dry.GROUP_NODE_KEYS[GROUP])}

    for group in (None, GROUP):
        api = Api({"nodes": [{"id": "n"}], "edges": []})
        c = _chain(dry, api, group)
        c.args.topic = "t"
        assert c.create() is True
        paths = [p for _, p, _ in api.calls]
        assert paths[0] == "/api/projects"
        assert any("/canvas/groups/" in p for p in paths) is bool(group)
        assert (len(paths) == 1) is (group is None)                       # without the option: the creation call and nothing else


# ───────── F10-D12: the livelock behind the group's chain, and the operator press that gets round it ─────────

STALE = {"meta": {"active_excel_gpt_node_key": "n_excel_gpt_fw_report",
                  "excel_gpt_completed_keys": ["n_excel_gpt_1", "n_excel_gpt_fw_report"]}}


def _press(dry, project, status, group=GROUP, times=2):
    api = _Api({"nodes": [], "edges": []})
    c = _chain(dry, api, group)
    c.UNSTICK_AFTER_S = 0.0                               # the real wait is 15 s; the condition is what matters here
    for _ in range(times):
        c.operator_unstick_after_group(project, status)
    pressed = [x for x in api.calls if x[1].endswith("/steps/excel_gpt/run")]
    return c, api, pressed


def test_unstick_presses_play_on_the_second_generic_node_once_and_only_on_the_stale_key(dry):
    c, api, pressed = _press(dry, STALE, "enriching_2", times=3)
    assert len(pressed) == 1 and pressed[0][1] == "/api/projects/5/steps/excel_gpt/run" and api.params == {"node_key": "n_excel_gpt_2"}
    assert c.interventions == [{"finding": "F10-D12", "status": "enriching_2", "stale_active_key": "n_excel_gpt_fw_report",
                                "pressed": "▶ n_excel_gpt_2", "http": 200}]
    assert c.R.advice and "F10-D12" in c.R.advice[0]                # never silent: advisory + result.json interventions
    # the first poll only starts the clock: nothing is pressed on an instant transition
    _, api2, early = _press(dry, STALE, "enriching_2", times=1)
    assert early == []


@pytest.mark.parametrize("project,status,group", [
    (STALE, "enriching_2", None),                                                                   # option off: never
    (STALE, "enriching_1", GROUP),                                                                  # other statuses: never
    (STALE, "generating_images", GROUP),
    ({"meta": {"active_excel_gpt_node_key": "n_excel_gpt_2", "excel_gpt_completed_keys": ["n_excel_gpt_2"]}}, "enriching_2", GROUP),
    ({"meta": {"active_excel_gpt_node_key": "n_excel_gpt_fw_report", "excel_gpt_completed_keys": ["n_excel_gpt_1"]}}, "enriching_2", GROUP),
    ({"meta": {}}, "enriching_2", GROUP),                                                           # a healthy project: no stale key
    ({}, "enriching_2", GROUP),
])
def test_unstick_leaves_a_healthy_project_alone(dry, project, status, group):
    c, api, pressed = _press(dry, project, status, group)
    assert pressed == [] and c.interventions == []


def test_f10_d12_finding_points_at_living_code(dry):
    import re
    f = [x for x in dry.FINDINGS if x["id"] == "F10-D12"][0]
    assert f["masked_by"] and f["stops_at"] == "enriching_2"
    for rel, needle in (("app/main.py", "excel_gpt_already_completed_no_force(p)"),
                        ("app/services/excel_gpt_node.py", "def excel_gpt_already_completed_no_force")):
        lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
        hits = [i + 1 for i, ln in enumerate(lines) if needle in ln]
        assert hits, f"{rel} no longer contains {needle!r}"
        cited = [int(m.group(1)) for w in f["where"] if (m := re.match(re.escape(rel) + r":(\d+)", w))]
        assert cited and any(abs(h - c) <= 30 for h in hits for c in cited), (rel, hits, cited)


# ───────── the group's report never leaves the work dir ─────────

def test_group_report_root_moves_into_the_work_dir(dry, tmp_path, monkeypatch):
    from app.services import shots_report
    monkeypatch.setattr(shots_report, "find_project_root", shots_report.find_project_root)      # restore the original afterwards
    before = shots_report.report_paths(type("P", (), {"slug": "s1", "data_dir": tmp_path / "d"})())
    assert before[-1] == ROOT / "exports" / "s1-shots-report.html"                                   # the production default: INSIDE the repository
    dry.keep_group_report_out_of_repo(tmp_path / "work")
    after = shots_report.report_paths(type("P", (), {"slug": "s1", "data_dir": tmp_path / "d"})())
    assert after[-1] == tmp_path / "work" / "exports" / "s1-shots-report.html"
    assert not any(ROOT in p.parents for p in after if "exports" in p.parts)


def test_both_child_processes_apply_the_report_fix():
    dry_src = DRY.read_text(encoding="utf-8")
    live_src = LIVE.read_text(encoding="utf-8")
    seams = dry_src[dry_src.index("def install_child_seams"):dry_src.index("def keep_group_report_out_of_repo")]
    assert "keep_group_report_out_of_repo(work)" in seams
    child = live_src[live_src.index("def child_live_main"):live_src.index("# ───────────────────────────── ledger")]
    assert "fc.keep_group_report_out_of_repo(Path(cfg[\"work\"]))" in child


def test_stand_sources_still_never_read_a_dotenv():
    for p in (DRY, LIVE):
        src = p.read_text(encoding="utf-8")
        assert "\".env\"" not in src and "'.env'" not in src
