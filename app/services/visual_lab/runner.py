"""Main loop — generate → analyze → think → build → ...

The runner is the public entrypoint that ties together the visual_lab
phases with the existing Playwright bots (ChatGPT + outsee). It applies:

* ``_safe_phase`` wrapping (catch, retry up to ``MAX_PHASE_RETRIES``).
* skip-on-3-errors at the iteration level.
* MAX_CONSECUTIVE_FAILED_ITERS pause-on-streak.
* Excel + knowledge_base.json refreshing between phases.

It is async because every phase ultimately drives Playwright bots, which
are async.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from app.services.visual_lab.analyze import analyze_iteration
from app.services.visual_lab.build import build_phase
from app.services.visual_lab.excel_export import rebuild_excel
from app.services.visual_lab.limits import (
    MAX_CONSECUTIVE_FAILED_ITERS,
    MAX_PHASE_RETRIES,
    PromptTooLongError,
    check_prompt_length,
)
from app.services.visual_lab.models import (
    ErrorRecord,
    IterDoc,
    IterImage,
    IterPrompt,
    ProjectDoc,
)
from app.services.visual_lab.storage import LabStorage
from app.services.visual_lab.think import think_phase

# Concrete async callable signatures used by the runner.
GenerateImageFn = Callable[[str, Path, str], Awaitable[None]]
"""(prompt, out_path, prompt_id_prefix) -> writes image to out_path."""

ChatGPTAskWithFilesFn = Callable[[str, list[Path]], Awaitable[str]]


# --------------------------- _safe_phase ------------------------------------


async def _safe_phase(
    name: str,
    fn: Callable[[], Awaitable[object]],
    iter_doc: IterDoc | None,
    *,
    retries: int = MAX_PHASE_RETRIES,
    backoff_seconds: tuple[int, ...] = (5, 15, 30),
) -> tuple[bool, object | None, Exception | None]:
    """Run a phase coroutine with retries; log errors into iter_doc.

    Returns ``(success, value, last_error)``. Never re-raises.
    """
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            value = await fn()
            return True, value, None
        except PromptTooLongError as e:
            # No point retrying — same prompt would come back.
            logger.error("visual_lab._safe_phase[{}]: {}", name, e)
            last_err = e
            if iter_doc is not None:
                iter_doc.error_log.append(
                    ErrorRecord(
                        phase=name, retry_attempt=attempt, message=str(e)
                    )
                )
            return False, None, e
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "visual_lab._safe_phase[{}]: attempt {}/{} failed: {}",
                name,
                attempt,
                retries,
                e,
            )
            if iter_doc is not None:
                iter_doc.error_log.append(
                    ErrorRecord(
                        phase=name, retry_attempt=attempt, message=str(e)
                    )
                )
            if attempt < retries:
                sleep_for = backoff_seconds[
                    min(attempt - 1, len(backoff_seconds) - 1)
                ]
                await asyncio.sleep(sleep_for)
    return False, None, last_err


# --------------------------- runner -----------------------------------------


class VisualLabRunner:
    """Drives a visual-lab project end-to-end.

    Stateless w.r.t. running iterations — every step reads/writes the
    on-disk JSON via :class:`LabStorage`, so the runner can be restarted
    at any time without losing progress.
    """

    def __init__(
        self,
        storage: LabStorage,
        *,
        generate_image: GenerateImageFn,
        chatgpt_ask_with_files: ChatGPTAskWithFilesFn,
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.storage = storage
        self.generate_image = generate_image
        self.chatgpt_ask_with_files = chatgpt_ask_with_files
        self.progress = progress

    async def _emit(self, message: str) -> None:
        if self.progress is None:
            return
        with contextlib.suppress(Exception):
            await self.progress(message)

    # --- single iteration ---

    async def run_one(self, *, run_build_before: bool = False) -> IterDoc:
        """Produce one new iteration (Gen + Analyze). Optionally Build first.

        Returns the IterDoc (even on failure — check ``iter_doc.phase``).
        """
        project = self.storage.load_project()
        if project is None:
            raise RuntimeError(
                f"project.json missing for {self.storage.slug!r}"
            )

        prompt_to_use = project.master_prompt or project.base_visual_prompt
        rationale = ""

        if run_build_before:
            await self._emit("🧠 Build: пересобираю master_prompt…")
            ok, value, err = await _safe_phase(
                "build",
                lambda: build_phase(
                    self.storage,
                    chatgpt_ask_with_files=self.chatgpt_ask_with_files,
                ),
                None,
            )
            if ok and value is not None:
                build = value
                prompt_to_use = build.master_prompt  # type: ignore[union-attr]
                rationale = "build phase ok"
            else:
                logger.warning(
                    "visual_lab.runner[{}]: build failed → falling back to previous master_prompt",
                    self.storage.slug,
                )

        # Hard guard before sending to outsee.
        try:
            check_prompt_length(prompt_to_use)
        except PromptTooLongError as e:
            return self._record_failed_iter(
                project, prompt_to_use, phase="error_prompt_too_long", note=str(e)
            )

        # Allocate the iter number atomically.
        new_iter_num = project.current_iter + 1
        project.current_iter = new_iter_num
        project.meta.total_iterations_attempted += 1
        project.status = "running"
        self.storage.save_project(project)

        prefix = f"[ID: lab-{self.storage.slug[:20]}-iter{new_iter_num}-{uuid.uuid4().hex[:8]}]"
        iter_dir = self.storage.iter_dir(new_iter_num)
        iter_dir.mkdir(parents=True, exist_ok=True)

        iter_doc = IterDoc(
            iter=new_iter_num,
            parent_iter=(
                new_iter_num - 1 if new_iter_num > 1 else None
            ),
            phase="running",
            prompt=IterPrompt(text=prompt_to_use, rationale=rationale),
        )
        self.storage.save_iter(iter_doc)

        # ---- 1. Generate ----
        image_path = self.storage.iter_image_path(new_iter_num, ext="jpg")
        await self._emit(
            f"🎨 Outsee: генерирую кадр iter={new_iter_num} ({len(prompt_to_use)} симв)…"
        )

        async def _gen() -> None:
            await self.generate_image(prompt_to_use, image_path, prefix)

        ok, _, err = await _safe_phase("outsee", _gen, iter_doc)
        if not ok or not image_path.exists():
            iter_doc.phase = "error_outsee"
            iter_doc.verdict = "FAILED"
            iter_doc.notes = f"outsee generate failed: {err}"
            self.storage.save_iter(iter_doc)
            return self._after_failed(project, iter_doc, "outsee")

        iter_doc.image = IterImage(
            path=str(image_path.relative_to(self.storage.root))
        )
        self.storage.save_iter(iter_doc)

        # ---- 2. Analyze ----
        await self._emit(f"🔍 GPT-Vision: анализирую iter={new_iter_num}…")

        async def _analyze() -> object:
            return await analyze_iteration(
                self.storage,
                iter_doc,
                image_path=image_path,
                chatgpt_ask_with_files=self.chatgpt_ask_with_files,
                include_references_in_attachments=(new_iter_num == 1),
            )

        ok, _, err = await _safe_phase("analyze", _analyze, iter_doc)
        if not ok:
            iter_doc.phase = "error_analyze"
            iter_doc.verdict = "FAILED"
            iter_doc.notes = f"analyze failed: {err}"
            self.storage.save_iter(iter_doc)
            return self._after_failed(project, iter_doc, "analyze")

        await self._emit(
            f"✅ iter={new_iter_num} score={iter_doc.weighted_score:.2f} "
            f"verdict={iter_doc.verdict}"
        )

        # Reset failure streak on success.
        if "consecutive_failed" in (project.meta.total_phase_errors_by_type or {}):
            project.meta.total_phase_errors_by_type["consecutive_failed"] = 0
        project.status = "idle"
        self.storage.save_project(project)
        rebuild_excel(self.storage)
        return iter_doc

    # --- auto loop ---

    async def run_auto(self, *, iterations: int) -> list[IterDoc]:
        """Run up to ``iterations`` iterations with auto think+build between."""
        project = self.storage.load_project()
        if project is None:
            raise RuntimeError(
                f"project.json missing for {self.storage.slug!r}"
            )

        if project.current_iter == 0:
            # iter 1: just gen + analyze with the base prompt.
            done = [await self.run_one(run_build_before=False)]
            if self._should_stop(done[-1]):
                return done
        else:
            done = []

        for _ in range(iterations - len(done)):
            # Think on the latest data.
            await self._emit("🧠 Think: думаю над следующим шагом…")
            ok, _, err = await _safe_phase(
                "think",
                lambda: think_phase(
                    self.storage,
                    chatgpt_ask_with_files=self.chatgpt_ask_with_files,
                ),
                None,
            )
            if not ok:
                logger.warning(
                    "visual_lab.runner[{}]: think failed: {} — skipping to next iter without new hypotheses",
                    self.storage.slug,
                    err,
                )

            iter_doc = await self.run_one(run_build_before=True)
            done.append(iter_doc)

            project = self.storage.load_project()
            if project is None or self._should_stop(iter_doc):
                break

        return done

    # --- helpers ---

    def _after_failed(
        self, project: ProjectDoc, iter_doc: IterDoc, phase: str
    ) -> IterDoc:
        project.meta.total_phase_errors_by_type[phase] = (
            project.meta.total_phase_errors_by_type.get(phase, 0) + 1
        )
        consec = project.meta.total_phase_errors_by_type.get(
            "consecutive_failed", 0
        ) + 1
        project.meta.total_phase_errors_by_type["consecutive_failed"] = consec
        project.last_error = iter_doc.notes
        if consec >= MAX_CONSECUTIVE_FAILED_ITERS:
            project.status = "paused"
            logger.error(
                "visual_lab.runner[{}]: {} consecutive failed iters — pausing",
                self.storage.slug,
                consec,
            )
        else:
            project.status = "idle"
        self.storage.save_project(project)
        rebuild_excel(self.storage)
        return iter_doc

    def _record_failed_iter(
        self,
        project: ProjectDoc,
        prompt: str,
        *,
        phase: str,
        note: str,
    ) -> IterDoc:
        new_iter_num = project.current_iter + 1
        project.current_iter = new_iter_num
        project.meta.total_iterations_attempted += 1
        self.storage.save_project(project)
        iter_doc = IterDoc(
            iter=new_iter_num,
            parent_iter=new_iter_num - 1 if new_iter_num > 1 else None,
            phase=phase,  # type: ignore[arg-type]
            prompt=IterPrompt(text=prompt[:5000]),
            verdict="FAILED",
            notes=note,
        )
        self.storage.save_iter(iter_doc)
        return self._after_failed(project, iter_doc, phase)

    def _should_stop(self, last_iter: IterDoc) -> bool:
        project = self.storage.load_project()
        if project is None:
            return True
        if project.status == "paused":
            return True
        if last_iter.weighted_score >= project.stopping_rules.target_avg_score:
            project.status = "completed"
            self.storage.save_project(project)
            return True
        if project.current_iter >= project.stopping_rules.max_iterations:
            project.status = "completed"
            self.storage.save_project(project)
            return True
        return False


__all__ = [
    "VisualLabRunner",
    "_safe_phase",
    "GenerateImageFn",
    "ChatGPTAskWithFilesFn",
]
