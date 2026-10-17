"""Эмбеддинги с обязательными префиксами e5 (``query:`` / ``passage:``).

Боевая модель ``multilingual-e5-small`` (FastEmbed) подключается только если
пакет установлен и выбран явно. Тесты и CI по умолчанию используют
детерминированный hashing-фейк и не качают веса.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

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
        self._model = TextEmbedding(model_name=model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # FastEmbed/e5 сам ждёт префиксы в тексте — мы уже их добавили.
        return [list(map(float, vec)) for vec in self._model.embed(texts)]


def build_embedder(kind: str = "hashing") -> E5PrefixEmbedder:
    """``hashing`` (по умолчанию, офлайн) или ``fastembed`` (явный выбор)."""
    if kind == "fastembed":
        return E5PrefixEmbedder(FastEmbedE5())
    if kind != "hashing":
        raise ValueError(f"неизвестный эмбеддер: {kind}")
    return E5PrefixEmbedder(HashingEmbedder())
