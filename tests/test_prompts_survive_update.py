"""Five independent proofs that user prompts survive Studio update / sync / API.

No data/ overlay. Source of truth remains ``prompts/``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "scripts" / "return_prompts_from_stash.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("return_prompts_from_stash", HELPER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_prompt_repo(tmp_path: Path) -> tuple[Path, str]:
    """Tiny git repo mimicking prompts/ + app/, returns (repo, origin_tip_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "prompts" / "05_excel_gpt").mkdir(parents=True)
    (repo / "app").mkdir()
    (repo / "prompts" / "05_excel_gpt" / "a.md").write_text("stock A\n", encoding="utf-8")
    (repo / "prompts" / "05_excel_gpt" / "b.md").write_text("stock B\n", encoding="utf-8")
    (repo / "app" / "main.py").write_text("code1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    (repo / "prompts" / "05_excel_gpt" / "b.md").write_text("stock B v2\n", encoding="utf-8")
    (repo / "app" / "main.py").write_text("code2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "origin update")
    origin = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "reset", "--hard", "HEAD~1")
    return repo, origin


# ---------------------------------------------------------------------------
# 1) Full STUDIO [4] simulation: stash → reset --hard → return prompts
# ---------------------------------------------------------------------------
def test_1_studio_update_keeps_edited_and_new_prompts(tmp_path: Path) -> None:
    helper = _load_helper()
    repo, origin = _init_prompt_repo(tmp_path)
    (repo / "prompts" / "05_excel_gpt" / "a.md").write_text("USER custom A\n", encoding="utf-8")
    (repo / "prompts" / "05_excel_gpt" / "custom.md").write_text("USER new\n", encoding="utf-8")
    (repo / "app" / "main.py").write_text("local hack\n", encoding="utf-8")

    report = helper.simulate_studio_update(repo, branch_ref=origin)
    assert report.get("ok"), report

    assert (repo / "prompts" / "05_excel_gpt" / "a.md").read_text(encoding="utf-8") == "USER custom A\n"
    assert (repo / "prompts" / "05_excel_gpt" / "custom.md").read_text(encoding="utf-8") == "USER new\n"
    # Untouched stock file must take origin update, not old stash snapshot.
    assert (repo / "prompts" / "05_excel_gpt" / "b.md").read_text(encoding="utf-8") == "stock B v2\n"
    # App code must stay on origin (local hack discarded by design).
    assert (repo / "app" / "main.py").read_text(encoding="utf-8") == "code2\n"


# ---------------------------------------------------------------------------
# 2) Without return step prompts vanish — proves the failure mode we fixed
# ---------------------------------------------------------------------------
def test_2_reset_alone_wipes_prompts_proves_bug(tmp_path: Path) -> None:
    repo, origin = _init_prompt_repo(tmp_path)
    (repo / "prompts" / "05_excel_gpt" / "a.md").write_text("USER custom A\n", encoding="utf-8")
    (repo / "prompts" / "05_excel_gpt" / "custom.md").write_text("USER new\n", encoding="utf-8")
    _git(repo, "stash", "push", "-u", "-m", "studio: автосохранение перед обновлением")
    _git(repo, "reset", "--hard", origin)

    assert (repo / "prompts" / "05_excel_gpt" / "a.md").read_text(encoding="utf-8") == "stock A\n"
    assert not (repo / "prompts" / "05_excel_gpt" / "custom.md").exists()


# ---------------------------------------------------------------------------
# 3) API upload/write path: file lands in prompts/ and survives simulated update
# ---------------------------------------------------------------------------
def test_3_write_prompt_then_update_keeps_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_helper()
    repo, origin = _init_prompt_repo(tmp_path)

    from app.services import prompt_library as plib

    monkeypatch.setattr(plib, "PROMPTS_ROOT", repo / "prompts")
    # excel_gpt folder map already points at 05_excel_gpt
    plib.write_prompt("excel_gpt", "uploaded_by_user", "UPLOAD BODY\n")
    path = repo / "prompts" / "05_excel_gpt" / "uploaded_by_user.md"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "UPLOAD BODY\n"

    report = helper.simulate_studio_update(repo, branch_ref=origin)
    assert report.get("ok"), report
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "UPLOAD BODY\n"


# ---------------------------------------------------------------------------
# 4) sync_prompts_from_files must NOT delete or rewrite custom prompt files
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_4_db_sync_does_not_touch_custom_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base
    from app.prompts_loader import sync_prompts_from_files
    from app.services import prompt_library as plib

    prompts = tmp_path / "prompts"
    for _code, folder in plib.STEP_FOLDERS.items():
        d = prompts / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "default.md").write_text(f"default for {_code}\n", encoding="utf-8")
    custom = prompts / "05_excel_gpt" / "keep_me.md"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text("CUSTOM MUST STAY\n", encoding="utf-8")
    before = custom.read_bytes()

    monkeypatch.setattr(plib, "PROMPTS_ROOT", prompts)
    monkeypatch.setattr("app.prompts_loader.PROMPTS_ROOT", prompts)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sync.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.prompts_loader.session_scope", _scope)
    monkeypatch.setattr("app.db.session_scope", _scope)

    loader_src = (REPO / "app" / "prompts_loader.py").read_text(encoding="utf-8")
    assert "unlink" not in loader_src
    assert "rmtree" not in loader_src

    await sync_prompts_from_files()
    assert custom.is_file()
    assert custom.read_bytes() == before


# ---------------------------------------------------------------------------
# 5) StudioUpdateCore / helper script path + abort contract in studio.ps1 text
# ---------------------------------------------------------------------------
def test_5_update_scripts_wire_prompt_return_and_abort() -> None:
    studio = (REPO / "scripts" / "studio.ps1").read_text(encoding="utf-8")
    core = (REPO / "scripts" / "StudioUpdateCore.ps1").read_text(encoding="utf-8")
    launcher = (REPO / "installer" / "VideoPipelineLauncher.ps1").read_text(encoding="utf-8")
    helper_ps1 = REPO / "scripts" / "Return-PromptsFromStash.ps1"
    helper_py = REPO / "scripts" / "return_prompts_from_stash.py"

    assert helper_ps1.is_file()
    assert helper_py.is_file()
    assert "Invoke-StudioReturnPromptEditsFromStash" in studio
    assert "Test-StudioPromptsDirty" in studio
    assert "stash не удался — обновление отменено" in studio
    assert "return_prompts_from_stash.py" in studio
    assert "return_prompts_from_stash.py" in core
    assert "return_prompts_from_stash.py" in launcher
    assert "Return local prompts/" in launcher
    assert "Return-PromptsFromStash.ps1" in studio
    assert "stash failed and prompts/" in core
    assert "stash push -u" in core
    # No banned migration resurfacing
    assert "prompts_preserve" not in studio
    assert "prompts_preserve" not in core
    assert not (REPO / "app" / "services" / "prompt_update_protect.py").exists()


# ---------------------------------------------------------------------------
# Bonus path: Cyrillic / spaces in filename (real user uploads)
# ---------------------------------------------------------------------------
def test_bonus_unicode_prompt_name_survives_update(tmp_path: Path) -> None:
    helper = _load_helper()
    repo, origin = _init_prompt_repo(tmp_path)
    name = "мой промт 11.6 полька.md"
    target = repo / "prompts" / "05_excel_gpt" / name
    target.write_text("UNICODE BODY\n", encoding="utf-8")

    report = helper.simulate_studio_update(repo, branch_ref=origin)
    assert report.get("ok"), report
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "UNICODE BODY\n"
