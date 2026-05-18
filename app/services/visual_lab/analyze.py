"""Analyze phase — GPT-Vision scores one iteration against 20 criteria.

Inputs (passed as attachments to ChatGPT):
    - image_path (the generated picture for this iteration)
    - scores.xlsx (the cumulative history, see excel_export.rebuild_excel)
    - optional: 1-5 reference images (only on iter 1 to anchor "good")

Output:
    - AnalysisResult (validated against the Pydantic model)
    - iter.analysis is set, iter.weighted_score is computed,
      iter.deltas_from_parent is populated, iter.phase = "ok".

The phase is wrapped by ``runner._safe_phase``, so any raised exception
inside is caught, retried, and finally written to ``iter.error_log``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from app.services.visual_lab.criteria import weighted_score
from app.services.visual_lab.excel_export import rebuild_excel
from app.services.visual_lab.gpt_io import ask_gpt_validated
from app.services.visual_lab.limits import MAX_PHASE_RETRIES
from app.services.visual_lab.models import (
    AnalysisResult,
    IterDoc,
    ScoreHistoryPoint,
)
from app.services.visual_lab.prompts import ANALYZE_SYSTEM_PROMPT, analyze_user_prompt
from app.services.visual_lab.storage import LabStorage


async def analyze_iteration(
    storage: LabStorage,
    iter_doc: IterDoc,
    *,
    image_path: Path,
    chatgpt_ask_with_files: Callable[[str, list[Path]], Awaitable[str]],
    include_references_in_attachments: bool = False,
    retries: int = MAX_PHASE_RETRIES,
) -> AnalysisResult:
    """Run analyze on a single iteration in-place.

    ``chatgpt_ask_with_files`` is the only browser-side callable; the
    rest is pure Python. Caller is responsible for passing it (see
    :func:`runner._make_chatgpt_caller`). Keeping it injectable lets us
    unit-test the analyze logic with a fake ask_fn.
    """
    project = storage.load_project()
    if project is None:
        raise RuntimeError(f"project.json missing for {storage.slug!r}")

    # Refresh excel right before the call so GPT sees up-to-date history.
    rebuild_excel(storage)

    attachments: list[Path] = [image_path]
    if storage.excel_path.exists():
        attachments.append(storage.excel_path)
    if include_references_in_attachments:
        for fname in project.references[:5]:
            p = storage.reference_dir / fname
            if p.exists():
                attachments.append(p)

    user_msg = (
        f"{ANALYZE_SYSTEM_PROMPT}\n\n"
        f"---\n\n"
        f"{analyze_user_prompt(prompt_used=iter_doc.prompt.text, iter_num=iter_doc.iter, project_name=project.name)}"
    )

    async def _ask_fn(prompt: str, **kw: object) -> str:
        atts = kw.get("attachments") or attachments
        return await chatgpt_ask_with_files(prompt, atts)  # type: ignore[arg-type]

    result = await ask_gpt_validated(
        ask_fn=_ask_fn,
        base_prompt=user_msg,
        model=AnalysisResult,
        attachments=attachments,
        schema_hint="See ANALYZE_SYSTEM_PROMPT above; scores must be ints 1..10",
        retries=retries,
        chat_log_dir=storage.iter_chat_dir(iter_doc.iter),
        label=f"analyze_iter_{iter_doc.iter}",
    )
    assert isinstance(result, AnalysisResult)

    iter_doc.analysis = result
    iter_doc.weighted_score = weighted_score(
        {k: float(v) for k, v in result.scores.items()}
    )

    # Compute deltas from parent iter.
    parent_doc = (
        storage.load_iter(iter_doc.parent_iter)
        if iter_doc.parent_iter is not None
        else None
    )
    if parent_doc and parent_doc.analysis and parent_doc.weighted_score > 0:
        iter_doc.deltas_from_parent = {
            cid: float(result.scores.get(cid, 0))
            - float(parent_doc.analysis.scores.get(cid, 0))
            for cid in set(result.scores) & set(parent_doc.analysis.scores)
        }
        delta_w = iter_doc.weighted_score - parent_doc.weighted_score
        if delta_w > 0.2:
            iter_doc.verdict = "IMPROVED"
        elif delta_w < -0.2:
            iter_doc.verdict = "REGRESSED"
        else:
            iter_doc.verdict = "NEUTRAL"
    else:
        iter_doc.deltas_from_parent = {}
        iter_doc.verdict = "NEUTRAL"

    iter_doc.phase = "ok"
    storage.save_iter(iter_doc)

    # Update project meta.
    if project.best_score < iter_doc.weighted_score:
        project.best_score = iter_doc.weighted_score
        project.best_iter = iter_doc.iter
    project.meta.total_iterations_succeeded += 1
    project.average_scores_history.append(
        ScoreHistoryPoint(
            iter=iter_doc.iter, weighted_score=iter_doc.weighted_score
        )
    )
    storage.save_project(project)

    # Rebuild excel one more time so subsequent phases (think/build) see
    # the freshly-scored row.
    rebuild_excel(storage)

    logger.info(
        "visual_lab.analyze[{}] iter={} weighted={:.2f} verdict={}",
        storage.slug,
        iter_doc.iter,
        iter_doc.weighted_score,
        iter_doc.verdict,
    )
    # Small async yield so we don't starve the worker loop.
    await asyncio.sleep(0)
    return result
