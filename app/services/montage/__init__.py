"""Монтаж по Excel R15 — overlay + extend clone (absolute setpts)."""

from app.services.montage.variant2 import MONTAGE_ENGINE_V2, run_variant2
from app.services.montage.variants import MONTAGE_VARIANTS
from app.services.montage.workspace import wipe_montage_workspace

__all__ = [
    "MONTAGE_ENGINE_V2",
    "MONTAGE_VARIANTS",
    "run_variant2",
    "wipe_montage_workspace",
]
