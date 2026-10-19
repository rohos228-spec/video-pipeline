from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "vp_check_delivery", Path(__file__).resolve().parent.parent / "scripts" / "check_delivery.py")
cd = importlib.util.module_from_spec(_SPEC)
sys.modules["vp_check_delivery"] = cd
_SPEC.loader.exec_module(cd)

FAKE_KEY = "s" + "k-" + "A1b2C3d4" * 3
OWNER_PATH = "C:" + chr(92) + "Users" + chr(92) + "Ник" + "ита" + chr(92) + "work"
FOREIGN_PATH = "C:" + chr(92) + "Users" + chr(92) + "SomeoneElse" + chr(92) + "work"


def _make(tmp_path: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return sorted(files)


def _run(tmp_path: Path, files: dict[str, str], **kw) -> dict:
    kw.setdefault("required_docs", ())
    return cd.run_checks(tmp_path, _make(tmp_path, files), **kw)


def test_clean_tree_passes(tmp_path):
    rep = _run(tmp_path, {"README.md": "# ok", "app/x.py": "print(1)\n", ".env.example": "KEY=\n"})
    assert rep["errors"] == [] and rep["warnings"] == []


@pytest.mark.parametrize("rel", [
    ".env", ".env.local", "data/state.db", "app/x.sqlite", "exports/a.json", "logs/a.txt", "web/.next/a.js",
    "app/__pycache__/x.pyc", "pkg/node_modules/a.js", "video_pipeline.egg-info/PKG-INFO", ".clinerules",
    ".skill.deep.txt", "START_AI_ORCHESTRA_PROMPT.txt", ".ai-orchestra-mcp/server.js", "keys/server.pem",
])
def test_forbidden_paths_are_errors(tmp_path, rel):
    rep = _run(tmp_path, {rel: "x"})
    assert any("запрещённый файл" in e and rel in e for e in rep["errors"]), rep


def test_env_example_is_allowed_but_env_content_never_read(tmp_path):
    rep = _run(tmp_path, {".env": f"TOKEN={FAKE_KEY}\n", ".env.example": "TOKEN=\n"})
    assert any(".env " in e or e.endswith(".env (файл по маске .env не поставляется)") for e in rep["errors"])
    assert not any("секрет" in e for e in rep["errors"])
    assert FAKE_KEY not in " ".join(rep["errors"] + rep["warnings"])


def test_secret_in_source_is_error_and_value_not_printed(tmp_path):
    rep = _run(tmp_path, {"app/cfg.py": f'KEY = "{FAKE_KEY}"\n'})
    assert any("возможный секрет в app/cfg.py" in e for e in rep["errors"])
    assert FAKE_KEY not in " ".join(rep["errors"])


def test_secret_in_tests_is_warning_unless_strict(tmp_path):
    files = {"tests/test_a.py": f'K = "{FAKE_KEY}"\n'}
    assert _run(tmp_path, files)["errors"] == []
    assert any("tests/test_a.py" in w for w in _run(tmp_path, files)["warnings"])
    assert any("tests/test_a.py" in e for e in _run(tmp_path, files, strict_tests=True)["errors"])


def test_personal_path_error_foreign_path_warning(tmp_path):
    rep = _run(tmp_path, {"docs/a.md": f"путь {OWNER_PATH}\n", "docs/b.md": f"путь {FOREIGN_PATH}\n",
                          "docs/c.md": "путь C:" + chr(92) + "Users" + chr(92) + "<user>" + chr(92) + "x\n"})
    assert any("личный путь в docs/a.md" in e for e in rep["errors"])
    assert not any("docs/b.md" in e for e in rep["errors"])
    assert any("docs/b.md" in w for w in rep["warnings"])
    assert not any("docs/c.md" in w for w in rep["warnings"])


def test_customer_data_in_path(tmp_path):
    rep = _run(tmp_path, {"docs/tkach-report.html": "x", "scripts/run_spesivtsev_qc.py": "x"})
    assert any("данные заказчика в пути: docs/tkach-report.html" in e for e in rep["errors"])
    assert not any("run_spesivtsev_qc" in e for e in rep["errors"])
    assert any("run_spesivtsev_qc" in w for w in rep["warnings"])


def test_size_limits(tmp_path):
    files = {"a.bin": "x" * 5000, "b.txt": "y"}
    assert any("файл больше" in e for e in _run(tmp_path, files, max_file_mb=0.001)["errors"])
    assert any("общий размер" in e for e in _run(tmp_path, files, max_total_mb=0.001)["errors"])
    assert _run(tmp_path, files)["errors"] == []


def test_required_docs(tmp_path):
    rep = _run(tmp_path, {"README.md": "x"}, required_docs=("README.md", "docs/TESTING.md"))
    assert rep["errors"] == ["нет обязательного файла: docs/TESTING.md"]


def test_real_required_docs_exist_in_repo():
    root = Path(__file__).resolve().parent.parent
    missing = [d for d in cd.REQUIRED_DOCS if not (root / d).exists()]
    assert not missing, missing


def test_push_url_check_is_exact_not_substring():
    ok = "https://github.com/acme/delivery.git"
    assert cd.check_push_url(ok, ok, ["rohos228-spec"]) == []
    sneaky = "https://github.com/rohos228-spec/video-pipeline.git?x=acme/delivery.git"
    errs = cd.check_push_url(sneaky, ok, ["rohos228-spec"])
    assert len(errs) == 2
    assert cd.check_push_url("DISABLED_PUSH_FOR_DIAGNOSTICS", ok, [])
    assert cd.check_push_url("", ok, [])


def test_main_exit_codes_walk_mode(tmp_path, capsys):
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    assert cd.main(["--root", str(tmp_path), "--walk"]) == 1
    out = capsys.readouterr().out
    assert "нет обязательного файла" in out
    for rel in cd.REQUIRED_DOCS:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    assert cd.main(["--root", str(tmp_path), "--walk"]) == 0
