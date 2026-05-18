"""Hard limits and retry budgets for the visual lab.

These are the lab's safety rails:

* ``MAX_PROMPT_CHARS = 4800`` — outsee.io refuses anything above this. Hit
  this and the iteration is skipped (after one re-shrink retry to GPT).
* ``MAX_PHASE_RETRIES = 3`` — each analyze/think/build phase retries up to
  three times before marking ``phase="error_*"``.
* ``MAX_CONSECUTIVE_FAILED_ITERS = 5`` — the runner pauses the project after
  this many failed iterations in a row, so we don't burn the whole night
  on a broken phase.
* ``ID_PREFIX_RESERVE = 80`` — outsee adds a ``[ID: ...]`` line on top of
  each prompt; we keep an extra reserve so it never pushes us over 4800.
"""

from __future__ import annotations

MAX_PROMPT_CHARS: int = 4800
MAX_PHASE_RETRIES: int = 3
MAX_CONSECUTIVE_FAILED_ITERS: int = 5
ID_PREFIX_RESERVE: int = 80


class PromptTooLongError(ValueError):
    """Raised when a generated prompt exceeds ``MAX_PROMPT_CHARS``."""

    def __init__(self, length: int, limit: int = MAX_PROMPT_CHARS) -> None:
        super().__init__(
            f"Prompt is {length} characters, hard limit is {limit}. "
            f"Outsee.io will reject it."
        )
        self.length = length
        self.limit = limit


def check_prompt_length(text: str, *, include_id_reserve: bool = True) -> None:
    """Raise ``PromptTooLongError`` if the prompt is over the limit.

    With ``include_id_reserve=True`` (default) the effective limit is
    ``MAX_PROMPT_CHARS - ID_PREFIX_RESERVE`` so an ``[ID: ...]`` line can
    still be prepended by outsee.generate_image without crossing the line.
    """
    limit = MAX_PROMPT_CHARS - (ID_PREFIX_RESERVE if include_id_reserve else 0)
    n = len(text)
    if n > limit:
        raise PromptTooLongError(length=n, limit=limit)


def soft_limit(*, include_id_reserve: bool = True) -> int:
    """Effective length cap, used in GPT instructions ("≤ N chars please")."""
    return MAX_PROMPT_CHARS - (ID_PREFIX_RESERVE if include_id_reserve else 0)
