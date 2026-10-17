"""Движок RAG: один reindex-воркер, гибридный retrieve, четыре корпуса."""

from __future__ import annotations

import concurrent.futures
import math
import queue
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.orchestrator.rag.canon import sha256_payload
from app.orchestrator.rag.embedder import E5PrefixEmbedder, HashingEmbedder
from app.orchestrator.rag.loaders import load_history, load_nodes_and_prompts, load_rules
from app.orchestrator.rag.node_store import NodeStore
from app.orchestrator.rag.rerank import Reranker
from app.orchestrator.rag.rrf import rrf_merge
from app.orchestrator.rag.store import RagStore, chunk_id_for
from app.orchestrator.rag.types import CORPORA, Chunk, ReindexResult, SourceDoc

_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)
_RRF_K = 60


def fts_match_query(text: str) -> str:
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return ""
    min_len = 3 if any(len(token) >= 3 for token in tokens) else 1
    seen: list[str] = []
    lowered: set[str] = set()
    for token in tokens:
        if len(token) < min_len:
            continue
        key = token.lower()
        if key in lowered:
            continue
        lowered.add(key)
        seen.append(token.replace('"', '""'))
        if len(seen) >= 32:
            break
    if not seen:
        return ""
    return " OR ".join(f'"{token}"' for token in seen)


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    norm_l = 0.0
    norm_r = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        norm_l += a * a
        norm_r += b * b
    if norm_l == 0.0 or norm_r == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_l) * math.sqrt(norm_r))


class ReindexWorker:
    """Один фоновый поток на базу. Повторный ``reindex`` ждёт очередь, не пишет параллельно."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="orch-rag-reindex", daemon=True)
        self._closed = False
        self._thread.start()

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except Exception as exc:
                future.set_exception(exc)

    def submit(self, fn: Callable[[], Any], *, timeout: float = 120) -> Any:
        if self._closed:
            raise RuntimeError("reindex-воркер остановлен")
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._queue.put((fn, future))
        return future.result(timeout=timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)


class RagEngine:
    def __init__(
        self,
        *,
        db_path: Path,
        embedder: E5PrefixEmbedder | None = None,
        rules_path: Path | None = None,
        repo_root: Path | None = None,
        project_db: Path | None = None,
        node_store: NodeStore | None = None,
        history_documents: list[dict[str, Any]] | None = None,
        scan_git: bool = True,
        static_docs: dict[str, list[SourceDoc]] | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.embedder = embedder or E5PrefixEmbedder(HashingEmbedder())
        self.rules_path = rules_path
        self.repo_root = repo_root
        self.project_db = project_db
        self.node_store = node_store
        self.history_documents = history_documents if history_documents is not None else []
        self.scan_git = scan_git
        self.static_docs = static_docs
        self.reranker = reranker
        self.store = RagStore(self.db_path)
        self._worker = ReindexWorker()
        self._node_cache: tuple[list[SourceDoc], list[SourceDoc], int] | None = None

    def close(self) -> None:
        self._worker.close()

    def rules_system_block(self) -> str:
        """Полный текст правил для system planner/critic, не top-k retrieval."""
        path = self._rules_path()
        if path is None or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def reindex(self, corpus: str | None = None) -> ReindexResult | dict[str, ReindexResult]:
        corpora = self._corpora_arg(corpus)
        return self._worker.submit(lambda: self._reindex_many(corpora), timeout=900)

    def retrieve(
        self,
        query: str,
        corpora: list[str] | tuple[str, ...] | None = None,
        top_k: int = 8,
        *,
        channel: str = "hybrid",
        rrf_k: int = _RRF_K,
        rrf_weights: tuple[float, float] | None = None,
        rerank: bool | None = None,
        rerank_pool: int = 30,
        bm25_scope: str = "full",
        source_aggregate: str = "off",
        source_cap: int = 3,
    ) -> list[Chunk]:
        """``rerank=None`` берёт подключённый реранкер, ``False`` выключает его.

        Cross-encoder применяется только к гибриду: топ ``rerank_pool`` после RRF,
        затем срез до ``top_k``. BM25 и вектор реранкер не трогает.

        ``source_aggregate`` ``max`` или ``sum2`` склеивает чанки нод и промптов
        одного ``source_id``: оценка документа — максимум или сумма двух лучших
        чанков. В выдаче не больше ``source_cap`` чанков документа, чтобы один
        источник не занял весь топ. ``off`` — прежний порядок чанков. Правила
        не склеиваются: у них разметка по part_key.
        """
        selected = self._corpora_arg(None if corpora is None else list(corpora))
        if top_k <= 0 or not str(query).strip():
            return []
        if not self.db_path.is_file():
            return []
        if channel not in {"hybrid", "bm25", "vector"}:
            raise ValueError("channel должен быть hybrid, bm25 или vector")
        if rerank_pool <= 0:
            raise ValueError("rerank_pool должен быть > 0")
        if bm25_scope not in {"full", "head"}:
            raise ValueError("bm25_scope должен быть full или head")
        if source_aggregate not in {"off", "max", "sum2"}:
            raise ValueError("source_aggregate должен быть off, max или sum2")
        if source_cap < 1:
            raise ValueError("source_cap должен быть >= 1")
        use_rerank = (self.reranker is not None) if rerank is None else rerank
        if use_rerank and channel != "hybrid":
            use_rerank = False
        if use_rerank and self.reranker is None:
            raise RuntimeError("rerank=True, но реранкер не подключён")
        pool = max(top_k * 4, 20)
        if use_rerank:
            pool = max(pool, rerank_pool)
        if source_aggregate != "off":
            pool = max(pool, 80)
        lexical = (
            self.store.bm25(fts_match_query(query), selected, limit=pool, scope=bm25_scope)
            if channel != "vector"
            else []
        )
        lexical_ids = [chunk_id for chunk_id, _score in lexical]
        lexical_scores = dict(lexical)
        stored = self.store.fetch_chunks(selected)
        vector_scored: list[tuple[float, Chunk]] = []
        if channel == "bm25":
            for chunk in stored:
                chunk.metadata.pop("_embedding", None)
                vector_scored.append((0.0, chunk))
            query_vector_ids: list[str] = []
        else:
            query_vector = self.embedder.embed_query(query)
            for chunk in stored:
                embedding = chunk.metadata.pop("_embedding", None)
                score = cosine(query_vector, embedding or [])
                vector_scored.append((score, chunk))
            vector_scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
            query_vector_ids = [chunk.chunk_id for _score, chunk in vector_scored[:pool]]
        vector_scores = {chunk.chunk_id: score for score, chunk in vector_scored}
        if channel == "bm25":
            fused = {chunk_id: score for chunk_id, score in lexical}
        elif channel == "vector":
            fused = {chunk_id: score for chunk_id, score in vector_scores.items()}
        else:
            weights = None if rrf_weights is None else [rrf_weights[0], rrf_weights[1]]
            fused = rrf_merge([lexical_ids, query_vector_ids], k=rrf_k, weights=weights)
        by_id = {chunk.chunk_id: chunk for _score, chunk in vector_scored}
        ordered = [
            chunk_id
            for chunk_id in sorted(fused, key=lambda chunk_id: (-fused[chunk_id], chunk_id))
            if chunk_id in by_id
        ]
        rerank_scores: dict[str, float] = {}
        if use_rerank and self.reranker is not None:
            # В реранкер идёт сырой топ RRF. Агрегация по source_id режет уже
            # переоценённый список, иначе чужой документ не пускает нужные секции.
            candidates = ordered[:rerank_pool]
            passages = [_rerank_passage(by_id[chunk_id]) for chunk_id in candidates]
            raw_scores = self.reranker.score(query, passages)
            if len(raw_scores) != len(candidates):
                raise RuntimeError("реранкер вернул не столько оценок, сколько пассажей")
            rerank_scores = dict(zip(candidates, raw_scores, strict=True))
            ordered = [
                chunk_id
                for chunk_id, _score in sorted(
                    rerank_scores.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ]
            if source_aggregate != "off":
                ordered = _diversify_ids(
                    ordered,
                    rerank_scores,
                    by_id,
                    limit=top_k,
                    cap=source_cap,
                    mode=source_aggregate,
                )
        elif source_aggregate != "off":
            ordered = _diversify_ids(
                ordered,
                fused,
                by_id,
                limit=top_k,
                cap=source_cap,
                mode=source_aggregate,
            )
        result: list[Chunk] = []
        for rank, chunk_id in enumerate(ordered[:top_k], start=1):
            chunk = by_id[chunk_id]
            chunk.score = rerank_scores[chunk_id] if chunk_id in rerank_scores else fused[chunk_id]
            chunk.score_bm25 = float(lexical_scores.get(chunk_id, 0.0))
            chunk.score_vec = float(vector_scores.get(chunk_id, 0.0))
            chunk.rank = rank
            result.append(chunk)
        return result

    def _reindex_many(self, corpora: list[str]) -> ReindexResult | dict[str, ReindexResult]:
        self._node_cache = None
        results = {corpus: self._reindex_one(corpus) for corpus in corpora}
        self._node_cache = None
        if len(corpora) == 1:
            return results[corpora[0]]
        return results

    def _reindex_one(self, corpus: str) -> ReindexResult:
        docs, skipped = self._load(corpus)
        grouped = _group_docs(docs, self.embedder.embedder_id)
        existing = self.store.existing_revisions(corpus)
        to_embed: list[SourceDoc] = []
        for source_id, (revision, parts) in grouped.items():
            if existing.get(source_id) != revision:
                to_embed.extend(parts)
        embeddings: dict[str, list[float]] = {}
        if to_embed:
            vectors = self.embedder.embed_passages([doc.text for doc in to_embed])
            for doc, vector in zip(to_embed, vectors, strict=True):
                embeddings[chunk_id_for(doc.corpus, doc.source_id, doc.part_key)] = vector
        written, unchanged, deleted = self.store.sync(
            corpus,
            grouped,
            embeddings,
            indexed_at=_now(),
        )
        return ReindexResult(
            corpus=corpus,
            sources=len(grouped),
            written=written,
            unchanged=unchanged,
            deleted=deleted,
            skipped_invalid_keys=skipped,
        )

    def _load(self, corpus: str) -> tuple[list[SourceDoc], int]:
        if self.static_docs is not None and corpus in self.static_docs:
            return list(self.static_docs[corpus]), 0
        if corpus == "rules":
            path = self._rules_path()
            return (load_rules(path) if path is not None else []), 0
        if corpus == "history":
            return load_history(project_db=self.project_db, documents=self.history_documents), 0
        nodes, prompts, skipped = self._load_nodes()
        if corpus == "nodes":
            return nodes, skipped
        return prompts, skipped

    def _load_nodes(self) -> tuple[list[SourceDoc], list[SourceDoc], int]:
        if self._node_cache is None:
            root = self.repo_root or _default_repo_root()
            self._node_cache = load_nodes_and_prompts(
                repo_root=root,
                node_store=self.node_store,
                scan_git=self.scan_git,
            )
        return self._node_cache

    def _rules_path(self) -> Path | None:
        if self.rules_path is not None:
            return self.rules_path
        return _default_repo_root() / "docs" / "fable" / "user_rules.md"

    def _corpora_arg(self, corpus: str | list[str] | None) -> list[str]:
        if corpus is None:
            return list(CORPORA)
        selected = [corpus] if isinstance(corpus, str) else list(corpus)
        unknown = [name for name in selected if name not in CORPORA]
        if unknown:
            raise ValueError(f"неизвестный корпус: {', '.join(unknown)}; допустимо: {', '.join(CORPORA)}")
        return selected


def _source_group(chunk: Chunk) -> tuple[str, ...]:
    """Ноды и промпты одного ключа — один документ. Правила остаются по part_key."""
    if chunk.corpus in {"nodes", "prompts"}:
        return ("doc", chunk.source_id)
    part = str(chunk.metadata.get("part_key") or "")
    return (chunk.corpus, chunk.source_id, part)


def _diversify_ids(
    ordered: list[str],
    scores: dict[str, float],
    by_id: dict[str, Chunk],
    *,
    limit: int,
    cap: int,
    mode: str,
) -> list[str]:
    """Оценка документа — max или сумма двух лучших чанков, затем не больше ``cap``."""
    groups: dict[tuple[str, ...], list[str]] = {}
    for chunk_id in ordered:
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        groups.setdefault(_source_group(chunk), []).append(chunk_id)
    ranked_docs: list[tuple[float, tuple[str, ...], list[str]]] = []
    for key, chunk_ids in groups.items():
        chunk_ids.sort(key=lambda chunk_id: (-scores.get(chunk_id, 0.0), chunk_id))
        if mode == "max":
            doc_score = scores.get(chunk_ids[0], 0.0)
        else:
            doc_score = sum(scores.get(chunk_id, 0.0) for chunk_id in chunk_ids[:2])
        ranked_docs.append((doc_score, key, chunk_ids))
    ranked_docs.sort(key=lambda item: (-item[0], item[1]))
    chosen: list[str] = []
    for _doc_score, _key, chunk_ids in ranked_docs:
        chosen.extend(chunk_ids[:cap])
        if len(chosen) >= limit:
            break
    return chosen[:limit]


def _rerank_passage(chunk: Chunk) -> str:
    """В cross-encoder уходит заголовок и начало чанка, не весь текст."""
    title = (chunk.title or "").strip()
    node_key = str(chunk.metadata.get("node_key") or "").strip()
    role = str(chunk.metadata.get("group_role") or "").strip()
    body = (chunk.text or "").strip()[:1200]
    parts = [part for part in (title, node_key, role, body) if part]
    return "\n".join(parts)


def _group_docs(docs: list[SourceDoc], embedder_id: str) -> dict[str, tuple[str, list[SourceDoc]]]:
    by_source: dict[str, list[SourceDoc]] = {}
    for doc in docs:
        by_source.setdefault(doc.source_id, []).append(doc)
    grouped: dict[str, tuple[str, list[SourceDoc]]] = {}
    for source_id, parts in by_source.items():
        ordered = sorted(parts, key=lambda doc: doc.part_key)
        revision = sha256_payload(
            {
                "embedder": embedder_id,
                "parts": [
                    {
                        "metadata": doc.metadata,
                        "part_key": doc.part_key,
                        "text": doc.text,
                        "title": doc.title,
                    }
                    for doc in ordered
                ],
            }
        )
        grouped[source_id] = (revision, ordered)
    return grouped


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_repo_root() -> Path:
    from app.project_root import find_project_root

    return find_project_root()
