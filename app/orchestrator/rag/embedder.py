"""Эмбеддинги с обязательными префиксами e5 (``query:`` / ``passage:``).

Боевая модель плана — ``intfloat/multilingual-e5-small``. CI её не вызывает:
по умолчанию детерминированный hashing-фейк, веса не качаются.
``build_embedder("e5-small")`` грузит официальный ONNX в кэш Hugging Face.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any, Protocol

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "

_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)


class Embedder(Protocol):
    """Внутренний эмбеддер. На вход уже приходит текст с префиксом e5."""

    embedder_id: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def strip_e5_prefix(text: str) -> str:
    if text.startswith(QUERY_PREFIX):
        return text[len(QUERY_PREFIX) :]
    if text.startswith(PASSAGE_PREFIX):
        return text[len(PASSAGE_PREFIX) :]
    return text


class HashingEmbedder:
    """Детерминированный фейк: мешок токенов в фиксированную размерность.

    Префикс ``query:`` / ``passage:`` снимается перед хешированием, чтобы
    запрос и пассаж одного текста были близки (так обучен e5). Сам префикс
    обязан прийти снаружи — это проверяет обёртка ``E5PrefixEmbedder``.
    """

    embedder_id = "hashing-e5-v1"

    def __init__(self, dim: int = 64) -> None:
        if dim < 8:
            raise ValueError("dim должен быть >= 8")
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_l2_normalize(self._embed_one(text)) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        body = strip_e5_prefix(text).lower()
        vec = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(body)
        if not tokens:
            tokens = ["empty"]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[bucket] += sign
        return vec


class E5PrefixEmbedder:
    """Добавляет префиксы e5 и делегирует внутреннему эмбеддеру."""

    def __init__(self, inner: Embedder) -> None:
        self.inner = inner

    @property
    def embedder_id(self) -> str:
        return f"e5:{self.inner.embedder_id}"

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        prefixed = [PASSAGE_PREFIX + text for text in texts]
        return self.inner.embed_documents(prefixed)

    def embed_query(self, text: str) -> list[float]:
        vectors = self.inner.embed_documents([QUERY_PREFIX + text])
        return vectors[0]


def _weight_cache_dir() -> Path:
    """Кэш весов e5. Не ``settings.data_dir``: стандартный каталог HF/fastembed."""
    raw = os.environ.get("FASTEMBED_CACHE_PATH") or os.environ.get("HF_HOME")
    cache = Path(raw).expanduser() if raw else Path.home() / ".cache" / "huggingface"
    try:
        from app.settings import settings

        data = Path(settings.data_dir).resolve()
    except Exception:
        data = None
    resolved = cache.resolve()
    if data is not None and (resolved == data or data in resolved.parents):
        raise RuntimeError("кэш весов e5 не должен лежать в data/")
    return cache


class OnnxMultilingualE5Small:
    """``intfloat/multilingual-e5-small`` из официального ONNX.

    FastEmbed 0.3–0.9 в каталоге имеет только ``multilingual-e5-large``, не small
    из плана §5. Веса качаются в кэш Hugging Face, не в ``data/``.
    """

    embedder_id = "onnx-intfloat-multilingual-e5-small"

    def __init__(self) -> None:
        import numpy as np
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        cache = _weight_cache_dir()
        repo = "intfloat/multilingual-e5-small"
        model_path = hf_hub_download(repo, "onnx/model.onnx", cache_dir=str(cache))
        tokenizer_path = hf_hub_download(repo, "tokenizer.json", cache_dir=str(cache))
        tokenizer = Tokenizer.from_file(tokenizer_path)
        tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        tokenizer.enable_truncation(max_length=512)
        self._np = np
        self._tokenizer = tokenizer
        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._input_names = {item.name for item in self._session.get_inputs()}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 16):
            vectors.extend(self._embed_batch(texts[start : start + 16]))
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        encoded = self._tokenizer.encode_batch(texts)
        input_ids = self._np.array([item.ids for item in encoded], dtype=self._np.int64)
        attention = self._np.array([item.attention_mask for item in encoded], dtype=self._np.int64)
        feeds: dict[str, Any] = {}
        if "input_ids" in self._input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = self._np.zeros_like(input_ids)
        hidden = self._session.run(None, feeds)[0]
        mask = attention.astype(self._np.float32)[:, :, None]
        summed = (hidden * mask).sum(axis=1)
        counts = self._np.clip(mask.sum(axis=1), 1.0, None)
        pooled = summed / counts
        norms = self._np.linalg.norm(pooled, axis=1, keepdims=True)
        norms = self._np.clip(norms, 1e-12, None)
        pooled = pooled / norms
        return [row.astype(float).tolist() for row in pooled]


class FastEmbedE5:
    """Опциональный бэкенд. Импорт fastembed происходит только здесь."""

    embedder_id = "fastembed-multilingual-e5-small"

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small") -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - зависит от окружения
            raise RuntimeError(
                "fastembed не установлен; CI и тесты должны использовать HashingEmbedder"
            ) from exc
        cache = _weight_cache_dir()
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # FastEmbed/e5 сам ждёт префиксы в тексте — мы уже их добавили.
        return [list(map(float, vec)) for vec in self._model.embed(texts)]


def build_embedder(kind: str = "hashing") -> E5PrefixEmbedder:
    """``hashing`` (по умолчанию, офлайн), ``e5-small`` или ``fastembed``.

    ``e5-small`` — модель из плана §5. CI его не вызывает.
    """
    if kind == "fastembed":
        return E5PrefixEmbedder(FastEmbedE5())
    if kind == "e5-small":
        return E5PrefixEmbedder(OnnxMultilingualE5Small())
    if kind != "hashing":
        raise ValueError(f"неизвестный эмбеддер: {kind}")
    return E5PrefixEmbedder(HashingEmbedder())
