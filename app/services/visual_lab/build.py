"""Build phase — assemble a new master_prompt.

Asks ChatGPT to combine the current master prompt + knowledge base +
top hypotheses into a new prompt of length ≤ 4720 chars. Enforces the
hard length limit by:

1. Telling GPT the limit in the system prompt.
2. Validating the returned ``master_prompt.text`` length client-side.
3. On overflow — one extra retry asking GPT to shrink to the cap.
4. If still over → raise ``PromptTooLongError`` and let the runner mark
   the iteration as ``error_prompt_too_long``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from app.services.visual_lab.excel_export import rebuild_excel
from app.services.visual_lab.gpt_io import ask_gpt_validated
from app.services.visual_lab.limits import (
    MAX_PHASE_RETRIES,
    PromptTooLongError,
    check_prompt_length,
    soft_limit,
)
from app.services.visual_lab.models import BuildResult
from app.services.visual_lab.prompts import BUILD_SYSTEM_PROMPT, build_user_prompt
from app.services.visual_lab.storage import LabStorage
from app.services.visual_lab.think import all_weak_criteria, pick_top_hypotheses


async def build_phase(
    storage: LabStorage,
    *,
    chatgpt_ask_with_files: Callable[[str, list[Path]], Awaitable[str]],
    retries: int = MAX_PHASE_RETRIES,
) -> BuildResult:
    project = storage.load_project()
    if project is None:
        raise RuntimeError(f"project.json missing for {storage.slug!r}")

    rebuild_excel(storage)

    weak = all_weak_criteria(storage)
    top_hyps = pick_top_hypotheses(storage)

    attachments: list[Path] = []
    if storage.excel_path.exists():
        attachments.append(storage.excel_path)
    if storage.knowledge_path.exists():
        attachments.append(storage.knowledge_path)

    user_msg = (
        f"{BUILD_SYSTEM_PROMPT}\n\n---\n\n"
        + build_user_prompt(
            project_name=project.name,
            current_master_prompt=project.master_prompt or project.base_visual_prompt,
            base_visual_prompt=project.base_visual_prompt,
            weakest_criteria=weak,
            top_hypotheses=[
                f"#{h.id} [{h.type}] {h.text}" for h in top_hyps
            ],
        )
    )

    async def _ask_fn(prompt: str, **kw: object) -> str:
        atts = kw.get("attachments") or attachments
        return await chatgpt_ask_with_files(prompt, atts)  # type: ignore[arg-type]

    result = await ask_gpt_validated(
        ask_fn=_ask_fn,
        base_prompt=user_msg,
        model=BuildResult,
        attachments=attachments,
        schema_hint=(
            f"master_prompt must be ≤ {soft_limit()} characters "
            f"(hard cap). Return only one JSON object."
        ),
        retries=retries,
        chat_log_dir=storage.root / "chat",
        label=f"build_iter_{project.current_iter + 1}",
    )
    assert isinstance(result, BuildResult)

    # Length enforcement.
    if len(result.master_prompt) > soft_limit():
        logger.warning(
            "visual_lab.build[{}]: GPT returned {}-char prompt, retrying to shrink",
            storage.slug,
            len(result.master_prompt),
        )
        shrink_note = (
            f"\n\nThe master_prompt you returned is {len(result.master_prompt)} "
            f"characters. The HARD limit is {soft_limit()}. Shrink it: drop "
            f"redundant adjectives, merge synonyms, but keep the scene/character "
            f"intact and keep all STABLE_POSITIVE words from the knowledge base. "
            f"Return ONE JSON object."
        )
        result = await ask_gpt_validated(
            ask_fn=_ask_fn,
            base_prompt=user_msg + shrink_note,
            model=BuildResult,
            attachments=attachments,
            retries=1,
            chat_log_dir=storage.root / "chat",
            label=f"build_iter_{project.current_iter + 1}_shrink",
        )
        check_prompt_length(result.master_prompt)

    # Persist as the new master_prompt on the project doc (the runner
    # decides whether to actually use it for the next iteration).
    project.master_prompt = result.master_prompt
    storage.save_project(project)

    logger.info(
        "visual_lab.build[{}] new master_prompt len={} expected_gain_keys={}",
        storage.slug,
        len(result.master_prompt),
        list(result.expected_gain.keys()),
    )
    return result


def assert_prompt_fits(prompt: str) -> None:
    """Public guard for callers that build a prompt locally (not via GPT)."""
    check_prompt_length(prompt)


__all__ = [
    "build_phase",
    "assert_prompt_fits",
    "PromptTooLongError",
]
