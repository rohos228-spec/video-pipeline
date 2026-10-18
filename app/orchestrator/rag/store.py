"""SQLite-хранилище ``orch_rag.db``: WAL, FTS5 BM25, векторы float32.

Один writer — воркер движка. Чтения идут отдельным соединением (WAL).
sqlite-vec используется только если расширение импортируется и режим
``vector_backend='sqlite-vec'``. Иначе косинус считается по BLOB — так CI
не зависит от расширения.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
from pathlib import Path

from app.orchestrator.rag.canon import sha256_payload
from app.orchestrator.rag.lexical import lexical_index_text
from app.orchestrator.rag.types import CORPORA, Chunk, SourceDoc, normalize_metadata

logger = logging.getLogger(__name__)

# Доля существующих source_id, которую sync может снять без явного разрешения.
PURGE_MAX_FRACTION = 0.5


class RagSyncError(RuntimeError):
    """Синхронизация сняла бы пустой incoming или больше половины корпуса."""

    def __init__(self, message: str, *, source_ids: list[str]) -> None:
        super().__init__(message)
        self.source_ids = source_ids

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    corpus TEXT NOT NULL,
    source_id TEXT NOT NULL,
    part_key TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    embedding BLOB NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_corpus ON chunks(corpus);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(corpus, source_id);

CREATE TABLE IF NOT EXISTS corpus_revisions (
    corpus TEXT NOT NULL,
    source_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    PRIMARY KEY (corpus, source_id)
);
"""

_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    title,
    text,
    corpus UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_head_fts USING fts5(
    chunk_id UNINDEXED,
    head,
    corpus UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def chunk_id_for(corpus: str, source_id: str, part_key: str) -> str:
    return sha256_payload({"corpus": corpus, "part_key": part_key, "source_id": source_id})


def lexical_head(title: str, metadata: dict | None) -> str:
    """Текст для BM25 только по названию, ключу ноды и роли в группе."""
    meta = metadata or {}
    parts = [title or "", str(meta.get("node_key") or ""), str(meta.get("group_role") or "")]
    return " ".join(part.strip() for part in parts if part and part.strip())


RAG_DB_NAME = "orch_rag.db"
RAG_SIDECAR_NAMES = frozenset({f"{RAG_DB_NAME}-wal", f"{RAG_DB_NAME}-shm"})


def connect(db_path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Писатель создаёт только ``orch_rag.db``. Каталог — лишь у записи (reindex)."""
    if readonly and not db_path.is_file():
        raise FileNotFoundError(db_path)
    if not readonly:
        if db_path.name != RAG_DB_NAME:
            raise RuntimeError("rag пишет только orch_rag.db (и соседние -wal/-shm) и только из reindex")
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) if readonly else sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    else:
        conn.execute("PRAGMA query_only=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.executescript(_FTS)
    _try_sqlite_vec(conn)


def _try_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Подключение расширения не создаёт виртуальную таблицу само по себе.

    Поиск в этом модуле идёт по BLOB (детерминированно). Факт загрузки
    расширения фиксируется в ``user_version`` старшего бита не трогаем —
    просто возвращаем доступность, чтобы вызывающий код мог её залогировать.
    """
    try:
        import sqlite_vec  # type: ignore
    except ImportError:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.Error):
        return False
    return True


class RagStore:
    """Писатель открывается только из ``RagEngine.reindex`` → ``sync``."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def sync(
        self,
        corpus: str,
        grouped: dict[str, tuple[str, list[SourceDoc]]],
        embeddings: dict[str, list[float]],
        *,
        indexed_at: str,
        allow_full_purge: bool = False,
    ) -> tuple[int, int, int]:
        """Синхронизирует один корпус.

        ``grouped``: source_id -> (revision, docs).
        ``embeddings``: chunk_id -> vector (только для документов, которые пишем).

        Пустой ``grouped`` при уже записанных источниках и снятие больше
        ``PURGE_MAX_FRACTION`` источников требуют ``allow_full_purge=True``.
        Пустой корпус и пустой incoming — обычный первый прогон.

        Возвращает (written_chunks, unchanged_chunks, deleted_chunks).
        """
        if corpus not in CORPORA:
            raise ValueError(corpus)
        conn = connect(self.db_path)
        try:
            ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            written, unchanged, deleted = self._sync_locked(
                conn,
                corpus,
                grouped,
                embeddings,
                indexed_at=indexed_at,
                allow_full_purge=allow_full_purge,
            )
            conn.commit()
            return written, unchanged, deleted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _sync_locked(
        self,
        conn: sqlite3.Connection,
        corpus: str,
        grouped: dict[str, tuple[str, list[SourceDoc]]],
        embeddings: dict[str, list[float]],
        *,
        indexed_at: str,
        allow_full_purge: bool = False,
    ) -> tuple[int, int, int]:
        existing = {
            row["source_id"]: (row["revision"], int(row["chunk_count"]))
            for row in conn.execute(
                "SELECT source_id, revision, chunk_count FROM corpus_revisions WHERE corpus = ?",
                (corpus,),
            )
        }
        written = 0
        unchanged = 0
        deleted = 0
        incoming = set(grouped)
        doomed = sorted(source_id for source_id in existing if source_id not in incoming)
        if doomed:
            logger.warning(
                "rag sync corpus=%s удаляются source_id (%s): %s",
                corpus,
                len(doomed),
                doomed,
            )
        fraction = (len(doomed) / len(existing)) if existing else 0.0
        wipe = (not incoming and bool(existing)) or fraction > PURGE_MAX_FRACTION
        if wipe and not allow_full_purge:
            raise RagSyncError(
                (
                    f"sync корпуса {corpus} снял бы {len(doomed)} из {len(existing)} "
                    f"source_id (доля {fraction:.2f}). Нужен allow_full_purge=True."
                ),
                source_ids=doomed,
            )
        for source_id in doomed:
            deleted += self._delete_source(conn, corpus, source_id)
        for source_id, (revision, docs) in grouped.items():
            prev = existing.get(source_id)
            if prev is not None and prev[0] == revision:
                unchanged += prev[1]
                continue
            if prev is not None:
                self._delete_source(conn, corpus, source_id)
            for doc in docs:
                chunk_id = chunk_id_for(doc.corpus, doc.source_id, doc.part_key)
                vector = embeddings[chunk_id]
                self._insert_chunk(conn, doc, chunk_id, vector, indexed_at=indexed_at)
                written += 1
            conn.execute(
                """
                INSERT INTO corpus_revisions(corpus, source_id, revision, chunk_count)
                VALUES (?, ?, ?, ?)
                """,
                (corpus, source_id, revision, len(docs)),
            )
        return written, unchanged, deleted

    def _delete_source(self, conn: sqlite3.Connection, corpus: str, source_id: str) -> int:
        rows = conn.execute(
            "SELECT chunk_id FROM chunks WHERE corpus = ? AND source_id = ?",
            (corpus, source_id),
        ).fetchall()
        for row in rows:
            conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (row["chunk_id"],))
            conn.execute("DELETE FROM chunks_head_fts WHERE chunk_id = ?", (row["chunk_id"],))
        conn.execute(
            "DELETE FROM chunks WHERE corpus = ? AND source_id = ?",
            (corpus, source_id),
        )
        conn.execute(
            "DELETE FROM corpus_revisions WHERE corpus = ? AND source_id = ?",
            (corpus, source_id),
        )
        return len(rows)

    def _insert_chunk(
        self,
        conn: sqlite3.Connection,
        doc: SourceDoc,
        chunk_id: str,
        vector: list[float],
        *,
        indexed_at: str,
    ) -> None:
        metadata = normalize_metadata(doc.metadata)
        metadata["part_key"] = doc.part_key
        meta_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        content_hash = sha256_payload({"text": doc.text, "title": doc.title, "metadata": metadata})
        conn.execute(
            """
            INSERT INTO chunks(
                chunk_id, corpus, source_id, part_key, title, text,
                content_hash, metadata_json, embedding, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                doc.corpus,
                doc.source_id,
                doc.part_key,
                doc.title,
                doc.text,
                content_hash,
                meta_json,
                pack_vector(vector),
                indexed_at,
            ),
        )
        conn.execute(
            "INSERT INTO chunks_fts(chunk_id, title, text, corpus) VALUES (?, ?, ?, ?)",
            (chunk_id, lexical_index_text(doc.title), lexical_index_text(doc.text), doc.corpus),
        )
        conn.execute(
            "INSERT INTO chunks_head_fts(chunk_id, head, corpus) VALUES (?, ?, ?)",
            (chunk_id, lexical_index_text(lexical_head(doc.title, metadata)), doc.corpus),
        )

    def existing_revisions(self, corpus: str) -> dict[str, str]:
        if not self.db_path.is_file():
            return {}
        conn = connect(self.db_path, readonly=True)
        try:
            ensure_ok = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='corpus_revisions'"
            ).fetchone()
            if ensure_ok is None:
                return {}
            rows = conn.execute(
                "SELECT source_id, revision FROM corpus_revisions WHERE corpus = ?",
                (corpus,),
            ).fetchall()
        finally:
            conn.close()
        return {row["source_id"]: row["revision"] for row in rows}

    def fetch_chunks(self, corpora: list[str]) -> list[Chunk]:
        conn = connect(self.db_path, readonly=True)
        try:
            marks = ",".join("?" for _ in corpora)
            rows = conn.execute(
                f"""
                SELECT chunk_id, corpus, source_id, title, text, metadata_json, embedding
                FROM chunks WHERE corpus IN ({marks})
                """,
                corpora,
            ).fetchall()
        finally:
            conn.close()
        chunks: list[Chunk] = []
        for row in rows:
            meta = json.loads(row["metadata_json"])
            chunk = Chunk(
                chunk_id=row["chunk_id"],
                corpus=row["corpus"],
                source_id=row["source_id"],
                title=row["title"],
                text=row["text"],
                metadata=meta,
            )
            chunk.metadata["_embedding"] = unpack_vector(row["embedding"])
            chunks.append(chunk)
        return chunks

    def bm25(
        self,
        match: str,
        corpora: list[str],
        *,
        limit: int,
        scope: str = "full",
    ) -> list[tuple[str, float]]:
        """``scope=full`` — title и текст. ``scope=head`` — title, node_key и роль."""
        if not match.strip():
            return []
        if scope not in {"full", "head"}:
            raise ValueError("scope должен быть full или head")
        table = "chunks_head_fts" if scope == "head" else "chunks_fts"
        conn = connect(self.db_path, readonly=True)
        try:
            marks = ",".join("?" for _ in corpora)
            rows = conn.execute(
                f"""
                SELECT chunk_id, bm25({table}) AS score
                FROM {table}
                WHERE {table} MATCH ? AND corpus IN ({marks})
                ORDER BY score ASC
                LIMIT ?
                """,
                (match, *corpora, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            logger.exception("rag bm25 corpus=%s scope=%s: ошибка FTS, это не пустая выдача", corpora, scope)
            raise
        finally:
            conn.close()
        # sqlite bm25: меньше (отрицательнее) — лучше. Наружу отдаём «больше = лучше».
        return [(row["chunk_id"], -float(row["score"])) for row in rows]

    def snapshot(self) -> list[dict[str, str]]:
        """Стабильный снимок для проверки идемпотентности (без эмбеддингов)."""
        conn = connect(self.db_path, readonly=True)
        try:
            rows = conn.execute(
                """
                SELECT chunk_id, corpus, source_id, part_key, title, text,
                       content_hash, metadata_json, indexed_at
                FROM chunks
                ORDER BY chunk_id
                """
            ).fetchall()
            revisions = conn.execute(
                """
                SELECT corpus, source_id, revision, chunk_count
                FROM corpus_revisions
                ORDER BY corpus, source_id
                """
            ).fetchall()
        finally:
            conn.close()
        payload = [dict(row) for row in rows]
        payload.append({"revisions": [dict(row) for row in revisions]})
        return payload
