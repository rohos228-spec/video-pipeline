"""Временные контракты WS6.

Имена полей ``Chunk`` / ``LLMUsage`` / ``LLMResponse`` повторяют
``app/orchestrator/contracts.py`` из PR #346 (ветка ``cursor/ws3-agents-core-r1-3c7d``)
один к одному. Модуль WS3 в этой ветке не импортируется: файла ещё нет в ``main``.

``FixtureMiss`` — один класс на проект, владелец routing (WS5, PR #345).
Пока модуля нет в ``main``, ниже локальный фолбэк.
TODO(WS5): убрать фолбэк после мёржа #345.

``Chunk.metadata`` в #346 — закрытая модель из четырёх полей. Для нод сюда
дополнительно кладутся поля утверждённого ``NodeDef`` (``source``, ``version``,
``db_version``, ``git_sha256``, ``stale_base``): иначе разрешённую версию
некуда положить, пока WS3 не расширит ``ChunkMetadata``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CorpusName = Literal["rules", "history", "nodes", "prompts"]
CORPORA: tuple[str, ...] = ("rules", "history", "nodes", "prompts")

META_KEYS: tuple[str, ...] = ("node_key", "version", "project_id", "updated_at")


def normalize_metadata(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Гарантирует ключи черновика, не выкидывая дополнительные поля."""
    meta = dict(raw or {})
    for key in META_KEYS:
        meta.setdefault(key, None)
    return meta


@dataclass
class Chunk:
    """Фрагмент корпуса после индексации или retrieval.

    ``chunk_id`` — sha256 канонического JSON ``{corpus, source_id, part_key}``,
    стабильный между реиндексами одной и той же части источника.
    """

    chunk_id: str
    corpus: str
    source_id: str
    title: str
    text: str
    score: float = 0.0
    score_bm25: float = 0.0
    score_vec: float = 0.0
    rank: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = normalize_metadata(self.metadata)


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": int(self.prompt_tokens),
            "completion_tokens": int(self.completion_tokens),
            "reasoning_tokens": int(self.reasoning_tokens),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> LLMUsage:
        raw = raw or {}
        return cls(
            prompt_tokens=int(raw.get("prompt_tokens") or 0),
            completion_tokens=int(raw.get("completion_tokens") or 0),
            reasoning_tokens=int(raw.get("reasoning_tokens") or 0),
        )


@dataclass
class LLMResponse:
    """Ответ роутера WS5. ``fixture_key`` заполнен только у replay."""

    text: str
    call_id: str
    model: str
    provider: str
    tier: str
    finish_reason: str
    usage: LLMUsage
    cost_usd: float
    latency_ms: int
    fallback_index: int
    deterministic: bool
    fixture_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "call_id": self.call_id,
            "model": self.model,
            "provider": self.provider,
            "tier": self.tier,
            "finish_reason": self.finish_reason,
            "usage": self.usage.to_dict(),
            "cost_usd": self.cost_usd,
            "latency_ms": int(self.latency_ms),
            "fallback_index": self.fallback_index,
            "deterministic": self.deterministic,
            "fixture_key": self.fixture_key,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LLMResponse:
        # ``escalation`` из WS5 (#345) здесь нет: в #346 поля нет.
        return cls(
            text=str(raw.get("text") or ""),
            call_id=str(raw.get("call_id") or ""),
            model=str(raw.get("model") or ""),
            provider=str(raw.get("provider") or ""),
            tier=str(raw.get("tier") or ""),
            finish_reason=str(raw.get("finish_reason") or ""),
            usage=LLMUsage.from_dict(raw.get("usage")),
            cost_usd=float(raw.get("cost_usd") or 0.0),
            latency_ms=int(raw.get("latency_ms") or 0),
            fallback_index=int(raw.get("fallback_index") or 0),
            deterministic=bool(raw.get("deterministic")),
            fixture_key=(None if raw.get("fixture_key") in (None, "") else str(raw.get("fixture_key"))),
        )


try:
    from app.orchestrator.routing.types import FixtureMiss as FixtureMiss
except ImportError:  # #345 ещё не в main
    # TODO(WS5): убрать этот класс после мёржа #345 и оставить импорт из routing.

    class FixtureMiss(LookupError):  # type: ignore[no-redef]
        """Нет LLM-фикстуры на ключ. Живой вызов запрещён (контур A / D7)."""

        label = "fixture-miss"

        def __init__(self, fixture_key: str) -> None:
            self.fixture_key = fixture_key
            super().__init__(f"fixture-miss: {fixture_key}")


@dataclass
class ReindexResult:
    corpus: str
    sources: int = 0
    written: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped_invalid_keys: int = 0


@dataclass
class SourceDoc:
    """Уже нарезанный документ, который пишет индекс (1 чанк = 1 SourceDoc)."""

    corpus: str
    source_id: str
    title: str
    text: str
    part_key: str = "full"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.corpus not in CORPORA:
            raise ValueError(f"неизвестный корпус: {self.corpus}")
        self.metadata = normalize_metadata(self.metadata)
