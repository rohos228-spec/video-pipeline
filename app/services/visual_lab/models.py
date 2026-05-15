"""Pydantic models for all visual-lab JSON documents.

These match the on-disk JSON layout one-to-one. Every phase response from
GPT is validated against one of ``AnalysisResult`` / ``ThinkResult`` /
``BuildResult`` before we trust it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.visual_lab.criteria import CRITERION_IDS

# --------------------------- common helpers ---------------------------------


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_scores(scores: dict[str, int]) -> dict[str, int]:
    """Ensure every score is an int 1..10 and key is a known criterion id.

    Missing criteria are tolerated for partial / failed analyses — we
    clip them later.
    """
    out: dict[str, int] = {}
    for k, v in scores.items():
        if k not in CRITERION_IDS:
            # Drop unknown keys silently — older logs / typos shouldn't
            # break loading the whole project.
            continue
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        out[k] = max(1, min(10, iv))
    return out


# ------------------------------ phase outputs -------------------------------


class CriterionExplanation(BaseModel):
    """GPT's per-criterion explanation in the analyze phase."""

    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=1, le=10)
    what: str = ""
    responsible_words: list[str] = Field(default_factory=list)
    missing_words: list[str] = Field(default_factory=list)
    fix_suggestion: str = ""


class AnalysisResult(BaseModel):
    """Output of the analyze phase (per iteration)."""

    model_config = ConfigDict(extra="ignore")

    scores: dict[str, int] = Field(default_factory=dict)
    visual_pros: list[str] = Field(default_factory=list)
    visual_cons: list[str] = Field(default_factory=list)
    criterion_explanations: dict[str, CriterionExplanation] = Field(
        default_factory=dict
    )
    keyword_effects: dict[str, dict[str, float]] = Field(default_factory=dict)

    @field_validator("scores")
    @classmethod
    def _normalize_scores(cls, v: dict[str, Any]) -> dict[str, int]:
        return _validate_scores(v)

    @property
    def weighted_score(self) -> float:
        from app.services.visual_lab.criteria import weighted_score

        return weighted_score({k: float(v) for k, v in self.scores.items()})


class Hypothesis(BaseModel):
    """One hypothesis the lab will (or did) test."""

    model_config = ConfigDict(extra="ignore")

    id: int
    text: str
    type: Literal["ADD_WORD", "REMOVE_WORD", "REPLACE_WORD", "COMBO", "OTHER"] = (
        "ADD_WORD"
    )
    test_word: str | None = None
    replacement_for: str | None = None
    target_criteria: list[str] = Field(default_factory=list)
    priority: int = Field(default=5, ge=1, le=10)
    status: Literal[
        "PROPOSED", "TESTING", "CONFIRMED", "REJECTED", "INCONCLUSIVE"
    ] = "PROPOSED"
    evidence: str = ""


class ThinkResult(BaseModel):
    """Output of the think (chain-of-thought) phase."""

    model_config = ConfigDict(extra="ignore")

    reasoning_summary: str = ""
    key_observations: list[str] = Field(default_factory=list)
    weakest_criteria: list[str] = Field(default_factory=list)
    new_hypotheses: list[Hypothesis] = Field(default_factory=list)
    confirmed_hypotheses_ids: list[int] = Field(default_factory=list)
    rejected_hypotheses_ids: list[int] = Field(default_factory=list)
    antihypotheses: list[str] = Field(default_factory=list)


class WordRationale(BaseModel):
    """Why a particular word is in the new master prompt."""

    model_config = ConfigDict(extra="ignore")

    word: str
    rationale: str = ""
    source_test_ids: list[int] = Field(default_factory=list)
    target_criteria: list[str] = Field(default_factory=list)


class BuildResult(BaseModel):
    """Output of the build phase — a new master prompt."""

    model_config = ConfigDict(extra="ignore")

    master_prompt: str
    word_rationale: list[WordRationale] = Field(default_factory=list)
    expected_gain: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


# ------------------------------ word tests ----------------------------------


class WordTest(BaseModel):
    """One A/B (or combo) word test with full deltas."""

    model_config = ConfigDict(extra="ignore")

    id: int
    hypothesis_id: int | None = None
    word: str | None = None  # for single-word tests
    words: list[str] = Field(default_factory=list)  # for combo tests
    operation: Literal["ADD", "REMOVE", "REPLACE", "COMBO"] = "ADD"
    target_criteria: list[str] = Field(default_factory=list)
    base_iter: int
    test_iter: int
    base_scores: dict[str, int] = Field(default_factory=dict)
    test_scores: dict[str, int] = Field(default_factory=dict)
    delta_per_criterion: dict[str, float] = Field(default_factory=dict)
    weighted_delta: float = 0.0
    repeat_scores: list[float] = Field(default_factory=list)
    stability: Literal[
        "STABLE_POSITIVE", "STABLE_NEGATIVE", "STABLE", "UNSTABLE", "UNTESTED"
    ] = "UNTESTED"
    verdict: Literal[
        "IMPROVED", "REGRESSED", "NEUTRAL", "FAILED", "INCONCLUSIVE"
    ] = "INCONCLUSIVE"
    side_effects: dict[str, float] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utcnow_iso)


# ------------------------------ knowledge base ------------------------------


class WordEffect(BaseModel):
    """Accumulated effect of one word across all its tests."""

    model_config = ConfigDict(extra="ignore")

    tested: int = 0
    avg_delta: dict[str, float] = Field(default_factory=dict)
    avg_weighted_delta: float = 0.0
    stability: Literal[
        "STABLE_POSITIVE", "STABLE_NEGATIVE", "STABLE", "UNSTABLE", "UNTESTED"
    ] = "UNTESTED"
    conflicts_with: list[str] = Field(default_factory=list)
    synergizes_with: list[str] = Field(default_factory=list)


class ComboEffect(BaseModel):
    """Accumulated effect of one word combination."""

    model_config = ConfigDict(extra="ignore")

    tested: int = 0
    avg_total_delta: float = 0.0
    individual_sum_delta: float = 0.0
    synergy_verdict: Literal["SYNERGY", "NEUTRAL", "CONFLICT"] = "NEUTRAL"


class VisualRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rule: str
    supporting_evidence: str = ""


class KnowledgeBase(BaseModel):
    """The accumulated lab knowledge — persisted as ``knowledge_base.json``.

    Never overwrite — only merge. Each iteration deepens this.
    """

    model_config = ConfigDict(extra="ignore")

    word_effects: dict[str, WordEffect] = Field(default_factory=dict)
    combo_effects: dict[str, ComboEffect] = Field(default_factory=dict)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    visual_rules: dict[str, list[VisualRule]] = Field(
        default_factory=lambda: {"do": [], "dont": []}
    )
    evolution_log: list[str] = Field(default_factory=list)


# ----------------------------- iteration & project --------------------------


class IterPrompt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    added_tokens: list[str] = Field(default_factory=list)
    removed_tokens: list[str] = Field(default_factory=list)
    rationale: str = ""


class IterImage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    outsee_url: str | None = None
    outsee_gen_id: str | None = None


class ErrorRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timestamp: str = Field(default_factory=_utcnow_iso)
    phase: str
    retry_attempt: int = 0
    message: str = ""


class IterDoc(BaseModel):
    """One iteration on disk: ``iter_<N>/iter.json``."""

    model_config = ConfigDict(extra="ignore")

    iter: int
    parent_iter: int | None = None
    phase: Literal[
        "ok",
        "running",
        "error_analyze",
        "error_think",
        "error_build",
        "error_outsee",
        "error_gpt",
        "error_prompt_too_long",
        "error_unknown",
        "skipped",
    ] = "running"
    prompt: IterPrompt
    image: IterImage | None = None
    analysis: AnalysisResult | None = None
    weighted_score: float = 0.0
    deltas_from_parent: dict[str, float] = Field(default_factory=dict)
    verdict: Literal[
        "IMPROVED", "REGRESSED", "NEUTRAL", "FAILED", "INCONCLUSIVE"
    ] = "INCONCLUSIVE"
    notes: str = ""
    error_log: list[ErrorRecord] = Field(default_factory=list)
    timestamp: str = Field(default_factory=_utcnow_iso)


class StoppingRules(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_avg_score: float = 8.5
    max_iterations: int = 100
    stop_if_no_improvement_for: int = 3
    max_consecutive_failed_iters: int = 5


class ProjectMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_iterations_attempted: int = 0
    total_iterations_succeeded: int = 0
    total_phase_errors_by_type: dict[str, int] = Field(default_factory=dict)


class ScoreHistoryPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    iter: int
    weighted_score: float


class ProjectDoc(BaseModel):
    """``project.json`` — top-level meta of a visual lab project."""

    model_config = ConfigDict(extra="ignore")

    slug: str
    name: str
    base_visual_prompt: str = ""
    master_prompt: str = ""
    aspect_ratio: str = "16:9"
    model_slug: str = "nano-banana-pro"
    relax: bool = True
    current_iter: int = 0
    best_iter: int | None = None
    best_score: float = 0.0
    status: Literal[
        "idle", "running", "paused", "completed", "error"
    ] = "idle"
    last_error: str = ""
    stopping_rules: StoppingRules = Field(default_factory=StoppingRules)
    meta: ProjectMeta = Field(default_factory=ProjectMeta)
    average_scores_history: list[ScoreHistoryPoint] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)  # filenames in reference/
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)


# ------------------------------ references ----------------------------------


class ReferenceImage(BaseModel):
    """``reference/ref_<N>.json`` — one user-supplied benchmark image."""

    model_config = ConfigDict(extra="ignore")

    file: str  # e.g. "ref_1.png"
    prompt: str = ""
    scores: dict[str, int] = Field(default_factory=dict)
    notes: str = ""

    @field_validator("scores")
    @classmethod
    def _norm_ref_scores(cls, v: dict[str, Any]) -> dict[str, int]:
        return _validate_scores(v)

    @property
    def weighted_score(self) -> float:
        from app.services.visual_lab.criteria import weighted_score

        return weighted_score({k: float(v) for k, v in self.scores.items()})


# ------------------------------ thinking log --------------------------------


class ThinkingLogEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    iter: int
    timestamp: str = Field(default_factory=_utcnow_iso)
    reasoning_summary: str = ""
    raw_response: str = ""
    new_hypotheses_count: int = 0


class ThinkingLog(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entries: list[ThinkingLogEntry] = Field(default_factory=list)
