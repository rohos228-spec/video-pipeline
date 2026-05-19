"""Round-trip tests for ``LabStorage`` — write a project, read it back, check schema integrity."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.visual_lab.models import (
    AnalysisResult,
    IterDoc,
    IterImage,
    IterPrompt,
    KnowledgeBase,
    ReferenceImage,
    ScoreHistoryPoint,
    WordEffect,
    WordTest,
)
from app.services.visual_lab.storage import LabStorage


def _make_storage(tmp_path: Path, slug: str = "demo") -> LabStorage:
    return LabStorage(slug, root=tmp_path / slug)


def test_skeleton_creates_all_files(tmp_path: Path) -> None:
    s = _make_storage(tmp_path)
    proj = s.ensure_skeleton("Demo project")
    assert s.project_path.exists()
    assert s.knowledge_path.exists()
    assert s.thinking_path.exists()
    assert s.word_tests_path.exists()
    assert s.reference_dir.exists()
    assert proj.slug == "demo"
    assert proj.name == "Demo project"


def test_round_trip_project_with_iter(tmp_path: Path) -> None:
    s = _make_storage(tmp_path)
    proj = s.ensure_skeleton("Demo")
    proj.base_visual_prompt = "kittens in pixel art"
    proj.master_prompt = "kittens in pixel art, cinematic"
    proj.current_iter = 2
    proj.best_score = 7.3
    proj.average_scores_history = [
        ScoreHistoryPoint(iter=1, weighted_score=6.5),
        ScoreHistoryPoint(iter=2, weighted_score=7.3),
    ]
    s.save_project(proj)

    iter_doc = IterDoc(
        iter=2,
        parent_iter=1,
        phase="ok",
        prompt=IterPrompt(text="kittens in pixel art, cinematic"),
        image=IterImage(path="iter_2/image.jpg"),
        analysis=AnalysisResult(
            scores={
                "color_harmony": 8,
                "fur_quality": 7,
                "pixel_sharpness": 9,
            },
            visual_pros=["sharp pixels"],
            visual_cons=["fur a bit flat"],
        ),
        weighted_score=7.3,
        verdict="IMPROVED",
    )
    s.save_iter(iter_doc)

    reloaded = s.load_project()
    assert reloaded is not None
    assert reloaded.master_prompt == proj.master_prompt
    assert reloaded.current_iter == 2
    assert len(reloaded.average_scores_history) == 2

    iter_reloaded = s.load_iter(2)
    assert iter_reloaded is not None
    assert iter_reloaded.iter == 2
    assert iter_reloaded.weighted_score == pytest.approx(7.3)
    assert iter_reloaded.analysis is not None
    assert iter_reloaded.analysis.scores["pixel_sharpness"] == 9
    assert iter_reloaded.verdict == "IMPROVED"


def test_list_iters_sorted(tmp_path: Path) -> None:
    s = _make_storage(tmp_path)
    s.ensure_skeleton("D")
    for n in (3, 1, 2):
        s.save_iter(
            IterDoc(
                iter=n,
                phase="ok",
                prompt=IterPrompt(text="x"),
                weighted_score=float(n),
            )
        )
    assert s.list_iter_numbers() == [1, 2, 3]
    assert [i.iter for i in s.load_all_iters()] == [1, 2, 3]


def test_round_trip_knowledge_and_word_tests(tmp_path: Path) -> None:
    s = _make_storage(tmp_path)
    s.ensure_skeleton("D")
    kb = KnowledgeBase()
    kb.word_effects["crisp pixel edges"] = WordEffect(
        tested=2,
        avg_delta={"pixel_sharpness": 1.5, "outline_thickness": 0.8},
        avg_weighted_delta=0.9,
        stability="STABLE_POSITIVE",
    )
    s.save_knowledge(kb)

    tests = [
        WordTest(
            id=1,
            word="crisp pixel edges",
            operation="ADD",
            target_criteria=["pixel_sharpness"],
            base_iter=1,
            test_iter=2,
            base_scores={"pixel_sharpness": 6, "outline_thickness": 6},
            test_scores={"pixel_sharpness": 8, "outline_thickness": 7},
            delta_per_criterion={"pixel_sharpness": 2.0, "outline_thickness": 1.0},
            weighted_delta=0.9,
            stability="STABLE_POSITIVE",
            verdict="IMPROVED",
        ),
    ]
    s.save_word_tests(tests)

    reloaded_kb = s.load_knowledge()
    assert "crisp pixel edges" in reloaded_kb.word_effects
    assert reloaded_kb.word_effects["crisp pixel edges"].stability == "STABLE_POSITIVE"

    reloaded_tests = s.load_word_tests()
    assert len(reloaded_tests) == 1
    assert reloaded_tests[0].verdict == "IMPROVED"


def test_reference_image_normalizes_scores(tmp_path: Path) -> None:
    ref = ReferenceImage(
        file="ref_1.png",
        prompt="x",
        scores={"color_harmony": 99, "totally_unknown": 7, "fur_quality": 0},
    )
    # 99 clipped to 10, 0 clipped to 1, unknown dropped.
    assert ref.scores == {"color_harmony": 10, "fur_quality": 1}


def test_score_clipping_in_analysis_result(tmp_path: Path) -> None:
    r = AnalysisResult(scores={"color_harmony": 12, "fur_quality": 0, "bogus": 5})
    assert r.scores == {"color_harmony": 10, "fur_quality": 1}


def test_atomic_write_does_not_leak_tmp(tmp_path: Path) -> None:
    s = _make_storage(tmp_path)
    s.ensure_skeleton("D")
    tmp_files = list(s.root.glob(".*.tmp"))
    assert tmp_files == []
