"""Норма длины закадра: 200–300 символов на фрагмент.

Чистая функция без зависимостей от агентного ядра. Пайплайн
(``scene_boundaries``) и валидаторы оркестратора зовут только её.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_VO_CHARS = 200
MAX_VO_CHARS = 300


@dataclass(frozen=True)
class Violation:
    index: int
    length: int
    kind: str
    fragment: str

    @property
    def message(self) -> str:
        if self.kind == "too_short":
            return (
                f"фрагмент {self.index}: {self.length} симв. "
                f"< {MIN_VO_CHARS}"
            )
        return (
            f"фрагмент {self.index}: {self.length} симв. "
            f"> {MAX_VO_CHARS}"
        )


def check_vo_lengths(fragments: list[str]) -> list[Violation]:
    """Вернуть нарушения нормы 200–300 символов. Пустой список — норма."""
    out: list[Violation] = []
    for index, raw in enumerate(fragments or []):
        text = raw if isinstance(raw, str) else str(raw)
        n = len(text)
        if n < MIN_VO_CHARS:
            kind = "too_short"
        elif n > MAX_VO_CHARS:
            kind = "too_long"
        else:
            continue
        out.append(Violation(index=index, length=n, kind=kind, fragment=text))
    return out
