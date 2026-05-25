from pathlib import Path

from app.web.studio_version import read_studio_version


def test_read_studio_version_from_file(tmp_path: Path, monkeypatch) -> None:
    vf = tmp_path / "STUDIO_VERSION"
    vf.write_text(
        "42\nabc1234\ndrag-drop-primary-v75\nxlsx_step_runners-v70\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.web.studio_version._version_file", lambda: vf)
    data = read_studio_version()
    assert data["build"] == 42
    assert data["sha"] == "abc1234"
    assert data["label"] == "v42 · abc1234"
    assert data["attach_expected"] == "drag-drop-primary-v75"
    assert data["backend_attach"] == "drag-drop-primary-v75"
    assert data["backend_ok"] is True
    assert data["orchestrator_ok"] is True
    assert data["pipeline_ok"] is True
