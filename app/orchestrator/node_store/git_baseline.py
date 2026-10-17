"""Read-only git-шаблон ноды из templates/node_groups/**.

Файл шаблона — ``<node_key>.json`` или ``<node_key>.md``. sha256 считается
по байтам файла целиком: это ``git_sha256`` / ``base_git_sha256``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.orchestrator.node_store._db import GROUP_KEY_RE, NODE_KEY_RE, NodeStoreError

_FRONT_MARK = "---"


@dataclass(frozen=True)
class GitBaseline:
    path: Path | None
    prompt_template: str
    params: dict[str, Any]
    sha256: str | None
    group_key: str | None
    title: str | None

    @property
    def found(self) -> bool:
        return self.path is not None


def default_templates_root() -> Path:
    return Path(__file__).resolve().parents[3] / "templates" / "node_groups"


def load_git_baseline(node_key: str, templates_root: Path | None = None) -> GitBaseline:
    if not NODE_KEY_RE.fullmatch(node_key or ""):
        raise ValueError("node_key должен соответствовать ^n_[a-z0-9_]+$")
    root = templates_root if templates_root is not None else default_templates_root()
    path = _find_template(root, node_key)
    if path is None:
        return GitBaseline(
            path=None,
            prompt_template="",
            params={},
            sha256=None,
            group_key=None,
            title=None,
        )
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if path.suffix == ".json":
        prompt, params, group_key, title = _parse_json(path, raw)
    else:
        prompt, params, group_key, title = _parse_markdown(raw)
    if not group_key:
        group_key = _directory_group(path, root)
    return GitBaseline(
        path=path,
        prompt_template=prompt,
        params=params,
        sha256=digest,
        group_key=group_key,
        title=title,
    )


def _directory_group(path: Path, root: Path) -> str | None:
    """Имя каталога над файлом, если файл не лежит прямо в корне шаблонов."""
    if path.parent.resolve() == root.resolve():
        return None
    name = path.parent.name
    if GROUP_KEY_RE.fullmatch(name):
        return name
    return None


def _find_template(root: Path, node_key: str) -> Path | None:
    if not root.is_dir():
        return None
    matches: list[Path] = []
    for name in (f"{node_key}.json", f"{node_key}.md"):
        matches.extend(path for path in root.rglob(name) if path.is_file())
    if not matches:
        return None
    unique = sorted(set(matches))
    if len(unique) > 1:
        raise NodeStoreError(
            f"несколько git-шаблонов для {node_key}: "
            + ", ".join(str(path) for path in unique)
        )
    return unique[0]


def _parse_json(path: Path, raw: bytes) -> tuple[str, dict[str, Any], str | None, str | None]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NodeStoreError(f"git-шаблон {path} не JSON") from exc
    if not isinstance(payload, dict):
        raise NodeStoreError(f"git-шаблон {path} должен быть объектом")
    if "prompt_template" not in payload:
        raise NodeStoreError(f"git-шаблон {path} без prompt_template")
    prompt = payload["prompt_template"]
    if not isinstance(prompt, str):
        raise NodeStoreError(f"prompt_template в {path} должен быть строкой")
    params = _params_object(payload.get("params", {}), path)
    group_key = payload.get("group_key")
    title = payload.get("title")
    if group_key is not None and not isinstance(group_key, str):
        raise NodeStoreError(f"group_key в {path} должен быть строкой")
    if title is not None and not isinstance(title, str):
        raise NodeStoreError(f"title в {path} должен быть строкой")
    return prompt, params, group_key, title


def _parse_markdown(raw: bytes) -> tuple[str, dict[str, Any], str | None, str | None]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NodeStoreError("git-шаблон markdown не в utf-8") from exc
    if not text.startswith("---"):
        return text, {}, None, None
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FRONT_MARK:
        return text, {}, None, None
    end: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONT_MARK:
            end = index
            break
    if end is None:
        return text, {}, None, None
    meta_text = "".join(lines[1:end]).strip()
    if not meta_text:
        body = "".join(lines[end + 1 :])
        if body.startswith("\n"):
            body = body[1:]
        return body, {}, None, None
    try:
        meta = json.loads(meta_text)
    except json.JSONDecodeError as exc:
        raise NodeStoreError("frontmatter git-шаблона должен быть JSON-объектом") from exc
    if not isinstance(meta, dict):
        raise NodeStoreError("frontmatter git-шаблона должен быть объектом")
    params = _params_object(meta.get("params", {}), Path("frontmatter"))
    group_key = meta.get("group_key")
    title = meta.get("title")
    if group_key is not None and not isinstance(group_key, str):
        raise NodeStoreError("group_key во frontmatter должен быть строкой")
    if title is not None and not isinstance(title, str):
        raise NodeStoreError("title во frontmatter должен быть строкой")
    body = "".join(lines[end + 1 :])
    if body.startswith("\n"):
        body = body[1:]
    return body, params, group_key, title


def _params_object(value: object, path: Path) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise NodeStoreError(f"params в {path} должен быть объектом")
    return dict(value)
