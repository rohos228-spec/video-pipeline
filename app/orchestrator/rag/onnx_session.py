"""Опции onnxruntime.

Режим эвалов (контур B) фиксирует один поток и сид, чтобы три прохода
совпадали. Обычный режим не трогает сид и оставляет прежнее число потоков:
CI на hashing сессию не создаёт.
"""

from __future__ import annotations

import os
from typing import Any

EVAL_ONNX_SEED = 0
EVAL_INTRA_OP_THREADS = 1
EVAL_INTER_OP_THREADS = 1


def session_identity(base: str, *, graph: str, eval_mode: bool) -> tuple[str, int]:
    """Имя модели с вариантом графа, режимом и числом intra-потоков."""
    intra = EVAL_INTRA_OP_THREADS if eval_mode else max(1, os.cpu_count() or 1)
    mode = "eval" if eval_mode else "prod"
    return f"{base}:{graph}:{mode}:intra{intra}", intra


def make_session_options(*, eval_mode: bool, production_inter_op: int | None) -> Any:
    """Сессия эвалов: intra=1, inter=1, фиксированный сид.

    ``production_inter_op=None`` — как у эмбеддера вне эвалов: inter_op не задаётся.
    Реранкер вне эвалов по-прежнему ставит inter_op=1.
    """
    import onnxruntime as ort

    options = ort.SessionOptions()
    if eval_mode:
        import random

        import numpy as np

        options.intra_op_num_threads = EVAL_INTRA_OP_THREADS
        options.inter_op_num_threads = EVAL_INTER_OP_THREADS
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.use_deterministic_compute = True
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        random.seed(EVAL_ONNX_SEED)
        np.random.seed(EVAL_ONNX_SEED)
        ort.set_seed(EVAL_ONNX_SEED)
        return options
    options.intra_op_num_threads = max(1, os.cpu_count() or 1)
    if production_inter_op is not None:
        options.inter_op_num_threads = production_inter_op
    return options
