"""Think phase — chain-of-thought hypothesis generation.

Reads the accumulated history + knowledge base and asks ChatGPT to
reason about which words to test next. The result is appended to
``thinking_log.json`` and new hypotheses are merged into
``knowledge_base.json``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from app.services.visual_lab.criteria import CRITERION_IDS
from app.services.visual_lab.excel_export import rebuild_excel
from app.services.visual_lab.gpt_io import ask_gpt_validated
from app.services.visual_lab.limits import MAX_PHASE_RETRIES
from app.services.visual_lab.models import (
    Hypothesis,
    ThinkingLogEntry,
    ThinkResult,
)
from app.services.visual_lab.prompts import THINK_SYSTEM_PROMPT, think_user_prompt
from app.services.visual_lab.storage import LabStorage


def _compute_weak_criteria(storage: LabStorage, *, top_n: int = 5) -> list[str]:
    """Return up to ``top_n`` criterion_ids with the lowest mean score.

    Considers only iterations with phase="ok" and weighted_score > 0.
    """
    iters = [
        i for i in storage.load_all_iters()
        if i.phase == "ok" and i.analysis and i.weighted_score > 0
    ]
    if not iters:
        return []
    sums: dict[str, float] = {cid: 0.0 for cid in CRITERION_IDS}
    counts: dict[str, int] = {cid: 0 for cid in CRITERION_IDS}
    for it in iters:
        for cid, score in it.analysis.scores.items():
            if cid in sums:
                sums[cid] += float(score)
                counts[cid] += 1
    means = {
        cid: (sums[cid] / counts[cid]) if counts[cid] else 10.0
        for cid in CRITERION_IDS
    }
    return [cid for cid, _ in sorted(means.items(), key=lambda x: x[1])[:top_n]]


def _knowledge_summary(storage: LabStorage, *, max_words: int = 20) -> str:
    """A short text dump of the knowledge base for the user_prompt.

    GPT also gets knowledge_base.json as an attachment, but a textual
    summary is helpful when the file upload silently drops on retry.
    """
    kb = storage.load_knowledge()
    if not kb.word_effects and not kb.hypotheses:
        return "(пусто — это первая итерация)"
    lines = []
    sorted_words = sorted(
        kb.word_effects.items(),
        key=lambda x: abs(x[1].avg_weighted_delta),
        reverse=True,
    )[:max_words]
    if sorted_words:
        lines.append("Word effects:")
        for word, eff in sorted_words:
            lines.append(
                f"  - {word!r}: tested={eff.tested}, "
                f"stability={eff.stability}, "
                f"avg_weighted_delta={eff.avg_weighted_delta:+.2f}"
            )
    open_h = [h for h in kb.hypotheses if h.status == "PROPOSED"]
    if open_h:
        lines.append(f"Open hypotheses ({len(open_h)}):")
        for h in sorted(open_h, key=lambda x: -x.priority)[:10]:
            lines.append(
                f"  - #{h.id} [{h.type}] prio={h.priority}: {h.text[:120]}"
            )
    return "\n".join(lines)


def _next_hypothesis_id(storage: LabStorage) -> int:
    kb = storage.load_knowledge()
    return (max((h.id for h in kb.hypotheses), default=0) or 0) + 1


async def think_phase(
    storage: LabStorage,
    *,
    chatgpt_ask_with_files: Callable[[str, list[Path]], Awaitable[str]],
    retries: int = MAX_PHASE_RETRIES,
) -> ThinkResult:
    """Run the think phase. Mutates knowledge_base.json & thinking_log.json.

    Returns the validated ``ThinkResult``.
    """
    project = storage.load_project()
    if project is None:
        raise RuntimeError(f"project.json missing for {storage.slug!r}")

    rebuild_excel(storage)

    weak = _compute_weak_criteria(storage)
    knowledge_summary = _knowledge_summary(storage)

    attachments: list[Path] = []
    if storage.excel_path.exists():
        attachments.append(storage.excel_path)
    if storage.knowledge_path.exists():
        attachments.append(storage.knowledge_path)
    for fname in project.references[:5]:
        p = storage.reference_dir / fname
        if p.exists():
            attachments.append(p)

    iters_done = project.meta.total_iterations_succeeded

    user_msg = (
        f"{THINK_SYSTEM_PROMPT}\n\n---\n\n"
        f"{think_user_prompt(project_name=project.name, iters_done=iters_done, weakest_criteria_hint=weak, knowledge_summary=knowledge_summary)}"
    )

    async def _ask_fn(prompt: str, **kw: object) -> str:
        atts = kw.get("attachments") or attachments
        return await chatgpt_ask_with_files(prompt, atts)  # type: ignore[arg-type]

    result = await ask_gpt_validated(
        ask_fn=_ask_fn,
        base_prompt=user_msg,
        model=ThinkResult,
        attachments=attachments,
        schema_hint=(
            "See THINK_SYSTEM_PROMPT; new_hypotheses must have "
            "incrementing ids and priority 1..10."
        ),
        retries=retries,
        chat_log_dir=storage.root / "chat",
        label=f"think_iter_{project.current_iter}",
    )
    assert isinstance(result, ThinkResult)

    # Merge new_hypotheses into knowledge_base (auto-assign ids if missing).
    kb = storage.load_knowledge()
    next_id = _next_hypothesis_id(storage)
    merged: list[Hypothesis] = []
    for h in result.new_hypotheses:
        # Force unique sequential ID — GPT-supplied IDs can clash.
        h.id = next_id
        next_id += 1
        # Validate target_criteria against known ids.
        h.target_criteria = [
            c for c in h.target_criteria if c in CRITERION_IDS
        ]
        merged.append(h)
    kb.hypotheses.extend(merged)

    # Mark confirmed / rejected per the think result.
    for hyp in kb.hypotheses:
        if hyp.id in result.confirmed_hypotheses_ids:
            hyp.status = "CONFIRMED"
        elif hyp.id in result.rejected_hypotheses_ids:
            hyp.status = "REJECTED"

    storage.save_knowledge(kb)

    # Append to thinking log.
    log = storage.load_thinking_log()
    log.entries.append(
        ThinkingLogEntry(
            iter=project.current_iter,
            reasoning_summary=result.reasoning_summary[:8000],
            raw_response=result.model_dump_json(),
            new_hypotheses_count=len(merged),
        )
    )
    storage.save_thinking_log(log)

    logger.info(
        "visual_lab.think[{}] iter={} added {} hypotheses, weakest={}",
        storage.slug,
        project.current_iter,
        len(merged),
        weak,
    )
    return result


def pick_top_hypotheses(
    storage: LabStorage, *, limit: int = 3
) -> list[Hypothesis]:
    """Return up to ``limit`` highest-priority PROPOSED hypotheses, weakest-criteria-first."""
    kb = storage.load_knowledge()
    weak = _compute_weak_criteria(storage, top_n=10)
    weak_set = set(weak)

    def sort_key(h: Hypothesis) -> tuple[int, int]:
        overlaps_weak = any(c in weak_set for c in h.target_criteria)
        return (-h.priority, 0 if overlaps_weak else 1)

    open_h = [h for h in kb.hypotheses if h.status == "PROPOSED"]
    return sorted(open_h, key=sort_key)[:limit]


def all_weak_criteria(storage: LabStorage) -> list[str]:
    """Public accessor used by other phases (build/report)."""
    return _compute_weak_criteria(storage)


# Exported for build phase / report.
__all__ = [
    "think_phase",
    "pick_top_hypotheses",
    "all_weak_criteria",
]
