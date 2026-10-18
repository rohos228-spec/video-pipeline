"""Движок RAG: один reindex-воркер, гибридный retrieve, четыре корпуса."""

from __future__ import annotations

import concurrent.futures
import math
import queue
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.orchestrator.rag.canon import sha256_payload
from app.orchestrator.rag.embedder import E5PrefixEmbedder, HashingEmbedder
from app.orchestrator.rag.family_boost import boost_scores
from app.orchestrator.rag.family_synonyms import expand_family_query
from app.orchestrator.rag.lexical import LEXICAL_ANALYZER, fts_or_query
from app.orchestrator.rag.loaders import load_history, load_nodes_and_prompts, load_rules
from app.orchestrator.rag.node_store import NodeStore
from app.orchestrator.rag.rerank import Reranker
from app.orchestrator.rag.rrf import rrf_merge
from app.orchestrator.rag.store import RagStore, chunk_id_for
from app.orchestrator.rag.types import CORPORA, Chunk, ReindexResult, SourceDoc

_RRF_K = 60
_AGGREGATES = (
    "none",
    "prompt_pool",
    "prompt_expand",
    "expand_tail",
    "none_cand_expand_rerank",
    "rrf_none_expand",
)
_RERANK_FUSIONS = frozenset({"replace", "rrf"})


def fts_match_query(text: str) -> str:
    """FTS-запрос в тех же формах, что лежат в индексе BM25."""
    return fts_or_query(text)


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
    """Один фоновый поток на базу. Повторный ``reindex`` ждёт очередь, не пишет параллельно.

    Воркер индексирует корпуса и не вызывает LLM. Правило очереди про пачки
    LLM к этому потоку не относится; таймаут ``submit`` остаётся 120 с.
    """

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
        self.retrieve_defaults: dict[str, Any] = {}
        self.store = RagStore(self.db_path)
        self._worker = ReindexWorker()
        self._node_cache: tuple[list[SourceDoc], list[SourceDoc], int] | None = None
        self._cache_lock = threading.Lock()
        self._index_generation = 0
        self._query_vectors: dict[str, list[float]] = {}
        self._corpus_cache: dict[tuple[str, ...], tuple[list[Chunk], dict[str, list[float]]]] = {}

    def close(self) -> None:
        self._worker.close()

    def rules_system_block(self) -> str:
        """Полный текст правил для system planner/critic, не top-k retrieval."""
        path = self._rules_path()
        if path is None or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def reindex(
        self,
        corpus: str | None = None,
        *,
        timeout: float = 120,
    ) -> ReindexResult | dict[str, ReindexResult]:
        corpora = self._corpora_arg(corpus)
        return self._worker.submit(lambda: self._reindex_many(corpora), timeout=timeout)

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
        aggregate: str = "none",
        rerank_fusion: str = "replace",
        rerank_fusion_weights: tuple[float, float] = (1.0, 1.0),
        bm25_protect: float = 0.0,
        family_boost: float = 1.0,
    ) -> list[Chunk]:
        """``rerank=None`` берёт подключённый реранкер, ``False`` выключает его.

        Cross-encoder применяется только к гибриду: топ ``rerank_pool`` после RRF,
        затем срез до ``top_k``. BM25 и вектор реранкер не трогает.
        Эмбеддинги чанков и повторных запросов берутся из кэша процесса.
        ``aggregate=prompt_pool`` склеивает части одного промпта внутри пула.
        ``aggregate=prompt_expand`` поднимает все части такого промпта.
        ``aggregate=expand_tail`` оставляет top-5 как ``none`` и заполняет
        позиции 6–8 остальными частями промптов из top-5.
        ``rerank_fusion=rrf`` не отдаёт порядок реранкеру: RRF с BM25.
        ``bm25_protect`` — во сколько раз топ-1 BM25 должен обгонять топ-2,
        чтобы остаться первым. 0 выключает защиту.
        ``family_boost`` умножает оценку чанков семейства, которое запрос
        назвал. 1 оставляет порядок как есть.
        BM25 и вектор запроса видят синоним семейства, если лемма
        закреплена за одним семейством. Реранкер и буст считают исходный текст.
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
        if aggregate not in _AGGREGATES:
            raise ValueError(
                "aggregate должен быть none, prompt_pool, prompt_expand, "
                "expand_tail, none_cand_expand_rerank или rrf_none_expand"
            )
        if rrf_weights is not None and (rrf_weights[0] <= 0 or rrf_weights[1] <= 0):
            raise ValueError("веса RRF должны быть > 0")
        if rerank_fusion not in _RERANK_FUSIONS:
            raise ValueError("rerank_fusion должен быть replace или rrf")
        if bm25_protect < 0:
            raise ValueError("bm25_protect должен быть >= 0")
        if rerank_fusion_weights[0] <= 0 or rerank_fusion_weights[1] <= 0:
            raise ValueError("веса слияния BM25 и реранкера должны быть > 0")
        if family_boost < 1:
            raise ValueError("family_boost должен быть >= 1")
        use_rerank = (self.reranker is not None) if rerank is None else rerank
        if use_rerank and channel != "hybrid":
            use_rerank = False
        if use_rerank and self.reranker is None:
            raise RuntimeError("rerank=True, но реранкер не подключён")
        pool = max(top_k * 4, 20)
        if use_rerank:
            pool = max(pool, rerank_pool)
        expanded = expand_family_query(query)
        lexical = (
            self.store.bm25(fts_match_query(expanded), selected, limit=pool, scope=bm25_scope)
            if channel != "vector"
            else []
        )
        lexical_ids = [chunk_id for chunk_id, _score in lexical]
        lexical_scores = dict(lexical)
        stored, vectors = self._cached_corpus(selected)
        vector_scored: list[tuple[float, Chunk]] = []
        if channel == "bm25":
            for chunk in stored:
                vector_scored.append((0.0, chunk))
            query_vector_ids: list[str] = []
        else:
            query_vector = self._cached_query_vector(expanded)
            for chunk in stored:
                score = cosine(query_vector, vectors.get(chunk.chunk_id) or [])
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
            # Один чанк на родителя, с лучшей оценкой. Смотрим весь fused-список,
            # а не срез rerank_pool: дубль окна не занимает место чужого семейства.
            # В реранкер по-прежнему уходит rerank_pool пассажей, дальше порядок базовый.
            candidates = _unique_parent_pool(ordered, fused, by_id, rerank_pool)
            passages = [_rerank_passage(by_id[chunk_id]) for chunk_id in candidates]
            raw_scores = self.reranker.score(query, passages)
            if len(raw_scores) != len(candidates):
                raise RuntimeError("реранкер вернул не столько оценок, сколько пассажей")
            rerank_scores = dict(zip(candidates, raw_scores, strict=True))
            rerank_ids = [
                chunk_id
                for chunk_id, _score in sorted(
                    rerank_scores.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ]
            if rerank_fusion == "rrf":
                blended = _fuse_bm25_and_rerank(
                    lexical_ids,
                    rerank_ids,
                    lexical_scores,
                    k=rrf_k,
                    weights=rerank_fusion_weights,
                    protect=bm25_protect,
                )
                rerank_scores = blended
                ordered = [
                    chunk_id
                    for chunk_id, _score in sorted(
                        blended.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                    if chunk_id in by_id
                ]
            else:
                ordered = rerank_ids
        rank_scores = rerank_scores if rerank_scores else fused
        ordered, rank_scores = _collapse_to_parents(ordered, rank_scores, by_id)
        if family_boost != 1:
            rank_scores = boost_scores(rank_scores, by_id, query, family_boost)
            ordered = [
                chunk_id
                for chunk_id, _score in sorted(
                    ((chunk_id, rank_scores.get(chunk_id, fused.get(chunk_id, 0.0))) for chunk_id in ordered),
                    key=lambda item: (-item[1], item[0]),
                )
            ]
        if aggregate == "none_cand_expand_rerank" and use_rerank and self.reranker is not None:
            ordered = _none_cand_expand_rerank(ordered, by_id, query, self.reranker, rerank_pool)
        elif aggregate == "rrf_none_expand":
            expand_order = _aggregate_parts(ordered, rank_scores, by_id, "prompt_expand")
            ordered = _rrf_none_expand(ordered, expand_order, k=rrf_k)
        else:
            ordered = _aggregate_parts(ordered, rank_scores, by_id, aggregate)
        result: list[Chunk] = []
        for rank, chunk_id in enumerate(ordered[:top_k], start=1):
            chunk = by_id[chunk_id]
            score = rank_scores[chunk_id] if chunk_id in rank_scores else fused.get(chunk_id, 0.0)
            result.append(
                replace(
                    chunk,
                    score=float(score),
                    score_bm25=float(lexical_scores.get(chunk_id, 0.0)),
                    score_vec=float(vector_scores.get(chunk_id, 0.0)),
                    rank=rank,
                    metadata=dict(chunk.metadata),
                )
            )
        return result

    def _clear_retrieval_cache(self) -> None:
        with self._cache_lock:
            self._query_vectors.clear()
            self._corpus_cache.clear()
            self._index_generation += 1

    def _cached_query_vector(self, query: str) -> list[float]:
        with self._cache_lock:
            generation = self._index_generation
            cached = self._query_vectors.get(query)
        if cached is not None:
            return cached
        vector = self.embedder.embed_query(query)
        with self._cache_lock:
            if self._index_generation == generation:
                self._query_vectors[query] = vector
        return vector

    def _cached_corpus(self, selected: list[str]) -> tuple[list[Chunk], dict[str, list[float]]]:
        key = tuple(selected)
        with self._cache_lock:
            generation = self._index_generation
            cached = self._corpus_cache.get(key)
        if cached is not None:
            return cached
        stored = self.store.fetch_chunks(selected)
        vectors: dict[str, list[float]] = {}
        for chunk in stored:
            embedding = chunk.metadata.pop("_embedding", None)
            if embedding:
                vectors[chunk.chunk_id] = embedding
        snapshot = (stored, vectors)
        with self._cache_lock:
            if self._index_generation == generation:
                self._corpus_cache[key] = snapshot
        return snapshot

    def _reindex_many(self, corpora: list[str]) -> ReindexResult | dict[str, ReindexResult]:
        from app.orchestrator.rag.loaders import invalidate_group_role_cache

        invalidate_group_role_cache()
        self._node_cache = None
        self._clear_retrieval_cache()
        try:
            results = {corpus: self._reindex_one(corpus) for corpus in corpora}
            if len(corpora) == 1:
                return results[corpora[0]]
            return results
        finally:
            self._node_cache = None
            self._clear_retrieval_cache()

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
        if corpus in {"nodes", "prompts"}:
            from app.orchestrator.rag.node_api import skipped_node_keys

            for source_id in skipped_node_keys():
                if source_id not in grouped and source_id in existing:
                    grouped[source_id] = (existing[source_id], [])
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


def _doc_key(chunk: Chunk) -> str:
    if chunk.corpus == "prompts":
        return f"prompts:{chunk.source_id}"
    return f"chunk:{chunk.chunk_id}"


def _is_section_window(chunk: Chunk) -> bool:
    return bool(str(chunk.metadata.get("parent_part_key") or ""))


def _pool_parent_key(chunk: Chunk) -> tuple[str, str, str]:
    """Окно и его секция — один родитель. Чужая секция — другой."""
    part = str(chunk.metadata.get("parent_part_key") or chunk.metadata.get("part_key") or "")
    if not part:
        return (chunk.corpus, chunk.source_id, chunk.chunk_id)
    return (chunk.corpus, chunk.source_id, part)


def _unique_parent_pool(
    ordered: list[str],
    scores: dict[str, float],
    by_id: dict[str, Chunk],
    limit: int,
) -> list[str]:
    """До ``limit`` чанков: у каждого родителя остаётся чанк с лучшей оценкой.

    Список ``ordered`` уже по убыванию оценки. Победитель родителя
    встаёт на место этого победителя, не на место худшего дубля.
    Без дублей результат совпадает с ``ordered[:limit]``.
    """
    best_score: dict[tuple[str, str, str], float] = {}
    best_id: dict[tuple[str, str, str], str] = {}
    for chunk_id in ordered:
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        key = _pool_parent_key(chunk)
        score = float(scores.get(chunk_id, 0.0))
        if key not in best_score or score > best_score[key]:
            best_score[key] = score
            best_id[key] = chunk_id
    winners = set(best_id.values())
    kept: list[str] = []
    for chunk_id in ordered:
        if chunk_id not in winners:
            continue
        kept.append(chunk_id)
        if len(kept) >= limit:
            break
    return kept


def _collapse_to_parents(
    ordered: list[str],
    scores: dict[str, float],
    by_id: dict[str, Chunk],
) -> tuple[list[str], dict[str, float]]:
    """Окно секции отдаёт оценку родительскому part_key. Gold id не меняется."""
    parents = {
        (chunk.corpus, chunk.source_id, str(chunk.metadata.get("part_key") or "")): chunk.chunk_id
        for chunk in by_id.values()
        if not _is_section_window(chunk)
    }
    collapsed = dict(scores)
    best: dict[str, float] = {}
    for chunk_id in ordered:
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        target = chunk_id
        parent_key = str(chunk.metadata.get("parent_part_key") or "")
        if parent_key:
            target = parents.get((chunk.corpus, chunk.source_id, parent_key), chunk_id)
        score = float(scores.get(chunk_id, 0.0))
        previous = best.get(target)
        if previous is None or score > previous:
            best[target] = score
            collapsed[target] = score
    ranked = sorted(best, key=lambda chunk_id: (-best[chunk_id], chunk_id))
    return ranked, collapsed


def _expand_tail(
    ordered: list[str],
    scores: dict[str, float],
    by_id: dict[str, Chunk],
) -> list[str]:
    """Top-5 совпадает с ``none``. Позиции 6–8 — остальные части тех же промптов.

    Порядок соседей тот же, что у ``prompt_expand``: промпт с большей оценкой
    в top-5 раньше, внутри промпта части по убыванию оценки. Если соседей
    меньше трёх, хвост добирается следующими чанками ``none``.
    """
    if not ordered:
        return ordered
    head = list(ordered[:5])
    seen = set(head)
    doc_score: dict[str, float] = {}
    for chunk_id in head:
        chunk = by_id.get(chunk_id)
        if chunk is None or chunk.corpus != "prompts" or _is_section_window(chunk):
            continue
        key = _doc_key(chunk)
        score = float(scores.get(chunk_id, 0.0))
        doc_score[key] = score if key not in doc_score else max(doc_score[key], score)
    members: dict[str, list[str]] = {key: [] for key in doc_score}
    for chunk in by_id.values():
        if chunk.corpus != "prompts" or _is_section_window(chunk):
            continue
        bucket = members.get(_doc_key(chunk))
        if bucket is not None and chunk.chunk_id not in seen:
            bucket.append(chunk.chunk_id)

    def part_order(chunk_id: str) -> tuple[float, str]:
        return (-float(scores.get(chunk_id, float("-inf"))), chunk_id)

    siblings: list[str] = []
    for key in sorted(doc_score, key=lambda item: (-doc_score[item], item)):
        for chunk_id in sorted(members.get(key, []), key=part_order):
            if chunk_id in seen or chunk_id not in by_id:
                continue
            seen.add(chunk_id)
            siblings.append(chunk_id)
    tail = siblings[:3]
    for chunk_id in ordered:
        if len(tail) == 3:
            break
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        tail.append(chunk_id)
    rest = [chunk_id for chunk_id in siblings[3:] if chunk_id not in set(tail)]
    rest.extend(chunk_id for chunk_id in ordered if chunk_id not in seen and chunk_id not in rest)
    return head + tail + rest


def _expanded_prompt_passage(chunk: Chunk, by_id: dict[str, Chunk]) -> str:
    """Текст реранкера: у промпта все части, не один кусок."""
    if chunk.corpus != "prompts":
        return _rerank_passage(chunk)
    parts = [
        other
        for other in by_id.values()
        if other.corpus == "prompts" and other.source_id == chunk.source_id and not _is_section_window(other)
    ]
    parts.sort(key=lambda other: (str(other.metadata.get("part_key") or ""), other.chunk_id))
    title = (chunk.title or "").strip()
    node_key = str(chunk.metadata.get("node_key") or "").strip()
    role = str(chunk.metadata.get("group_role") or "").strip()
    body = "\n".join(part.text.strip() for part in parts if (part.text or "").strip())[:1200]
    return "\n".join(part for part in (title, node_key, role, body) if part)


def _none_cand_expand_rerank(
    ordered: list[str],
    by_id: dict[str, Chunk],
    query: str,
    reranker: Any,
    pool: int,
) -> list[str]:
    """Пул — top-``pool`` списка none. Порядок внутри пула задаёт реранк развёрнутого промпта."""
    candidates = [chunk_id for chunk_id in ordered if chunk_id in by_id][:pool]
    if not candidates:
        return ordered
    passages = [_expanded_prompt_passage(by_id[chunk_id], by_id) for chunk_id in candidates]
    raw_scores = reranker.score(query, passages)
    if len(raw_scores) != len(candidates):
        raise RuntimeError("реранкер вернул не столько оценок, сколько пассажей")
    none_index = {chunk_id: index for index, chunk_id in enumerate(ordered)}
    ranked = [
        chunk_id
        for chunk_id, _score in sorted(
            zip(candidates, raw_scores, strict=True),
            key=lambda item: (-float(item[1]), none_index[item[0]]),
        )
    ]
    seen = set(ranked)
    return ranked + [chunk_id for chunk_id in ordered if chunk_id not in seen]


def _rrf_none_expand(none_order: list[str], expand_order: list[str], *, k: int) -> list[str]:
    """RRF списков none и prompt_expand. Top-5 none остаётся на местах 1–5.

    Так попадание none в top-5 не выпадает: множество первых пяти id не меняется.
    Хвост идёт по RRF.
    """
    if k <= 0:
        raise ValueError("k RRF должен быть > 0")
    head = list(none_order[:5])
    seen = set(head)
    fused = rrf_merge([none_order, expand_order], k=k)
    tail = [
        chunk_id
        for chunk_id, _score in sorted(fused.items(), key=lambda item: (-item[1], item[0]))
        if chunk_id not in seen
    ]
    seen.update(tail)
    tail.extend(chunk_id for chunk_id in none_order if chunk_id not in seen)
    return head + tail


def _aggregate_parts(
    ordered: list[str],
    scores: dict[str, float],
    by_id: dict[str, Chunk],
    mode: str,
) -> list[str]:
    """Склеивает части одного промпта. Правила и каталог остаются отдельными чанками."""
    if mode == "expand_tail":
        return _expand_tail(ordered, scores, by_id)
    if mode == "none" or not ordered:
        return ordered
    doc_score: dict[str, float] = {}
    pool_members: dict[str, list[str]] = {}
    for chunk_id in ordered:
        chunk = by_id[chunk_id]
        key = _doc_key(chunk)
        score = float(scores.get(chunk_id, 0.0))
        doc_score[key] = score if key not in doc_score else max(doc_score[key], score)
        pool_members.setdefault(key, []).append(chunk_id)
    if mode == "prompt_expand":
        members: dict[str, list[str]] = {key: [] for key in doc_score}
        for chunk in by_id.values():
            if _is_section_window(chunk):
                continue
            key = _doc_key(chunk)
            bucket = members.get(key)
            if bucket is not None and chunk.chunk_id not in bucket:
                bucket.append(chunk.chunk_id)
    else:
        members = pool_members

    def part_order(chunk_id: str) -> tuple[float, str]:
        return (-float(scores.get(chunk_id, float("-inf"))), chunk_id)

    flat: list[str] = []
    seen: set[str] = set()
    for key in sorted(doc_score, key=lambda item: (-doc_score[item], item)):
        for chunk_id in sorted(members.get(key, []), key=part_order):
            if chunk_id in seen or chunk_id not in by_id:
                continue
            seen.add(chunk_id)
            flat.append(chunk_id)
    return flat


def _fuse_bm25_and_rerank(
    bm25_ids: list[str],
    rerank_ids: list[str],
    bm25_scores: dict[str, float],
    *,
    k: int,
    weights: tuple[float, float],
    protect: float,
) -> dict[str, float]:
    """RRF порядка BM25 и реранкера. Сильный топ-1 BM25 остаётся первым."""
    fused = rrf_merge([bm25_ids, rerank_ids], k=k, weights=[weights[0], weights[1]])
    if protect <= 0 or not bm25_ids:
        return fused
    leader = bm25_ids[0]
    leader_score = float(bm25_scores.get(leader, 0.0))
    second = float(bm25_scores.get(bm25_ids[1], 0.0)) if len(bm25_ids) > 1 else 0.0
    ratio = float("inf") if second <= 0 else leader_score / second
    if ratio < protect:
        return fused
    fused[leader] = max(fused.values(), default=0.0) + 1.0
    return fused


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
                "lexical": LEXICAL_ANALYZER,
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
