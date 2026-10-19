"""Локальный cross-encoder поверх гибрида. По умолчанию выключен.

CI остаётся на hashing и не вызывает живые виды: веса не качаются.
``stub`` — детерминированный лексический overlap без сети.
``jina`` — квантованный ONNX ``jinaai/jina-reranker-v2-base-multilingual``
(тот же файл, что каталог FastEmbed). ``bge-m3`` — квантованный ONNX
``BAAI/bge-reranker-v2-m3``. ``bge`` — прежний английский ``bge-reranker-base``.
Кэш — ``weight_cache_dir()``, не ``data/``.
"""

from __future__ import annotations

import re
from typing import Protocol

_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)

BGE_REPO = "Xenova/bge-reranker-base"
BGE_FILE = "onnx/model_quantized.onnx"
JINA_REPO = "jinaai/jina-reranker-v2-base-multilingual"
JINA_FILE = "onnx/model_quantized.onnx"
BGE_M3_REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
BGE_M3_FILE = "onnx/model_quantized.onnx"
# Квантованный граф не бит-стабилен даже при одном потоке. В эвалах — fp32.
BGE_M3_EVAL_FILE = "onnx/model.onnx"
BGE_M3_EVAL_DATA = "onnx/model.onnx_data"


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


class OnnxPairReranker:
    """Cross-encoder ONNX. Выше логит — релевантнее.

    Пара обрезается токенайзером до ``max_length`` (256–384 на контуре B).
    Батч идёт одним прогоном onnxruntime. Вне эвалов ``intra_op`` равен числу ядер.
    В режиме эвалов оба числа потоков равны 1 и сид фиксирован.
    Холодная загрузка весов — в конструкторе, не в ``score``.
    """

    def __init__(
        self,
        *,
        repo: str,
        filename: str,
        reranker_id: str,
        max_length: int = 384,
        eval_mode: bool = False,
    ) -> None:
        import numpy as np
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        from app.orchestrator.rag.embedder import weight_cache_dir
        from app.orchestrator.rag.onnx_session import make_session_options, session_identity

        if max_length < 32:
            raise ValueError("max_length слишком мал")
        graph = "fp32" if filename == BGE_M3_EVAL_FILE else "q8"
        self.reranker_id, self.intra_op_threads = session_identity(
            reranker_id, graph=graph, eval_mode=eval_mode
        )
        self.max_length = max_length
        self.eval_mode = eval_mode
        cache = weight_cache_dir()
        if filename == BGE_M3_EVAL_FILE:
            hf_hub_download(repo, BGE_M3_EVAL_DATA, cache_dir=str(cache))
        model_path = hf_hub_download(repo, filename, cache_dir=str(cache))
        tokenizer_path = hf_hub_download(repo, "tokenizer.json", cache_dir=str(cache))
        tokenizer = Tokenizer.from_file(tokenizer_path)
        tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        tokenizer.enable_truncation(max_length=max_length)
        options = make_session_options(eval_mode=eval_mode, production_inter_op=1)
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


class OnnxBgeReranker(OnnxPairReranker):
    """Прежний ``bge-reranker-base``. На русских корпусах nDCG не поднимает."""

    def __init__(self, *, max_length: int = 384, eval_mode: bool = False) -> None:
        super().__init__(
            repo=BGE_REPO,
            filename=BGE_FILE,
            reranker_id="onnx-bge-reranker-base",
            max_length=max_length,
            eval_mode=eval_mode,
        )


class OnnxJinaReranker(OnnxPairReranker):
    """``jina-reranker-v2-base-multilingual``, квантованный ONNX."""

    def __init__(self, *, max_length: int = 384, eval_mode: bool = False) -> None:
        super().__init__(
            repo=JINA_REPO,
            filename=JINA_FILE,
            reranker_id="onnx-jina-reranker-v2-base-multilingual",
            max_length=max_length,
            eval_mode=eval_mode,
        )


class OnnxBgeM3Reranker(OnnxPairReranker):
    """``bge-reranker-v2-m3``, квантованный ONNX."""

    def __init__(self, *, max_length: int = 384, eval_mode: bool = False) -> None:
        super().__init__(
            repo=BGE_M3_REPO,
            filename=BGE_M3_EVAL_FILE if eval_mode else BGE_M3_FILE,
            reranker_id="onnx-bge-reranker-v2-m3",
            max_length=max_length,
            eval_mode=eval_mode,
        )


def build_reranker(
    kind: str = "none",
    *,
    max_length: int = 384,
    eval_mode: bool = False,
) -> Reranker | None:
    """``none`` — реранкера нет (дефолт CI). ``stub`` — без весов.

    ``jina`` и ``bge-m3`` — мультиязычные cross-encoder. ``bge`` — английский base.
    ``eval_mode=True`` — один поток и фиксированный сид.
    """
    if kind in {"none", "off", ""}:
        return None
    if kind == "stub":
        return StubReranker()
    if kind == "bge":
        return OnnxBgeReranker(max_length=max_length, eval_mode=eval_mode)
    if kind == "jina":
        return OnnxJinaReranker(max_length=max_length, eval_mode=eval_mode)
    if kind == "bge-m3":
        return OnnxBgeM3Reranker(max_length=max_length, eval_mode=eval_mode)
    raise ValueError(f"неизвестный реранкер: {kind}")
