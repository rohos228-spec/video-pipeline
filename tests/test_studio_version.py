from pathlib import Path

from app.web.studio_version import read_studio_version


def test_read_studio_version_from_file(tmp_path: Path, monkeypatch) -> None:
    vf = tmp_path / "STUDIO_VERSION"
    vf.write_text(
        "42\nabc1234\nanim-pr-two-phase-v79\nxlsx_step_runners-v70\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.web.studio_version._version_file", lambda: vf)
    data = read_studio_version()
    assert data["build"] == 42
    assert data["sha"] == "abc1234"
    assert data["label"] == "v42 · abc1234"
    assert data["attach_expected"] == "anim-pr-two-phase-v79"
    assert data["backend_attach"] == "anim-pr-two-phase-v79"
    assert data["backend_ok"] is True
    assert data["orchestrator_ok"] is True
    assert data["pipeline_ok"] is True


def test_ui_stale_when_out_old(tmp_path: Path, monkeypatch) -> None:
    vf = tmp_path / "STUDIO_VERSION"
    vf.write_text("99\ndeadbeef\n\n\n", encoding="utf-8")
    monkeypatch.setattr("app.web.studio_version._version_file", lambda: vf)

    def fake_baked() -> int:
        return 102

    monkeypatch.setattr("app.web.studio_version._read_baked_ui_build", fake_baked)
    data = read_studio_version()
    assert data["build"] == 99
    assert data["ui_baked_build"] == 102
    assert data["ui_stale"] is True
