"""Atomic JSON storage for visual-lab projects.

Lives on disk under ``data/test_prompts/<slug>/``. Reads return Pydantic
models; writes are atomic (write to ``.tmp`` then rename) so a crashed
phase never leaves a half-written JSON file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from loguru import logger
from pydantic import BaseModel

from app.services.visual_lab.models import (
    IterDoc,
    KnowledgeBase,
    ProjectDoc,
    ThinkingLog,
    WordTest,
)
from app.settings import settings


def project_dir(slug: str) -> Path:
    """Resolve the on-disk root for a given project slug."""
    return Path(settings.data_dir) / "test_prompts" / slug


M = TypeVar("M", bound=BaseModel)


def _atomic_write_json(path: Path, payload: dict | list) -> None:
    """Write JSON to a temp file in the same dir then rename atomically.

    On Windows os.replace handles cross-file overwrites, but we still
    cannot rename across drives — temp file is created in the same dir
    explicitly.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with _suppress():
            os.unlink(tmp_name)
        raise


class _suppress:
    """Local replacement for contextlib.suppress(Exception) without imports."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return True


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class LabStorage:
    """File-system facade for one visual-lab project.

    All methods are synchronous (file IO) and side-effect-free aside from
    writing under ``self.root``.
    """

    def __init__(self, slug: str, *, root: Path | None = None) -> None:
        self.slug = slug
        self.root = root if root is not None else project_dir(slug)

    # ----- paths -----

    @property
    def project_path(self) -> Path:
        return self.root / "project.json"

    @property
    def knowledge_path(self) -> Path:
        return self.root / "knowledge_base.json"

    @property
    def thinking_path(self) -> Path:
        return self.root / "thinking_log.json"

    @property
    def word_tests_path(self) -> Path:
        return self.root / "word_tests.json"

    @property
    def excel_path(self) -> Path:
        return self.root / "scores.xlsx"

    @property
    def report_path(self) -> Path:
        return self.root / "report.md"

    @property
    def reference_dir(self) -> Path:
        return self.root / "reference"

    def iter_dir(self, n: int) -> Path:
        return self.root / f"iter_{n}"

    def iter_path(self, n: int) -> Path:
        return self.iter_dir(n) / "iter.json"

    def iter_image_path(self, n: int, ext: str = "jpg") -> Path:
        return self.iter_dir(n) / f"image.{ext}"

    def iter_chat_dir(self, n: int) -> Path:
        return self.iter_dir(n) / "chat"

    # ----- read helpers -----

    def _read_model(self, path: Path, model: type[M]) -> M | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.error("visual_lab.storage: cannot parse {}: {}", path, e)
            return None
        try:
            return model.model_validate(raw)
        except Exception as e:  # noqa: BLE001
            logger.error("visual_lab.storage: schema mismatch in {}: {}", path, e)
            return None

    def load_project(self) -> ProjectDoc | None:
        return self._read_model(self.project_path, ProjectDoc)

    def load_knowledge(self) -> KnowledgeBase:
        kb = self._read_model(self.knowledge_path, KnowledgeBase)
        return kb if kb is not None else KnowledgeBase()

    def load_thinking_log(self) -> ThinkingLog:
        tl = self._read_model(self.thinking_path, ThinkingLog)
        return tl if tl is not None else ThinkingLog()

    def load_iter(self, n: int) -> IterDoc | None:
        return self._read_model(self.iter_path(n), IterDoc)

    def load_word_tests(self) -> list[WordTest]:
        if not self.word_tests_path.exists():
            return []
        try:
            raw = json.loads(self.word_tests_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.error("visual_lab.storage: word_tests parse error: {}", e)
            return []
        tests = raw.get("tests", []) if isinstance(raw, dict) else raw
        out: list[WordTest] = []
        for item in tests:
            try:
                out.append(WordTest.model_validate(item))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "visual_lab.storage: skipping bad word_test entry: {}", e
                )
        return out

    # ----- write helpers -----

    def save_project(self, project: ProjectDoc) -> None:
        project.updated_at = _now()
        _atomic_write_json(self.project_path, project.model_dump(mode="json"))

    def save_knowledge(self, kb: KnowledgeBase) -> None:
        _atomic_write_json(self.knowledge_path, kb.model_dump(mode="json"))

    def save_thinking_log(self, tl: ThinkingLog) -> None:
        _atomic_write_json(self.thinking_path, tl.model_dump(mode="json"))

    def save_iter(self, doc: IterDoc) -> None:
        self.iter_dir(doc.iter).mkdir(parents=True, exist_ok=True)
        _atomic_write_json(
            self.iter_path(doc.iter), doc.model_dump(mode="json")
        )

    def save_word_tests(self, tests: list[WordTest]) -> None:
        payload = {"tests": [t.model_dump(mode="json") for t in tests]}
        _atomic_write_json(self.word_tests_path, payload)

    # ----- listing -----

    def list_iter_numbers(self) -> list[int]:
        nums: list[int] = []
        if not self.root.exists():
            return nums
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            name = child.name
            if not name.startswith("iter_"):
                continue
            try:
                nums.append(int(name.split("_", 1)[1]))
            except ValueError:
                continue
        return sorted(nums)

    def load_all_iters(self) -> list[IterDoc]:
        out: list[IterDoc] = []
        for n in self.list_iter_numbers():
            doc = self.load_iter(n)
            if doc is not None:
                out.append(doc)
        return out

    # ----- bootstrap -----

    def ensure_skeleton(self, name: str) -> ProjectDoc:
        """Create empty project/knowledge/thinking/word_tests files if absent.

        Returns the loaded (or freshly created) ProjectDoc.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self.reference_dir.mkdir(parents=True, exist_ok=True)

        proj = self.load_project()
        if proj is None:
            proj = ProjectDoc(slug=self.slug, name=name)
            self.save_project(proj)
        if not self.knowledge_path.exists():
            self.save_knowledge(KnowledgeBase())
        if not self.thinking_path.exists():
            self.save_thinking_log(ThinkingLog())
        if not self.word_tests_path.exists():
            self.save_word_tests([])
        return proj
