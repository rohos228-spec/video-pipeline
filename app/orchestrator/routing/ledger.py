"""In-memory spend ledger внутренних ходов (D4).

Суб-бюджет $10 на ``run_id`` (scope=internal). Исчерпание не бросает исключение:
роутер ставит ``task_status=waiting_approval`` и ``ApprovalEvent.reason=budget_exhausted``.
Scope ``pipeline`` в лимит $10 не входит.

Память процесса, под ``RLock``. Таблицы ``orch_llm_calls`` на этой ветке нет:
её пишет WS3. После рестарта WS3 гидрирует сумму через ``hydrate``.

Публичное чтение для WS1 ``spend_guard`` (#351, ``d37c63e4``):

- ``spent(run_id)`` и атрибут ``internal_budget_usd`` — internal $10;
- ``pipeline_spent(run_id)`` и ``read_pipeline_spent(*, project_id, run_id)`` —
  pipeline-scope; приватный ``_pipeline`` валидатор не читает;
- ``totals(run_id)`` — снимок, где pipeline в $10 не входит.

Процессный леджер: ``get_spend_ledger()`` без экземпляра роутера.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

_PROCESS_LOCK = threading.RLock()
_PROCESS: SpendLedger | None = None


@dataclass(frozen=True)
class ChargeResult:
    spent_usd: float
    budget_usd: float | None
    over_budget: bool


class SpendLedger:
    def __init__(self, internal_budget_usd: float = 10.0) -> None:
        self.internal_budget_usd = float(internal_budget_usd)
        self._internal: dict[str, float] = {}
        self._pipeline: dict[str, float] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(run_id: str | None) -> str:
        return run_id or "_anonymous"

    def spent(self, run_id: str | None) -> float:
        """Internal по ``run_id``. Так читает ``spend_guard`` в #351.

        ``None`` — анонимный бакет роутера, не «неизвестный запуск».
        Pipeline сюда не входит.
        """
        with self._lock:
            return self._internal.get(self._key(run_id), 0.0)

    def pipeline_spent(self, run_id: str | None) -> float:
        """Pipeline-scope по ``run_id``. В лимит $10 не входит.

        Имя, которое #351 (``d37c63e4``) зовёт позиционно: ``fn(run_id)``.
        Пустой или неизвестный ``run_id`` — 0, анонимный бакет не отдаётся.
        """
        return self._pipeline_of(run_id)

    def read_pipeline_spent(
        self,
        *,
        project_id: int | None = None,
        run_id: str | None = None,
    ) -> float:
        """То же число, что ``pipeline_spent``. #351 пробует это имя первым.

        ``project_id`` не фильтрует: леджер keyed только по ``run_id``.
        """
        del project_id
        return self._pipeline_of(run_id)

    def _pipeline_of(self, run_id: str | None) -> float:
        if run_id is None or str(run_id).strip() == "":
            return 0.0
        key = run_id if isinstance(run_id, str) else str(run_id)
        with self._lock:
            return float(self._pipeline.get(key, 0.0))

    def exhausted(self, run_id: str | None) -> bool:
        with self._lock:
            spent = self._internal.get(self._key(run_id), 0.0)
            return spent >= self.internal_budget_usd

    def hydrate(self, run_id: str | None, internal_spent_usd: float) -> None:
        with self._lock:
            self._internal[self._key(run_id)] = float(internal_spent_usd)

    def totals(self, run_id: str | None = None) -> dict[str, float | bool]:
        """Снимок только для чтения.

        Неизвестный или пустой ``run_id`` — нулевой расход (лимит леджера
        остаётся, ``budget_exhausted`` ложь). Pipeline в лимит $10 не входит.
        """
        limit = float(self.internal_budget_usd)
        if run_id is None or str(run_id).strip() == "":
            return _zero_totals(limit)
        key = run_id if isinstance(run_id, str) else str(run_id)
        with self._lock:
            internal = float(self._internal.get(key, 0.0))
            pipeline = float(self._pipeline.get(key, 0.0))
        remaining = limit - internal
        return {
            "internal_spent_usd": internal,
            "pipeline_spent_usd": pipeline,
            "internal_limit_usd": limit,
            "internal_remaining_usd": remaining,
            "budget_exhausted": internal >= limit,
        }

    def charge(
        self,
        run_id: str | None,
        cost_usd: float,
        *,
        scope: str = "internal",
    ) -> ChargeResult:
        key = self._key(run_id)
        amount = max(0.0, float(cost_usd))
        with self._lock:
            if scope == "pipeline":
                self._pipeline[key] = self._pipeline.get(key, 0.0) + amount
                return ChargeResult(
                    spent_usd=self._pipeline[key],
                    budget_usd=None,
                    over_budget=False,
                )
            self._internal[key] = self._internal.get(key, 0.0) + amount
            spent = self._internal[key]
            limit = self.internal_budget_usd
        return ChargeResult(
            spent_usd=spent,
            budget_usd=limit,
            over_budget=spent >= limit,
        )


def _zero_totals(limit: float) -> dict[str, float | bool]:
    return {
        "internal_spent_usd": 0.0,
        "pipeline_spent_usd": 0.0,
        "internal_limit_usd": limit,
        "internal_remaining_usd": limit,
        "budget_exhausted": False,
    }


def get_spend_ledger() -> SpendLedger:
    """Процессный леджер. Роутер без своего экземпляра пишет сюда."""
    global _PROCESS
    with _PROCESS_LOCK:
        if _PROCESS is None:
            _PROCESS = SpendLedger()
        return _PROCESS


def reset_spend_ledger(internal_budget_usd: float = 10.0) -> SpendLedger:
    """Подмена процессного леджера. Нужна тестам, не бою."""
    global _PROCESS
    with _PROCESS_LOCK:
        _PROCESS = SpendLedger(internal_budget_usd)
        return _PROCESS
