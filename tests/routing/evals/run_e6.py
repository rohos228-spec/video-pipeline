"""E6: точность маршрутизации на 40 размеченных запросах.

Контур A: детерминированные правила считаются напрямую, спорные ходы
классификатора читают Replay-фикстуры (живой вызов запрещён).
Доля детерминированных классификаций — отчётная метрика, без порога.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.orchestrator.routing.classifier import classifier_messages
from app.orchestrator.routing.config import load_config
from app.orchestrator.routing.hashing import fixture_key, messages_sha256
from app.orchestrator.routing.model_router import primary_key_fields
from app.orchestrator.routing.replay import ReplayRouter

DATASET = Path(__file__).with_name("e6_dataset.jsonl")
REPORT = Path(__file__).with_name("e6_report.md")


@dataclass
class E6RowResult:
    id: str
    expected_tier: str
    predicted_tier: str
    expected_mode: str
    predicted_mode: str
    deterministic: bool
    expect_deterministic: bool
    ok: bool


@dataclass
class E6Report:
    rows: list[E6RowResult] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def correct(self) -> int:
        return sum(1 for row in self.rows if row.ok)

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0

    @property
    def simple_on_fast(self) -> float:
        simple = [row for row in self.rows if row.expected_tier == "fast"]
        if not simple:
            return 0.0
        return sum(1 for row in simple if row.predicted_tier == "fast") / len(simple)

    @property
    def plans_on_strong(self) -> float:
        plans = [row for row in self.rows if row.expected_tier == "strong"]
        if not plans:
            return 0.0
        return sum(1 for row in plans if row.predicted_tier == "strong") / len(plans)

    @property
    def deterministic_share(self) -> float:
        if not self.n:
            return 0.0
        return sum(1 for row in self.rows if row.deterministic) / self.n

    def markdown(self) -> str:
        lines = [
            "# E6 — маршрутизация",
            "",
            f"- запросов: {self.n}",
            f"- accuracy: {self.accuracy:.3f} ({self.correct}/{self.n}), порог 0.90",
            f"- простых на fast: {self.simple_on_fast:.3f}, порог 0.90",
            f"- планов на strong: {self.plans_on_strong:.3f}, порог 1.00",
            f"- доля детерминированных классификаций: {self.deterministic_share:.3f} "
            "(гипотеза ~0.80, порога нет)",
            "",
            "Спорные строки закрыты replay-фикстурами классификатора (контур A), "
            "не живой моделью.",
            "",
            "| id | expected | predicted | det | ok |",
            "|---|---|---|---|---|",
        ]
        for row in self.rows:
            lines.append(
                f"| {row.id} | {row.expected_tier} | {row.predicted_tier} | "
                f"{int(row.deterministic)} | {int(row.ok)} |"
            )
        lines.append("")
        return "\n".join(lines)


def load_dataset(path: Path = DATASET) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_classifier_fixture(fixture_dir: Path, config, message: str, tier: str) -> None:
    messages = classifier_messages(message)
    fields = primary_key_fields(
        config,
        tier="fast",
        messages=messages,
        temperature=config.classifier_temperature,
        max_tokens=config.classifier_max_tokens,
    )
    key = fixture_key(**fields)
    body = {
        "key": key,
        "request_meta": {
            "tier": fields["tier"],
            "temperature": fields["temperature"],
            "max_tokens": fields["max_tokens"],
            "reasoning_effort": fields["reasoning_effort"],
            "messages_sha256": messages_sha256(messages),
        },
        "response": {
            "text": json.dumps(
                {"tier": tier, "mode": "task", "confidence": 0.92},
                ensure_ascii=False,
            ),
            "call_id": "e6-recorded",
            "model": "gpt-5.6-sol",
            "provider": "52pay",
            "tier": fields["tier"],
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 8,
                "reasoning_tokens": 0,
            },
            "cost_usd": 0.0,
            "latency_ms": 5,
            "fallback_index": 0,
            "deterministic": False,
            "fixture_key": key,
        },
        "recorded_at": "2026-10-17T00:00:00Z",
        "recorded_model": "gpt-5.6-sol",
    }
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / f"{key}.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def evaluate(fixture_dir: Path, dataset: Path = DATASET) -> E6Report:
    config = load_config(apply_env=False)
    rows = load_dataset(dataset)
    for row in rows:
        if not row["expect_deterministic"]:
            _write_classifier_fixture(
                fixture_dir, config, row["message"], row["expected_tier"]
            )
    router = ReplayRouter(config, fixture_dir=fixture_dir)
    report = E6Report()
    for row in rows:
        decision = await router.classify(
            row["message"],
            agent_role=row.get("agent_role"),
            risk=row.get("risk"),
            mutating=bool(row.get("mutating")),
        )
        ok = (
            decision.tier == row["expected_tier"]
            and decision.mode == row["expected_mode"]
            and decision.deterministic is row["expect_deterministic"]
        )
        report.rows.append(
            E6RowResult(
                id=row["id"],
                expected_tier=row["expected_tier"],
                predicted_tier=decision.tier,
                expected_mode=row["expected_mode"],
                predicted_mode=decision.mode,
                deterministic=decision.deterministic,
                expect_deterministic=row["expect_deterministic"],
                ok=ok,
            )
        )
    return report


def write_report(report: E6Report, path: Path = REPORT) -> None:
    path.write_text(report.markdown(), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="E6 routing accuracy")
    parser.add_argument("--fixture-dir", type=Path, default=Path(__file__).with_name("fixtures"))
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.fixture_dir))
    write_report(report)
    print(report.markdown())
    if report.accuracy < 0.90 or report.simple_on_fast < 0.90 or report.plans_on_strong < 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
