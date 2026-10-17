"""In-memory spend ledger внутренних ходов (D4).

Суб-бюджет $10 на ``run_id`` (scope=internal). Исчерпание не бросает исключение:
роутер ставит ``task_status=waiting_approval`` и ``ApprovalEvent.reason=budget_exhausted``.
Scope ``pipeline`` в этот суб-бюджет не входит ($40 ведёт WS2/WS3).

Память процесса. После рестарта WS3 гидрирует сумму из ``orch_llm_calls``.
"""

from __future__ import annotations

from dataclasses import dataclass


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

    @staticmethod
    def _key(run_id: str | None) -> str:
        return run_id or "_anonymous"

    def spent(self, run_id: str | None) -> float:
        return self._internal.get(self._key(run_id), 0.0)

    def exhausted(self, run_id: str | None) -> bool:
        return self.spent(run_id) >= self.internal_budget_usd

    def hydrate(self, run_id: str | None, internal_spent_usd: float) -> None:
        self._internal[self._key(run_id)] = float(internal_spent_usd)

    def charge(
        self,
        run_id: str | None,
        cost_usd: float,
        *,
        scope: str = "internal",
    ) -> ChargeResult:
        key = self._key(run_id)
        amount = max(0.0, float(cost_usd))
        if scope == "pipeline":
            self._pipeline[key] = self._pipeline.get(key, 0.0) + amount
            return ChargeResult(
                spent_usd=self._pipeline[key],
                budget_usd=None,
                over_budget=False,
            )
        self._internal[key] = self._internal.get(key, 0.0) + amount
        spent = self._internal[key]
        return ChargeResult(
            spent_usd=spent,
            budget_usd=self.internal_budget_usd,
            over_budget=spent >= self.internal_budget_usd,
        )
