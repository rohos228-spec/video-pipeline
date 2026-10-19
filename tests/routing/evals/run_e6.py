"""E6: точность маршрутизации на размеченных запросах.

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

from app.orchestrator.routing.config import load_config
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
    kill_switch: bool
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
    def deterministic_rows(self) -> list[E6RowResult]:
        return [row for row in self.rows if row.expect_deterministic]

    @property
    def classifier_rows(self) -> list[E6RowResult]:
        return [row for row in self.rows if not row.expect_deterministic]

    @staticmethod
    def _ratio(rows: list[E6RowResult]) -> float:
        if not rows:
            return 0.0
        return sum(1 for row in rows if row.ok) / len(rows)

    @property
    def deterministic_accuracy(self) -> float:
        return self._ratio(self.deterministic_rows)

    @property
    def classifier_accuracy(self) -> float:
        return self._ratio(self.classifier_rows)

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
        det = self.deterministic_rows
        clf = self.classifier_rows
        det_ok = sum(1 for row in det if row.ok)
        clf_ok = sum(1 for row in clf if row.ok)
        lines = [
            "# E6 — маршрутизация",
            "",
            f"- запросов: {self.n}",
            f"- детерминированные: {self.deterministic_accuracy:.3f} "
            f"({det_ok}/{len(det)}), порог 0.90",
            f"- классификаторные replay: {self.classifier_accuracy:.3f} "
            f"({clf_ok}/{len(clf)}). Это не измерение модели и в общую цифру не сводится",
            f"- простых на fast: {self.simple_on_fast:.3f}, порог 0.90",
            f"- планов на strong: {self.plans_on_strong:.3f}, порог 1.00",
            f"- доля детерминированных классификаций: {self.deterministic_share:.3f} "
            "(гипотеза ~0.80, порога нет)",
            "",
            "Фикстуры классификатора не генерируются из expected_tier. "
            "Промах файла — ошибка fixture-miss.",
            "",
            "| id | expected | predicted | det | kill | ok |",
            "|---|---|---|---|---|---|",
        ]
        for row in self.rows:
            lines.append(
                f"| {row.id} | {row.expected_tier} | {row.predicted_tier} | "
                f"{int(row.deterministic)} | {int(row.kill_switch)} | {int(row.ok)} |"
            )
        lines.append("")
        return "\n".join(lines)


def load_dataset(path: Path = DATASET) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


async def evaluate(fixture_dir: Path, dataset: Path = DATASET) -> E6Report:
    """Читает готовые фикстуры. Промах — ``FixtureMiss`` (``fixture-miss``)."""
    config = load_config(apply_env=False)
    rows = load_dataset(dataset)
    router = ReplayRouter(config, fixture_dir=fixture_dir)
    report = E6Report()
    for row in rows:
        decision = await router.classify(
            row["message"],
            agent_role=row.get("agent_role"),
            risk=row.get("risk"),
            mutating=bool(row.get("mutating")),
            run_id=row["id"],
        )
        expect_kill = bool(row.get("expect_kill_switch", False))
        ok = (
            decision.tier == row["expected_tier"]
            and decision.mode == row["expected_mode"]
            and decision.deterministic is row["expect_deterministic"]
            and decision.kill_switch is expect_kill
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
                kill_switch=decision.kill_switch,
                ok=ok,
            )
        )
    return report


def write_report(report: E6Report, path: Path = REPORT) -> None:
    path.write_text(report.markdown(), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="E6 routing accuracy")
    parser.add_argument("--fixture-dir", type=Path, default=Path(__file__).with_name("fixtures"))
    parser.add_argument(
        "--write-report",
        type=Path,
        default=REPORT,
        help="куда записать committed-отчёт; тест этот путь не трогает",
    )
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.fixture_dir))
    write_report(report, args.write_report)
    print(report.markdown())
    det_ok = report.deterministic_accuracy >= 0.90
    if not det_ok or report.simple_on_fast < 0.90 or report.plans_on_strong < 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
