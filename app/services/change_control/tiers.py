from __future__ import annotations

T0, T1, T2, T3 = "T0", "T1", "T2", "T3"
TIERS = (T0, T1, T2, T3)

K_SUGGESTION = "suggestion"
K_PROMPT_VARIANT = "prompt_variant"
K_NODE_OPTIONS = "node_options"
K_STEP_PARAMS = "step_params"
K_PROMPT_TEXT = "prompt_text"
K_DEVELOPER_REVIEW = "developer_review"

KINDS_BY_TIER = {
    T0: frozenset({K_SUGGESTION}),
    T1: frozenset({K_PROMPT_VARIANT, K_NODE_OPTIONS, K_STEP_PARAMS}),
    T2: frozenset({K_PROMPT_TEXT}),
    T3: frozenset({K_DEVELOPER_REVIEW}),
}
APPLICABLE_TIERS = frozenset({T1, T2})

STEP_PARAM_LIMITS: dict[str, dict[str, dict]] = {
    "plan": {"duration_seconds": {"type": "number", "min": 5, "max": 1800}},
    "script": {"duration_seconds": {"type": "number", "min": 5, "max": 1800}},
    "split": {
        "cell_min_chars": {"type": "integer", "min": 1, "max": 1000},
        "cell_max_chars": {"type": "integer", "min": 1, "max": 2000},
        "cell_avg_min": {"type": "integer", "min": 1, "max": 2000},
        "cell_avg_max": {"type": "integer", "min": 1, "max": 2000},
    },
}
MAX_STEP_PARAM_FACTOR = 2.0

NODE_OPTION_FIELDS = ("outputMode", "emitKinds", "checkFix", "useSnapshot")

ALLOW_LIST: dict[str, dict[str, object]] = {
    "plan": {K_PROMPT_VARIANT: True, K_PROMPT_TEXT: True,
             K_STEP_PARAMS: tuple(STEP_PARAM_LIMITS["plan"])},
    "script": {K_PROMPT_VARIANT: True, K_PROMPT_TEXT: True,
              K_STEP_PARAMS: tuple(STEP_PARAM_LIMITS["script"])},
    "split": {K_PROMPT_VARIANT: True, K_PROMPT_TEXT: True,
             K_STEP_PARAMS: tuple(STEP_PARAM_LIMITS["split"])},
    "excel_gpt": {K_PROMPT_VARIANT: True, K_PROMPT_TEXT: True,
                  K_NODE_OPTIONS: NODE_OPTION_FIELDS},
}

PROMPT_MIN_CHARS = 40
PROMPT_MAX_CHARS = 200_000
PROMPT_MIN_RATIO = 0.25
PROMPT_MAX_RATIO = 4.0

MAX_OPEN_PER_PROJECT = 50
MAX_RATIONALE = 1000
MAX_DIFF_CHARS = 100_000


def allowed_kinds(node_type: str) -> dict[str, object]:
    return dict(ALLOW_LIST.get(node_type, {}))


def describe_allow_list() -> dict:
    return {
        "tiers": {
            T0: {"title_ru": "Только совет (не применяется)", "applicable": False,
                 "kinds": sorted(KINDS_BY_TIER[T0])},
            T1: {"title_ru": "Параметры из явного списка", "applicable": True,
                 "kinds": sorted(KINDS_BY_TIER[T1])},
            T2: {"title_ru": "Текст промпта проекта (новый вариант)", "applicable": True,
                 "kinds": sorted(KINDS_BY_TIER[T2])},
            T3: {"title_ru": "Код, логика, общие файлы: только запись для разработчика",
                 "applicable": False, "kinds": sorted(KINDS_BY_TIER[T3])},
        },
        "node_types": {nt: {k: (list(v) if isinstance(v, tuple) else v) for k, v in kinds.items()}
                       for nt, kinds in ALLOW_LIST.items()},
        "step_param_limits": STEP_PARAM_LIMITS,
        "max_step_param_factor": MAX_STEP_PARAM_FACTOR,
        "prompt_limits": {"min_chars": PROMPT_MIN_CHARS, "max_chars": PROMPT_MAX_CHARS,
                          "min_ratio": PROMPT_MIN_RATIO, "max_ratio": PROMPT_MAX_RATIO},
    }
