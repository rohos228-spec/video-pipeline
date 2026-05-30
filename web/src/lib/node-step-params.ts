/** Параметры шагов plan / script / split → блок в GPT-сообщении. */

export const CHARS_PER_SEC = 14;

export type PlanScriptStepParams = {
  duration_seconds?: number | null;
};

export type SplitStepParams = {
  cell_min_chars?: number | null;
  cell_max_chars?: number | null;
  cell_avg_min?: number | null;
  cell_avg_max?: number | null;
};

export type NodeStepParamsMeta = {
  plan?: PlanScriptStepParams;
  script?: PlanScriptStepParams;
  split?: SplitStepParams;
};

export function readNodeStepParams(meta: Record<string, unknown> | undefined): NodeStepParamsMeta {
  const raw = meta?.node_step_params;
  if (!raw || typeof raw !== "object") return {};
  return raw as NodeStepParamsMeta;
}

export function charCountFromDuration(sec: number | null | undefined): number | null {
  if (sec == null || !Number.isFinite(sec) || sec <= 0) return null;
  return Math.round(sec * CHARS_PER_SEC);
}

export function effectiveDurationSeconds(
  params: NodeStepParamsMeta,
  step: "plan" | "script",
): number | null {
  const own = params[step]?.duration_seconds;
  if (own != null && own > 0) return own;
  if (step === "script") {
    const fromPlan = params.plan?.duration_seconds;
    if (fromPlan != null && fromPlan > 0) return fromPlan;
  }
  return null;
}

export function withNodeStepParams(
  meta: Record<string, unknown>,
  step: keyof NodeStepParamsMeta,
  patch: PlanScriptStepParams | SplitStepParams,
): Record<string, unknown> {
  const current = readNodeStepParams(meta);
  return {
    ...meta,
    node_step_params: {
      ...current,
      [step]: { ...(current[step] || {}), ...patch },
    },
  };
}
