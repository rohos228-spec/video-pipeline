"""Visual prompt analysis & improvement laboratory.

Iterative, self-learning system for visual prompts. Generates images via
``outsee.io`` (Banana Pro 16:9 / Relax), scores each iteration with
ChatGPT-Vision against 20 hard-coded visual criteria across 6 groups,
accumulates a JSON knowledge base of word effects, formulates and tests
hypotheses, then evolves a master prompt.

All state lives on disk under ``data/test_prompts/<slug>/`` next to the
existing legacy "test prompts" project tree:

    data/test_prompts/<slug>/
        project.json
        knowledge_base.json
        thinking_log.json
        word_tests.json
        report.md
        scores.xlsx           ← updated every iteration, re-uploaded to GPT
        reference/
            ref_1.png + ref_1.json (prompt + scores)
            ...
        iter_<N>/
            iter.json
            image.jpg
            chat/raw_analyze.txt (for debugging)

ChatGPT runs through the existing Playwright bot (``app/bots/chatgpt.py``);
no OpenAI API key is used. All phases validate their JSON response against
Pydantic models and retry up to ``MAX_PHASE_RETRIES`` times before marking
the iteration as failed and moving on (skip-on-3-errors).

CLI:

    python -m app.services.visual_lab.cli analyze --slug my-project
    python -m app.services.visual_lab.cli think  --slug my-project
    python -m app.services.visual_lab.cli test   --slug my-project --word "crisp pixel edges"
    python -m app.services.visual_lab.cli build  --slug my-project
    python -m app.services.visual_lab.cli full   --slug my-project --iterations 5
"""

from app.services.visual_lab.criteria import (
    CRITERIA,
    CRITERION_IDS,
    GROUPS,
    weighted_score,
)
from app.services.visual_lab.limits import (
    MAX_CONSECUTIVE_FAILED_ITERS,
    MAX_PHASE_RETRIES,
    MAX_PROMPT_CHARS,
    PromptTooLongError,
    check_prompt_length,
)
from app.services.visual_lab.models import (
    AnalysisResult,
    BuildResult,
    IterDoc,
    KnowledgeBase,
    ProjectDoc,
    ReferenceImage,
    ThinkResult,
    WordTest,
)
from app.services.visual_lab.storage import (
    LabStorage,
    project_dir,
)

__all__ = [
    "CRITERIA",
    "CRITERION_IDS",
    "GROUPS",
    "weighted_score",
    "MAX_CONSECUTIVE_FAILED_ITERS",
    "MAX_PHASE_RETRIES",
    "MAX_PROMPT_CHARS",
    "PromptTooLongError",
    "check_prompt_length",
    "AnalysisResult",
    "BuildResult",
    "IterDoc",
    "KnowledgeBase",
    "ProjectDoc",
    "ReferenceImage",
    "ThinkResult",
    "WordTest",
    "LabStorage",
    "project_dir",
]
