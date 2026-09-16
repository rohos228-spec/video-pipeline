"""Errors for cinematic scene space."""

from __future__ import annotations


class DegenerateAxisError(ValueError):
    """Camera lies on the action axis (cross == 0)."""
