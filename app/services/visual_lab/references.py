"""Reference image management for the visual lab.

References are the 5 user-supplied "this is what good looks like" images
plus their prompts. They serve as:

* anchor for GPT in the analyze/think phases ("score relative to these")
* benchmark for the stopping rule (target = mean reference score)
* seed for ``keyword_effects`` (words from reference prompts are tagged
  as known-positive in the first knowledge update).

Sample reference images are bundled with the repo at
``assets/visual_lab/reference_examples/`` so a fresh project can
``copy_seed_references`` to bootstrap.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from loguru import logger

from app.services.visual_lab.models import ReferenceImage
from app.services.visual_lab.storage import LabStorage

# Repo-bundled examples ship as PNG (pixel-art friendly).
_REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_REFERENCE_DIR: Path = _REPO_ROOT / "assets" / "visual_lab" / "reference_examples"


def add_reference(
    storage: LabStorage,
    *,
    image_path: Path,
    prompt: str,
    scores: dict[str, int] | None = None,
    notes: str = "",
    target_filename: str | None = None,
) -> ReferenceImage:
    """Copy ``image_path`` into ``<project>/reference/`` and write JSON metadata."""
    storage.reference_dir.mkdir(parents=True, exist_ok=True)
    if not image_path.exists():
        raise FileNotFoundError(f"reference image not found: {image_path}")

    ext = image_path.suffix.lower().lstrip(".")
    if not ext:
        ext = "png"

    if target_filename:
        target_name = target_filename
    else:
        existing = [p for p in storage.reference_dir.iterdir() if p.is_file()]
        n = len([p for p in existing if p.suffix.lower() != ".json"]) + 1
        target_name = f"ref_{n}.{ext}"

    target_path = storage.reference_dir / target_name
    shutil.copy2(image_path, target_path)

    ref = ReferenceImage(
        file=target_name,
        prompt=prompt.strip(),
        scores=scores or {},
        notes=notes,
    )
    json_path = target_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(ref.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    project = storage.load_project()
    if project is not None and target_name not in project.references:
        project.references.append(target_name)
        storage.save_project(project)

    logger.info(
        "visual_lab.references[{}]: added {} (prompt={} chars)",
        storage.slug,
        target_name,
        len(prompt),
    )
    return ref


def copy_seed_references(
    storage: LabStorage,
    *,
    prompts: list[str] | None = None,
    seed_dir: Path | None = None,
) -> list[ReferenceImage]:
    """Copy all bundled reference examples into the project, with optional prompts.

    ``prompts`` should be the same length as the number of seed files
    (sorted alphabetically). If shorter, remaining references get an
    empty prompt placeholder. Missing seed dir or empty seed list is a
    no-op + warning.
    """
    src = seed_dir if seed_dir is not None else SEED_REFERENCE_DIR
    if not src.exists():
        logger.warning("visual_lab.references: seed dir missing: {}", src)
        return []
    seed_files = sorted(
        p for p in src.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}
    )
    if not seed_files:
        logger.warning("visual_lab.references: no seed files in {}", src)
        return []

    prompts = prompts or []
    out: list[ReferenceImage] = []
    for i, src_file in enumerate(seed_files):
        prompt = prompts[i] if i < len(prompts) else ""
        out.append(
            add_reference(
                storage,
                image_path=src_file,
                prompt=prompt,
                target_filename=src_file.name,
            )
        )
    return out


def load_references(storage: LabStorage) -> list[ReferenceImage]:
    """Load all ``ref_<n>.json`` files from the project's reference/ dir."""
    out: list[ReferenceImage] = []
    if not storage.reference_dir.exists():
        return out
    for json_path in sorted(storage.reference_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            out.append(ReferenceImage.model_validate(data))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "visual_lab.references: cannot load {}: {}", json_path, e
            )
    return out


__all__ = [
    "SEED_REFERENCE_DIR",
    "add_reference",
    "copy_seed_references",
    "load_references",
]
