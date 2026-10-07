"""W1-7 / A19: e2e scripts refuse live data, never use a hard-coded project id; subtitle file names are escaped."""
from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from app.services.assembly import subtitles_vf_arg

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SLUG = "diagnostic-synthetic-scene-2026-09-24"
NODE = "n_excel_gpt_fw_script"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("w17_" + name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load("e2e_guard")


@pytest.fixture
def tmp_path():
    """A work folder outside the repository: the shared pytest temp may sit inside ``data/`` (the guard would
    rightly refuse it), so these tests use their own root."""
    import os
    import tempfile

    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "video-pipeline-temp" / "w17-tests"
    base.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="t_", dir=base))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _seed(root: Path, project_id: int = 7) -> Path:
    """Minimal seed: two projects (the target and a bystander), their runs, frames and the per-project db."""
    data = root / "seed"
    (data / "videos" / SLUG).mkdir(parents=True)
    for path in (data / "state.db", data / "videos" / SLUG / "project.db"):
        with closing(sqlite3.connect(str(path))) as db:
            db.executescript("""
                CREATE TABLE projects (id INTEGER PRIMARY KEY, slug TEXT, status TEXT, meta TEXT,
                                       general_plan TEXT, script_text TEXT);
                CREATE TABLE workflow_runs (id INTEGER PRIMARY KEY, project_id INTEGER, nodes_snapshot TEXT, edges_snapshot TEXT);
                CREATE TABLE node_runs (id INTEGER PRIMARY KEY, workflow_run_id INTEGER, node_key TEXT, node_type TEXT,
                                        status TEXT, progress REAL, attempts INTEGER, error TEXT, meta TEXT,
                                        created_at TEXT, updated_at TEXT);
                CREATE TABLE frames (id INTEGER PRIMARY KEY, project_id INTEGER, number INTEGER, voiceover_text TEXT,
                                     status TEXT, attrs TEXT, uuid TEXT, sort_key REAL, created_at TEXT, updated_at TEXT);
                CREATE TABLE hitl_requests (id INTEGER PRIMARY KEY, project_id INTEGER, kind TEXT, decision TEXT);
            """)
            node = '[{"id": "%s", "type": "excel_gpt", "data": {}}, {"id": "other", "type": "x", "data": {}}]' % NODE
            for pid, slug in ((project_id, SLUG), (project_id + 1, "bystander")):
                db.execute("INSERT INTO projects(id, slug, status, meta) VALUES (?,?,?,?)", (pid, slug, "running", "{}"))
                db.execute("INSERT INTO workflow_runs(id, project_id, nodes_snapshot, edges_snapshot) VALUES (?,?,?,?)",
                           (pid * 10, pid, node, "[]"))
                db.executemany("INSERT INTO node_runs(workflow_run_id,node_key,node_type,status,progress,attempts) "
                               "VALUES (?,?,?,?,0,3)", [(pid * 10, NODE, "excel_gpt", "failed"), (pid * 10, "other", "x", "done")])
                db.execute("INSERT INTO frames(project_id,number,voiceover_text,status,attrs) VALUES (?,?,?,?,?)",
                           (pid, 1, "t", "done", "{}"))
                db.execute("INSERT INTO hitl_requests(project_id,kind,decision) VALUES (?,?,?)", (pid, "k", "approved"))
            db.commit()
    return data


def _rows(db_path: Path, sql: str):
    with closing(sqlite3.connect(str(db_path))) as db:
        return db.execute(sql).fetchall()


# ---------------------------------------------------------------- layout guard
def test_work_dir_inside_or_equal_to_live_data_is_refused(tmp_path):
    live = tmp_path / "live" / "data"
    live.mkdir(parents=True)
    seed = _seed(tmp_path)
    env = {"DATA_DIR": str(live)}
    for work in (live, live / "scratch", tmp_path / "live"):  # equal, inside, and a parent of the live data
        with pytest.raises(SystemExit) as exc:
            guard.assert_safe_layout(seed, work, repo_root=tmp_path / "repo", env=env)
        assert "боевым" in str(exc.value)
    env2 = {"SQLITE_PATH": str(live / "state.db")}  # SQLITE_PATH alone also marks the live folder
    with pytest.raises(SystemExit):
        guard.assert_safe_layout(seed, live / "w", repo_root=tmp_path / "repo", env=env2)
    with pytest.raises(SystemExit):  # the repository's own data folder
        guard.assert_safe_layout(seed, tmp_path / "repo" / "data" / "w", repo_root=tmp_path / "repo", env={})


def test_work_dir_overlapping_the_seed_is_refused_and_seed_survives(tmp_path):
    seed = _seed(tmp_path)  # tmp/seed
    before = sorted(p.name for p in seed.rglob("*"))
    for work in (seed, seed.parent):  # work == seed; and seed lying inside work (work/data does not overlap it)
        with pytest.raises(SystemExit):
            guard.prepare_scratch_data(seed, work, repo_root=tmp_path / "repo", env={})
    alias = tmp_path / "w"
    (alias).mkdir()
    shutil.copytree(seed, alias / "data")  # work/data pre-exists: not created by the scripts, no marker
    with pytest.raises(SystemExit) as exc:
        guard.prepare_scratch_data(seed, alias, repo_root=tmp_path / "repo", env={})
    assert "маркера" in str(exc.value)
    assert sorted(p.name for p in seed.rglob("*")) == before and (alias / "data" / "state.db").exists()


def test_home_drive_root_and_repo_root_are_too_general(tmp_path):
    seed = _seed(tmp_path)
    for work in (Path.home(), Path(Path.home().anchor), ROOT):
        with pytest.raises(SystemExit):
            guard.assert_safe_layout(seed, work, env={})


def test_prepare_copies_seed_marks_folder_and_may_be_repeated(tmp_path):
    seed, work = _seed(tmp_path), tmp_path / "work"
    data = guard.prepare_scratch_data(seed, work, repo_root=tmp_path / "repo", env={})
    assert (work / guard.MARKER).exists() and (data / "state.db").exists()
    (data / "extra.txt").write_text("x")
    again = guard.prepare_scratch_data(seed, work, repo_root=tmp_path / "repo", env={})  # our own folder: recreated
    assert again == data and not (data / "extra.txt").exists()
    reset = guard.reset_scratch_data(work, repo_root=tmp_path / "repo", env={})
    assert reset == data and list(data.iterdir()) == []


def test_scratch_writes_are_limited_to_the_work_folder(tmp_path):
    seed, work = _seed(tmp_path), tmp_path / "work"
    data = guard.prepare_scratch_data(seed, work, repo_root=tmp_path / "repo", env={})
    with guard.connect_scratch(data / "state.db", work) as db:
        db.execute("DELETE FROM frames")
    with pytest.raises(SystemExit):
        guard.connect_scratch(seed / "state.db", work)  # the seed lies outside the work folder
    with pytest.raises(SystemExit):
        guard.assert_scratch_file(data / "state.db", work, repo_root=tmp_path / "repo", env={"DATA_DIR": str(data)})


def test_data_dir_launchers_refuse_live_data_dir(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    with pytest.raises(SystemExit):
        guard.assert_scratch_data_dir(live, repo_root=tmp_path / "repo", env={"DATA_DIR": str(live)})
    with pytest.raises(SystemExit):
        guard.assert_scratch_data_dir(live / "inner", repo_root=tmp_path / "repo", env={"DATA_DIR": str(live)})
    assert guard.assert_scratch_data_dir(tmp_path / "other", repo_root=tmp_path / "repo", env={"DATA_DIR": str(live)})


def test_project_id_is_looked_up_by_slug_not_hard_coded(tmp_path):
    seed = _seed(tmp_path, project_id=7)
    assert guard.resolve_project_id(seed / "state.db", SLUG) == 7
    assert guard.resolve_project_id(seed / "state.db", SLUG, explicit=41) == 41
    with pytest.raises(SystemExit):
        guard.resolve_project_id(seed / "state.db", "no-such-slug")


# ---------------------------------------------------------------- the real scripts
def test_live_excel_script_prepares_only_its_copy_and_only_its_project(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    mod = _load("e2e_live_excel_gpt_recovery")
    seed, work = _seed(tmp_path, project_id=7), tmp_path / "work"
    seed_state_before = (seed / "state.db").read_bytes()
    data = mod._prepare_data(seed, work)
    assert mod.PROJECT_ID == 7 and mod.BASE.endswith("/api/projects/7")
    for db_path in (data / "state.db", data / "videos" / SLUG / "project.db"):
        # target project: only the node under test survives; bystander project 8 is untouched
        assert _rows(db_path, "SELECT node_key,status,attempts FROM node_runs WHERE workflow_run_id=70") == [(NODE, "pending", 0)]
        assert len(_rows(db_path, "SELECT 1 FROM node_runs WHERE workflow_run_id=80")) == 2
        assert _rows(db_path, "SELECT status FROM projects WHERE id=8") == [("running",)]
    assert (seed / "state.db").read_bytes() == seed_state_before  # the seed is only read


def test_live_excel_script_refuses_live_work_dir_before_any_delete(tmp_path, monkeypatch):
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setenv("DATA_DIR", str(live))
    mod = _load("e2e_live_excel_gpt_recovery")
    seed = _seed(tmp_path)
    with pytest.raises(SystemExit):
        mod._prepare_data(seed, live)
    with pytest.raises(SystemExit):
        mod._prepare_data(seed, live / "sub")
    assert not (live / "data").exists() and not list(live.iterdir())


def test_text_nodes_script_deletes_frames_only_of_the_resolved_project(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    mod = _load("e2e_text_nodes_recovery")
    seed, work = _seed(tmp_path, project_id=7), tmp_path / "work"
    data = mod._prepare_data(seed, work, "plan", "native-plan-A")
    state = data / "state.db"
    assert _rows(state, "SELECT count(*) FROM frames WHERE project_id=7") == [(0,)]
    assert _rows(state, "SELECT count(*) FROM hitl_requests WHERE project_id=7") == [(0,)]
    assert _rows(state, "SELECT count(*) FROM frames WHERE project_id=8") == [(1,)]  # bystander kept
    assert _rows(state, "SELECT count(*) FROM hitl_requests WHERE project_id=8") == [(1,)]
    assert _rows(state, "SELECT node_key FROM node_runs WHERE workflow_run_id=70") == [("native-plan-A",)]
    assert _rows(state, "SELECT count(*) FROM node_runs WHERE workflow_run_id=80") == [(2,)]


def test_text_nodes_script_refuses_work_dir_equal_to_seed(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    mod = _load("e2e_text_nodes_recovery")
    seed = _seed(tmp_path)
    files = sorted(str(p.relative_to(seed)) for p in seed.rglob("*"))
    with pytest.raises(SystemExit):
        mod._prepare_data(seed, seed, "plan", "native-plan-A")
    assert sorted(str(p.relative_to(seed)) for p in seed.rglob("*")) == files


def test_no_e2e_script_has_a_hard_coded_project_id_or_user_path():
    import re
    for path in sorted(SCRIPTS.glob("e2e_*.py")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"project_id\s*=\s*2\b|\bWHERE id\s*=\s*2\b|projects/2\b|VALUES\(2,", text), path.name
        assert "C:\\video-pipeline" not in text and "C:\\Users" not in text and "/workspace" not in text, path.name
        assert '"/tmp/' not in text, path.name


def test_every_destructive_e2e_script_goes_through_the_guard():
    for name in ("e2e_live_excel_gpt_recovery", "e2e_text_nodes_recovery", "e2e_stop_resume_excel_gpt",
                 "e2e_vibecode_script_frames_qc", "e2e_full_project_check", "e2e_action_layer_fake_paid"):
        text = (SCRIPTS / f"{name}.py").read_text(encoding="utf-8")
        assert "import e2e_guard as guard" in text or "guard = base.guard" in text or "base._prepare_data" in text, name
        assert "shutil.rmtree(" not in text, name  # removal goes only through guard.prepare/reset_scratch_data


# ---------------------------------------------------------------- assembly.subtitles_vf_arg (A19, second half)
def test_plain_subtitle_name_is_unchanged():
    assert subtitles_vf_arg() == "subtitles=subs.ass"
    assert subtitles_vf_arg("my-subs_2.ass") == "subtitles=my-subs_2.ass"


@pytest.mark.parametrize("name", ["a:b.ass", "a,b.ass", "a;b.ass", "a[1].ass", "it's.ass", "a b.ass", "x=y.ass",
                                  "c:\\dir\\f.ass", "a\\b.ass"])
def test_special_characters_are_escaped_on_both_ffmpeg_levels(name):
    vf = subtitles_vf_arg(name)
    assert vf.startswith("subtitles=")
    body = vf[len("subtitles="):]
    if "=" in name:
        assert body.startswith("filename=")
        body = body[len("filename="):]
    # no bare filter/option separator survives: every one is preceded by a backslash
    for i, ch in enumerate(body):
        if ch in ",;[]:' ":
            assert body[i - 1] == "\\", (name, vf)


def test_filter_injection_through_the_file_name_is_not_possible():
    vf = subtitles_vf_arg("x.ass,movie=evil.mp4[v];[v]overlay")
    assert ",movie" not in vf.replace("\\,movie", "") and ";[" not in vf.replace("\\;\\[", "")


@pytest.mark.parametrize("bad", ["", "a\nb.ass", "a\x00.ass"])
def test_unusable_subtitle_names_are_rejected(bad):
    with pytest.raises(ValueError):
        subtitles_vf_arg(bad)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
@pytest.mark.parametrize("name", ["plain.ass", "a,b.ass", "a;b.ass", "a[1].ass", "it's.ass", "a b.ass", "x=y.ass"])
def test_ffmpeg_really_opens_the_escaped_name(tmp_path, name):
    import subprocess
    ass = tmp_path / name
    ass.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 240\n\n[V4+ Styles]\n"
                   "Format: Name, Fontname, Fontsize, PrimaryColour\nStyle: Default,Arial,20,&H00FFFFFF\n\n[Events]\n"
                   "Format: Layer, Start, End, Style, Text\nDialogue: 0,0:00:00.00,0:00:01.00,Default,Hi\n", encoding="utf-8")
    proc = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.2",
                           "-vf", subtitles_vf_arg(name), "-frames:v", "1", "-f", "null", "-"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-400:]
