"""runtime_streams.json + defaults."""

from __future__ import annotations

from pathlib import Path

from app.services import runtime_streams as rs


def test_save_and_load_runtime_streams(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rs.settings, "data_dir", tmp_path)
    monkeypatch.setattr(rs.settings, "worker_max_parallel", 1)
    monkeypatch.setattr(rs.settings, "img_max_streams", 1)
    monkeypatch.setattr(rs.settings, "check_max_streams", 2)

    cfg = rs.save_runtime_streams(
        {
            "worker_max_parallel": 3,
            "default_outsee_streams": 2,
            "default_check_streams": 4,
        }
    )
    assert cfg["worker_max_parallel"] == 3
    assert cfg["default_outsee_streams"] == 2
    assert cfg["default_check_streams"] == 4
    assert (tmp_path / "runtime_streams.json").is_file()
    assert rs.get_worker_max_parallel() == 3
    assert rs.get_default_outsee_streams() == 2


def test_clamp_worker_bounds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rs.settings, "data_dir", tmp_path)
    monkeypatch.setattr(rs.settings, "worker_max_parallel", 1)
    cfg = rs.save_runtime_streams({"worker_max_parallel": 99})
    assert cfg["worker_max_parallel"] == 4
    cfg = rs.save_runtime_streams({"worker_max_parallel": 0})
    assert cfg["worker_max_parallel"] == 1
