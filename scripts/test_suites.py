"""Единый перечень наборов тестов: какие файлы в каком наборе, и что ни один тестовый файл не выпадает.

Наборы:
  orchestrator - фиксированный список в scripts/run_orchestrator_tests.py (быстрые + приёмочные).
  legacy       - остальные tests/test_*.py и tests/*_test.py (верхний уровень).
  observe      - tests/observe_v1/ (test_*.py и *_test.py), кроме пяти docker-модулей.
  docker       - пять модулей tests/observe_v1, которым нужен /.dockerenv (гоняет задача docker в CI).

Раньше около 60 модулей observe_v1 не входили ни в один автоматический набор: красный тест там никто не видел.
Тест tests/w40_test_infra_test.py требует, чтобы любой тестовый файл попадал хотя бы в один набор.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
DOCKER_ONLY = (
    "tests/observe_v1/test_decision_tool_cycle_gateway_ack.py",
    "tests/observe_v1/test_gateway_route_policy.py",
    "tests/observe_v1/test_hitl_project_authority.py",
    "tests/observe_v1/test_managed_restart_safety.py",
    "tests/observe_v1/test_readonly_pilot.py",
)
SKIP_DIRS = {"__pycache__", "fixtures"}


def is_test_file(path: Path) -> bool:
    return path.suffix == ".py" and (path.name.startswith("test_") or path.name.endswith("_test.py"))


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def all_test_files() -> list[str]:
    """Каждый тестовый файл под tests/ на любой глубине (кроме fixtures)."""
    out = []
    for p in TESTS.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.relative_to(TESTS).parts) or not is_test_file(p):
            continue
        out.append(_rel(p))
    return sorted(out)


def legacy_files() -> list[str]:
    return sorted(_rel(p) for p in TESTS.glob("*.py") if is_test_file(p))


def observe_files() -> list[str]:
    docker = set(DOCKER_ONLY)
    return sorted(_rel(p) for p in (TESTS / "observe_v1").glob("*.py") if is_test_file(p) and _rel(p) not in docker)


def orchestrator_files() -> list[str]:
    spec = importlib.util.spec_from_file_location("run_orchestrator_tests_mod", ROOT / "scripts" / "run_orchestrator_tests.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return sorted(set(mod.FAST + mod.ACCEPTANCE))


def suites() -> dict[str, list[str]]:
    return {
        "orchestrator": orchestrator_files(),
        "legacy": legacy_files(),
        "observe": observe_files(),
        "docker": sorted(DOCKER_ONLY),
    }


def uncovered() -> list[str]:
    """Тестовые файлы, которых нет ни в одном наборе."""
    covered = {f for files in suites().values() for f in files}
    return [f for f in all_test_files() if f not in covered]
