"""Ensure local .env has ORCHESTRATOR_GIT_BRANCH=housepc (no secrets printed)."""

from __future__ import annotations

from pathlib import Path

KEY = "ORCHESTRATOR_GIT_BRANCH"
WANT = "housepc"
ENV = Path(".env")


def main() -> None:
    if not ENV.is_file():
        ENV.write_text(f"{KEY}={WANT}\n", encoding="utf-8")
        print("created .env with", KEY)
        return
    lines = ENV.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[str] = []
    found = False
    for ln in lines:
        if ln.strip().startswith(f"{KEY}="):
            out.append(f"{KEY}={WANT}")
            found = True
        else:
            out.append(ln)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"# PC-local git branch for orchestrator push")
        out.append(f"{KEY}={WANT}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("updated" if found else "appended", KEY, "=", WANT)


if __name__ == "__main__":
    main()
