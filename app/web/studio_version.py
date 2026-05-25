"""Read web/STUDIO_VERSION — UI build label + backend runtime fingerprint."""

from __future__ import annotations

from pathlib import Path


def _version_file() -> Path:
    return Path(__file__).resolve().parents[2] / "web" / "STUDIO_VERSION"


def _parse_version_file() -> tuple[int, str, str, str]:
    path = _version_file()
    build = 0
    sha = "dev"
    attach_expected = ""
    orchestrator_expected = ""
    if path.is_file():
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            try:
                build = int(lines[0].strip())
            except ValueError:
                build = 0
        if len(lines) > 1 and lines[1].strip():
            sha = lines[1].strip()
        if len(lines) > 2 and lines[2].strip():
            attach_expected = lines[2].strip()
        if len(lines) > 3 and lines[3].strip():
            orchestrator_expected = lines[3].strip()
    return build, sha, attach_expected, orchestrator_expected


def read_studio_version() -> dict[str, str | int | bool]:
    build, sha, attach_expected, orchestrator_expected = _parse_version_file()
    label = f"v{build} · {sha[:7]}" if sha and sha != "dev" else f"v{build}"

    from app.bots.chatgpt import CHATGPT_ATTACH_LOGIC_ID
    from app.services.xlsx_step_runners import XLSX_STEP_RUNNERS_ID

    backend_attach = CHATGPT_ATTACH_LOGIC_ID
    backend_orchestrator = XLSX_STEP_RUNNERS_ID
    attach_ok = not attach_expected or attach_expected == backend_attach
    orchestrator_ok = (
        not orchestrator_expected or orchestrator_expected == backend_orchestrator
    )

    return {
        "build": build,
        "sha": sha,
        "label": label,
        "attach_expected": attach_expected,
        "backend_attach": backend_attach,
        "backend_ok": attach_ok,
        "orchestrator_expected": orchestrator_expected,
        "backend_orchestrator": backend_orchestrator,
        "orchestrator_ok": orchestrator_ok,
        "pipeline_ok": attach_ok and orchestrator_ok,
    }


def read_studio_version_label() -> str:
    return str(read_studio_version()["label"])
