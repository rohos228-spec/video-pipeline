"""MockActionBackend: ответы Action Layer без сети и без оплат.

Платные действия запоминаются по каноническому ключу. Повтор — paid-retry guard,
стоимость повторного вызова 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.orchestrator.rag.canon import sha256_payload
from app.orchestrator.rag.node_store import NODE_KEY_RE, MemoryNodeStore, ResolvedNode
from evals.orchestrator.checks import ACTION_WHITELIST, path_denied
from evals.orchestrator.sanitize import sanitize_html

PAID_ACTIONS = frozenset({"run_node", "resume_node"})

# Тот же реестр, что у plan_sanity. rollback доступен только через rollback_prompt.
ALLOWED_ACTIONS = ACTION_WHITELIST


@dataclass
class ActionResult:
    ok: bool
    data: dict[str, Any]
    change_id: str | None = None
    cost: float = 0.0


@dataclass
class MockActionBackend:
    node_store: MemoryNodeStore = field(default_factory=MemoryNodeStore)
    calls: list[dict[str, Any]] = field(default_factory=list)
    reports: dict[int, str] = field(default_factory=dict)
    _paid: dict[str, ActionResult] = field(default_factory=dict)
    spend_usd: float = 0.0
    _report_seq: int = 1
    stop_key: bool = False
    allow_new_actions: bool = True

    def execute(
        self, name: str, params: dict[str, Any] | None = None, task_ctx: dict[str, Any] | None = None
    ) -> ActionResult:
        params = dict(params or {})
        self.calls.append({"action": name, "params": params})
        if not self.allow_new_actions or self.stop_key:
            return ActionResult(False, {"error": "killed"}, cost=0.0)
        if name not in ALLOWED_ACTIONS:
            return ActionResult(False, {"error": "not_in_whitelist", "action": name}, cost=0.0)
        handler = getattr(self, f"_act_{name}", None)
        if handler is None:
            return ActionResult(False, {"error": "not_in_whitelist", "action": name}, cost=0.0)
        if name in PAID_ACTIONS:
            key = sha256_payload({"action": name, "params": params})
            previous = self._paid.get(key)
            if previous is not None:
                return ActionResult(
                    False,
                    {"error": "paid_retry_blocked", "action": name},
                    change_id=previous.change_id,
                    cost=0.0,
                )
            result = handler(params, task_ctx or {})
            self._paid[key] = result
            self.spend_usd += result.cost
            return result
        return handler(params, task_ctx or {})

    def _act_read_pipeline_code(self, params: dict[str, Any], _ctx: dict[str, Any]) -> ActionResult:
        path = str(params.get("path") or "")
        if path_denied(path):
            return ActionResult(False, {"error": "path_denied", "path": path})
        return ActionResult(True, {"content": "stub", "truncated": False, "path": path})

    def _act_get_node(self, params: dict[str, Any], _ctx: dict[str, Any]) -> ActionResult:
        node_key = str(params.get("node_key") or "")
        node = self.node_store.resolve_node(node_key)
        if node is None:
            return ActionResult(True, {"node_key": node_key, "source": "mock", "found": False})
        return ActionResult(
            True,
            {
                "node_key": node.node_key,
                "title": node.title,
                "version": node.version,
                "source": node.source,
                "found": True,
            },
        )

    def _act_run_node(self, params: dict[str, Any], _ctx: dict[str, Any]) -> ActionResult:
        return ActionResult(
            True,
            {"project_id": params.get("project_id"), "node_key": params.get("node_key"), "mock": True},
            change_id="chg-run-node",
            cost=1.0,
        )

    def _act_resume_node(self, params: dict[str, Any], ctx: dict[str, Any]) -> ActionResult:
        return self._act_run_node(params, ctx)

    def _act_run_validators(self, params: dict[str, Any], _ctx: dict[str, Any]) -> ActionResult:
        return ActionResult(
            True, {"project_id": params.get("project_id"), "validators": params.get("validators") or []}
        )

    def _act_create_node(self, params: dict[str, Any], _ctx: dict[str, Any]) -> ActionResult:
        node_key = str(params.get("node_key") or "")
        if not NODE_KEY_RE.match(node_key):
            return ActionResult(False, {"error": "bad_node_key"})
        if not str(params.get("prompt_template") or "").strip():
            return ActionResult(False, {"error": "prompt_required"})
        if not str(params.get("group") or params.get("group_key") or "").strip():
            return ActionResult(False, {"error": "group_required"})
        raw_params = params.get("params") if isinstance(params.get("params"), dict) else {}
        node = ResolvedNode(
            node_key=node_key,
            group_key=str(params.get("group") or params.get("group_key")),
            title=str(params.get("title") or node_key),
            status="active",
            prompt_template=str(params["prompt_template"]),
            source="db",
            version=1,
            db_version=1,
            git_sha256=None,
            stale_base=False,
            params=dict(raw_params),
            change_id="chg-create-node",
            description=str(params.get("title") or ""),
        )
        self.node_store.upsert(node)
        return ActionResult(True, {"node_key": node_key, "version": 1}, change_id="chg-create-node")

    def _act_edit_node_prompt(self, params: dict[str, Any], _ctx: dict[str, Any]) -> ActionResult:
        node_key = str(params.get("node_key") or "")
        node = self.node_store.resolve_node(node_key)
        if node is None:
            return ActionResult(False, {"error": "node_missing"})
        mode = str(params.get("patch_mode") or "")
        content = str(params.get("content") or "")
        anchor = str(params.get("section_anchor") or "")
        if mode == "replace_full":
            if not content.strip():
                return ActionResult(False, {"error": "content_required"})
            node.prompt_template = content if content.endswith("\n") else content + "\n"
        elif mode == "append_section":
            if not anchor.strip():
                return ActionResult(False, {"error": "anchor_required"})
            block = f"## {anchor}\n{content.rstrip()}\n"
            base = node.prompt_template
            if base and not base.endswith("\n"):
                base += "\n"
            node.prompt_template = base + block
        elif mode == "replace_section":
            updated, ok = replace_section(node.prompt_template, anchor, content)
            if not ok:
                return ActionResult(False, {"error": "section_missing", "anchor": anchor})
            node.prompt_template = updated
        else:
            return ActionResult(False, {"error": "unsupported_patch_mode"})
        node.version += 1
        node.db_version = node.version
        node.change_id = f"chg-prompt-{node.version}"
        self.node_store.upsert(node)
        return ActionResult(True, {"node_key": node_key, "version": node.version}, change_id=node.change_id)

    def _act_rollback(self, params: dict[str, Any], _ctx: dict[str, Any]) -> ActionResult:
        node_key = str(params.get("node_key") or "")
        previous = str(params.get("prompt_template") or "")
        node = self.node_store.resolve_node(node_key)
        if node is None:
            return ActionResult(False, {"error": "node_missing"})
        node.prompt_template = previous
        node.version += 1
        node.db_version = node.version
        node.change_id = f"chg-rollback-{node.version}"
        node.params = {**node.params, "kind": "rollback"}
        self.node_store.upsert(node)
        return ActionResult(True, {"node_key": node_key, "version": node.version}, change_id=node.change_id)

    def _act_create_report(self, params: dict[str, Any], _ctx: dict[str, Any]) -> ActionResult:
        html = _sections_html(params.get("sections") or params.get("html"))
        clean = sanitize_html(html)
        report_id = self._report_seq
        self._report_seq += 1
        self.reports[report_id] = clean
        return ActionResult(
            True, {"report_id": report_id, "html": clean}, change_id=f"chg-report-{report_id}"
        )

    def _act_update_report(self, params: dict[str, Any], _ctx: dict[str, Any]) -> ActionResult:
        report_id = int(params.get("report_id") or 0)
        if report_id not in self.reports:
            return ActionResult(False, {"error": "report_missing"})
        html = _sections_html(params.get("sections_patch") or params.get("html"))
        clean = sanitize_html(html)
        self.reports[report_id] = clean
        return ActionResult(
            True, {"report_id": report_id, "html": clean}, change_id=f"chg-report-upd-{report_id}"
        )


def rollback_prompt(backend: MockActionBackend, node_key: str, prompt_template: str) -> ActionResult:
    """Откат промпта для тестов. Не проходит через ``execute`` и не в whitelist."""
    return backend._act_rollback(
        {"node_key": node_key, "prompt_template": prompt_template},
        {},
    )


def replace_section(template: str, anchor: str, content: str) -> tuple[str, bool]:
    marker = f"## {anchor}"
    lines = template.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == marker:
            start = index
            break
    if start is None:
        return template, False
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    new_lines = [*lines[: start + 1], content.rstrip("\n"), *lines[end:]]
    return "\n".join(new_lines).rstrip() + "\n", True


def _sections_html(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for section in raw:
            if isinstance(section, str):
                parts.append(section)
            elif isinstance(section, dict):
                parts.append(str(section.get("html") or ""))
        return "\n".join(parts)
    return ""
