"""Ревью #352 п.20: dev-скрипты требуют VP_DEV_SEED и явный id, PNG не затирают."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dev_checks_refuse_without_seed(monkeypatch) -> None:
    monkeypatch.delenv("VP_DEV_SEED", raising=False)
    for name in (
        "dev_check_montage_anchor_add.py",
        "dev_check_montage_rows.py",
        "dev_check_montage_refs.py",
        "run_scene_design_group.py",
    ):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / name)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "VP_DEV_SEED"},
            check=False,
        )
        assert proc.returncode != 0
        assert "VP_DEV_SEED" in (proc.stderr + proc.stdout)


def test_seed_png_is_not_overwritten(tmp_path, monkeypatch) -> None:
    from PIL import Image

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dev_seed_montage_scene", ROOT / "scripts" / "dev_seed_montage_scene.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    dest_dir = tmp_path / "characters"
    dest_dir.mkdir()
    marker = dest_dir / "c01.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(marker)
    before = marker.read_bytes()

    class _Dir:
        def __truediv__(self, name):
            return tmp_path / name

    monkeypatch.setattr(mod, "ASSETS", [("characters", "c01", "character", "x", (9, 9, 9))])
    mod._seed_assets(tmp_path)
    assert marker.read_bytes() == before
