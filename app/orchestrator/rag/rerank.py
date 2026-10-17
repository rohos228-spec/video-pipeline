"""Локальный cross-encoder поверх топ-30 гибрида. По умолчанию выключен.

CI остаётся на hashing и не вызывает ``build_reranker("bge")``: веса не качаются.
``stub`` — детерминированный лексический overlap без сети.
``bge`` — ONNX ``BAAI/bge-reranker-base`` (квантованный экспорт Xenova, тот же,
что каталог FastEmbed отдаёт как cross-encoder). Кэш — ``weight_cache_dir()``,
не ``data/``.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)

BGE_REPO = "Xenova/bge-reranker-base"
BGE_FILE = "onnx/model_quantized.onnx"


class Reranker(Protocol):
    reranker_id: str

    def score(self, query: str, passages: list[str]) -> list[float]: ...


class StubReranker:
    """Детерминированная заглушка: доля токенов запроса, встретившихся в пассаже.

    Веса не скачивает. Одинаковый вход даёт одинаковые оценки.
    """

    reranker_id = "stub-lexical-v1"

    def score(self, query: str, passages: list[str]) -> list[float]:
        query_tokens = _TOKEN_RE.findall(query.lower())
        if not query_tokens:
            return [0.0 for _ in passages]
        unique = set(query_tokens)
        scored: list[float] = []
        for passage in passages:
            body = set(_TOKEN_RE.findall(passage.lower()))
            scored.append(len(unique & body) / len(unique))
        return scored


class OnnxBgeReranker:
    """Cross-encoder ``bge-reranker-base``. Выше логит — релевантнее.

    Пара ``query`` / пассаж кодируется токенайзером модели (``<s> q </s></s> p </s>``).
    Холодная загрузка весов — в конструкторе, не в ``score``.
    """

    reranker_id = "onnx-bge-reranker-base"

    def __init__(self) -> None:
        import numpy as np
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        from app.orchestrator.rag.embedder import weight_cache_dir

        cache = weight_cache_dir()
        model_path = hf_hub_download(BGE_REPO, BGE_FILE, cache_dir=str(cache))
        tokenizer_path = hf_hub_download(BGE_REPO, "tokenizer.json", cache_dir=str(cache))
        tokenizer = Tokenizer.from_file(tokenizer_path)
        tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        tokenizer.enable_truncation(max_length=512)
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, os.cpu_count() or 1)
        self._np = np
        self._tokenizer = tokenizer
        self._session = ort.InferenceSession(
            model_path,
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {item.name for item in self._session.get_inputs()}

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        scored: list[float] = []
        for start in range(0, len(passages), 8):
            scored.extend(self._score_batch(query, passages[start : start + 8]))
        return scored

    def _score_batch(self, query: str, passages: list[str]) -> list[float]:
        encoded = self._tokenizer.encode_batch([(query, passage) for passage in passages])
        input_ids = self._np.array([item.ids for item in encoded], dtype=self._np.int64)
        attention = self._np.array([item.attention_mask for item in encoded], dtype=self._np.int64)
        feeds: dict[str, object] = {}
        if "input_ids" in self._input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = self._np.zeros_like(input_ids)
        logits = self._session.run(None, feeds)[0]
        flat = self._np.asarray(logits).reshape(len(passages), -1)[:, 0]
        return [float(value) for value in flat]


def build_reranker(kind: str = "none") -> Reranker | None:
    """``none`` — реранкера нет (дефолт CI). ``stub`` — без весов. ``bge`` — локальный cross-encoder."""
    if kind in {"none", "off", ""}:
        return None
    if kind == "stub":
        return StubReranker()
    if kind == "bge":
        return OnnxBgeReranker()
    raise ValueError(f"неизвестный реранкер: {kind}")
