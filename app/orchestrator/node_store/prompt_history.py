"""Откат мастер-промта: текущий текст сверяется с after снимка.

Снимок самой записи на #342 пишет `_wrap_executor`. Здесь — отказ 409,
если после снимка текст уже изменили.
"""

from __future__ import annotations

from app.orchestrator.node_store.errors import VersionConflict


class PromptHistory:
    """Тексты общих промтов и снимки save_master."""

    def __init__(self) -> None:
        self.texts: dict[tuple[str, str], str] = {}
        self.versions: dict[tuple[str, str], int] = {}
        self.snapshots: list[dict] = []

    def read(self, step_code: str, name: str) -> str:
        key = (step_code, name)
        if key not in self.texts:
            raise KeyError("промт не найден")
        return self.texts[key]

    def save_master(
        self, *, step_code: str, name: str, content: str, expected_version: int
    ) -> dict:
        key = (step_code, name)
        current = self.versions.get(key, 0)
        if current != int(expected_version):
            raise VersionConflict(
                f"версия мастер-промта {expected_version} устарела, текущая {current}"
            )
        before = self.texts.get(key, "")
        self.texts[key] = content
        self.versions[key] = current + 1
        row = {
            "id": len(self.snapshots) + 1,
            "action": "prompt.save_master",
            "target": {"step_code": step_code, "name": name},
            "before": before,
            "after": content,
        }
        self.snapshots.append(row)
        return dict(row)

    def rollback_master_prompt(self, snapshot_id: int) -> dict:
        row = self._get(snapshot_id)
        target = row["target"]
        current = self.read(target["step_code"], target["name"])
        if current != row["after"]:
            raise VersionConflict(
                "текст мастер-промта изменился после снимка: откат отклонён"
            )
        self.texts[(target["step_code"], target["name"])] = row["before"]
        return {"restored_snapshot_id": row["id"], "content": row["before"]}

    def _get(self, snapshot_id: int) -> dict:
        for row in self.snapshots:
            if row["id"] == snapshot_id and row["action"] == "prompt.save_master":
                return row
        raise KeyError(f"снимка мастер-промта {snapshot_id} нет")
