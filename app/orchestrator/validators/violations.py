"""Нарушение валидатора. Только данные, без записи в проект."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Violation:
    code: str
    severity: str
    path: str
    id: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def sorted_violations(items: list[Violation]) -> list[Violation]:
    return sorted(items, key=lambda item: (item.code, item.path, item.id, item.severity))
